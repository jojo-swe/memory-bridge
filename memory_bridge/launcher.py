#!/usr/bin/env python3
"""Small donor launcher; does not modify swap, disks or firewall rules."""

import argparse
import os
from pathlib import Path
import secrets
import shutil
import stat
from memory_bridge.config import load_limits, validate_nbd_limits


def private_key(path):
    """Require a private, regular key file; never print its contents."""
    # Do not resolve a final symlink before O_NOFOLLOW can reject it. Validate
    # and read the same descriptor, not a path that could change between calls.
    path = Path(path).absolute()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Key must be a private regular file")
        if info.st_uid != os.getuid():
            raise ValueError("Key must belong to the current user")
        # Bound reads even if the file is concurrently enlarged.
        raw = source.read(129)
        if len(raw) > 128:
            raise ValueError("Key file is too large")
    lines = raw.decode("ascii").splitlines()
    if len(lines) != 1 or not lines[0].startswith("borrower:"):
        raise ValueError("Expected one borrower PSK entry")
    key = lines[0].partition(":")[2]
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError("Expected a 256-bit hexadecimal key")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    keygen = commands.add_parser("keygen", help="Create a private shared key, without overwriting")
    keygen.add_argument("path", type=Path)
    donor = commands.add_parser("donor", help="Start one volatile, single-use RAM export")
    donor.add_argument("--key", type=Path, required=True)
    donor.add_argument("--bind", default="127.0.0.1", help="Explicit LAN address for two-machine tests")
    donor.add_argument("--port", type=int, default=10809)
    donor.add_argument("--size-mib", type=int, default=None)
    donor.add_argument("--config", type=Path, help="TOML memory budgets enforced by the donor")
    donor.add_argument("--lab-delay-ms", type=int, default=0,
                       help="Laboratory only: add this delay to every read/write "
                            "(nbdkit delay filter) to model network latency")
    args = parser.parse_args()
    if args.action == "keygen":
        # Obtain entropy before creating the file: failures must not leave an
        # empty key file that prevents a subsequent retry.
        contents = "borrower:" + secrets.token_hex(32) + "\n"
        # Exclusive create prevents accidental replacement of an active key.
        fd = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(contents)
        print("Private key created. Transfer securely to the borrower; do not commit it.")
        return
    # Resolve configuration before launching nbdkit. With a configuration and
    # no explicit size, use up to 4 MiB within the owner's configured budget.
    config_path = args.config.resolve(strict=True) if args.config else None
    limits = load_limits(config_path) if config_path else None
    if limits:
        try:
            validate_nbd_limits(limits)
        except ValueError as error:
            parser.error(str(error))
    if args.size_mib is None:
        args.size_mib = min(4, limits.per_owner_bytes // (1024 * 1024)) if limits else 4
    if limits and args.size_mib * 1024 * 1024 > limits.per_owner_bytes:
        parser.error("Requested export exceeds per-owner memory budget")
    if not 1 <= args.size_mib <= 65536 or not 1 <= args.port <= 65535:
        parser.error("Export size must be 1..65536 MiB and port must be 1..65535")
    if not 0 <= args.lab_delay_ms <= 1000:
        parser.error("--lab-delay-ms must be 0..1000")
    # Reject a known impossible locked-memory budget before reading the key or
    # starting nbdkit. This is a necessary check, not a promise of free RAM;
    # the backend's mandatory mlock remains authoritative at allocation time.
    # CAP_IPC_LOCK exempts a process from RLIMIT_MEMLOCK (e.g. a non-root
    # donor started with only that ambient capability).
    import resource
    from memory_bridge.memlock import has_cap_ipc_lock
    soft_limit, _ = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    if (soft_limit != resource.RLIM_INFINITY and not has_cap_ipc_lock()
            and args.size_mib * 1024 * 1024 > soft_limit):
        parser.error("Export exceeds current memlock limit; reduce size or configure the OS limit")
    key = private_key(args.key)
    executable = shutil.which("nbdkit")
    if not executable:
        parser.error("Install nbdkit and nbdkit-plugin-python first")
    plugin = Path(__file__).resolve().with_name("nbd_plugin.py")
    # --foreground avoids daemon fork after mlock (locks are not inherited).
    # Required PSK TLS authenticates the borrower and forbids plaintext peers.
    command = [executable, "--foreground", "--threads=1", "--tls=require",
               f"--tls-psk={key}", f"--ipaddr={args.bind}", f"--port={args.port}"]
    if args.lab_delay_ms:
        # Filters wrap the plugin; TLS, binding and the single-use RAM export
        # are unchanged. The delay applies per NBD request at the donor.
        command.append("--filter=delay")
    command += ["python", str(plugin), f"size_mib={args.size_mib}"]
    if args.lab_delay_ms:
        command += [f"delay-read={args.lab_delay_ms}ms", f"delay-write={args.lab_delay_ms}ms"]
    if config_path:
        command.append(f"config_file={config_path}")
    print(f"Volatile RAM export: {args.size_mib} MiB at {args.bind}:{args.port}", flush=True)
    os.execv(executable, command)


if __name__ == "__main__":
    main()
