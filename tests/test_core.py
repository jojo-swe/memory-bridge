"""Behavioral tests: quotas, races, isolation, faults and cleanup invariants."""

from concurrent.futures import ThreadPoolExecutor
import random
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from memory_bridge.config import load_limits
from memory_bridge.core import BackendError, CapacityError, Limits, MemoryPool, SessionError
from memory_bridge.simulation import SimulatedMemory


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.stores = []

        def factory(size):
            backend = SimulatedMemory(size)
            self.stores.append(backend)
            return backend

        self.pool = MemoryPool(Limits(16384, 8192, 4, 4096), factory)

    def tearDown(self):
        try:
            self.pool.close()
        except BackendError:
            pass  # Some tests deliberately quarantine failed cleanup.

    def test_isolation_and_reuse(self):
        a = self.pool.allocate("a", 8192)
        b = self.pool.allocate("b", 8192)
        self.pool.write(a, 0, b"secret")
        self.assertEqual(self.pool.read(b, 0, 6), bytes(6))
        self.pool.release(a)
        c = self.pool.allocate("a", 8192)
        self.assertEqual(self.pool.read(c, 0, 6), bytes(6))
        with self.assertRaises(SessionError):
            self.pool.read(a, 0, 6)

    def test_owner_quota(self):
        self.pool.allocate("a", 8192)
        with self.assertRaises(CapacityError):
            self.pool.allocate("a", 4096)

    def test_total_quota(self):
        self.pool.allocate("a", 8192)
        self.pool.allocate("b", 8192)
        with self.assertRaises(CapacityError):
            self.pool.allocate("c", 4096)

    def test_session_limit(self):
        with MemoryPool(Limits(16384, 16384, 1, 4096), SimulatedMemory) as pool:
            pool.allocate("a", 4096)
            with self.assertRaises(CapacityError):
                pool.allocate("b", 4096)

    def test_concurrent_quota_admission(self):
        def allocate(index):
            try:
                return self.pool.allocate(str(index), 4096)
            except CapacityError:
                return None
        with ThreadPoolExecutor(max_workers=16) as executor:
            tokens = list(executor.map(allocate, range(40)))
        self.assertEqual(sum(t is not None for t in tokens), 4)
        self.assertEqual(self.pool.snapshot()["allocated_bytes"], 16384)

    def test_failed_allocation_does_not_charge_quota(self):
        def fail(size):
            raise OSError("allocation failed")
        with MemoryPool(Limits(), fail) as pool:
            with self.assertRaises(OSError):
                pool.allocate("a", 4096)
            self.assertEqual(pool.snapshot()["allocated_bytes"], 0)

    def test_invalid_requests_do_not_invalidate_good_session(self):
        token = self.pool.allocate("a", 8192)
        for offset, count in [(-1, 1), (0, 0), (0, 4097), (8192, 1), (True, 1)]:
            with self.subTest(offset=offset, count=count), self.assertRaises(ValueError):
                self.pool.read(token, offset, count)
        self.assertEqual(self.pool.read(token, 0, 1), b"\0")

    def test_write_failure_revokes_only_affected_session(self):
        bad = self.pool.allocate("a", 4096)
        good = self.pool.allocate("b", 4096)
        self.stores[0].fail_writes = True
        with self.assertRaises(BackendError):
            self.pool.write(bad, 0, b"x")
        with self.assertRaises(SessionError):
            self.pool.read(bad, 0, 1)
        self.pool.write(good, 0, b"y")
        self.assertEqual(self.pool.read(good, 0, 1), b"y")

    def test_read_failure_revokes(self):
        token = self.pool.allocate("a", 4096)
        self.stores[0].fail_reads = True
        with self.assertRaises(BackendError):
            self.pool.read(token, 0, 1)
        self.assertEqual(self.pool.snapshot()["sessions"], 0)

    def test_short_read_is_not_accepted(self):
        token = self.pool.allocate("a", 4096)
        self.stores[0].read = lambda offset, count: b""
        with self.assertRaises(BackendError):
            self.pool.read(token, 0, 1)

    def test_cleanup_failure_quarantines_capacity(self):
        token = self.pool.allocate("a", 8192)
        self.stores[0].fail_close = True
        with self.assertRaises(BackendError):
            self.pool.release(token)
        self.assertEqual(self.pool.snapshot()["quarantined_sessions"], 1)
        self.assertEqual(self.pool.snapshot()["allocated_bytes"], 8192)
        with self.assertRaises(CapacityError):
            self.pool.allocate("a", 4096)
        with self.assertRaises(SessionError):
            self.pool.read(token, 0, 1)

    def test_shutdown_continues_after_cleanup_failure(self):
        self.pool.allocate("a", 4096)
        self.pool.allocate("b", 4096)
        self.stores[0].fail_close = True
        with self.assertRaises(BackendError):
            self.pool.close()
        self.assertTrue(self.stores[1].closed)
        self.assertEqual(self.pool.snapshot()["sessions"], 0)

    def test_close_is_terminal_and_idempotent(self):
        self.pool.close()
        self.pool.close()
        with self.assertRaises(SessionError):
            self.pool.allocate("a", 4096)

    def test_randomized_reads_match_reference(self):
        token = self.pool.allocate("a", 8192)
        reference = bytearray(8192)
        rng = random.Random(21025)
        for _ in range(500):
            count = rng.randint(1, 1024)
            offset = rng.randint(0, len(reference) - count)
            payload = rng.randbytes(count)
            self.pool.write(token, offset, payload)
            reference[offset:offset + count] = payload
            offset = rng.randint(0, len(reference) - count)
            self.assertEqual(self.pool.read(token, offset, count), bytes(reference[offset:offset + count]))

    def test_telemetry_omits_identity_and_capabilities(self):
        token = self.pool.allocate("private-owner", 4096)
        telemetry = str(self.pool.snapshot())
        self.assertNotIn(token, telemetry)
        self.assertNotIn("private-owner", telemetry)

    def test_invalid_allocation_types(self):
        for size in (True, 0, -4096, 4097, 4096.0):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.pool.allocate("a", size)

    def test_drain_preserves_existing_data_and_rejects_new_allocations(self):
        token = self.pool.allocate("a", 4096)
        self.pool.write(token, 0, b"safe")
        self.pool.drain()
        self.pool.drain()  # Repeated drain requests do not discard data.
        with self.assertRaises(SessionError):
            self.pool.allocate("b", 4096)
        self.assertEqual(self.pool.read(token, 0, 4), b"safe")
        self.pool.write(token, 0, b"live")
        self.assertEqual(self.pool.read(token, 0, 4), b"live")
        self.pool.release(token)
        self.assertEqual(self.pool.snapshot()["allocated_bytes"], 0)
        self.assertTrue(self.pool.snapshot()["draining"])

    def test_closed_pool_cannot_be_drained_or_reopened(self):
        self.pool.close()
        with self.assertRaises(SessionError):
            self.pool.drain()

    def test_mutated_backend_size_cannot_bypass_quota(self):
        self.pool.allocate("a", 8192)
        self.stores[0].size = 0
        self.assertEqual(self.pool.snapshot()["allocated_bytes"], 8192)
        with self.assertRaises(CapacityError):
            self.pool.allocate("a", 4096)

    def test_entropy_failure_allocates_no_storage(self):
        with patch("memory_bridge.core.secrets.token_urlsafe", side_effect=OSError):
            with self.assertRaises(OSError):
                self.pool.allocate("a", 4096)
        self.assertEqual(len(self.stores), 0)

    def test_repeated_entropy_cannot_overwrite_live_session(self):
        with patch("memory_bridge.core.secrets.token_urlsafe", return_value="fixed"):
            first = self.pool.allocate("a", 4096)
            self.pool.write(first, 0, b"original")
            second = self.pool.allocate("b", 4096)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.stores), 2)
        self.assertEqual(self.pool.read(first, 0, 8), b"original")
        self.assertEqual(self.pool.read(second, 0, 8), bytes(8))

    def test_released_capability_cannot_be_reissued(self):
        with patch("memory_bridge.core.secrets.token_urlsafe", return_value="fixed"):
            token = self.pool.allocate("a", 4096)
            self.pool.release(token)
            replacement = self.pool.allocate("b", 4096)
        self.assertNotEqual(token, replacement)
        with self.assertRaises(SessionError):
            self.pool.read(token, 0, 1)
        self.assertEqual(self.pool.read(replacement, 0, 1), b"\0")

    def test_quarantined_capability_cannot_be_reissued(self):
        with patch("memory_bridge.core.secrets.token_urlsafe", return_value="fixed"):
            token = self.pool.allocate("a", 4096)
            self.stores[0].fail_close = True
            with self.assertRaises(BackendError):
                self.pool.release(token)
            replacement = self.pool.allocate("b", 4096)
        self.assertNotEqual(token, replacement)
        with self.assertRaises(SessionError):
            self.pool.write(token, 0, b"x")
        self.assertEqual(self.pool.read(replacement, 0, 1), b"\0")


class ConfigTests(unittest.TestCase):
    def test_sample_config(self):
        self.assertEqual(load_limits("config/local.toml").total_bytes, 4194304)

    def test_invalid_limits(self):
        for kwargs in ({"max_sessions": True}, {"total_bytes": 1},
                       {"per_owner_bytes": 8388608}, {"max_request_bytes": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Limits(**kwargs)

    def test_unknown_config_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.toml"
            path.write_text('[memory]\nmax_sesions = 2\n')
            with self.assertRaises(ValueError):
                load_limits(path)
