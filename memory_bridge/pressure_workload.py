#!/usr/bin/env python3
"""Verified anonymous-memory workload for swap experiments under a cgroup limit.

The process maps `dataset` MiB of anonymous memory and touches it in phases.
Every page has deterministic content derived from (seed, page index, version)
so each read is verified; a swap path that corrupts or loses a page is counted
as a verification error, never hidden. The orchestrator places this process in
a memory-limited cgroup; the workload itself changes no system setting.

Phases:
  fill    sequential write of every page
  sweep   sequential verification of every page
  random  rounds * pages random page touches; every eighth touch rewrites the
          page with a new version so dirty pages must be swapped out again

Latency is the wall time of one page access (read + compare, or write),
which includes any page fault served from swap. Samples are kept in a bounded
array so the workload's own footprint stays small and equal between arms.
"""

import argparse
from array import array
import hashlib
import json
import mmap
import random
import sys
import time

PAGE = 4096
MAX_SAMPLES = 262144


def page_content(seed, index, version):
    return hashlib.shake_128(seed.to_bytes(4, "little") + index.to_bytes(4, "little")
                             + version.to_bytes(4, "little")).digest(PAGE)


class Sampler:
    """Fixed-capacity latency samples; keeps every k-th sample once full."""

    def __init__(self):
        self.values = array("d")
        self.count = 0
        self.total = 0.0
        self.maximum = 0.0
        self.stride = 1

    def add(self, seconds):
        self.count += 1
        self.total += seconds
        if seconds > self.maximum:
            self.maximum = seconds
        if self.count % self.stride == 0:
            if len(self.values) >= MAX_SAMPLES:
                # Halve the retained samples and double the stride; exact
                # count, total and maximum are kept separately.
                self.values = array("d", self.values[::2])
                self.stride *= 2
            else:
                self.values.append(seconds)

    def report(self):
        values = sorted(self.values)

        def percentile(fraction):
            if not values:
                return None
            return values[min(len(values) - 1, int(len(values) * fraction))]

        return {"count": self.count, "total_seconds": self.total,
                "mean_seconds": self.total / self.count if self.count else None,
                "median_seconds": percentile(0.5), "p95_seconds": percentile(0.95),
                "p99_seconds": percentile(0.99), "max_seconds": self.maximum,
                "retained_samples": len(values), "sample_stride": self.stride}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-mib", type=int, default=512)
    parser.add_argument("--rounds", type=int, default=2, help="Random rounds, each `pages` touches")
    parser.add_argument("--pattern", choices=("uniform", "hotset"), default="uniform")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deadline", type=float, default=0,
                        help="Stop the random phase after this many seconds (0 = unlimited)")
    args = parser.parse_args()
    if not 1 <= args.dataset_mib <= 65536 or not 1 <= args.rounds <= 1000:
        parser.error("dataset-mib must be 1..65536 and rounds 1..1000")
    if not 0 <= args.seed <= 0xffffffff:
        parser.error("seed must be an unsigned 32-bit integer")
    pages = args.dataset_mib * 1024 * 1024 // PAGE
    versions = array("I", bytes(4 * pages))  # 4 bytes per page: version numbers
    data = mmap.mmap(-1, pages * PAGE)
    report = {"dataset_mib": args.dataset_mib, "pages": pages, "pattern": args.pattern,
              "seed": args.seed, "rounds_requested": args.rounds, "phases": {},
              "verification_errors": 0, "rounds_completed": 0, "deadline_hit": False}
    errors = 0
    started_all = time.monotonic()

    def phase(name, sampler, started):
        report["phases"][name] = dict(sampler.report(), wall_seconds=time.monotonic() - started)
        print(json.dumps({"phase": name, "wall_seconds": time.monotonic() - started,
                          "errors": errors}), file=sys.stderr, flush=True)

    sampler = Sampler()
    started = time.monotonic()
    for index in range(pages):
        content = page_content(args.seed, index, 0)
        t = time.perf_counter()
        data[index * PAGE:(index + 1) * PAGE] = content
        sampler.add(time.perf_counter() - t)
    phase("fill", sampler, started)

    sampler = Sampler()
    started = time.monotonic()
    for index in range(pages):
        expected = page_content(args.seed, index, versions[index])
        t = time.perf_counter()
        actual = data[index * PAGE:(index + 1) * PAGE]
        sampler.add(time.perf_counter() - t)
        if actual != expected:
            errors += 1
    phase("sweep", sampler, started)

    generator = random.Random(args.seed)
    hot = pages // 5 or 1
    reads = Sampler()
    writes = Sampler()
    started = time.monotonic()
    deadline = started + args.deadline if args.deadline else None
    for round_number in range(args.rounds):
        for touch in range(pages):
            if args.pattern == "hotset" and generator.random() < 0.8:
                index = generator.randrange(hot)
            else:
                index = generator.randrange(pages)
            if touch % 8 == 7:
                versions[index] += 1
                content = page_content(args.seed, index, versions[index])
                t = time.perf_counter()
                data[index * PAGE:(index + 1) * PAGE] = content
                writes.add(time.perf_counter() - t)
            else:
                expected = page_content(args.seed, index, versions[index])
                t = time.perf_counter()
                actual = data[index * PAGE:(index + 1) * PAGE]
                reads.add(time.perf_counter() - t)
                if actual != expected:
                    errors += 1
            if deadline is not None and touch % 256 == 0 and time.monotonic() > deadline:
                report["deadline_hit"] = True
                break
        if report["deadline_hit"]:
            break
        report["rounds_completed"] = round_number + 1
    report["phases"]["random_reads"] = reads.report()
    report["phases"]["random_writes"] = writes.report()
    report["phases"]["random"] = {"wall_seconds": time.monotonic() - started,
                                  "touches": reads.count + writes.count}
    print(json.dumps({"phase": "random", "wall_seconds": time.monotonic() - started,
                      "errors": errors}), file=sys.stderr, flush=True)
    report["verification_errors"] = errors
    report["total_wall_seconds"] = time.monotonic() - started_all
    print(json.dumps(report), flush=True)
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
