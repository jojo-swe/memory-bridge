"""FUSE block export: error mapping without a mount, and a real mount when possible."""

import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from memory_bridge.simulation import SimulatedMemory

FUSEPY = importlib.util.find_spec("fuse") is not None
MOUNTABLE = FUSEPY and Path("/dev/fuse").exists() and bool(
    shutil.which("fusermount3") or shutil.which("fusermount"))


@unittest.skipUnless(FUSEPY, "Requires fusepy")
class OperationsTests(unittest.TestCase):
    def setUp(self):
        from memory_bridge.block_export import BlockFileOperations
        import fuse
        self.fuse = fuse
        self.storage = SimulatedMemory(16384)
        self.operations = BlockFileOperations(self.storage, max_request_bytes=4096)

    def test_volume_roundtrip_chunks_requests(self):
        data = bytes(range(256)) * 32  # 8192 bytes: two storage requests
        self.assertEqual(self.operations("write", "/volume", data, 4096, 0), len(data))
        self.assertEqual(self.operations("read", "/volume", 8192, 4096, 0), data)
        stats = self.operations.stats.snapshot()
        self.assertEqual((stats["reads"], stats["writes"], stats["errors"]), (1, 1, 0))
        self.assertEqual(self.operations("read", "/volume", 10, 16384, 0), b"")

    def test_storage_failure_maps_to_eio(self):
        self.storage.fail_reads = True
        with self.assertRaises(self.fuse.FuseOSError) as context:
            self.operations("read", "/volume", 4096, 0, 0)
        self.assertEqual(context.exception.errno, errno.EIO)
        self.storage.fail_writes = True
        with self.assertRaises(self.fuse.FuseOSError) as context:
            self.operations("write", "/volume", b"x", 0, 0)
        self.assertEqual(context.exception.errno, errno.EIO)
        self.assertEqual(self.operations.stats.snapshot()["errors"], 2)

    def test_resize_and_unknown_paths_are_rejected(self):
        with self.assertRaises(self.fuse.FuseOSError):
            self.operations("truncate", "/volume", 4096)
        self.assertEqual(self.operations("truncate", "/volume", 16384), 0)
        with self.assertRaises(self.fuse.FuseOSError) as context:
            self.operations("write", "/stats.json", b"x", 0, 0)
        self.assertEqual(context.exception.errno, errno.EACCES)
        with self.assertRaises(self.fuse.FuseOSError) as context:
            self.operations("getattr", "/other")
        self.assertEqual(context.exception.errno, errno.ENOENT)
        with self.assertRaises(self.fuse.FuseOSError) as context:
            self.operations("write", "/volume", b"xx", 16383, 0)
        self.assertEqual(context.exception.errno, errno.ENOSPC)

    def test_stats_file_reports_storage_snapshot(self):
        report = json.loads(self.operations("read", "/stats.json", 65536, 0, 1))
        self.assertEqual(report["capacity_bytes"], 16384)
        self.assertIn("export", report)
        self.assertEqual(self.operations("getattr", "/volume")["st_size"], 16384)

    def test_flush_without_backend_flush_is_a_noop(self):
        self.assertEqual(self.operations("fsync", "/volume", 0, 0), 0)
        self.assertEqual(self.operations.stats.snapshot()["flushes"], 1)


@unittest.skipUnless(MOUNTABLE, "Requires fusepy, /dev/fuse and fusermount")
class MountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.mountpoint = Path(self.directory.name) / "mnt"
        self.mountpoint.mkdir()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "memory_bridge.block_export_cli", str(self.mountpoint),
             "--size-mib", "1", "--backing", "temp", "--directory", self.directory.name,
             "--allow-unlocked"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if line and json.loads(line).get("ready"):
                return
            if self.process.poll() is not None:
                error = self.process.stderr.read()
                if "permission denied" in error.lower() or "/dev/fuse" in error.lower():
                    self.skipTest("FUSE mount not permitted here: " + error.strip()[-200:])
                self.fail(error)
        self.fail("export did not become ready")

    def stop(self):
        tool = shutil.which("fusermount3") or shutil.which("fusermount")
        if self.process.poll() is None:
            subprocess.run([tool, "-u", str(self.mountpoint)], capture_output=True)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.communicate()

    def test_volume_roundtrip_through_kernel(self):
        volume = self.mountpoint / "volume"
        self.assertEqual(volume.stat().st_size, 1024 * 1024)
        payload = os.urandom(65536)
        with open(volume, "r+b", buffering=0) as handle:
            handle.seek(4096)
            handle.write(payload)
            os.fsync(handle.fileno())
            handle.seek(4096)
            self.assertEqual(handle.read(65536), payload)
            handle.seek(4094)
            self.assertEqual(handle.read(4), bytes(2) + payload[:2])
        report = json.loads((self.mountpoint / "stats.json").read_text())
        self.assertGreaterEqual(report["export"]["writes"], 1)
        self.assertGreaterEqual(report["export"]["flushes"], 1)
        self.assertEqual(report["export"]["errors"], 0)
        with self.assertRaises(OSError):
            os.truncate(volume, 4096)


if __name__ == "__main__":
    unittest.main()
