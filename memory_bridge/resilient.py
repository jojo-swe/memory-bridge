"""Local authoritative storage with an optional best-effort remote block cache.

The remote backend must enforce its own deadlines; RemoteNBDStorage does so
per transport operation. This wrapper provides neither crash durability nor
integrity against a malicious remote donor. Backings must be exclusively owned by this wrapper after creation.
"""

import threading

from .core import BackendError, Storage


class ResilientStorage:
    """Keep local writes authoritative and permanently abandon a failed cache.

    Cache validity costs one bit per block. transfer_bytes batches contiguous
    same-source reads and complete block mirrors; None retains block transfers.
    Explicit transfer sizes must fit the public request maximum. Historically
    a smaller public maximum still permits internal one-block mirror I/O.
    """

    def __init__(self, local: Storage, remote: Storage | None = None, *,
                 block_size: int = 4096, max_request_bytes: int = 1024 * 1024,
                 transfer_bytes: int | None = None):
        if type(block_size) is not int or block_size <= 0:
            raise ValueError("Block size must be a positive integer")
        if type(max_request_bytes) is not int or max_request_bytes <= 0:
            raise ValueError("Request maximum must be a positive integer")
        if transfer_bytes is not None and (
                type(transfer_bytes) is not int or transfer_bytes <= 0
                or transfer_bytes % block_size or transfer_bytes > max_request_bytes):
            raise ValueError("Transfer size must be block aligned and within request maximum")
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
        # The default preserves historical one-block transfers, including
        # callers whose public request maximum is smaller than a block.
        self._transfer_bytes = block_size if transfer_bytes is None else transfer_bytes
        self._valid = bytearray((self._size // block_size + 7) // 8) if remote is not None else None
        self._valid_count = 0
        self._remote_enabled = remote is not None
        self._closed = False
        self._failed = False
        self._close_failed = False
        self._remote_errors = 0
        self._lock = threading.RLock()

    def _is_valid(self, block):
        return self._valid is not None and bool(self._valid[block >> 3] & (1 << (block & 7)))

    def _mark(self, block, valid):
        if self._valid is None:
            return
        byte, mask = block >> 3, 1 << (block & 7)
        previous = bool(self._valid[byte] & mask)
        if previous != valid:
            self._valid[byte] ^= mask
            self._valid_count += 1 if valid else -1

    def _clear_valid(self):
        # Release the bitmap rather than constructing an allocation-sized
        # zero buffer. The cache never reconnects after terminal failure.
        self._valid = None
        self._valid_count = 0

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
        except BaseException as error:
            self._failed = True
            self._remote_enabled = False
            self._clear_valid()
            if not isinstance(error, Exception):
                raise
            raise BackendError("Local read failed; storage invalidated") from None

    def _disable_remote(self):
        self._remote_enabled = False
        self._clear_valid()
        self._remote_errors += 1

    def read(self, offset: int, count: int) -> bytes:
        with self._lock:
            self._validate(offset, count)
            result = []
            end = offset + count
            while offset < end:
                block = offset // self._block_size
                use_remote = self._remote_enabled and self._is_valid(block)
                limit = min(end, offset + self._transfer_bytes, offset + self._max_request_bytes)
                stop = min(limit, (block + 1) * self._block_size)
                # Coalesce adjacent blocks only while their backing agrees.
                while stop < limit:
                    next_remote = self._remote_enabled and self._is_valid(stop // self._block_size)
                    if next_remote != use_remote:
                        break
                    stop = min(limit, (stop // self._block_size + 1) * self._block_size)
                length = stop - offset
                if use_remote:
                    try:
                        data = self._read(self._remote, offset, length)
                    except BaseException as error:
                        self._disable_remote()
                        if not isinstance(error, Exception):
                            raise
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
            for block in blocks:
                self._mark(block, False)
            try:
                self._local.write(offset, data)
            except BaseException as error:
                self._failed = True
                self._remote_enabled = False
                self._clear_valid()
                if not isinstance(error, Exception):
                    raise
                raise BackendError("Local write failed; storage invalidated") from None
            if self._remote_enabled:
                start = blocks.start * self._block_size
                end = blocks.stop * self._block_size
                while start < end:
                    length = min(self._transfer_bytes, end - start)
                    # Complete aligned caller bytes already are the exact
                    # authoritative block content; partial edges need readback.
                    if start >= offset and start + length <= offset + len(data):
                        relative = start - offset
                        full = data[relative:relative + length]
                    else:
                        full = self._local_read(start, length)
                    try:
                        self._remote.write(start, full)
                    except BaseException as error:
                        self._disable_remote()
                        if not isinstance(error, Exception):
                            raise
                        break
                    # A batch becomes eligible only after its remote ACK.
                    for block in range(start // self._block_size,
                                       (start + length) // self._block_size):
                        self._mark(block, True)
                    start += length

    def snapshot(self) -> dict:
        """Aggregate state only; never expose contents or backing identities."""
        with self._lock:
            return {"closed": self._closed, "failed": self._failed,
                    "remote_enabled": self._remote_enabled,
                    "cached_blocks": self._valid_count,
                    "valid_bitmap_bytes": len(self._valid) if self._valid is not None else 0,
                    "capacity_bytes": self._size,
                    "remote_errors": self._remote_errors}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._remote_enabled = False
                self._clear_valid()
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
