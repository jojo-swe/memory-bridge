"""Write-behind mirroring keeps local-first writes and ACK-gated cache validity."""

import threading
import time
import unittest

from memory_bridge.resilient import ResilientStorage
from memory_bridge.simulation import SimulatedMemory


class GatedRemote(SimulatedMemory):
    """Remote whose writes block until the test releases them."""

    def __init__(self, size):
        super().__init__(size)
        self.gate = threading.Event()
        self.gate.set()
        self.started = threading.Event()
        self.writes = []

    def write(self, offset, data):
        self.started.set()
        if not self.gate.wait(timeout=5):
            raise OSError("gate timeout")
        self.writes.append((offset, len(data)))
        super().write(offset, data)


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class WriteBehindTests(unittest.TestCase):
    def setUp(self):
        self.local = SimulatedMemory(32768)
        self.remote = GatedRemote(32768)
        self.storage = ResilientStorage(self.local, self.remote, transfer_bytes=8192,
                                        write_behind_bytes=16384)
        self.addCleanup(self.storage.close)

    def test_write_returns_before_mirror_and_cache_validates_after_ack(self):
        self.remote.gate.clear()
        self.storage.write(0, b"x" * 4096)
        self.assertEqual(self.local.data[:4096], b"x" * 4096)
        self.assertEqual(self.storage.snapshot()["cached_blocks"], 0)
        self.assertTrue(self.remote.started.wait(2))
        self.remote.gate.set()
        self.assertTrue(wait_until(lambda: self.storage.snapshot()["cached_blocks"] == 1))
        self.assertEqual(self.remote.data[:4096], b"x" * 4096)
        self.local.fail_reads = True  # a cached read must now come from the remote
        self.assertEqual(self.storage.read(100, 10), b"x" * 10)

    def test_superseded_inflight_block_is_never_marked_valid(self):
        self.remote.gate.clear()
        self.storage.write(0, b"old!" * 1024)
        self.assertTrue(self.remote.started.wait(2))
        # The mirror of "old!" is in flight; overwrite locally with new data.
        self.storage.write(0, b"new!" * 1024)
        self.remote.gate.set()
        self.assertTrue(wait_until(lambda: len(self.remote.writes) >= 2))
        self.assertTrue(wait_until(lambda: self.storage.snapshot()["pending_mirror_blocks"] == 0))
        self.assertEqual(self.storage.read(0, 4), b"new!")
        self.assertEqual(self.remote.data[:4], b"new!")

    def test_budget_overflow_keeps_blocks_local_only(self):
        self.remote.gate.clear()
        self.storage.write(0, b"a" * 32768)  # 8 blocks, budget 4: at most 4 queued
        snapshot = self.storage.snapshot()
        self.assertLessEqual(snapshot["pending_mirror_blocks"], 4)
        self.assertGreaterEqual(snapshot["mirror_dropped_blocks"], 2)
        self.remote.gate.set()
        self.assertTrue(wait_until(lambda: self.storage.snapshot()["pending_mirror_blocks"] == 0))
        self.assertTrue(self.storage.snapshot()["remote_enabled"])
        self.assertEqual(self.storage.read(0, 32768), b"a" * 32768)

    def test_partial_edge_write_mirrors_full_block_content(self):
        self.local.data[:] = b"b" * 32768
        self.storage.write(4095, b"XY")
        self.assertTrue(wait_until(lambda: self.storage.snapshot()["cached_blocks"] == 2))
        self.assertEqual(self.remote.data[:8192], self.local.data[:8192])

    def test_remote_failure_disables_cache_permanently(self):
        self.remote.fail_writes = True
        self.storage.write(0, b"c" * 4096)
        self.assertTrue(wait_until(lambda: not self.storage.snapshot()["remote_enabled"]))
        self.remote.fail_writes = False
        self.storage.write(4096, b"d" * 4096)
        self.assertEqual(self.storage.snapshot()["pending_mirror_blocks"], 0)
        self.assertEqual(self.storage.read(4096, 4), b"dddd")

    def test_close_joins_mirror_thread(self):
        self.remote.gate.clear()
        self.storage.write(0, b"e" * 4096)
        self.remote.gate.set()
        self.storage.close()
        self.assertFalse(self.storage._mirror_thread.is_alive())

    def test_invalid_budget_rejected(self):
        with self.assertRaises(ValueError):
            ResilientStorage(SimulatedMemory(4096), SimulatedMemory(4096), write_behind_bytes=-1)


if __name__ == "__main__":
    unittest.main()
