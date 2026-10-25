"""Authoritative local backing and best-effort cache failure invariants."""

import unittest
import random

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


class BatchedResilientTests(unittest.TestCase):
    def make_storage(self, blocks=16, transfer=8192, maximum=16384, remote=True):
        local = RecordingMemory(blocks * 4096)
        donor = RecordingMemory(blocks * 4096) if remote else None
        storage = ResilientStorage(local, donor, max_request_bytes=maximum,
                                   transfer_bytes=transfer)
        self.addCleanup(storage.close)
        return storage, local, donor

    def test_bitmap_byte_boundary_and_overwrite_counts(self):
        storage, local, remote = self.make_storage(blocks=17)
        self.assertEqual(storage.snapshot()["valid_bitmap_bytes"], 3)
        for page in (0, 7, 8, 15, 16):
            storage.write(page * 4096, b"x")
        self.assertEqual(storage.snapshot()["cached_blocks"], 5)
        for page in (7, 8):
            storage.write(page * 4096 + 2, b"y")
        self.assertEqual(storage.snapshot()["cached_blocks"], 5)
        self.assertEqual(storage.read(7 * 4096, 8192), bytes(local.data[7 * 4096:9 * 4096]))
        storage.close()
        self.assertEqual(storage.snapshot()["valid_bitmap_bytes"], 0)

    def test_no_bitmap_without_remote(self):
        storage, _, _ = self.make_storage(remote=False)
        storage.write(0, b"x")
        self.assertEqual(storage.snapshot()["valid_bitmap_bytes"], 0)
        self.assertEqual(storage.snapshot()["cached_blocks"], 0)

    def test_batched_calls_and_aligned_write_avoid_local_readback(self):
        storage, local, remote = self.make_storage()
        data = b"a" * 16384
        storage.write(0, data)
        self.assertEqual(local.calls, [("write", 0, 16384)])
        self.assertEqual(remote.calls, [("write", 0, 8192), ("write", 8192, 8192)])
        remote.calls.clear()
        self.assertEqual(storage.read(0, 16384), data)
        self.assertEqual(remote.calls, [("read", 0, 8192), ("read", 8192, 8192)])

    def test_mixed_cache_sources_and_partial_edges(self):
        storage, local, remote = self.make_storage(transfer=16384)
        local.data[:] = b"l" * local.size
        remote.data[:] = b"stale!!!" * (remote.size // 8)
        storage.write(4096, b"m" * 8192)
        local.calls.clear()
        remote.calls.clear()
        self.assertEqual(storage.read(4095, 8194), b"l" + b"m" * 8192 + b"l")
        self.assertEqual(local.calls, [("read", 4095, 1), ("read", 12288, 1)])
        self.assertEqual(remote.calls, [("read", 4096, 8192)])

    def test_partial_maximum_write_splits_full_block_mirrors(self):
        storage, local, remote = self.make_storage(maximum=8192)
        storage.write(1, b"a" * 8192)
        self.assertEqual(remote.calls, [("write", 0, 8192), ("write", 8192, 4096)])
        self.assertTrue(all(call[2] <= 8192 for call in local.calls + remote.calls))
        self.assertEqual(remote.data[:12288], local.data[:12288])
        self.assertEqual(storage.snapshot()["cached_blocks"], 3)

    def test_late_mirror_failure_discards_all_validity(self):
        storage, local, remote = self.make_storage()
        original = remote.write
        calls = 0

        def fail_second(offset, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                remote.data[offset] = 255
                raise OSError("partial remote write")
            original(offset, data)

        remote.write = fail_second
        expected = b"new!" * 4096
        storage.write(0, expected)
        self.assertEqual(storage.read(0, 16384), expected)
        self.assertFalse(storage.snapshot()["remote_enabled"])
        self.assertEqual(storage.snapshot()["cached_blocks"], 0)
        self.assertEqual(storage.snapshot()["valid_bitmap_bytes"], 0)

    def test_late_read_failure_falls_back_for_current_and_remaining_batches(self):
        storage, local, remote = self.make_storage()
        expected = b"new!" * 4096
        storage.write(0, expected)
        original = remote.read
        calls = 0

        def fail_second(offset, count):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("remote failed")
            return original(offset, count)

        remote.read = fail_second
        local.calls.clear()
        self.assertEqual(storage.read(0, 16384), expected)
        self.assertEqual(local.calls, [("read", 8192, 8192)])
        storage.write(0, b"z" * 16384)
        self.assertEqual(storage.read(0, 16384), b"z" * 16384)
        self.assertEqual(calls, 2)

    def test_transfer_validation_precedes_io(self):
        local, remote = RecordingMemory(), RecordingMemory()
        for transfer in (True, 0, -4096, 4097, 12288):
            with self.subTest(transfer=transfer), self.assertRaises(ValueError):
                ResilientStorage(local, remote, max_request_bytes=8192,
                                 transfer_bytes=transfer)
        self.assertEqual(local.calls + remote.calls, [])

    def test_randomized_mixed_sources_match_reference_through_remote_loss(self):
        for transfer in (4096, 8192, 16384):
            with self.subTest(transfer=transfer):
                storage, local, remote = self.make_storage(blocks=17, transfer=transfer)
                rng = random.Random(8392)
                reference = bytearray(local.size)
                remote.data[:] = b"s" * remote.size
                # Scatter initial validity across multiple bitmap bytes;
                # untouched blocks must still be read from local zeros.
                for page in (0, 3, 7, 8, 12, 16):
                    payload = rng.randbytes(4096)
                    storage.write(page * 4096, payload)
                    reference[page * 4096:(page + 1) * 4096] = payload
                for operation in range(300):
                    if operation == 100:
                        remote.fail_reads = remote.fail_writes = True
                    count = rng.randint(1, 16384)
                    offset = rng.randint(0, len(reference) - count)
                    if rng.randrange(2):
                        payload = rng.randbytes(count)
                        storage.write(offset, payload)
                        reference[offset:offset + count] = payload
                    else:
                        self.assertEqual(storage.read(offset, count),
                                         bytes(reference[offset:offset + count]))
                self.assertFalse(storage.snapshot()["remote_enabled"])
                self.assertEqual(storage.snapshot()["valid_bitmap_bytes"], 0)
                self.assertEqual(local.data, reference)
                # Check all bytes after loss, not only the sampled ranges.
                for start in range(0, len(reference), 16384):
                    count = min(16384, len(reference) - start)
                    self.assertEqual(storage.read(start, count), bytes(reference[start:start + count]))

class CancellationTests(unittest.TestCase):
    def test_interrupted_partial_local_write_is_terminal(self):
        local, remote = RecordingMemory(), RecordingMemory()
        with ResilientStorage(local, remote) as storage:
            storage.write(0, b'old')

            def interrupt(offset, data):
                local.data[offset] = data[0]
                raise KeyboardInterrupt

            local.write = interrupt
            with self.assertRaises(KeyboardInterrupt):
                storage.write(0, b'new')
            with self.assertRaises(BackendError):
                storage.read(0, 3)
            self.assertEqual(storage.snapshot()['cached_blocks'], 0)

    def test_interrupted_remote_write_disables_cache_but_keeps_local_data(self):
        local, remote = RecordingMemory(), RecordingMemory()
        with ResilientStorage(local, remote) as storage:
            def interrupt(*_):
                raise KeyboardInterrupt
            remote.write = interrupt
            with self.assertRaises(KeyboardInterrupt):
                storage.write(0, b'saved')
            self.assertFalse(storage.snapshot()['remote_enabled'])
            self.assertEqual(storage.read(0, 5), b'saved')
