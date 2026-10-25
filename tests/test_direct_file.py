"""DirectFileStorage: O_DIRECT local copy, exclusivity, restart and failure."""

import os
from pathlib import Path
import random
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from memory_bridge.core import BackendError
from memory_bridge.direct_file import DirectFileStorage

ROOT = Path(__file__).resolve().parent.parent
LINUX = sys.platform.startswith("linux") and hasattr(os, "O_DIRECT")


def _fd_flags(fd):
    with open(f"/proc/self/fdinfo/{fd}") as info:
        for line in info:
            if line.startswith("flags:"):
                return int(line.split()[1], 8)
    raise AssertionError("no flags line")


@unittest.skipUnless(LINUX, "O_DIRECT requires Linux")
class DirectFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.path = Path(self.directory.name) / "local.bin"

    def tearDown(self):
        self.directory.cleanup()

    def test_temporary_backing_bypasses_page_cache_and_round_trips(self):
        with DirectFileStorage.temporary(self.directory.name, 64 * 1024,
                                         max_request_bytes=16384) as store:
            # The swap experiment depends on this: no buffered page cache copy.
            self.assertTrue(_fd_flags(store._fd) & os.O_DIRECT)
            self.assertTrue(store.temporary)
            store.write(4096, b"x" * 8192)
            self.assertEqual(store.read(4096, 8192), b"x" * 8192)
            self.assertEqual(store.read(0, 4096), bytes(4096))
            # O_TMPFILE: nothing is visible in the directory.
            self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_randomized_unaligned_io_matches_reference(self):
        size = 64 * 1024
        reference = bytearray(size)
        generator = random.Random(7)
        with DirectFileStorage.temporary(self.directory.name, size,
                                         max_request_bytes=16384) as store:
            for _ in range(600):
                count = generator.randint(1, 16384)
                offset = generator.randint(0, size - count)
                if generator.random() < 0.5:
                    data = generator.randbytes(count)
                    store.write(offset, data)
                    reference[offset:offset + count] = data
                else:
                    self.assertEqual(store.read(offset, count),
                                     bytes(reference[offset:offset + count]))
            self.assertEqual(store.read(0, 16384), bytes(reference[:16384]))

    def test_unaligned_single_block_edges(self):
        with DirectFileStorage.temporary(self.directory.name, 16384) as store:
            store.write(0, b"A" * 16384)
            store.write(4090, b"0123456789AB")  # Spans one boundary.
            self.assertEqual(store.read(4088, 16), b"AA0123456789ABAA")
            store.write(5000, b"z")  # Inside one block, both edges unaligned.
            self.assertEqual(store.read(4999, 3), b"AzA")

    def test_named_backing_is_exclusive_private_and_size_checked(self):
        with DirectFileStorage.open_named(self.path, 16384, create=True) as store:
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(BackendError):
                DirectFileStorage.open_named(self.path, 16384, create=False)
            with self.assertRaises(FileExistsError):
                DirectFileStorage.open_named(self.path, 16384, create=True)
            store.write(0, b"keep")
        with self.assertRaisesRegex(ValueError, "different size"):
            DirectFileStorage.open_named(self.path, 8192, create=False)
        with DirectFileStorage.open_named(self.path, 16384, create=False) as store:
            self.assertEqual(store.read(0, 4), b"keep")

    def test_symlink_and_shared_files_rejected(self):
        DirectFileStorage.open_named(self.path, 4096, create=True).close()
        link = Path(self.directory.name) / "link"
        link.symlink_to(self.path)
        with self.assertRaises(OSError):
            DirectFileStorage.open_named(link, 4096, create=False)
        hard = Path(self.directory.name) / "hard"
        os.link(self.path, hard)
        with self.assertRaisesRegex(ValueError, "single-link"):
            DirectFileStorage.open_named(self.path, 4096, create=False)

    def test_acknowledged_writes_survive_process_kill(self):
        # Restart contract: a write that returned is visible to the next owner.
        code = ("import os,sys,signal; from memory_bridge.direct_file import DirectFileStorage;"
                "s=DirectFileStorage.open_named(sys.argv[1], 1<<20, create=True);"
                "[s.write(i*4096+13, bytes([i % 251 + 1])*1000) for i in range(200)];"
                "print('acked', flush=True); os.kill(os.getpid(), signal.SIGKILL)")
        result = subprocess.run([sys.executable, "-c", code, str(self.path)],
                                cwd=ROOT, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        self.assertIn(b"acked", result.stdout)
        with DirectFileStorage.open_named(self.path, 1 << 20, create=False) as store:
            for i in range(200):
                self.assertEqual(store.read(i * 4096 + 13, 1000), bytes([i % 251 + 1]) * 1000)

    def test_flush_and_failures_invalidate(self):
        store = DirectFileStorage.temporary(self.directory.name, 16384)
        store.write(0, b"a" * 4096)
        store.flush()
        with patch("os.pwritev", side_effect=OSError(5, "injected")):
            with self.assertRaises(OSError):
                store.write(0, b"b" * 4096)
        # A partially failed write leaves unknown contents: refuse everything.
        with self.assertRaises(BackendError):
            store.read(0, 4096)
        with self.assertRaises(BackendError):
            store.flush()
        store.close()
        store.close()  # Idempotent.

    def test_failed_fdatasync_is_never_retried_as_success(self):
        store = DirectFileStorage.temporary(self.directory.name, 16384)
        with patch("os.fdatasync", side_effect=OSError(5, "injected")):
            with self.assertRaises(OSError):
                store.flush()
        with self.assertRaises(BackendError):
            store.flush()
        store.close()

    def test_argument_validation(self):
        for size in (0, 4095, -4096, 4096.0):
            with self.assertRaises(ValueError):
                DirectFileStorage.temporary(self.directory.name, size)
        with self.assertRaises(ValueError):
            DirectFileStorage.temporary(self.directory.name, 4096, max_request_bytes=1000)
        with DirectFileStorage.temporary(self.directory.name, 8192,
                                         max_request_bytes=4096) as store:
            for offset, count in ((-1, 1), (0, 0), (8191, 2), (0, 4097)):
                with self.assertRaises(ValueError):
                    store.read(offset, count)
            with self.assertRaises(ValueError):
                store.write(0, bytearray(1))


if __name__ == "__main__":
    unittest.main()
