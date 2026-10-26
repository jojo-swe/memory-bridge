#!/usr/bin/env python3
"""Mount a borrower storage stack as a single FUSE file for loop/swap experiments.

Runs in the foreground until unmounted (fusermount -u MOUNTPOINT). Prints one
JSON line "ready" on stdout once the kernel has accepted the mount, and the
final export statistics on exit. It does not create loop devices or swap; the
swap lab does that in an explicitly disposable VM.

Backings:
  direct      O_DIRECT temporary file (default; fair for swap comparisons)
  temp        buffered temporary file (page cache may hold a second copy)
  persistent  SQLite page store at --path (created with --create)

With --key the local backing is wrapped in ResilientStorage with a TLS/NBD
remote cache; without it the stack is local only.
"""

import argparse
from contextlib import ExitStack
import json
import math
from pathlib import Path
import sys

from memory_bridge.block_export import serve
from memory_bridge.direct_file import DirectFileStorage
from memory_bridge.launcher import private_key
from memory_bridge.local_disk import TemporaryFileStorage
from memory_bridge.persistent import PersistentStorage
from memory_bridge.remote_nbd import RemoteNBDStorage
from memory_bridge.resilient import ResilientStorage


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mountpoint", type=Path, help="Existing empty directory")
    parser.add_argument("--size-mib", type=int, required=True)
    parser.add_argument("--backing", choices=("direct", "temp", "persistent"), default="direct")
    parser.add_argument("--path", type=Path, help="Persistent database path")
    parser.add_argument("--create", action="store_true", help="Create the persistent database")
    parser.add_argument("--directory", type=Path, help="Disk-backed directory for temporary backings")
    parser.add_argument("--key", type=Path, help="Private PSK; enables the TLS/NBD remote cache")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10809)
    parser.add_argument("--io-timeout", type=float, default=2.0, help="Seconds per remote operation")
    parser.add_argument("--transfer-kib", type=int, default=64, help="Remote mirror/read batch size")
    parser.add_argument("--write-behind-mib", type=int, default=16,
                        help="Mirror budget for asynchronous remote writes; 0 mirrors synchronously")
    parser.add_argument("--disk-latency-ms", type=float, default=0.0,
                        help="Emulate a slow local disk by sleeping before each local I/O (direct backing)")
    parser.add_argument("--allow-unlocked", action="store_true",
                        help="Continue if mlockall fails (unsafe under memory pressure)")
    args = parser.parse_args()
    if not 1 <= args.size_mib <= 65536 or not 1 <= args.port <= 65535:
        parser.error("size-mib must be 1..65536 and port 1..65535")
    if not math.isfinite(args.io_timeout) or args.io_timeout <= 0:
        parser.error("io-timeout must be finite and positive")
    if not 4 <= args.transfer_kib <= 1024 or args.transfer_kib & (args.transfer_kib - 1):
        parser.error("transfer-kib must be a power of two from 4 to 1024")
    if not 0 <= args.write_behind_mib <= 1024:
        parser.error("write-behind-mib must be 0..1024")
    if not 0 <= args.disk_latency_ms <= 1000:
        parser.error("disk-latency-ms must be 0..1000")
    if args.disk_latency_ms and args.backing != "direct":
        parser.error("disk latency emulation needs the direct backing")
    if args.backing == "persistent" and args.path is None:
        parser.error("--path is required for the persistent backing")
    if not args.mountpoint.is_dir():
        parser.error("Mountpoint must be an existing directory")
    key = private_key(args.key) if args.key is not None else None
    size = args.size_mib * 1024 * 1024

    def ready():
        print(json.dumps({"ready": True, "mountpoint": str(args.mountpoint.absolute()),
                          "size_bytes": size, "remote": key is not None}), flush=True)

    # Allocate local backing first so a failure never consumes the single-use
    # donor. ExitStack closes every successfully created layer in reverse.
    with ExitStack() as cleanup:
        if args.backing == "direct":
            local = DirectFileStorage(size, args.directory,
                                      emulated_latency_seconds=args.disk_latency_ms / 1000)
        elif args.backing == "temp":
            local = TemporaryFileStorage(size, args.directory)
        else:
            local = PersistentStorage(args.path, size, create=args.create)
        cleanup.callback(local.close)
        storage = local
        if key is not None:
            remote = RemoteNBDStorage(args.host, args.port, key, size, timeout=args.io_timeout)
            cleanup.callback(remote.close)
            storage = ResilientStorage(local, remote, transfer_bytes=args.transfer_kib * 1024,
                                       write_behind_bytes=args.write_behind_mib * 1024 * 1024)
            cleanup.callback(storage.close)
        try:
            stats = serve(storage, args.mountpoint, ready=ready,
                          lock_memory=not args.allow_unlocked)
        except OSError as error:
            parser.exit(1, f"Block export failed: {error}\n")
        final = {"export": stats}
        if key is not None:
            final["storage"] = storage.snapshot()
        print(json.dumps(final), flush=True)


if __name__ == "__main__":
    sys.exit(main())
