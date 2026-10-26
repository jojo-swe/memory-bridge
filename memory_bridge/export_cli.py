"""Start the borrower block export on a private Unix socket (no TCP listener).

The export serves the resilient store: local O_DIRECT copy first, optional
SHA-256-verified cache in a donor's RAM. It does not create swap or touch any
device; attaching it (nbd-client, or nbdfuse + loop) is a separate, explicit
step for a disposable test VM. See docs/KORGUIDE.md.
"""

import argparse
import os
from pathlib import Path
import shutil
import stat
import sys

from .launcher import private_key


def build_command(args, executable, python):
    plugin = Path(__file__).resolve().with_name("block_export.py")
    # --foreground: mlockall is not inherited by a forked daemon.
    # --unix only: the export is borrower-local; never listen on TCP.
    command = [executable, "--foreground", "--unix", str(args.socket),
               "python", str(plugin), f"size_mib={args.size_mib}",
               f"timeout={args.timeout}", f"transfer_kib={args.transfer_kib}",
               f"max_request_kib={args.max_request_kib}",
               f"lock_memory={'true' if args.lock_memory else 'false'}",
               f"worker_python={python}"]
    if args.local is not None:
        command += [f"local={args.local}", f"create={'true' if args.create else 'false'}"]
    else:
        command.append(f"local_dir={args.local_dir}")
    if args.donor_host is not None:
        command += [f"donor_host={args.donor_host}", f"donor_port={args.donor_port}",
                    f"key={args.key}"]
    if args.stats is not None:
        command.append(f"stats={args.stats}")
    return command


def parse(argv=None):
    parser = argparse.ArgumentParser(prog="memory-bridge export", description=__doc__)
    parser.add_argument("--socket", type=Path, required=True,
                        help="Unix socket path to create (must not exist)")
    parser.add_argument("--size-mib", type=int, required=True)
    backing = parser.add_mutually_exclusive_group(required=True)
    backing.add_argument("--local", type=Path,
                         help="Named, restartable local copy (O_DIRECT, exclusive lock)")
    backing.add_argument("--local-dir", type=Path,
                         help="Disk-backed directory for an anonymous temporary copy")
    parser.add_argument("--create", action="store_true",
                        help="Create --local; never replaces an existing file")
    parser.add_argument("--donor-host")
    parser.add_argument("--donor-port", type=int, default=10809)
    parser.add_argument("--key", type=Path, help="Private PSK file for the donor")
    parser.add_argument("--timeout", type=float, default=2.0,
                        help="Deadline per remote operation; then the cache is disabled")
    parser.add_argument("--transfer-kib", type=int, default=128)
    parser.add_argument("--max-request-kib", type=int, default=1024)
    parser.add_argument("--stats", type=Path, help="JSON statistics, rewritten every second")
    parser.add_argument("--no-lock-memory", dest="lock_memory", action="store_false",
                        help="Skip mlockall (tests only; unsafe for swap)")
    parser.add_argument("--dry-run", action="store_true", help="Print the nbdkit command")
    args = parser.parse_args(argv)
    if not 1 <= args.size_mib <= 1024 * 1024:
        parser.error("--size-mib must be 1..1048576")
    if args.create and args.local is None:
        parser.error("--create requires --local")
    if (args.donor_host is None) != (args.key is None):
        parser.error("--donor-host and --key must be given together")
    if not 1 <= args.donor_port <= 65535:
        parser.error("--donor-port must be 1..65535")
    if not 0 < args.timeout <= 600:
        parser.error("--timeout must be in (0, 600] seconds")
    if args.socket.exists() or args.socket.is_symlink():
        parser.error("--socket already exists; remove a stale socket explicitly")
    # Exclusive local access must not depend on the socket's own mode, which
    # nbdkit chooses: require a private parent directory instead.
    try:
        parent = os.stat(args.socket.absolute().parent, follow_symlinks=False)
    except OSError:
        parser.error("--socket directory does not exist")
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) & 0o077):
        parser.error("--socket must be inside a private directory (mode 0700, owned by you)")
    if args.lock_memory:
        # mlockall charges the whole address space (~300-400 MiB for nbdkit
        # with Python) to RLIMIT_MEMLOCK unless the process has CAP_IPC_LOCK.
        import resource
        from .memlock import has_cap_ipc_lock
        soft, _ = resource.getrlimit(resource.RLIMIT_MEMLOCK)
        if soft != resource.RLIM_INFINITY and not has_cap_ipc_lock():
            parser.error("Memory locking needs CAP_IPC_LOCK or an unlimited memlock limit "
                         "(ulimit -l unlimited); pass --no-lock-memory only for tests, "
                         "never for swap")
    if args.key is not None:
        args.key = private_key(args.key)
    for name in ("socket", "local", "local_dir", "stats"):
        value = getattr(args, name)
        if value is not None:
            # nbdkit receives key=value pairs; reject separators it would split.
            text = str(value.absolute())
            if "\0" in text or "\n" in text:
                parser.error(f"--{name.replace('_', '-')} contains invalid characters")
            setattr(args, name, Path(text))
    return args


def main(argv=None):
    args = parse(argv)
    executable = shutil.which("nbdkit")
    if not executable:
        raise SystemExit("Install nbdkit and nbdkit-plugin-python first")
    command = build_command(args, executable, sys.executable)
    if args.dry_run:
        print(" ".join(command))
        return 0
    # Defence in depth; the private parent directory is the actual guarantee.
    os.umask(0o077)
    print(f"Block export: {args.size_mib} MiB on unix:{args.socket}", flush=True)
    os.execv(executable, command)


if __name__ == "__main__":
    raise SystemExit(main())
