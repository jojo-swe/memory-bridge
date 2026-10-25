"""Deterministic workload correctness and timing boundaries."""

import json
import unittest
from unittest.mock import patch

from memory_bridge.core import BackendError
from memory_bridge.simulation import SimulatedMemory
from memory_bridge.workload import run_workload


class RecordingMemory(SimulatedMemory):
    def __init__(self, size=8 * 4096):
        super().__init__(size)
        self.trace = []

    def read(self, offset, count):
        self.trace.append(("read", offset, count))
        return super().read(offset, count)

    def write(self, offset, data):
        self.trace.append(("write", offset, data))
        super().write(offset, data)


class WorkloadTests(unittest.TestCase):
    def test_initializes_every_page_and_repeats_phase_offsets(self):
        storage = RecordingMemory()
        metrics = run_workload(storage, operations=20, seed=7)
        initial, reads = storage.trace[:8], storage.trace[8:28]
        writes = storage.trace[28::2]
        verification_reads = storage.trace[29::2]
        self.assertEqual([entry[1] for entry in initial], list(range(0, storage.size, 4096)))
        self.assertEqual([entry[1] for entry in reads], [entry[1] for entry in writes])
        self.assertTrue(all(entry[2] == 4096 for entry in reads))
        self.assertTrue(all(len(entry[2]) == 4096 for entry in initial + writes))
        self.assertTrue(all(entry[0] == "read" for entry in verification_reads))
        self.assertEqual(metrics["write"]["verification_reads"], 20)
        self.assertEqual(metrics["export_bytes"], storage.size)
        self.assertEqual(metrics["page_size"], 4096)
        self.assertEqual(metrics["seed"], 7)
        self.assertGreaterEqual(metrics["initialization_seconds"], 0)
        for phase in ("read", "write"):
            self.assertEqual(metrics[phase]["operations"], 20)
            for key in ("duration_seconds", "median_ms", "p95_ms", "iops"):
                self.assertGreater(metrics[phase][key], 0)
            self.assertGreaterEqual(metrics[phase]["p95_ms"], metrics[phase]["median_ms"])
        json.dumps(metrics, allow_nan=False)

    def test_same_seed_has_identical_trace_and_changed_seed_differs(self):
        first, second, changed = RecordingMemory(), RecordingMemory(), RecordingMemory()
        for storage, seed in ((first, 42), (second, 42), (changed, 43)):
            run_workload(storage, operations=32, seed=seed)
        self.assertEqual(first.trace, second.trace)
        self.assertNotEqual(first.trace, changed.trace)
        self.assertNotEqual([x[1] for x in first.trace[8:40]],
                            [x[1] for x in changed.trace[8:40]])

    def test_corruption_and_malformed_reads_are_detected(self):
        for result in (bytes(4096), b"short", bytearray(4096)):
            storage = RecordingMemory()
            storage.read = lambda *_: result
            with self.subTest(result_type=type(result)), self.assertRaises(BackendError):
                run_workload(storage, operations=1)
            # Initialization finishes; write benchmarking never starts.
            self.assertEqual(len(storage.trace), 8)

    def test_dropped_benchmark_writes_are_detected(self):
        storage = RecordingMemory(4096)
        original_write = storage.write
        calls = 0

        def drop_after_initialization(offset, data):
            nonlocal calls
            calls += 1
            if calls == 1:
                original_write(offset, data)

        storage.write = drop_after_initialization
        with self.assertRaisesRegex(BackendError, "write verification"):
            run_workload(storage, operations=1)
        self.assertEqual(calls, 2)

    def test_each_write_has_distinct_content(self):
        storage = RecordingMemory(4096)
        run_workload(storage, operations=3)
        payloads = [entry[2] for entry in storage.trace if entry[0] == "write"]
        self.assertEqual(len(set(payloads)), 4)

    def test_argument_validation_has_no_io(self):
        for kwargs in ({"operations": True}, {"operations": 0}, {"operations": 100001},
                       {"operations": 1.0}, {"seed": True}, {"seed": -1},
                       {"seed": 1 << 32}, {"seed": 1.0}):
            storage = RecordingMemory()
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                run_workload(storage, **kwargs)
            self.assertEqual(storage.trace, [])
        for size in (True, 0, -4096, 4097, 4096.0):
            storage = RecordingMemory()
            storage.size = size
            with self.subTest(size=size), self.assertRaises(ValueError):
                run_workload(storage)
            self.assertEqual(storage.trace, [])

    def test_only_storage_calls_contribute_to_phase_metrics(self):
        storage = RecordingMemory(4096)
        # Initialization has one start/end pair; each phase has three call
        # pairs. Gaps between calls must not contribute to reported duration.
        ticks = [0, 1000000000,
                 2000000000, 2001000000, 3000000000, 3002000000, 4000000000, 4003000000,
                 5000000000, 5004000000, 6000000000, 6005000000, 7000000000, 7006000000]
        with patch("memory_bridge.workload.time.perf_counter_ns", side_effect=ticks):
            metrics = run_workload(storage, operations=3)
        self.assertEqual(metrics["initialization_seconds"], 1)
        self.assertEqual(metrics["read"]["duration_seconds"], .006)
        self.assertEqual(metrics["read"]["median_ms"], 2)
        self.assertEqual(metrics["read"]["p95_ms"], 3)
        self.assertEqual(metrics["read"]["iops"], 500)
        self.assertEqual(metrics["write"]["duration_seconds"], .015)
        self.assertEqual(metrics["write"]["median_ms"], 5)
        self.assertEqual(metrics["write"]["p95_ms"], 6)

    def test_single_operation_and_seed_boundaries(self):
        for seed in (0, 0xffffffff):
            metrics = run_workload(SimulatedMemory(4096), operations=1, seed=seed)
            self.assertEqual(metrics["read"]["operations"], 1)
