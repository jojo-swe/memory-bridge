"""Exclusively owned local backing that bypasses the page cache (Linux O_DIRECT).

Why O_DIRECT: when this backing holds swapped-out pages, buffered I/O would
copy every swapped page into the OS page cache, i.e. back into RAM. That would
both distort a RAM-shortage experiment and defeat the purpose of swap. All I/O
goes through one preallocated, page-aligned buffer, so the local path does not
allocate per request beyond the bytes object handed back to Python callers.

Guarantees, stated narrowly:

* A completed write() has been submitted to the device and is visible to any
  later process that reopens the same named file (survives process crash).
* Durability across power loss requires flush(): fdatasync of this file. The
  device must honour cache flushes. Nothing here protects against bitrot.
* Exclusive ownership: an fcntl lock (flock) is held for the object's lifetime;
  a second cooperating opener fails immediately instead of sharing the file.
* Temporary mode uses O_TMPFILE: the data has no name and disappears on close
  or process death. It is therefore not restartable; named mode is.
"""

import ctypes
import errno
import fcntl
import mmap
import os
from pathlib import Path
import stat
import threading

from .core import BackendError

_ALIGN = 4096
_MAX_SIZE = 1 << 46  # 64 TiB; a sanity bound, far above any experiment.
_RAM_FILESYSTEMS = {0x01021994: "tmpfs", 0x858458F6: "ramfs", 0x01021997: "hugetlbfs"}


class _StatFs(ctypes.Structure):
    _fields_ = [("f_type", ctypes.c_long)] + [("_pad", ctypes.c_byte * 112)]


def _reject_ram_filesystem(fd):
    """A local copy in RAM would silently defeat the purpose of swap.

    statvfs cannot identify the filesystem type, so use fstatfs(2) directly.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    buffer = _StatFs()
    if libc.fstatfs(fd, ctypes.byref(buffer)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "fstatfs failed: " + os.strerror(error))
    name = _RAM_FILESYSTEMS.get(buffer.f_type & 0xFFFFFFFF)
    if name is not None:
        raise ValueError(f"Local backing must be on disk, not {name}")


class DirectFileStorage:
    """Storage protocol implementation over one O_DIRECT file descriptor."""

    def __init__(self, fd, size, max_request_bytes, *, temporary):
        # Construction goes through temporary()/open_named(); both pass an
        # already locked, validated descriptor that this object now owns.
        self.size = size
        self.temporary = temporary
        self._fd = fd
        self._maximum = max_request_bytes
        self._lock = threading.Lock()
        self._closed = False
        self._failed = False
        # Room for the largest request plus one alignment block on each side,
        # used for read-modify-write of unaligned edges. Anonymous mmap memory
        # is page aligned, as O_DIRECT requires on Linux.
        self._buffer_size = max_request_bytes + 2 * _ALIGN
        try:
            self._buffer = mmap.mmap(-1, self._buffer_size,
                                     flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        except BaseException:
            os.close(fd)
            raise
        self._view = memoryview(self._buffer)

    @staticmethod
    def _check_arguments(size, max_request_bytes):
        if type(size) is not int or not 0 < size <= _MAX_SIZE or size % _ALIGN:
            raise ValueError("Size must be a positive multiple of 4096")
        if (type(max_request_bytes) is not int or max_request_bytes <= 0
                or max_request_bytes % _ALIGN or max_request_bytes > 32 * 1024 * 1024):
            raise ValueError("Request maximum must be a 4096-aligned value up to 32 MiB")

    @staticmethod
    def _lock_descriptor(fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackendError("Local backing is already owned by another process") from None

    @classmethod
    def temporary(cls, directory, size, *, max_request_bytes=1048576):
        """Anonymous O_TMPFILE backing in a disk-backed directory."""
        cls._check_arguments(size, max_request_bytes)
        if not hasattr(os, "O_DIRECT") or not hasattr(os, "O_TMPFILE"):
            raise OSError(errno.ENOTSUP, "O_DIRECT and O_TMPFILE require Linux")
        flags = os.O_RDWR | os.O_DIRECT | os.O_TMPFILE | os.O_CLOEXEC
        fd = os.open(Path(directory), flags, 0o600)
        try:
            _reject_ram_filesystem(fd)
            cls._lock_descriptor(fd)
            # Reserve every block now: running out of space during swap-out
            # would turn a capacity problem into data loss.
            os.posix_fallocate(fd, 0, size)
        except BaseException:
            os.close(fd)
            raise
        return cls(fd, size, max_request_bytes, temporary=True)

    @classmethod
    def open_named(cls, path, size, *, create, max_request_bytes=1048576):
        """Named, restartable backing. create=True never replaces a file."""
        cls._check_arguments(size, max_request_bytes)
        if type(create) is not bool:
            raise ValueError("Create must be boolean")
        if not hasattr(os, "O_DIRECT"):
            raise OSError(errno.ENOTSUP, "O_DIRECT requires Linux")
        flags = os.O_RDWR | os.O_DIRECT | os.O_CLOEXEC | os.O_NOFOLLOW
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        path = Path(path).absolute()
        fd = os.open(path, flags, 0o600)
        try:
            _reject_ram_filesystem(fd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid() or info.st_nlink != 1):
                raise ValueError("Backing must be a private, owned, single-link regular file")
            cls._lock_descriptor(fd)
            if create:
                os.posix_fallocate(fd, 0, size)
            elif info.st_size != size:
                # Never reinterpret a backing of another size; offsets would
                # silently map to different data.
                raise ValueError("Existing backing has a different size")
        except BaseException:
            os.close(fd)
            if create:
                # Remove only what this call created; a retry must not hit
                # FileExistsError because of a half-initialised file.
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise
        return cls(fd, size, max_request_bytes, temporary=False)

    def _validate(self, offset, count):
        if type(offset) is not int or type(count) is not int:
            raise ValueError("Offset and count must be integers")
        if offset < 0 or count <= 0 or offset + count > self.size:
            raise ValueError("Request outside local backing")
        if count > self._maximum:
            raise ValueError("Request exceeds configured maximum")
        if self._closed or self._failed:
            raise BackendError("Local backing is closed or failed")

    @staticmethod
    def _window(offset, count):
        start = offset - offset % _ALIGN
        end = -(-(offset + count) // _ALIGN) * _ALIGN
        return start, end

    def _transfer(self, function, file_offset, length, buffer_offset=0):
        # O_DIRECT within the file size completes fully on Linux filesystems,
        # but loop anyway so a short transfer can never be mistaken for data.
        done = 0
        while done < length:
            # Release each sub-view deterministically, also on exceptions:
            # a traceback holding it would prevent closing the mmap buffer.
            with self._view[buffer_offset + done:buffer_offset + length] as window:
                moved = function(self._fd, [window], file_offset + done)
            if moved <= 0:
                raise OSError(errno.EIO, "Short direct transfer")
            done += moved

    def _fail(self):
        # Any local I/O error makes the backing untrustworthy: a failed write
        # may have changed part of the range. Refuse all later I/O.
        self._failed = True

    def read(self, offset, count):
        with self._lock:
            self._validate(offset, count)
            start, end = self._window(offset, count)
            try:
                self._transfer(os.preadv, start, end - start)
            except BaseException:
                self._fail()
                raise
            relative = offset - start
            with self._view[relative:relative + count] as result:
                return bytes(result)

    def write(self, offset, data):
        if not isinstance(data, bytes):
            raise ValueError("Writes require immutable bytes")
        with self._lock:
            count = len(data)
            self._validate(offset, count)
            start, end = self._window(offset, count)
            try:
                # Unaligned edges need the existing surrounding bytes first.
                # This read-modify-write is not atomic against power loss.
                head = start != offset
                if head:
                    self._transfer(os.preadv, start, _ALIGN)
                tail = end - _ALIGN
                if (offset + count) % _ALIGN and not (head and tail == start):
                    self._transfer(os.preadv, tail, _ALIGN, buffer_offset=tail - start)
                relative = offset - start
                self._view[relative:relative + count] = data
                self._transfer(os.pwritev, start, end - start)
            except BaseException:
                self._fail()
                raise

    def flush(self):
        """Ask the OS and device to make completed writes durable."""
        with self._lock:
            if self._closed or self._failed:
                raise BackendError("Local backing is closed or failed")
            try:
                os.fdatasync(self._fd)
            except BaseException:
                # After a failed fdatasync, Linux may have dropped dirty state;
                # a retry could falsely report success, so never allow one.
                self._fail()
                raise

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._view.release()
                try:
                    self._buffer.close()
                except BufferError:
                    # A caller-held view still exists; the mapping is freed
                    # by garbage collection. The data is not reachable here.
                    pass
            finally:
                # Closing the descriptor releases the ownership lock; for
                # O_TMPFILE it also releases the anonymous data.
                os.close(self._fd)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
