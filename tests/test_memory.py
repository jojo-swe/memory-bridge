"""Tests of actual locked memory, boundaries, session isolation and key handling."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bridge import private_key
from ram_plugin import LockedMemory, SingleSession


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.memory = LockedMemory(8192)

    def tearDown(self):
        self.memory.close()

    def test_initial_zeroes(self):
        self.assertEqual(self.memory.read(0, 8192), bytes(8192))

    def test_overwrite_and_edges(self):
        self.memory.write(4094, b"abcdef")
        self.memory.write(4096, b"XY")
        self.assertEqual(self.memory.read(4094, 6), b"abXYef")
        self.memory.write(8191, b"Z")
        self.assertEqual(self.memory.read(8191, 1), b"Z")

    def test_bounds(self):
        for offset, count in [(-1, 1), (8192, 1), (0, -1), (8190, 3)]:
            with self.subTest(offset=offset, count=count):
                with self.assertRaises(ValueError):
                    self.memory.read(offset, count)
        with self.assertRaises(ValueError):
            self.memory.write(8191, b"XX")

    def test_one_session_even_after_disconnect(self):
        session = SingleSession(self.memory)
        session.acquire()
        with self.assertRaises(RuntimeError):
            session.acquire()
        self.memory.close()
        with self.assertRaises(RuntimeError):
            session.acquire()

    def test_closed_memory_cannot_return_stale_data(self):
        self.memory.write(0, b"private")
        self.memory.close()
        with self.assertRaises(RuntimeError):
            self.memory.read(0, 7)
        self.memory.close()  # Cleanup is idempotent.

    def test_lock_failure_is_fatal(self):
        # A separate process can lower its limit without affecting this runner.
        code = "import resource; resource.setrlimit(resource.RLIMIT_MEMLOCK,(0,0)); from ram_plugin import LockedMemory; LockedMemory(4096)"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Cannot lock RAM", result.stderr)


class KeyTests(unittest.TestCase):
    def test_symlink_key_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "key.psk"
            target.write_text("borrower:" + "a" * 64 + "\n")
            target.chmod(0o600)
            link = Path(directory) / "link.psk"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                private_key(link)

    def test_oversized_key_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.psk"
            path.write_text("borrower:" + "a" * 10000)
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                private_key(path)

    def test_fifo_key_does_not_block(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.psk"
            os.mkfifo(path, 0o600)
            with self.assertRaises(ValueError):
                private_key(path)

    def test_key_generation_permissions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.psk"
            command = [sys.executable, "bridge.py", "keygen", str(path)]
            subprocess.run(command, check=True, capture_output=True)
            original = path.read_bytes()
            self.assertEqual(private_key(path), path)
            result = subprocess.run(command, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(original, path.read_bytes())
            os.chmod(path, 0o644)
            with self.assertRaises(ValueError):
                private_key(path)


if __name__ == "__main__":
    unittest.main()
