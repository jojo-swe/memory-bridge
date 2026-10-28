"""O_DIRECT local backing: alignment handling, flush, bounds and cleanup."""

import errno
import os
import random
import tempfile
import unittest

from memory_bridge.direct_file import DirectFileStorage


class DirectFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        try:
            self.storage = DirectFileStorage(64 * 1024, self.directory.name, max_request_bytes=16384)
        except OSError as error:
            if error.errno != errno.EINVAL:
                raise
            self.skipTest(f"O_DIRECT unsupported on this filesystem: {error}")
        self.addCleanup(self.storage.close)

    def test_randomized_unaligned_io_matches_reference(self):
        reference = bytearray(64 * 1024)
        generator = random.Random(7)
        for _ in range(300):
            offset = generator.randrange(0, 64 * 1024 - 1)
            length = generator.randrange(1, min(16384, 64 * 1024 - offset) + 1)
            data = generator.randbytes(length)
            self.storage.write(offset, data)
            reference[offset:offset + length] = data
            check = generator.randrange(0, 64 * 1024 - 1)
            count = generator.randrange(1, min(16384, 64 * 1024 - check) + 1)
            self.assertEqual(self.storage.read(check, count), bytes(reference[check:check + count]))
        self.assertEqual(self.storage.read(0, 16384), bytes(reference[:16384]))

    def test_partial_write_preserves_neighbours(self):
        self.storage.write(0, b"a" * 8192)
        self.storage.write(4094, b"XY")
        self.assertEqual(self.storage.read(4092, 6), b"aaXYaa")

    def test_bounds_and_maximum(self):
        with self.assertRaises(ValueError):
            self.storage.read(64 * 1024 - 1, 2)
        with self.assertRaises(ValueError):
            self.storage.read(0, 16385)
        with self.assertRaises(ValueError):
            self.storage.write(0, bytearray(4))
        with self.assertRaises(ValueError):
            DirectFileStorage(4096, self.directory.name, emulated_latency_seconds=2)

    def test_flush_and_close(self):
        self.storage.write(0, b"sync")
        self.storage.flush()
        self.assertEqual(self.storage.snapshot()["emulated_latency_seconds"], 0.0)
        self.storage.close()
        self.storage.close()  # idempotent
        with self.assertRaises(RuntimeError):
            self.storage.read(0, 4)
        with self.assertRaises(RuntimeError):
            self.storage.flush()

    def test_backing_is_unlinked(self):
        self.assertEqual(os.listdir(self.directory.name), [])


if __name__ == "__main__":
    unittest.main()
