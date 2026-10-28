"""Private, temporary local backing opened with O_DIRECT.

Buffered file I/O lets the OS page cache hold a second copy of every block in
host RAM, outside any memory-limited cgroup. For a swap experiment that would
silently turn "local disk" into "host RAM" for both the local-swap baseline
and Memory Bridge's local copy. O_DIRECT bypasses the page cache so that
local reads and writes cost real block I/O in every compared configuration.

Like TemporaryFileStorage this is process-lifetime storage, not a crash
recovery format. Unlinking is not secure erasure.
"""

import mmap
import os
import tempfile
import threading
import time

_ALIGN = 4096


class DirectFileStorage:
    """Own an anonymous O_DIRECT file and one page-aligned transfer buffer.

    Caller offsets and lengths may be unaligned; those requests are served
    with an aligned read-modify-write envelope. The transfer buffer is
    allocated once so that no I/O allocates a new large buffer.
    """

    def __init__(self, size, directory=None, *, max_request_bytes=1024 * 1024,
                 emulated_latency_seconds=0.0):
        if type(size) is not int or size <= 0 or size % _ALIGN:
            raise ValueError("Size must be a positive multiple of 4096")
        if (type(emulated_latency_seconds) not in (int, float)
                or not 0 <= emulated_latency_seconds <= 1):
            raise ValueError("Emulated latency must be 0..1 seconds")
        # Optional sensitivity knob: sleep before every I/O to emulate a slow
        # disk (HDD-class seek). It is reported in snapshots and never on by
        # default; it exists only to find the local-latency crossover point.
        self._emulated_latency = float(emulated_latency_seconds)
        if type(max_request_bytes) is not int or max_request_bytes <= 0 or max_request_bytes % _ALIGN:
            raise ValueError("Request maximum must be a positive multiple of 4096")
        self.size = size
        self._maximum = max_request_bytes
        self._lock = threading.Lock()
        # Envelope: one extra page on each side covers unaligned requests.
        self._buffer = mmap.mmap(-1, max_request_bytes + 2 * _ALIGN)
        self._fd = None
        fd, path = tempfile.mkstemp(dir=directory)
        try:
            # Reopen with O_DIRECT, then drop the name: the backing is private
            # to this process for its lifetime, like TemporaryFileStorage.
            self._fd = os.open(path, os.O_RDWR | os.O_DIRECT | os.O_CLOEXEC)
            os.ftruncate(self._fd, size)
            if hasattr(os, "posix_fallocate"):
                os.posix_fallocate(self._fd, 0, size)
        except BaseException:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self._buffer.close()
            raise
        finally:
            os.close(fd)
            os.unlink(path)

    def _validate(self, offset, count):
        if self._fd is None:
            raise RuntimeError("Local backing is closed")
        if type(offset) is not int or type(count) is not int:
            raise ValueError("Offset and count must be integers")
        if offset < 0 or count <= 0 or offset + count > self.size:
            raise ValueError("Request outside local backing")
        if count > self._maximum:
            raise ValueError("Request exceeds configured maximum")

    def _envelope(self, offset, count):
        start = offset - offset % _ALIGN
        end = -(-(offset + count) // _ALIGN) * _ALIGN
        return start, end

    def _read_aligned(self, start, length):
        if self._emulated_latency:
            time.sleep(self._emulated_latency)
        view = memoryview(self._buffer)[:length]
        done = 0
        while done < length:
            got = os.preadv(self._fd, [view[done:]], start + done)
            if got <= 0:
                raise OSError("Short local backing read")
            done += got

    def _read_edge(self, start, position):
        """Read one page into the buffer at a non-zero buffer position."""
        if self._emulated_latency:
            time.sleep(self._emulated_latency)
        view = memoryview(self._buffer)[position:position + _ALIGN]
        done = 0
        while done < _ALIGN:
            got = os.preadv(self._fd, [view[done:]], start + done)
            if got <= 0:
                raise OSError("Short local backing read")
            done += got

    def _write_aligned(self, start, length):
        if self._emulated_latency:
            time.sleep(self._emulated_latency)
        view = memoryview(self._buffer)[:length]
        done = 0
        while done < length:
            written = os.pwritev(self._fd, [view[done:]], start + done)
            if written <= 0:
                raise OSError("Short local backing write")
            done += written

    def read(self, offset, count):
        with self._lock:
            self._validate(offset, count)
            start, end = self._envelope(offset, count)
            self._read_aligned(start, end - start)
            relative = offset - start
            return self._buffer[relative:relative + count]  # mmap slicing yields bytes

    def write(self, offset, data):
        if not isinstance(data, bytes):
            raise ValueError("Writes require immutable bytes")
        with self._lock:
            self._validate(offset, len(data))
            start, end = self._envelope(offset, len(data))
            relative = offset - start
            aligned = relative == 0 and len(data) == end - start
            if not aligned:
                # Read-modify-write only the partial edge pages (never the whole
                # envelope); a failed read raises before any byte changes on disk.
                head_partial = relative != 0
                tail_partial = (offset + len(data)) % _ALIGN != 0
                if end - start == _ALIGN:
                    self._read_aligned(start, _ALIGN)  # one page, partial either way
                else:
                    if head_partial:
                        self._read_aligned(start, _ALIGN)
                    if tail_partial:
                        self._read_edge(end - _ALIGN, end - start - _ALIGN)
            self._buffer[relative:relative + len(data)] = data
            self._write_aligned(start, end - start)

    def snapshot(self):
        return {"backing": "direct", "capacity_bytes": self.size,
                "emulated_latency_seconds": self._emulated_latency}

    def flush(self):
        """O_DIRECT data has reached the device; sync metadata as well."""
        with self._lock:
            if self._fd is None:
                raise RuntimeError("Local backing is closed")
            os.fdatasync(self._fd)

    def close(self):
        with self._lock:
            if self._fd is not None:
                fd, self._fd = self._fd, None
                try:
                    os.close(fd)
                finally:
                    self._buffer.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
