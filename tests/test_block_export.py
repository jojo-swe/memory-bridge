"""Borrower block export: plugin callbacks (fake nbdkit) and real NBD integration."""

import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
NBD_AVAILABLE = bool(shutil.which("nbdkit") and importlib.util.find_spec("nbd"))


def _fake_nbdkit():
    module = types.ModuleType("nbdkit")
    module.THREAD_MODEL_SERIALIZE_ALL_REQUESTS = 0
    module.FUA_EMULATE = 1
    module.CACHE_NONE = 0
    module.FLAG_FUA = 4
    return module


def _load_plugin():
    with patch.dict(sys.modules, {"nbdkit": _fake_nbdkit()}):
        sys.modules.pop("memory_bridge.block_export", None)
        return importlib.import_module("memory_bridge.block_export")


class PluginCallbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.plugin = _load_plugin()

    def tearDown(self):
        if self.plugin._state is not None:
            self.plugin._state.store.close()
        sys.modules.pop("memory_bridge.block_export", None)
        self.directory.cleanup()

    def configure(self, **values):
        settings = {"size_mib": "1", "local_dir": self.directory.name,
                    "lock_memory": "false", **values}
        for key, value in settings.items():
            self.plugin.config(key, value)
        self.plugin.config_complete()

    def test_advertises_only_implemented_guarantees(self):
        self.configure()
        handle = self.plugin.open(False)
        self.assertTrue(self.plugin.can_flush(handle))
        self.assertEqual(self.plugin.can_fua(handle), 1)  # Emulated: write + flush.
        for name in ("can_multi_conn", "can_trim", "can_zero"):
            self.assertFalse(getattr(self.plugin, name)(handle), name)
        self.assertEqual(self.plugin.block_size(handle), (1, 4096, 1024 * 1024))
        self.assertEqual(self.plugin.get_size(handle), 1024 * 1024)

    def test_exclusive_connection_and_sequential_reconnect(self):
        self.configure()
        first = self.plugin.open(False)
        with self.assertRaisesRegex(RuntimeError, "exclusive"):
            self.plugin.open(False)
        self.plugin.pwrite(first, bytearray(b"persist"), 100, 0)
        self.plugin.close(first)
        second = self.plugin.open(False)
        buffer = bytearray(7)
        self.plugin.pread(second, buffer, 100, 0)
        self.assertEqual(bytes(buffer), b"persist")

    def test_large_requests_are_split_and_flush_reaches_local_copy(self):
        self.configure(max_request_kib="64", transfer_kib="64")
        handle = self.plugin.open(False)
        data = os.urandom(200 * 1024)
        self.plugin.pwrite(handle, bytearray(data), 4096, 0)
        buffer = bytearray(len(data))
        self.plugin.pread(handle, buffer, 4096, 0)
        self.assertEqual(bytes(buffer), data)
        with patch.object(self.plugin._state.local, "flush") as flush:
            self.plugin.flush(handle, 0)
            flush.assert_called_once()
        self.assertEqual(self.plugin._state.requests["flush"], 1)

    def test_readonly_connection_refuses_writes(self):
        self.configure()
        handle = self.plugin.open(True)
        self.assertFalse(self.plugin.can_write(handle))
        with self.assertRaises(PermissionError):
            self.plugin.pwrite(handle, bytearray(1), 0, 0)

    def test_stats_writer_does_not_call_the_open_callback(self):
        # Regression: the module-level open() callback shadows the builtin.
        stats = Path(self.directory.name) / "stats.json"
        self.configure()
        with patch.object(self.plugin, "open", side_effect=AssertionError("callback")):
            self.plugin._write_stats(stats)
        report = json.loads(stats.read_text())
        self.assertFalse(report["client_connected"])
        self.assertEqual(report["store"]["capacity_bytes"], 1024 * 1024)
        self.assertFalse(report["protections"]["mlockall"])

    def test_local_failure_is_loud_and_permanent(self):
        self.configure()
        handle = self.plugin.open(False)
        with patch("os.pwritev", side_effect=OSError(5, "injected")):
            with self.assertRaises(Exception):
                self.plugin.pwrite(handle, bytearray(4096), 0, 0)
        with self.assertRaises(Exception):
            self.plugin.pread(handle, bytearray(4096), 0, 0)
        self.assertEqual(self.plugin._state.requests["errors"], 2)

    def test_configuration_validation(self):
        bad = [
            {},  # Missing backing.
            {"local": "x", "local_dir": "y"},
            {"local_dir": "y", "create": "true"},
            {"local_dir": "y", "donor_host": "127.0.0.1"},  # Key missing.
            {"local_dir": "y", "max_request_kib": "100"},  # Not a power of two.
            {"local_dir": "y", "transfer_kib": "2048"},  # Above maximum.
        ]
        for values in bad:
            plugin = _load_plugin()
            plugin.config("size_mib", "1")
            plugin.config("lock_memory", "false")
            for key, value in values.items():
                plugin.config(key, value)
            with self.assertRaises(ValueError, msg=values):
                plugin.config_complete()
        with self.assertRaises(ValueError):
            self.plugin.config("unknown", "1")
        with self.assertRaises(ValueError):
            self.plugin.config("create", "maybe")


class ExportCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.private = Path(self.directory.name) / "run"
        self.private.mkdir(mode=0o700)

    def tearDown(self):
        self.directory.cleanup()

    def parse(self, *extra):
        from memory_bridge.export_cli import parse
        return parse(["--socket", str(self.private / "s.sock"), "--size-mib", "4",
                      "--local-dir", self.directory.name, *extra])

    def test_command_is_unix_only_and_forwards_settings(self):
        from memory_bridge.export_cli import build_command
        args = self.parse("--no-lock-memory", "--timeout", "1.5")
        command = build_command(args, "/usr/bin/nbdkit", "/usr/bin/python3")
        self.assertIn("--unix", command)
        self.assertNotIn("--port", " ".join(command))
        self.assertNotIn("--ipaddr", " ".join(command))
        self.assertIn("lock_memory=false", command)
        self.assertIn("timeout=1.5", command)
        self.assertIn("worker_python=/usr/bin/python3", command)

    def test_rejects_shared_socket_directory_and_bad_combinations(self):
        from memory_bridge.export_cli import parse
        shared = Path(self.directory.name) / "shared"
        shared.mkdir(mode=0o755)
        os.chmod(shared, 0o755)
        cases = [
            ["--socket", str(shared / "s.sock"), "--size-mib", "4", "--local-dir", "."],
            ["--socket", str(self.private / "s.sock"), "--size-mib", "4",
             "--local-dir", ".", "--create"],
            ["--socket", str(self.private / "s.sock"), "--size-mib", "4",
             "--local-dir", ".", "--donor-host", "127.0.0.1"],
            ["--socket", str(self.private / "s.sock"), "--size-mib", "0", "--local-dir", "."],
        ]
        for argv in cases:
            with self.assertRaises(SystemExit, msg=argv), patch("sys.stderr"):
                parse(argv)


@unittest.skipUnless(NBD_AVAILABLE, "Install nbdkit, nbdkit-plugin-python and python3-libnbd")
class BlockExportIntegrationTests(unittest.TestCase):
    """Real nbdkit + libnbd. These must run (not skip) in strict CI."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.base = Path(self.directory.name)
        self.run_dir = self.base / "run"
        self.run_dir.mkdir(mode=0o700)
        self.processes = []

    def tearDown(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                try:
                    os.kill(process.pid, signal.SIGCONT)
                except ProcessLookupError:
                    pass
                process.kill()
            process.wait(timeout=10)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        self.directory.cleanup()

    def start_export(self, name, *extra, expect_socket=True):
        sock = self.run_dir / f"{name}.sock"
        process = subprocess.Popen(
            [sys.executable, "-m", "memory_bridge", "export", "--socket", str(sock),
             "--no-lock-memory", *extra],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.processes.append(process)
        if expect_socket:
            deadline = time.monotonic() + 15
            while not sock.exists():
                if process.poll() is not None:
                    self.fail("export exited: " + process.stderr.read().decode()[-2000:])
                if time.monotonic() > deadline:
                    self.fail("export socket did not appear")
                time.sleep(0.05)
        return process, sock

    def start_donor(self, size_mib=4):
        key = self.base / "session.psk"
        subprocess.run([sys.executable, "bridge.py", "keygen", str(key)], cwd=ROOT,
                       check=True, capture_output=True)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        donor = subprocess.Popen([sys.executable, "bridge.py", "donor", "--key", str(key),
                                  "--size-mib", str(size_mib), "--port", str(port)],
                                 cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(donor)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with socket.socket() as client:
                if client.connect_ex(("127.0.0.1", port)) == 0:
                    return donor, key, port
            if donor.poll() is not None:
                self.fail("donor exited: " + donor.stderr.read().decode()[-2000:])
            time.sleep(0.05)
        self.fail("donor did not listen")

    @staticmethod
    def connect(sock):
        import nbd
        handle = nbd.NBD()
        handle.connect_unix(str(sock))
        return handle

    @staticmethod
    def read_all(handle, size, chunk=1024 * 1024):
        return b"".join(bytes(handle.pread(min(chunk, size - offset), offset))
                        for offset in range(0, size, chunk))

    @staticmethod
    def wait_stats(path, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                last = json.loads(path.read_text())
                if predicate(last):
                    return last
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise AssertionError(f"stats condition not met: {last}")

    def test_local_only_export_flags_data_and_exclusivity(self):
        import nbd
        stats = self.base / "stats.json"
        _, sock = self.start_export("a", "--size-mib", "4", "--local-dir", str(self.base),
                                    "--stats", str(stats))
        handle = self.connect(sock)
        self.assertTrue(handle.can_flush())
        self.assertTrue(handle.can_fua())
        self.assertFalse(handle.can_multi_conn())
        self.assertFalse(handle.can_trim())
        data = os.urandom(1024 * 1024)
        handle.pwrite(data, 12345, nbd.CMD_FLAG_FUA)
        handle.flush()
        self.assertEqual(bytes(handle.pread(len(data), 12345)), data)
        with self.assertRaises(nbd.Error):
            self.connect(sock)  # A second concurrent client would corrupt swap.
        report = self.wait_stats(stats, lambda r: r["requests"]["flush"] >= 2)
        self.assertFalse(report["remote_configured"])
        self.assertEqual(report["requests"]["errors"], 0)
        handle.shutdown()

    def test_remote_cache_serves_reads_and_frozen_donor_falls_back(self):
        donor, key, port = self.start_donor()
        stats = self.base / "stats.json"
        _, sock = self.start_export("r", "--size-mib", "4", "--local-dir", str(self.base),
                                    "--donor-host", "127.0.0.1", "--donor-port", str(port),
                                    "--key", str(key), "--timeout", "1", "--stats", str(stats))
        handle = self.connect(sock)
        data = os.urandom(4 * 1024 * 1024)
        megabyte = 1024 * 1024  # The advertised maximum request size.
        for offset in range(0, len(data), megabyte):
            handle.pwrite(data[offset:offset + megabyte], offset)
        self.assertEqual(self.read_all(handle, len(data)), data)
        report = self.wait_stats(stats, lambda r: r["store"]["remote_read_bytes"] > 0)
        self.assertTrue(report["store"]["remote_enabled"])
        self.assertIsNotNone(report["worker_pid"])
        os.kill(donor.pid, signal.SIGSTOP)
        started = time.monotonic()
        self.assertEqual(self.read_all(handle, len(data)), data)
        elapsed = time.monotonic() - started
        # One remote deadline (1 s) plus local reads, never an indefinite hang.
        self.assertLess(elapsed, 5)
        report = self.wait_stats(stats, lambda r: not r["store"]["remote_enabled"])
        self.assertGreaterEqual(report["store"]["remote_errors"], 1)
        handle.pwrite(b"after-fault", 4096)
        self.assertEqual(bytes(handle.pread(11, 4096)), b"after-fault")
        handle.shutdown()

    def test_named_copy_survives_export_kill_and_is_exclusive(self):
        local = self.base / "copy.bin"
        process, sock = self.start_export("n1", "--size-mib", "4", "--local", str(local),
                                          "--create")
        handle = self.connect(sock)
        pattern = bytes(range(256)) * 64
        for index in range(32):
            handle.pwrite(pattern, index * 65536 + 7)
        # A second export on the same copy must refuse to start.
        rival, _ = self.start_export("n2", "--size-mib", "4", "--local", str(local),
                                     expect_socket=False)
        self.assertNotEqual(rival.wait(timeout=20), 0)
        process.kill()
        process.wait(timeout=10)
        sock.unlink()
        _, sock = self.start_export("n1", "--size-mib", "4", "--local", str(local))
        handle = self.connect(sock)
        for index in range(32):
            self.assertEqual(bytes(handle.pread(len(pattern), index * 65536 + 7)), pattern)
        handle.shutdown()


if __name__ == "__main__":
    unittest.main()
