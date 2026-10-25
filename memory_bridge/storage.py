"""Bounded Linux RAM backend. Never silently falls back to swap."""

import ctypes
import mmap
import threading

class LockedMemory:
    """Allocate and pin the full budget before accepting a borrower.

    mlock is mandatory: silently allowing the donor to swap would invalidate
    the experiment. The OS locked-memory limit must cover the requested size.
    The budget covers data bytes, not Python/nbdkit/TLS process overhead.
    """

    def __init__(self, size):
        if type(size) is not int or size <= 0 or size % 4096:
            raise ValueError("Size must be a positive multiple of 4096 bytes")
        self.size = size
        self.data = mmap.mmap(-1, size)
        anchor = ctypes.c_char.from_buffer(self.data)
        self.address = ctypes.addressof(anchor)
        del anchor
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.mlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self.libc.mlock.restype = ctypes.c_int
        self.libc.munlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        if self.libc.mlock(self.address, size) != 0:
            error = ctypes.get_errno()
            self.data.close()
            raise OSError(error, "Cannot lock RAM; reduce --size-mib or raise memlock limit")
        self.closed = False
        self.mutex = threading.Lock()

    def _bounds(self, offset, count):
        if self.closed:
            raise RuntimeError("Memory is closed")
        if type(offset) is not int or type(count) is not int:
            raise ValueError("Offset and count must be integers")
        if offset < 0 or count < 0 or offset + count > self.size:
            raise ValueError("Request is outside the memory budget")

    def read(self, offset, count):
        with self.mutex:
            self._bounds(offset, count)
            return self.data[offset:offset + count]

    def write(self, offset, data):
        with self.mutex:
            self._bounds(offset, len(data))
            self.data[offset:offset + len(data)] = data

    def close(self):
        with self.mutex:
            if not self.closed:
                # Best-effort clearing of the mapped data before releasing it.
                # This cannot erase transient copies in Python or TLS buffers.
                ctypes.memset(self.address, 0, self.size)
                self.libc.munlock(self.address, self.size)
                self.data.close()
                self.closed = True

