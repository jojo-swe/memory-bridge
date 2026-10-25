"""Authoritative local backing and best-effort cache failure invariants."""

import unittest

from memory_bridge.core import BackendError
from memory_bridge.resilient import ResilientStorage
from memory_bridge.simulation import SimulatedMemory


class RecordingMemory(SimulatedMemory):
    def __init__(self, size=8192):
        super().__init__(size)
        self.calls = []

    def read(self, offset, count):
        self.calls.append(("read", offset, count))
        return super().read(offset, count)

    def write(self, offset, data):
        self.calls.append(("write", offset, len(data)))
        return super().write(offset, data)

    def close(self):
        self.calls.append(("close",))
        super().close()


class ResilientTests(unittest.TestCase):
    def setUp(self):
        self.local = RecordingMemory()
        self.remote = RecordingMemory()
        self.storage = ResilientStorage(self.local, self.remote, max_request_bytes=8192)
        self.addCleanup(self.storage.close)

    def test_initial_remote_contents_are_untrusted(self):
        self.remote.data[:] = b"x" * 8192
        self.assertEqual(self.storage.read(0, 8192), bytes(8192))
        self.assertEqual(self.remote.calls, [])

    def test_partial_cross_block_write_mirrors_complete_blocks(self):
        self.local.data[:] = b"a" * 8192
        self.storage.write(4095, b"XY")
        self.assertEqual(self.remote.data, self.local.data)
        self.local.calls.clear()
        self.assertEqual(self.storage.read(4094, 4), b"aXYa")
        self.assertEqual(self.local.calls, [])
        self.assertEqual(self.storage.snapshot()["cached_blocks"], 2)

    def test_remote_write_failure_preserves_authoritative_write(self):
        self.remote.fail_writes = True
        self.storage.write(3, b"saved")
        self.assertEqual(self.storage.read(3, 5), b"saved")
        calls = len(self.remote.calls)
        self.remote.fail_writes = False
        self.storage.write(0, b"a")
        self.assertEqual(len(self.remote.calls), calls)
        self.assertFalse(self.storage.snapshot()["remote_enabled"])

    def test_remote_read_failure_falls_back_permanently(self):
        self.storage.write(0, b"saved")
        self.remote.fail_reads = True
        self.assertEqual(self.storage.read(0, 5), b"saved")
        calls = len(self.remote.calls)
        self.remote.fail_reads = False
        self.assertEqual(self.storage.read(0, 5), b"saved")
        self.assertEqual(len(self.remote.calls), calls)

    def test_malformed_remote_read_falls_back(self):
        self.storage.write(0, b"saved")
        self.remote.read = lambda *_: b""
        self.assertEqual(self.storage.read(0, 5), b"saved")
        self.assertFalse(self.storage.snapshot()["remote_enabled"])

    def test_partial_local_write_failure_is_terminal(self):
        self.storage.write(0, b"old")

        def partial(offset, data):
            self.local.data[offset] = data[0]
            raise OSError("partial write")

        self.local.write = partial
        with self.assertRaises(BackendError):
            self.storage.write(0, b"new")
        with self.assertRaises(BackendError):
            self.storage.read(0, 3)
        self.assertTrue(self.storage.snapshot()["failed"])
        self.assertEqual(self.storage.snapshot()["cached_blocks"], 0)

    def test_local_read_failure_is_terminal(self):
        self.local.fail_reads = True
        with self.assertRaises(BackendError):
            self.storage.read(0, 1)
        self.local.fail_reads = False
        with self.assertRaises(BackendError):
            self.storage.write(0, b"x")

    def test_mirror_source_read_failure_is_terminal(self):
        self.local.read = lambda *_: b""
        with self.assertRaises(BackendError):
            self.storage.write(0, b"saved")
        self.assertEqual(self.local.data[:5], b"saved")
        self.assertEqual(self.remote.calls, [])
        with self.assertRaises(BackendError):
            self.storage.read(0, 5)

    def test_invalid_arguments_have_no_side_effects(self):
        for offset, count in ((True, 1), (-1, 1), (0, 0), (8192, 1), (0, 8193)):
            with self.subTest(offset=offset, count=count), self.assertRaises(ValueError):
                self.storage.read(offset, count)
        for offset, data in ((False, b"a"), (0, b""), (8191, b"aa"), (0, bytearray(b"a"))):
            with self.subTest(offset=offset, data=data), self.assertRaises(ValueError):
                self.storage.write(offset, data)
        self.assertEqual(self.local.calls, [])
        self.assertEqual(self.remote.calls, [])

    def test_request_limit_is_enforced(self):
        storage = ResilientStorage(RecordingMemory(), max_request_bytes=1)
        self.addCleanup(storage.close)
        with self.assertRaises(ValueError):
            storage.write(0, b"xx")
        self.assertEqual(storage._local.calls, [])

    def test_no_remote(self):
        with ResilientStorage(RecordingMemory()) as storage:
            storage.write(0, b"x")
            self.assertEqual(storage.read(0, 1), b"x")
            self.assertFalse(storage.snapshot()["remote_enabled"])

    def test_close_attempts_both_and_never_retries(self):
        local, remote = RecordingMemory(), RecordingMemory()
        local.fail_close = True
        storage = ResilientStorage(local, remote)
        for _ in range(2):
            with self.assertRaises(BackendError):
                storage.close()
        self.assertTrue(remote.closed)
        self.assertEqual(local.calls, [("close",)])
        self.assertEqual(remote.calls, [("close",)])
        with self.assertRaises(BackendError):
            storage.read(0, 1)

    def test_constructor_validation_does_not_close_callers_resources(self):
        for kwargs in ({"block_size": True}, {"block_size": 0},
                       {"max_request_bytes": 0}, {"max_request_bytes": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ResilientStorage(self.local, self.remote, **kwargs)
        with self.assertRaises(ValueError):
            ResilientStorage(self.local, self.local)
        with self.assertRaises(ValueError):
            ResilientStorage(self.local, RecordingMemory(4096))
        self.assertEqual(self.local.calls, [])
        self.assertEqual(self.remote.calls, [])
