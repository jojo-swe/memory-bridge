"""Memory Bridge: bounded, locked RAM storage for nbdkit's Python API v2.

One authenticated connection is allowed for the lifetime of the process.
This is volatile lab storage, not fault-tolerant swap or durable storage.
nbdkit owns framing, TLS, request validation and protocol negotiation.
"""

import threading
import sys
from pathlib import Path

# nbdkit embeds Python and may not put the plugin's directory on sys.path.
# Resolve relative to this file, never relative to the caller's working dir.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from memory_bridge.storage import LockedMemory
from memory_bridge.core import Limits, MemoryPool
from memory_bridge.config import load_limits

API_VERSION = 2


class SingleSession:
    """Never attach a second borrower or pretend a reconnect preserves data."""

    def __init__(self, memory):
        self.memory = memory
        self.claimed = False
        self.mutex = threading.Lock()

    def acquire(self):
        with self.mutex:
            if self.claimed:
                raise RuntimeError("Single-use export consumed; restart donor for a new experiment")
            self.claimed = True
            return self.memory


_size = 4 * 1024 * 1024
_session = None
_pool = None
_config_file = None


class PoolHandle:
    """NBD adapter over the shared engine; no second allocation path."""

    def __init__(self, pool, size):
        self.pool = pool
        self.size = size
        self.token = pool.allocate("nbd-borrower", size)
        self.readonly = False

    def read(self, offset, count):
        return self.pool.read(self.token, offset, count)

    def write(self, offset, data):
        if self.readonly:
            raise PermissionError("Export is read-only")
        self.pool.write(self.token, offset, bytes(data))

    def close(self):
        # Closing the entire single-use pool is idempotent after normal cleanup.
        self.pool.close()


def config(key, value):
    global _size, _config_file
    if key == "config_file":
        _config_file = value
        return
    if key != "size_mib":
        raise ValueError("Only size_mib and config_file are supported")
    amount = int(value)
    if not 1 <= amount <= 65536:
        raise ValueError("size_mib must be between 1 and 65536")
    _size = amount * 1024 * 1024


def config_complete():
    global _session, _pool
    limits = load_limits(_config_file) if _config_file else Limits(_size, _size, 1, 1024 * 1024)
    # NBD requires a power-of-two maximum block size; keep the transport's
    # advertised limit compatible with the core before allocating locked RAM.
    maximum = limits.max_request_bytes
    if maximum < 4096 or maximum > 32 * 1024 * 1024 or maximum & (maximum - 1):
        raise ValueError("NBD max_request_bytes must be a power of two from 4096 to 33554432")
    _pool = MemoryPool(limits, LockedMemory)
    _session = SingleSession(PoolHandle(_pool, _size))


def open(readonly):
    handle = _session.acquire()
    handle.readonly = readonly
    return handle


def get_size(handle):
    return handle.size


def can_multi_conn(handle):
    return False


def block_size(handle):
    # Bound per-request copies as well as the long-lived data allocation.
    return (1, 4096, handle.pool.limits.max_request_bytes)


def can_write(handle):
    return not handle.readonly


def pread(handle, buf, offset, flags):
    buf[:] = handle.read(offset, len(buf))


def pwrite(handle, buf, offset, flags):
    handle.write(offset, buf)


def close(handle):
    handle.close()


def cleanup():
    if _session is not None:
        _session.memory.close()
