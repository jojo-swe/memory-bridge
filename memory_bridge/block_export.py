"""Borrower block export: the resilient store as a local NBD device (nbdkit API v2).

This is the missing adapter between the borrower's recovery layer and the
kernel. It is NOT the donor's raw RAM export: every request goes through
ResilientStorage, so writes land on the local copy first, remote reads are
SHA-256 verified before exposure, remote timeouts disable the cache for good,
and the local copy stays authoritative.

Intended topology on the borrower (Unix socket only, never TCP):

    kernel nbd-client  ─┐                       ┌─ local copy (O_DIRECT file)
    or loop → nbdfuse ──┴─ nbdkit + this plugin ─┤
                                                └─ verified cache → TLS → donor

Guarantees advertised to the NBD client, and only these:

* can_write; flush = fdatasync of the local copy; FUA emulated by nbdkit as
  write + flush. The donor's RAM never counts toward durability.
* No multi-conn, trim, zero offload or caching hints: nbdkit emulates zero
  writes with ordinary writes, so every byte still follows the local-first path.
* One client connection at a time (exclusive ownership). A later client may
  connect after the previous one closed; data lives as long as this process,
  and across restarts only with a named local backing (see restart notes).

Memory-pressure notes: with lock_memory=true (default) the nbdkit process and
its NBD worker call mlockall(MCL_CURRENT|MCL_FUTURE) so they cannot be swapped
to themselves (recursive swap). PR_SET_IO_FLUSHER is attempted and reported.
Python still allocates per request; this is reduced, not eliminated, deadlock
risk. See docs/SWAP-EXPERIMENT-RESULTAT.md for what was and was not tested.
"""

import builtins
import json
import os
from pathlib import Path
import sys
import threading
import time

# nbdkit embeds Python and may not put the plugin's directory on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nbdkit  # noqa: E402  (only importable inside nbdkit)

from memory_bridge.direct_file import DirectFileStorage  # noqa: E402
from memory_bridge.launcher import private_key  # noqa: E402
from memory_bridge.memlock import lock_all_memory, set_io_flusher  # noqa: E402
from memory_bridge.remote_nbd import RemoteNBDStorage  # noqa: E402
from memory_bridge.resilient import ResilientStorage  # noqa: E402

API_VERSION = 2

_BOOLEANS = {"true": True, "false": False, "1": True, "0": False, "yes": True, "no": False}
_settings = {
    "size_mib": None, "local": None, "local_dir": None, "create": False,
    "donor_host": None, "donor_port": 10809, "key": None, "timeout": 2.0,
    "transfer_kib": 128, "max_request_kib": 1024, "worker_python": None,
    "lock_memory": True, "stats": None,
}
_state = None


class ExportState:
    """Own the store and enforce a single active client connection."""

    def __init__(self, store, size, max_request, protections, local, remote):
        self.store = store
        self.size = size
        self.max_request = max_request
        self.protections = protections
        self.local = local
        self.remote = remote
        self.mutex = threading.Lock()
        self.active = None
        self.connections = 0
        self.requests = {"read": 0, "write": 0, "flush": 0, "errors": 0}
        self.started = time.time()

    def acquire(self, readonly):
        with self.mutex:
            if self.active is not None:
                # Two writers on one swap device would corrupt it silently.
                raise RuntimeError("Export already has a client; exclusive ownership")
            self.connections += 1
            self.active = Handle(self, readonly, self.connections)
            return self.active

    def release(self, handle):
        with self.mutex:
            if self.active is handle:
                self.active = None

    def snapshot(self):
        report = {"pid": os.getpid(), "time": time.time(),
                  "uptime_s": time.time() - self.started,
                  "size_bytes": self.size, "connections": self.connections,
                  "client_connected": self.active is not None,
                  "requests": dict(self.requests), "protections": self.protections,
                  "local_backing": "named" if not self.local.temporary else "temporary",
                  "remote_configured": self.remote is not None,
                  "worker_pid": None}
        if self.remote is not None and self.remote._process is not None:
            report["worker_pid"] = self.remote._process.pid
        report["store"] = self.store.snapshot()
        return report


class Handle:
    def __init__(self, state, readonly, number):
        self.state = state
        self.readonly = readonly
        self.number = number


def _parse_bool(key, value):
    lowered = value.strip().lower()
    if lowered not in _BOOLEANS:
        raise ValueError(f"{key} must be true or false")
    return _BOOLEANS[lowered]


def config(key, value):
    if key not in _settings:
        raise ValueError(f"Unknown parameter {key!r}")
    if key in ("create", "lock_memory"):
        _settings[key] = _parse_bool(key, value)
    elif key in ("size_mib", "donor_port", "transfer_kib", "max_request_kib"):
        _settings[key] = int(value)
    elif key == "timeout":
        _settings[key] = float(value)
    else:
        _settings[key] = value


def config_complete():
    global _state
    s = _settings
    if s["size_mib"] is None or not 1 <= s["size_mib"] <= 1024 * 1024:
        raise ValueError("size_mib is required (1..1048576)")
    if (s["local"] is None) == (s["local_dir"] is None):
        raise ValueError("Give exactly one of local=PATH or local_dir=DIR")
    if s["local_dir"] is not None and s["create"]:
        raise ValueError("create applies only to a named local backing")
    remote_keys = [s["donor_host"], s["key"]]
    if any(v is not None for v in remote_keys) and not all(v is not None for v in remote_keys):
        raise ValueError("donor_host and key must be given together")
    max_request = s["max_request_kib"] * 1024
    transfer = s["transfer_kib"] * 1024
    if not 4096 <= max_request <= 32 * 1024 * 1024 or max_request & (max_request - 1):
        raise ValueError("max_request_kib must be a power of two between 4 and 32768")
    if transfer % 4096 or not 4096 <= transfer <= max_request:
        raise ValueError("transfer_kib must be 4 KiB aligned and at most max_request_kib")
    size = s["size_mib"] * 1024 * 1024

    protections = {"mlockall": False, "io_flusher": False}
    if s["lock_memory"]:
        # Lock before allocating buffers so every later page is locked too.
        # Failure is fatal: an unlocked swap server can be swapped to itself.
        lock_all_memory()
        protections["mlockall"] = True
        protections["io_flusher"] = set_io_flusher()

    if s["local"] is not None:
        local = DirectFileStorage.open_named(s["local"], size, create=s["create"],
                                             max_request_bytes=max_request)
    else:
        local = DirectFileStorage.temporary(s["local_dir"], size,
                                            max_request_bytes=max_request)
    remote = None
    try:
        if s["donor_host"] is not None:
            key = private_key(s["key"])
            python = s["worker_python"] or sys.executable
            remote = RemoteNBDStorage(s["donor_host"], s["donor_port"], key, size,
                                      timeout=s["timeout"], max_request_bytes=max_request,
                                      python=python, lock_memory=s["lock_memory"])
        store = ResilientStorage(local, remote, block_size=4096,
                                 max_request_bytes=max_request, transfer_bytes=transfer)
    except BaseException:
        local.close()
        if remote is not None:
            remote.close()
        raise
    _state = ExportState(store, size, max_request, protections, local, remote)
    if s["stats"]:
        threading.Thread(target=_stats_loop, args=(Path(s["stats"]),), daemon=True).start()


def _write_stats(path):
    data = json.dumps(_state.snapshot(), sort_keys=True)
    temporary = path.with_name(path.name + ".tmp")
    # nbdkit requires a module-level callback named open(); it shadows the
    # builtin here, so file access must name builtins.open explicitly.
    with builtins.open(temporary, "w") as output:
        output.write(data)
    os.replace(temporary, path)


def _stats_loop(path):
    # Telemetry must never disturb the I/O path: failures are ignored.
    while True:
        try:
            _write_stats(path)
        except Exception:
            pass
        time.sleep(1.0)


def thread_model():
    # The store serializes all I/O; say so instead of pretending parallelism.
    return nbdkit.THREAD_MODEL_SERIALIZE_ALL_REQUESTS


def open(readonly):
    return _state.acquire(readonly)


def close(handle):
    handle.state.release(handle)


def get_size(handle):
    return handle.state.size


def can_write(handle):
    return not handle.readonly


def can_flush(handle):
    return True


def can_fua(handle):
    return nbdkit.FUA_EMULATE


def can_multi_conn(handle):
    return False


def can_trim(handle):
    return False


def can_zero(handle):
    return False


def can_cache(handle):
    return nbdkit.CACHE_NONE


def is_rotational(handle):
    return False


def block_size(handle):
    return (1, 4096, handle.state.max_request)


def _chunks(offset, length, maximum):
    # Clients should honour the advertised maximum; split defensively anyway.
    end = offset + length
    while offset < end:
        size = min(maximum, end - offset)
        yield offset, size
        offset += size


def pread(handle, buf, offset, flags):
    state = handle.state
    try:
        position = 0
        for start, size in _chunks(offset, len(buf), state.max_request):
            buf[position:position + size] = state.store.read(start, size)
            position += size
        state.requests["read"] += 1
    except BaseException:
        state.requests["errors"] += 1
        raise  # nbdkit reports EIO; never return partial or guessed data.


def pwrite(handle, buf, offset, flags):
    state = handle.state
    if handle.readonly:
        raise PermissionError("Read-only connection")
    try:
        view = memoryview(buf)
        position = 0
        for start, size in _chunks(offset, len(buf), state.max_request):
            state.store.write(start, bytes(view[position:position + size]))
            position += size
        state.requests["write"] += 1
    except BaseException:
        state.requests["errors"] += 1
        raise


def flush(handle, flags):
    state = handle.state
    try:
        state.store.flush()
        state.requests["flush"] += 1
    except BaseException:
        state.requests["errors"] += 1
        raise


def cleanup():
    if _state is None:
        return
    try:
        if _settings["stats"]:
            _write_stats(Path(_settings["stats"]))
    finally:
        _state.store.close()
