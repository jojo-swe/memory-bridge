#!/usr/bin/env python3
"""Compare local file I/O with local-first TLS/NBD caching on synthetic pages.

Each mode runs in a fresh process. The coordinator enforces a whole-mode wall
clock limit and kills its entire process group on timeout, including NBD workers.
No root, system-wide cache dropping, device mapping or swap changes are used.
"""

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys

from bridge import private_key
from memory_bridge.local_disk import TemporaryFileStorage
from memory_bridge.memory_budget import apply_address_limit
from memory_bridge.remote_nbd import RemoteNBDStorage
from memory_bridge.resilient import ResilientStorage
from memory_bridge.workload import run_workload


def positive_seconds(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("Must be finite and positive")
    return value


def worker(args):
    import resource

    limit = apply_address_limit(args.memory_limit_mib)
    size = args.size_mib * 1024 * 1024
    key = private_key(args.key) if args.worker_mode == "remote" else None
    with ExitStack() as cleanup:
        local = TemporaryFileStorage(size, args.directory)
        cleanup.callback(local.close)
        storage = local
        if args.worker_mode == "remote":
            remote = RemoteNBDStorage(args.host, args.port, key, size, timeout=args.io_timeout)
            cleanup.callback(remote.close)
            storage = ResilientStorage(local, remote, transfer_bytes=args.request_kib * 1024)
            cleanup.callback(storage.close)
        metrics = run_workload(storage, operations=args.operations, seed=args.seed,
                               page_size=args.request_kib * 1024)
        cache = storage.snapshot() if args.worker_mode == "remote" else None
        # A fallback run must not be mislabeled as a successful remote benchmark.
        if cache is not None and not cache["remote_enabled"]:
            raise RuntimeError("Remote cache failed; comparison is invalid")
    # Linux reports KiB. Child peak is measured after reaping the NBD worker.
    # These are separate high-water marks, NOT simultaneous or total system RAM.
    metrics.update({"mode": args.worker_mode, "cache": cache,
                    "address_space_limit_bytes_per_process": limit,
                    "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    "child_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss})
    print(json.dumps(metrics))


def run_mode(args, mode):
    command = [sys.executable, str(Path(__file__).resolve()), "--worker-mode", mode,
               "--mode", mode, "--size-mib", str(args.size_mib),
               "--operations", str(args.operations), "--seed", str(args.seed),
               "--host", args.host, "--port", str(args.port),
               "--io-timeout", str(args.io_timeout), "--request-kib", str(args.request_kib)]
    if args.memory_limit_mib is not None:
        command += ["--memory-limit-mib", str(args.memory_limit_mib)]
    if args.key is not None:
        command += ["--key", str(args.key)]
    if args.directory is not None:
        command += ["--directory", str(args.directory)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=args.run_timeout)
    except BaseException:
        # Worker and its NBD subprocess share this isolated process group.
        # Never leave a blocked network child running after the experiment.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=2)
        raise
    if process.returncode:
        # Avoid echoing backend exception messages, which may contain paths.
        raise RuntimeError(f"{mode} benchmark failed; no valid comparison produced")
    return json.loads(stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "remote", "compare"), default="compare")
    parser.add_argument("--order", choices=("local-first", "remote-first"), default="local-first")
    parser.add_argument("--key", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10809)
    parser.add_argument("--size-mib", type=int, default=1)
    parser.add_argument("--operations", type=int, default=256)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--request-kib", type=int, default=4,
                        help="Power of two from 4 to 1024; same request size for both modes")
    parser.add_argument("--memory-limit-mib", type=int,
                        help="Per-worker virtual address limit; not a system RAM limit")
    parser.add_argument("--io-timeout", type=positive_seconds, default=2.0)
    parser.add_argument("--run-timeout", type=positive_seconds, default=120.0,
                        help="Whole-mode deadline, including initialization and cleanup")
    parser.add_argument("--worker-mode", choices=("local", "remote"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("This experiment currently requires Linux")
    if not 1 <= args.size_mib <= 65536 or not 1 <= args.operations <= 100000:
        parser.error("size-mib must be 1..65536; operations must be 1..100000")
    if not 0 <= args.seed <= 0xffffffff or not 1 <= args.port <= 65535:
        parser.error("seed must be an unsigned 32-bit integer; port must be 1..65535")
    if not 4 <= args.request_kib <= 1024 or args.request_kib & (args.request_kib - 1):
        parser.error("request-kib must be a power of two from 4 to 1024")
    if args.memory_limit_mib is not None and not 32 <= args.memory_limit_mib <= 65536:
        parser.error("memory-limit-mib must be 32..65536")
    if (args.worker_mode or args.mode) != "local" and args.key is None:
        parser.error("--key is required for a remote run")
    if args.worker_mode:
        worker(args)
        return
    modes = ["local", "remote"] if args.mode == "compare" else [args.mode]
    if args.order == "remote-first":
        modes.reverse()
    try:
        results = [run_mode(args, mode) for mode in modes]
    except subprocess.TimeoutExpired:
        parser.exit(1, "Benchmark deadline exceeded; no valid comparison produced\n")
    except (RuntimeError, ValueError):
        parser.exit(1, "Benchmark failed; check donor, dependencies, key and local backing\n")
    print(json.dumps(build_report(results), indent=2))


def build_report(results):
    comparisons = None
    if len(results) == 2:
        by_mode = {result["mode"]: result for result in results}
        comparisons = {phase + "_remote_over_local":
                       by_mode["remote"][phase]["duration_seconds"] /
                       by_mode["local"][phase]["duration_seconds"]
                       for phase in ("read", "write")}
    return {"schema_version": 1, "python": platform.python_version(),
                      "system": platform.system(), "order": [result["mode"] for result in results],
                      "results": results, "duration_ratios": comparisons,
                      "interpretation": "Ratio above 1 means remote mode was slower",
                      "limitations": ["Buffered file I/O; OS page cache is not dropped",
                                      "Not a cold SSD or memory-pressure benchmark",
                                      "RSS excludes donor and system page-cache accounting",
                                      "Separate RSS peaks cannot be added as simultaneous usage",
                                      "RLIMIT_AS, if selected, limits virtual address space per process only",
                                      "No application speedup or RAM saving is established"]}


if __name__ == "__main__":
    main()
