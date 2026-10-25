#!/usr/bin/env python3
"""Destructive test of a fresh Memory Bridge export, never a host disk.

Run with /usr/bin/python3 so Ubuntu's python3-libnbd package is visible.
The parent enforces a wall-clock deadline even if a synchronous NBD call hangs.
"""

import argparse
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

from bridge import private_key


def worker(args):
    import nbd  # Supplied by python3-libnbd, not a PyPI dependency.

    connection = nbd.NBD()
    connection.set_tls(nbd.TLS_REQUIRE)
    connection.set_tls_username("borrower")
    connection.set_tls_psk_file(str(private_key(args.key)))
    connection.connect_tcp(args.host, str(args.port))
    size = connection.get_size()
    page_size = 4096
    rng = random.Random(42)
    expected = {}
    write_ms, read_ms = [], []
    # Repeated offsets deliberately exercise overwrites as well as new pages.
    for _ in range(args.pages):
        offset = rng.randrange(size // page_size) * page_size
        data = os.urandom(page_size)
        start = time.perf_counter()
        connection.pwrite(data, offset)
        write_ms.append((time.perf_counter() - start) * 1000)
        expected[offset] = data
    for offset, data in expected.items():
        start = time.perf_counter()
        actual = connection.pread(page_size, offset)
        read_ms.append((time.perf_counter() - start) * 1000)
        if actual != data:
            raise RuntimeError(f"Data mismatch at offset {offset}")
    print(json.dumps({"export_bytes": size, "writes": args.pages,
                      "unique_pages_verified": len(expected),
                      "median_write_ms": statistics.median(write_ms),
                      "median_read_ms": statistics.median(read_ms),
                      "tls": "required", "scope": "synthetic page I/O, not application speedup"}, indent=2))
    connection.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10809)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--pages", type=int, default=256)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.pages <= 16384 or args.timeout <= 0:
        parser.error("pages must be 1..16384 and timeout must be positive")
    if args.worker:
        worker(args)
        return
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                    *sys.argv[1:], "--worker"], timeout=args.timeout)
        raise SystemExit(completed.returncode)
    except subprocess.TimeoutExpired:
        print("FAIL: deadline exceeded; remote data is not assumed recoverable", file=sys.stderr)
        raise SystemExit(124)


if __name__ == "__main__":
    main()
