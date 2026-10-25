"""Exercise actual filesystem I/O and allocation cleanup."""
import os
import random
import tempfile
import unittest
from unittest.mock import patch

from memory_bridge.local_disk import TemporaryFileStorage


class LocalDiskTests(unittest.TestCase):
    def test_zero_initialized_bounds_and_roundtrip(self):
        with TemporaryFileStorage(8192) as disk:
            self.assertEqual(disk.read(0, 8192), bytes(8192))
            disk.write(4094, b"across page")
            self.assertEqual(disk.read(4094, 11), b"across page")
            for offset, count in [(-1, 1), (8192, 1), (0, 0), (True, 1)]:
                with self.assertRaises(ValueError):
                    disk.read(offset, count)
            with self.assertRaises(ValueError):
                disk.write(0, bytearray(b"x"))
        disk.close()
        with self.assertRaises(RuntimeError):
            disk.read(0, 1)

    def test_private_file_and_cleanup_on_allocation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            file = tempfile.TemporaryFile(dir=directory, buffering=0)
            self.assertEqual(os.fstat(file.fileno()).st_mode & 0o777, 0o600)
            with patch("memory_bridge.local_disk.tempfile.TemporaryFile", return_value=file):
                with patch("memory_bridge.local_disk.os.posix_fallocate", create=True,
                           side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        TemporaryFileStorage(4096, directory)
            self.assertTrue(file.closed)

    def test_invalid_size_creates_no_file(self):
        with patch("memory_bridge.local_disk.tempfile.TemporaryFile") as create:
            for size in [0, -4096, 1, True, 4096.0]:
                with self.assertRaises(ValueError):
                    TemporaryFileStorage(size)
            create.assert_not_called()

    def test_resilient_file_matches_reference_through_donor_loss(self):
        from memory_bridge.resilient import ResilientStorage
        from memory_bridge.simulation import SimulatedMemory
        donor = SimulatedMemory(16384)
        reference = bytearray(16384)
        rng = random.Random(17)
        with ResilientStorage(TemporaryFileStorage(16384), donor) as storage:
            for step in range(200):
                if step == 100:
                    donor.fail_writes = donor.fail_reads = True
                count = rng.randrange(1, 5000)
                offset = rng.randrange(0, len(reference) - count + 1)
                payload = rng.randbytes(count)
                storage.write(offset, payload)
                reference[offset:offset + count] = payload
                self.assertEqual(storage.read(0, len(reference)), bytes(reference))
            self.assertFalse(storage.snapshot()["remote_enabled"])
