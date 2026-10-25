"""Reproducible page I/O comparison; measures latency, not RAM savings.

The workload overwrites the entire supplied export. It retains only one page
of generated content at a time and at most 100000 latency samples per phase.
"""

import hashlib
import math
import random
import statistics
import time

from .core import BackendError, Storage

_PAGE_SIZE = 4096


def _page(seed, index, operation=None, page_size=_PAGE_SIZE):
    # Derive each page independently: verification needs no shadow dataset.
    domain = "initial" if operation is None else f"write:{operation}"
    identity = f"memory-bridge-workload-v1:{domain}:{seed}:{index}".encode("ascii")
    return hashlib.shake_256(identity).digest(page_size)


def _metrics(samples):
    ordered = sorted(samples)
    duration = sum(samples) / 1_000_000_000
    return {"operations": len(samples), "duration_seconds": duration,
            "median_ms": statistics.median(samples) / 1_000_000,
            "p95_ms": ordered[math.ceil(len(samples) * .95) - 1] / 1_000_000,
            "iops": len(samples) / duration}


def run_workload(storage: Storage, *, operations=256, seed=42, page_size=4096) -> dict:
    """Initialize all pages, then verify random reads and issue random writes.

    Read and write phases use the same seeded page sequence. Each write uses
    distinct content and is followed by an untimed verification read. Those
    extra reads affect cache state: this is a verified comparison workload,
    not a pure write-throughput measurement. Only individual
    storage calls contribute to phase duration, latency and IOPS. Content
    generation, random selection, sorting and verification are excluded.
    Initialization time includes generation and writes and is reported alone.
    Exceptions propagate; a corrupt or malformed read raises BackendError.
    """
    if type(operations) is not int or not 1 <= operations <= 100000:
        raise ValueError("Operations must be an integer from 1 to 100000")
    if type(seed) is not int or not 0 <= seed <= 0xffffffff:
        raise ValueError("Seed must be an unsigned 32-bit integer")
    if type(page_size) is not int or not 4096 <= page_size <= 1048576 or page_size & (page_size - 1):
        raise ValueError("Page size must be a power of two from 4096 to 1048576")
    size = storage.size
    if type(size) is not int or size <= 0 or size % page_size:
        raise ValueError("Storage size must be positive and page aligned")
    pages = size // page_size
    started = time.perf_counter_ns()
    for index in range(pages):
        storage.write(index * page_size, _page(seed, index, page_size=page_size))
    initialization_seconds = (time.perf_counter_ns() - started) / 1_000_000_000
    phases = {}
    for phase in ("read", "write"):
        rng = random.Random(seed)
        samples = []
        for operation in range(operations):
            index = rng.randrange(pages)
            expected = _page(seed, index, operation if phase == "write" else None, page_size=page_size)
            offset = index * page_size
            if phase == "read":
                started = time.perf_counter_ns()
                actual = storage.read(offset, page_size)
                elapsed = time.perf_counter_ns() - started
                if not isinstance(actual, bytes) or actual != expected:
                    raise BackendError("Workload read verification failed")
            else:
                started = time.perf_counter_ns()
                storage.write(offset, expected)
                elapsed = time.perf_counter_ns() - started
                actual = storage.read(offset, page_size)
                if not isinstance(actual, bytes) or actual != expected:
                    raise BackendError("Workload write verification failed")
            # A coarse clock can report zero for an in-memory call. Use its
            # smallest integer unit so JSON metrics stay finite and positive.
            samples.append(max(1, elapsed))
        phases[phase] = _metrics(samples)
        if phase == "write":
            phases[phase]["verification_reads"] = operations
    return {"operations": operations, "seed": seed, "page_size": page_size,
            "export_bytes": size, "initialization_seconds": initialization_seconds,
            **phases}
