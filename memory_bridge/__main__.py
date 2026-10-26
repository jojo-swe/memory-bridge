"""Local development commands. No root privileges, swap changes or NAS needed."""

import argparse
import sys
from importlib import import_module
import importlib.util
import json
import platform
import shutil

from .config import load_limits
from .core import BackendError, Limits, MemoryPool, SessionError
from .simulation import SimulatedMemory


def demo():
    """Use the same allocation engine as the donor, with injected RAM loss."""
    stores = []

    def factory(size):
        store = SimulatedMemory(size)
        stores.append(store)
        return store

    with MemoryPool(Limits(16384, 8192, 4, 4096), factory) as pool:
        alice = pool.allocate("alice", 8192)
        bob = pool.allocate("bob", 8192)
        pool.write(alice, 0, b"hello")
        if pool.read(alice, 0, 5) != b"hello" or pool.read(bob, 0, 5) != bytes(5):
            raise RuntimeError("Isolation check failed")
        stores[0].fail_reads = True
        try:
            pool.read(alice, 0, 5)
        except BackendError:
            pass
        else:
            raise RuntimeError("Injected fault was not detected")
        try:
            pool.read(alice, 0, 5)
        except SessionError:
            pass
        else:
            raise RuntimeError("Stale session was accepted")
        pool.write(bob, 0, b"still alive")
        if pool.read(bob, 0, 11) != b"still alive":
            raise RuntimeError("Unaffected session failed")
        print(json.dumps({"mode": "local simulation, not a network benchmark",
                          "isolation": "passed", "fault_invalidation": "passed",
                          "unaffected_borrower": "passed", "metrics": pool.snapshot()}, indent=2))


def main():
    # Delegate complete argument parsing to each installed command. This keeps
    # --help and validation identical for the console entry point and modules.
    installed_commands = {
        "lab": "lab_cli", "compare": "compare_cli", "benchmark": "benchmark_cli",
        "recover": "recovery_cli", "checkpoint": "checkpoint_cli",
        "donor": "launcher", "keygen": "launcher", "export": "export_cli",
    }
    if len(sys.argv) > 1 and sys.argv[1] in installed_commands:
        command = sys.argv[1]
        forwarded = sys.argv[1:] if command in ("donor", "keygen") else sys.argv[2:]
        sys.argv = [f"memory-bridge {command}", *forwarded]
        return import_module("." + installed_commands[command], __package__).main()
    parser = argparse.ArgumentParser(description=__doc__,
        epilog="Installed commands: lab, compare, benchmark, recover, checkpoint, donor, keygen, export (use COMMAND --help)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo")
    commands.add_parser("doctor")
    recovery = commands.add_parser("recovery-demo")
    recovery.add_argument("--directory", help="Directory for temporary local backing")
    validate = commands.add_parser("validate-config")
    validate.add_argument("path")
    args = parser.parse_args()
    if args.command == "demo":
        demo()
    elif args.command == "recovery-demo":
        from .recovery_demo import recovery_demo
        recovery_demo(args.directory)
    elif args.command == "validate-config":
        limits = load_limits(args.path)
        print(json.dumps(limits.__dict__, indent=2))
    else:
        report = {"python": platform.python_version(), "system": platform.system(),
                  "nbdkit": shutil.which("nbdkit"),
                  "python_libnbd": importlib.util.find_spec("nbd") is not None}
        try:
            import resource
            report["memlock_limits_bytes"] = resource.getrlimit(resource.RLIMIT_MEMLOCK)
        except (ImportError, AttributeError):
            report["memlock_limits_bytes"] = None
        report["note"] = "Presence checks only; no claim of functioning NBD/TLS"
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
