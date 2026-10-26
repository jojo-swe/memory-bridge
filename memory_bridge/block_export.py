"""Expose a borrower Storage as one file that a loop device can turn into swap.

Why FUSE: the experiment kernel has no NBD client, no ublk and no loadable
modules, so the only unprivileged-code path to a kernel block device is
FUSE file -> loop device. The export is mounted with direct_io so the page
cache never holds a second copy of swap data, and the loop device is attached
with --direct-io=on by the lab for the same reason.

Guarantees: read/write map 1:1 onto the Storage. flush/fsync call the
Storage's flush() when it has one (local backing fdatasync); the remote cache
never participates. Any storage failure is reported to the kernel as EIO,
which it treats as a failed swap I/O. Nothing is retried here.

Deadlock caveat: this daemon allocates Python objects in the I/O path. It
locks its memory with mlockall so its own pages cannot be swapped to the
device it serves, but under global memory pressure the kernel can still wait
for an allocation that waits for reclaim. The lab therefore confines memory
pressure to the workload's cgroup and never runs this daemon inside it.
"""

import ctypes
import errno
import json
import os
import stat
import sys
import threading
import time

from .core import BackendError

VOLUME_NAME = "volume"
STATS_NAME = "stats.json"
_MCL_CURRENT = 1
_MCL_FUTURE = 2


def lock_all_memory():
    """Pin current and future pages; the caller decides whether failure is fatal."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mlockall.argtypes = [ctypes.c_int]
    libc.mlockall.restype = ctypes.c_int
    if libc.mlockall(_MCL_CURRENT | _MCL_FUTURE) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "mlockall failed; raise the memlock limit or pass --allow-unlocked")


def _fuse_module():
    try:
        import fuse
    except ImportError:
        raise RuntimeError("Install fusepy (pip install fusepy) and libfuse2 for the block export") from None
    if not hasattr(fuse, "FUSE") or not hasattr(fuse, "Operations"):
        raise RuntimeError("An incompatible 'fuse' module is installed; fusepy is required")
    return fuse


class ExportStats:
    """Aggregate counters only; no data, offsets or keys are retained."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reads = self.writes = self.flushes = self.errors = 0
        self.read_bytes = self.write_bytes = 0
        self.max_read_seconds = self.max_write_seconds = 0.0
        self.total_read_seconds = self.total_write_seconds = 0.0

    def record(self, kind, count, seconds):
        with self.lock:
            if kind == "read":
                self.reads += 1
                self.read_bytes += count
                self.total_read_seconds += seconds
                self.max_read_seconds = max(self.max_read_seconds, seconds)
            else:
                self.writes += 1
                self.write_bytes += count
                self.total_write_seconds += seconds
                self.max_write_seconds = max(self.max_write_seconds, seconds)

    def snapshot(self):
        with self.lock:
            return {"reads": self.reads, "writes": self.writes, "flushes": self.flushes,
                    "errors": self.errors, "read_bytes": self.read_bytes,
                    "write_bytes": self.write_bytes,
                    "max_read_seconds": self.max_read_seconds,
                    "max_write_seconds": self.max_write_seconds,
                    "total_read_seconds": self.total_read_seconds,
                    "total_write_seconds": self.total_write_seconds}


class BlockFileOperations:
    """fusepy Operations serving /volume (data) and /stats.json (read-only)."""

    use_ns = True  # fusepy passes nanosecond timestamps; we never use them.

    def __init__(self, storage, *, max_request_bytes=1024 * 1024, ready=None):
        fuse = _fuse_module()
        self._fuse = fuse
        if type(storage.size) is not int or storage.size <= 0 or storage.size % 4096:
            raise ValueError("Storage size must be a positive multiple of 4096")
        if type(max_request_bytes) is not int or max_request_bytes <= 0 or max_request_bytes % 4096:
            raise ValueError("Request maximum must be a positive multiple of 4096")
        self._storage = storage
        self._size = storage.size
        self._maximum = max_request_bytes
        self._ready = ready
        self._started = time.time_ns()
        self.stats = ExportStats()

    # fusepy looks up methods by name; unsupported ones raise ENOSYS/EROFS.
    def __call__(self, operation, *args):
        method = getattr(self, operation, None)
        if method is None:
            raise self._fuse.FuseOSError(errno.ENOSYS)
        return method(*args)

    def _fail(self, code):
        with self.stats.lock:
            self.stats.errors += 1
        raise self._fuse.FuseOSError(code)

    def init(self, path):
        if self._ready is not None:
            self._ready()

    def getattr(self, path, fh=None):
        now = self._started
        base = {"st_uid": os.getuid(), "st_gid": os.getgid(),
                "st_atime": now, "st_mtime": now, "st_ctime": now}
        if path == "/":
            return dict(base, st_mode=stat.S_IFDIR | 0o700, st_nlink=2)
        if path == "/" + VOLUME_NAME:
            return dict(base, st_mode=stat.S_IFREG | 0o600, st_nlink=1, st_size=self._size,
                        st_blksize=4096, st_blocks=self._size // 512)
        if path == "/" + STATS_NAME:
            return dict(base, st_mode=stat.S_IFREG | 0o400, st_nlink=1,
                        st_size=len(self._stats_bytes()))
        raise self._fuse.FuseOSError(errno.ENOENT)

    def readdir(self, path, fh):
        return [".", "..", VOLUME_NAME, STATS_NAME]

    def open(self, path, flags):
        if path == "/" + VOLUME_NAME:
            return 0
        if path == "/" + STATS_NAME:
            if flags & (os.O_WRONLY | os.O_RDWR):
                raise self._fuse.FuseOSError(errno.EACCES)
            return 1
        raise self._fuse.FuseOSError(errno.ENOENT)

    def _stats_bytes(self):
        report = {"export": self.stats.snapshot(), "capacity_bytes": self._size}
        snapshot = getattr(self._storage, "snapshot", None)
        if snapshot is not None:
            try:
                report["storage"] = snapshot()
            except Exception:
                report["storage"] = None
        return (json.dumps(report, indent=2) + "\n").encode()

    def read(self, path, size, offset, fh):
        if path == "/" + STATS_NAME:
            return self._stats_bytes()[offset:offset + size]
        if path != "/" + VOLUME_NAME:
            raise self._fuse.FuseOSError(errno.ENOENT)
        if offset < 0 or size < 0:
            raise self._fuse.FuseOSError(errno.EINVAL)
        if offset >= self._size:
            return b""
        size = min(size, self._size - offset)
        started = time.perf_counter()
        parts = []
        try:
            done = 0
            while done < size:
                length = min(self._maximum, size - done)
                parts.append(self._storage.read(offset + done, length))
                done += length
        except (BackendError, OSError, RuntimeError):
            self._fail(errno.EIO)
        except ValueError:
            self._fail(errno.EINVAL)
        self.stats.record("read", size, time.perf_counter() - started)
        return parts[0] if len(parts) == 1 else b"".join(parts)

    def write(self, path, data, offset, fh):
        if path != "/" + VOLUME_NAME:
            raise self._fuse.FuseOSError(errno.EACCES)
        if offset < 0 or offset + len(data) > self._size:
            raise self._fuse.FuseOSError(errno.ENOSPC)
        if not isinstance(data, bytes):
            data = bytes(data)
        started = time.perf_counter()
        try:
            done = 0
            while done < len(data):
                length = min(self._maximum, len(data) - done)
                chunk = data if length == len(data) else data[done:done + length]
                self._storage.write(offset + done, chunk)
                done += length
        except (BackendError, OSError, RuntimeError):
            self._fail(errno.EIO)
        except ValueError:
            self._fail(errno.EINVAL)
        self.stats.record("write", len(data), time.perf_counter() - started)
        return len(data)

    def _sync(self):
        flush = getattr(self._storage, "flush", None)
        with self.stats.lock:
            self.stats.flushes += 1
        if flush is None:
            return 0
        try:
            flush()
        except (BackendError, OSError, RuntimeError):
            self._fail(errno.EIO)
        return 0

    def flush(self, path, fh):
        return self._sync() if path == "/" + VOLUME_NAME else 0

    def fsync(self, path, datasync, fh):
        return self._sync() if path == "/" + VOLUME_NAME else 0

    def truncate(self, path, length, fh=None):
        # Resizing a swap volume underneath the kernel is never permitted.
        if path == "/" + VOLUME_NAME and length == self._size:
            return 0
        raise self._fuse.FuseOSError(errno.EPERM)

    def release(self, path, fh):
        return 0

    def statfs(self, path):
        return {"f_bsize": 4096, "f_frsize": 4096, "f_blocks": self._size // 4096,
                "f_bfree": 0, "f_bavail": 0, "f_files": 2, "f_ffree": 0, "f_namemax": 255}

    def destroy(self, path):
        return None


def serve(storage, mountpoint, *, max_request_bytes=1024 * 1024, ready=None,
          lock_memory=True, max_read=131072):
    """Mount in the foreground and return when the filesystem is unmounted."""
    fuse = _fuse_module()
    mountpoint = str(mountpoint)
    if not os.path.isdir(mountpoint):
        raise ValueError("Mountpoint must be an existing directory")
    if lock_memory:
        lock_all_memory()
    operations = BlockFileOperations(storage, max_request_bytes=max_request_bytes, ready=ready)
    # direct_io: no kernel page cache for the volume (the loop device and swap
    # already own the data). big_writes raises the write unit above 4 KiB.
    fuse.FUSE(operations, mountpoint, foreground=True, nothreads=False,
              direct_io=True, big_writes=True, max_read=max_read,
              fsname="memory-bridge", subtype="memory-bridge")
    return operations.stats.snapshot()
