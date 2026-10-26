#!/usr/bin/env python3
"""One-command local TLS/NBD experiment, with a temporary key and bounded cleanup.

Starts a loopback-only, single-use donor. No root, NAS, network exposure,
system swap, or persistent key is required. NBD system packages are required.
"""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import resource
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from memory_bridge.compare_cli import build_report, positive_seconds, run_mode
from memory_bridge.storage import memlock_limit_applies

ROOT = Path(__file__).resolve().parent.parent


def wait_for_donor(process, port, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Donor failed during startup; check memlock and nbdkit Python plugin")
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.1):
                return  # Unauthenticated probe does not consume the export.
        except OSError:
            time.sleep(.02)
    raise RuntimeError("Donor startup deadline exceeded")


def stop_donor(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size-mib', type=int, default=1)
    parser.add_argument('--operations', type=int, default=256)
    parser.add_argument('--request-kib', type=int, default=64)
    parser.add_argument('--memory-limit-mib', type=int)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--order', choices=('local-first', 'remote-first'), default='local-first')
    parser.add_argument('--io-timeout', type=positive_seconds, default=2)
    parser.add_argument('--run-timeout', type=positive_seconds, default=120)
    args = parser.parse_args()
    if not 1 <= args.size_mib <= 65536 or not 1 <= args.operations <= 100000:
        parser.error('size-mib must be 1..65536; operations must be 1..100000')
    if not 4 <= args.request_kib <= 1024 or args.request_kib & (args.request_kib - 1):
        parser.error('request-kib must be a power of two from 4 to 1024')
    if not 0 <= args.seed <= 0xffffffff:
        parser.error('seed must be an unsigned 32-bit integer')
    if args.memory_limit_mib is not None and not 32 <= args.memory_limit_mib <= 65536:
        parser.error('memory-limit-mib must be 32..65536')
    if not shutil.which('nbdkit') or importlib.util.find_spec('nbd') is None:
        parser.error('Install nbdkit, nbdkit-plugin-python and python3-libnbd; use /usr/bin/python3')
    soft, _ = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    if memlock_limit_applies() and soft != resource.RLIM_INFINITY and args.size_mib * 1024 * 1024 > soft:
        parser.error('Donor size exceeds current memlock limit; reduce --size-mib')
    args.host = '127.0.0.1'
    # Reserving a port then starting nbdkit has a small allocation race. A race
    # must fail authentication/startup, never connect without our ephemeral PSK.
    with socket.socket() as reservation:
        reservation.bind((args.host, 0))
        args.port = reservation.getsockname()[1]
    try:
        with tempfile.TemporaryDirectory(prefix='memory-bridge-lab-') as directory:
            args.key = Path(directory) / 'session.psk'
            fd = os.open(args.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output:
                output.write('borrower:' + secrets.token_hex(32) + '\n')
            donor = subprocess.Popen([sys.executable, "-m", "memory_bridge.launcher", 'donor',
                                      '--key', str(args.key), '--port', str(args.port),
                                      '--size-mib', str(args.size_mib)],
                                     cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                wait_for_donor(donor, args.port)
                modes = ['local', 'remote'] if args.order == 'local-first' else ['remote', 'local']
                report = build_report([run_mode(args, mode) for mode in modes])
                report['lab'] = {'scope': 'loopback only', 'ephemeral_key': True,
                                 'donor_size_mib': args.size_mib}
            finally:
                stop_donor(donor)
        # Emit success only after all process/file cleanup has completed.
        print(json.dumps(report, indent=2))
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        parser.exit(1, f'Lab failed ({type(error).__name__}); no valid comparison produced\n')


if __name__ == '__main__':
    main()
