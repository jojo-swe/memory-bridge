"""Local authoritative storage with an optional best-effort remote block cache.

Remote calls have no timeout: a hung donor can still block an operation. This
wrapper provides neither crash durability nor integrity against a malicious
remote donor. Backings must be exclusively owned by this wrapper after creation.
"""

import threading

from .core import BackendError, Storage


class ResilientStorage:
    """Keep local writes authoritative and permanently abandon a failed cache."""

    def __init__(self, local: Storage, remote: Storage | None = None, *,
                 block_size: int = 4096, max_request_bytes: int = 1024 * 1024):
        if type(block_size) is not int or block_size <= 0:
            raise ValueError("Block size must be a positive integer")
        if type(max_request_bytes) is not int or max_request_bytes <= 0:
            raise ValueError("Request maximum must be a positive integer")
        if type(local.size) is not int or local.size <= 0 or local.size % block_size:
            raise ValueError("Storage size must be positive and block aligned")
        if remote is local:
            raise ValueError("Local and remote storage must be distinct")
        if remote is not None and (type(remote.size) is not int or remote.size != local.size):
            raise ValueError("Storage sizes must match")
        self.size = local.size
        self._size = local.size
        self._local = local
        self._remote = remote
        self._block_size = block_size
        self._max_request_bytes = max_request_bytes
        self._valid = set()
        self._remote_enabled = remote is not None
        self._closed = False
        self._failed = False
        self._close_failed = False
        self._remote_errors = 0
        self._lock = threading.RLock()

    def _validate(self, offset, count):
        if type(offset) is not int or type(count) is not int:
            raise ValueError("Offset and length must be integers")
        if offset < 0 or count <= 0 or offset + count > self._size:
            raise ValueError("I/O is outside allocation")
        if count > self._max_request_bytes:
            raise ValueError("Request exceeds configured maximum")
        if self._closed or self._failed:
            raise BackendError("Storage is closed or invalidated")

    @staticmethod
    def _read(storage, offset, count):
        data = storage.read(offset, count)
        if not isinstance(data, bytes) or len(data) != count:
            raise OSError("Short or malformed read")
        return data

    def _local_read(self, offset, count):
        try:
            return self._read(self._local, offset, count)
        except Exception:
            self._failed = True
            self._valid.clear()
            raise BackendError("Local read failed; storage invalidated") from None

    def _disable_remote(self):
        self._remote_enabled = False
        self._valid.clear()
        self._remote_errors += 1

    def read(self, offset: int, count: int) -> bytes:
        with self._lock:
            self._validate(offset, count)
            result = []
            end = offset + count
            while offset < end:
                block = offset // self._block_size
                length = min(end - offset, self._block_size - offset % self._block_size)
                if self._remote_enabled and block in self._valid:
                    try:
                        data = self._read(self._remote, offset, length)
                    except Exception:
                        self._disable_remote()
                        data = self._local_read(offset, length)
                else:
                    data = self._local_read(offset, length)
                result.append(data)
                offset += length
            return b"".join(result)

    def write(self, offset: int, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise ValueError("Writes require immutable bytes")
        with self._lock:
            self._validate(offset, len(data))
            blocks = range(offset // self._block_size,
                           (offset + len(data) - 1) // self._block_size + 1)
            # Revoke cache validity before an authoritative write that may fail
            # after modifying only some bytes.
            self._valid.difference_update(blocks)
            try:
                self._local.write(offset, data)
            except Exception:
                self._failed = True
                self._valid.clear()
                raise BackendError("Local write failed; storage invalidated") from None
            if self._remote_enabled:
                for block in blocks:
                    start = block * self._block_size
                    full = self._local_read(start, self._block_size)
                    try:
                        self._remote.write(start, full)
                    except Exception:
                        self._disable_remote()
                        break
                    self._valid.add(block)

    def snapshot(self) -> dict:
        """Aggregate state only; never expose contents or backing identities."""
        with self._lock:
            return {"closed": self._closed, "failed": self._failed,
                    "remote_enabled": self._remote_enabled,
                    "cached_blocks": len(self._valid),
                    "capacity_bytes": self._size,
                    "remote_errors": self._remote_errors}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._remote_enabled = False
                self._valid.clear()
                for storage in (self._local, self._remote):
                    if storage is not None:
                        try:
                            storage.close()
                        except Exception:
                            self._close_failed = True
            if self._close_failed:
                raise BackendError("Storage cleanup failed") from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
