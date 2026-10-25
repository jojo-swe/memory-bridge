#!/usr/bin/env python3
"""Destructive recovery experiment against a fresh Memory Bridge test export.

Keeps a full temporary local file copy. Does not map a device or enable swap.
Run with system Python so the isolated worker can import python3-libnbd.
"""

import argparse
from contextlib import ExitStack
import json
import math
from pathlib import Path
import time

from bridge import private_key
from memory_bridge.local_disk import TemporaryFileStorage
from memory_bridge.remote_nbd import RemoteNBDStorage
from memory_bridge.resilient import ResilientStorage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10809)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--size-mib", type=int, default=1,
                        help="Must match the donor's entire export")
    parser.add_argument("--timeout", type=float, default=2.0,
                        help="Deadline in seconds per remote operation")
    parser.add_argument("--directory", type=Path, help="Disk-backed local directory")
    parser.add_argument("--pause-for-fault", action="store_true",
                        help="Pause after verified write so you can stop the donor")
    args = parser.parse_args()
    if not 1 <= args.size_mib <= 65536 or not 1 <= args.port <= 65535:
        parser.error("size-mib must be 1..65536 and port 1..65535")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    key = private_key(args.key)
    size = args.size_mib * 1024 * 1024
    # Allocate local backing first: don't consume a single-use donor if disk
    # reservation fails. ExitStack cleans up every successfully created layer.
    with ExitStack() as cleanup:
        local = TemporaryFileStorage(size, args.directory)
        cleanup.callback(local.close)
        remote = RemoteNBDStorage(args.host, args.port, key, size, timeout=args.timeout)
        cleanup.callback(remote.close)
        storage = ResilientStorage(local, remote)
        cleanup.callback(storage.close)
        payload = b"Memory Bridge verified recovery" * 100
        storage.write(4090, payload)
        if storage.read(4090, len(payload)) != payload:
            raise RuntimeError("Initial remote roundtrip failed")
        if not storage.snapshot()["remote_enabled"]:
            raise RuntimeError("Donor failed before fault injection; experiment invalid")
        if args.pause_for_fault:
            input("Verified write. Stop the donor, then press Enter to test fallback: ")
        started = time.monotonic()
        if storage.read(4090, len(payload)) != payload:
            raise RuntimeError("Recovery read mismatch")
        read_seconds = time.monotonic() - started
        storage.write(0, b"still writable")
        if storage.read(0, 14) != b"still writable":
            raise RuntimeError("Follow-up write mismatch")
        print(json.dumps({"verified": True, "read_seconds": read_seconds,
                          "fallback_observed": not storage.snapshot()["remote_enabled"],
                          "metrics": storage.snapshot(),
                          "scope": "temporary file plus TLS/NBD; not system swap"}, indent=2))


if __name__ == "__main__":
    main()
