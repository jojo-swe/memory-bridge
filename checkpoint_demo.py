#!/usr/bin/env python3
"""Create and verify a persistent synthetic checkpoint, optionally with a donor.

Create refuses existing files. Verify opens the local checkpoint after restart
and never needs the original donor or its key. This is not application swap.
"""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path

from bridge import private_key
from memory_bridge.persistent import PersistentStorage
from memory_bridge.remote_nbd import RemoteNBDStorage
from memory_bridge.resilient import ResilientStorage

PAYLOAD = b'Memory Bridge persistent checkpoint version 1\n' * 200
OFFSET = 4090
MARKER = b'checkpoint complete'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'verify'))
    parser.add_argument('path', type=Path)
    parser.add_argument('--size-mib', type=int, default=1)
    parser.add_argument('--key', type=Path, help='Optional donor key for create only')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=10809)
    args = parser.parse_args()
    if not 1 <= args.size_mib <= 65536 or not 1 <= args.port <= 65535:
        parser.error('size-mib must be 1..65536; port must be 1..65535')
    if args.action == 'verify' and args.key is not None:
        parser.error('verify uses only the local checkpoint; omit --key')
    key = private_key(args.key) if args.key is not None else None
    size = args.size_mib * 1024 * 1024
    with ExitStack() as cleanup:
        local = PersistentStorage(args.path, size, create=args.action == 'create')
        cleanup.callback(local.close)
        remote = None
        if key is not None:
            remote = RemoteNBDStorage(args.host, args.port, key, size)
            cleanup.callback(remote.close)
        storage = ResilientStorage(local, remote, transfer_bytes=65536)
        cleanup.callback(storage.close)
        if args.action == 'create':
            # Each write commits locally before any optional remote mirror.
            storage.write(OFFSET, PAYLOAD)
            storage.write(0, MARKER)
        if storage.read(OFFSET, len(PAYLOAD)) != PAYLOAD or storage.read(0, len(MARKER)) != MARKER:
            raise RuntimeError('Checkpoint contents failed verification')
        state = storage.snapshot()
        if key is not None and not state['remote_enabled']:
            raise RuntimeError('Checkpoint saved locally, but remote-cache verification failed')
    print(json.dumps({'verified': True, 'action': args.action,
                      'backing': 'persistent SQLite; retained after close',
                      'remote_cache_used': key is not None,
                      'scope': 'synthetic restart test, not power-loss or system-swap validation'}, indent=2))


if __name__ == '__main__':
    main()
