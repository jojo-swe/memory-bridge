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

    size = args.size_mib * 1024 * 1024
    key = private_key(args.key) if args.worker_mode == "remote" else None
    with ExitStack() as cleanup:
        local = TemporaryFileStorage(size, args.directory)
        cleanup.callback(local.close)
        storage = local
        if args.worker_mode == "remote":
            remote = RemoteNBDStorage(args.host, args.port, key, size, timeout=args.io_timeout)
            cleanup.callback(remote.close)
            storage = ResilientStorage(local, remote)
            cleanup.callback(storage.close)
        metrics = run_workload(storage, operations=args.operations, seed=args.seed)
        cache = storage.snapshot() if args.worker_mode == "remote" else None
        # A fallback run must not be mislabeled as a successful remote benchmark.
        if cache is not None and not cache["remote_enabled"]:
            raise RuntimeError("Remote cache failed; comparison is invalid")
    # Linux reports KiB. Child peak is measured after reaping the NBD worker.
    # These are separate high-water marks, NOT simultaneous or total system RAM.
    metrics.update({"mode": args.worker_mode, "cache": cache,
                    "process_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    "child_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss})
    print(json.dumps(metrics))


def run_mode(args, mode):
    command = [sys.executable, str(Path(__file__).resolve()), "--worker-mode", mode,
               "--mode", mode, "--size-mib", str(args.size_mib),
               "--operations", str(args.operations), "--seed", str(args.seed),
               "--host", args.host, "--port", str(args.port),
               "--io-timeout", str(args.io_timeout)]
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
    comparisons = None
    if args.mode == "compare":
        by_mode = {result["mode"]: result for result in results}
        comparisons = {phase + "_remote_over_local":
                       by_mode["remote"][phase]["duration_seconds"] /
                       by_mode["local"][phase]["duration_seconds"]
                       for phase in ("read", "write")}
    print(json.dumps({"schema_version": 1, "python": platform.python_version(),
                      "system": platform.system(), "order": modes,
                      "results": results, "duration_ratios": comparisons,
                      "interpretation": "Ratio above 1 means remote mode was slower",
                      "limitations": ["Buffered file I/O; OS page cache is not dropped",
                                      "Not a cold SSD or memory-pressure benchmark",
                                      "RSS excludes donor and system page-cache accounting",
                                      "Separate RSS peaks cannot be added as simultaneous usage",
                                      "No application speedup or RAM saving is established"]}, indent=2))


if __name__ == "__main__":
    main()
