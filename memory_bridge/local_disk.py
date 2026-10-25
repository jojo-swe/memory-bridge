"""Private, temporary local backing for donor-loss experiments.

This is process-lifetime storage, not a crash recovery format. File data may
remain in the OS page cache. Use a disk-backed directory, not tmpfs, when
measuring RAM usage. Unlinking is not secure erasure.
"""

import os
import tempfile
import threading


class TemporaryFileStorage:
    """Own an anonymous file; reserve disk space where posix_fallocate exists."""

    def __init__(self, size, directory=None):
        if type(size) is not int or size <= 0 or size % 4096:
            raise ValueError("Size must be a positive multiple of 4096")
        self.size = size
        self._lock = threading.Lock()
        self._file = tempfile.TemporaryFile(dir=directory, buffering=0)
        try:
            self._file.truncate(size)
            if hasattr(os, "posix_fallocate"):
                # Fail early on unsupported allocation or insufficient space;
                # never silently promise capacity that was not reserved.
                os.posix_fallocate(self._file.fileno(), 0, size)
        except BaseException:
            self._file.close()
            raise

    def _validate(self, offset, count):
        if self._file.closed:
            raise RuntimeError("Local backing is closed")
        if type(offset) is not int or type(count) is not int:
            raise ValueError("Offset and count must be integers")
        if offset < 0 or count <= 0 or offset + count > self.size:
            raise ValueError("Request outside local backing")

    def read(self, offset, count):
        with self._lock:
            self._validate(offset, count)
            self._file.seek(offset)
            data = self._file.read(count)
            if len(data) != count:
                raise OSError("Short local backing read")
            return data

    def write(self, offset, data):
        if not isinstance(data, bytes):
            raise ValueError("Writes require immutable bytes")
        with self._lock:
            self._validate(offset, len(data))
            self._file.seek(offset)
            remaining = memoryview(data)
            while remaining:
                written = self._file.write(remaining)
                if written is None or written <= 0:
                    raise OSError("Short local backing write")
                remaining = remaining[written:]

    def close(self):
        with self._lock:
            self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
