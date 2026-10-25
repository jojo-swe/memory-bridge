#!/usr/bin/env python3
"""Small donor launcher; does not modify swap, disks or firewall rules."""

import argparse
import os
from pathlib import Path
import secrets
import shutil
import stat


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
    donor.add_argument("--size-mib", type=int, default=4)
    args = parser.parse_args()
    if args.action == "keygen":
        # Exclusive create prevents accidental replacement of an active key.
        fd = os.open(args.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write("borrower:" + secrets.token_hex(32) + "\n")
        print("Private key created. Transfer securely to the borrower; do not commit it.")
        return
    key = private_key(args.key)
    if not 1 <= args.size_mib <= 65536 or not 1 <= args.port <= 65535:
        parser.error("Invalid size or port")
    executable = shutil.which("nbdkit")
    if not executable:
        parser.error("Install nbdkit and nbdkit-plugin-python first")
    plugin = Path(__file__).resolve().with_name("ram_plugin.py")
    # --foreground avoids daemon fork after mlock (locks are not inherited).
    # Required PSK TLS authenticates the borrower and forbids plaintext peers.
    command = [executable, "--foreground", "--threads=1", "--tls=require",
               f"--tls-psk={key}", f"--ipaddr={args.bind}",
               f"--port={args.port}", "python", str(plugin),
               f"size_mib={args.size_mib}"]
    print(f"Volatile RAM export: {args.size_mib} MiB at {args.bind}:{args.port}", flush=True)
    os.execv(executable, command)


if __name__ == "__main__":
    main()
