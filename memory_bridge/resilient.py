"""Local authoritative storage with an optional best-effort remote block cache.

The remote backend must enforce its own deadlines; RemoteNBDStorage does so
per transport operation. Local SHA256 digests verify remote block contents
before exposure. This wrapper provides no crash durability; the local backend
and process must be trusted. Backings must be exclusively owned after creation.
"""

import threading
import hashlib

from .core import BackendError, Storage


class ResilientStorage:
    """Keep local writes authoritative and permanently abandon a failed cache.

    Cache validity costs one bit and integrity costs 32 bytes per block.
    transfer_bytes batches contiguous
    same-source reads and complete block mirrors; None retains block transfers.
    Explicit transfer sizes must fit the public request maximum. Historically
    a smaller public maximum still permits internal one-block mirror I/O.
    """

    def __init__(self, local: Storage, remote: Storage | None = None, *,
                 block_size: int = 4096, max_request_bytes: int = 1024 * 1024,
                 transfer_bytes: int | None = None, write_behind_bytes: int = 0):
        if type(block_size) is not int or block_size <= 0:
            raise ValueError("Block size must be a positive integer")
        if type(write_behind_bytes) is not int or write_behind_bytes < 0:
            raise ValueError("Write-behind budget must be a non-negative integer")
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
        self._digests = bytearray(self._size // block_size * 32) if remote is not None else None
        self._valid_count = 0
        self._remote_enabled = remote is not None
        self._closed = False
        self._failed = False
        self._close_failed = False
        self._remote_errors = 0
        self._lock = threading.RLock()
        # Write-behind mirroring (optional): the caller's write returns after
        # the authoritative local write; a mirror thread copies whole blocks to
        # the remote cache afterwards. A block becomes readable from the cache
        # only after its remote ACK, exactly as in the synchronous path, so no
        # unverified or stale remote content can ever be exposed.
        self._write_behind = write_behind_bytes if remote is not None else 0
        self._pending = {}       # block index -> full block bytes awaiting mirror
        self._pending_bytes = 0
        self._inflight = {}      # block index -> bytes being mirrored, None if superseded
        self._mirror_busy = False  # a mirror write holds the remote; reads go local meanwhile
        self._mirror_dropped = 0  # blocks left local-only because the budget was full
        self._stopping = False
        self._condition = threading.Condition(self._lock)
        self._mirror_thread = None
        if self._write_behind:
            self._mirror_thread = threading.Thread(target=self._mirror_loop,
                                                   name="memory-bridge-mirror", daemon=True)
            self._mirror_thread.start()

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
        self._digests = None
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
                # While the mirror thread is inside a remote write, the remote
                # lock is taken for up to one deadline; serving such reads from
                # the local copy keeps cached reads from stalling behind it.
                use_remote = (self._remote_enabled and not self._mirror_busy
                              and self._is_valid(block))
                # Remote reads include whole blocks even for a partial caller
                # range, so corruption outside that range is still detected.
                start = block * self._block_size if use_remote else offset
                limit = min(end, start + self._transfer_bytes)
                if not use_remote:
                    limit = min(limit, offset + self._max_request_bytes)
                stop = min(limit, (block + 1) * self._block_size)
                # Coalesce adjacent blocks only while their backing agrees.
                while stop < limit:
                    next_remote = (self._remote_enabled and not self._mirror_busy
                               and self._is_valid(stop // self._block_size))
                    if next_remote != use_remote:
                        break
                    stop = min(limit, (stop // self._block_size + 1) * self._block_size)
                length = stop - offset
                if use_remote:
                    try:
                        remote_end = ((stop + self._block_size - 1) // self._block_size) * self._block_size
                        full = self._read(self._remote, start, remote_end - start)
                        view = memoryview(full)
                        for index in range(start // self._block_size,
                                           remote_end // self._block_size):
                            relative = index * self._block_size - start
                            digest = hashlib.sha256(view[relative:relative + self._block_size]).digest()
                            if digest != self._digests[index * 32:(index + 1) * 32]:
                                raise OSError("Remote block integrity failure")
                        # Expose no part of a batch until every block passes.
                        data = full[offset - start:stop - start]
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
            if self._remote_enabled and self._write_behind:
                self._queue_mirror(blocks, offset, data)
            elif self._remote_enabled:
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
                        relative = block * self._block_size - start
                        digest = hashlib.sha256(memoryview(full)[relative:relative + self._block_size]).digest()
                        self._digests[block * 32:(block + 1) * 32] = digest
                        self._mark(block, True)
                    start += length

    def _queue_mirror(self, blocks, offset, data):
        """Record the authoritative content of every touched block for mirroring."""
        size = self._block_size
        for block in blocks:
            if block in self._inflight:
                # A mirror of older content is in progress; its ACK must not
                # mark the block valid any more.
                self._inflight[block] = None
            start = block * size
            if start >= offset and start + size <= offset + len(data):
                full = data[start - offset:start - offset + size]
            else:
                full = self._local_read(start, size)  # partial edge: read back
            previous = self._pending.pop(block, None)
            if previous is not None:
                self._pending_bytes -= size
            if self._pending_bytes + size > self._write_behind:
                # Budget exhausted: the block simply stays local-only. Never
                # block the caller (the kernel swap path) on the network.
                self._mirror_dropped += 1
                continue
            self._pending[block] = full
            self._pending_bytes += size
        self._condition.notify()

    def _take_batch(self):
        """Pop one contiguous run of pending blocks, bounded by transfer size."""
        first = min(self._pending)
        batch = []
        block = first
        while block in self._pending and len(batch) * self._block_size < self._transfer_bytes:
            content = self._pending.pop(block)
            self._pending_bytes -= self._block_size
            self._inflight[block] = content
            batch.append((block, content))
            block += 1
        return batch

    def _mirror_loop(self):
        while True:
            with self._condition:
                while not self._pending and not self._stopping and self._remote_enabled:
                    self._condition.wait()
                if self._stopping or not self._remote_enabled:
                    self._pending.clear()
                    self._pending_bytes = 0
                    return
                batch = self._take_batch()
                self._mirror_busy = True
            start = batch[0][0] * self._block_size
            payload = b"".join(content for _, content in batch)
            try:
                # Outside the storage lock: callers keep reading and writing
                # locally while this remote write waits on its own deadline.
                self._remote.write(start, payload)
            except Exception:
                with self._condition:
                    self._mirror_busy = False
                    self._disable_remote()
                    self._pending.clear()
                    self._pending_bytes = 0
                    self._inflight.clear()
                return
            with self._condition:
                self._mirror_busy = False
                for block, content in batch:
                    if self._inflight.pop(block, None) is content and self._digests is not None:
                        digest = hashlib.sha256(content).digest()
                        self._digests[block * 32:(block + 1) * 32] = digest
                        self._mark(block, True)

    def flush(self) -> None:
        """Forward a sync request to the authoritative local backing only.

        The remote cache never participates in any durability guarantee, so a
        flush must not wait for it. A local backend without flush() offers no
        sync at all; callers must not advertise more than the backend gives.
        """
        with self._lock:
            if self._closed or self._failed:
                raise BackendError("Storage is closed or invalidated")
            flush = getattr(self._local, "flush", None)
            if flush is None:
                return
            try:
                flush()
            except BaseException as error:
                # An uncertain sync is treated like a failed local write.
                self._failed = True
                self._remote_enabled = False
                self._clear_valid()
                if not isinstance(error, Exception):
                    raise
                raise BackendError("Local flush failed; storage invalidated") from None

    def snapshot(self) -> dict:
        """Aggregate state only; never expose contents or backing identities."""
        with self._lock:
            return {"closed": self._closed, "failed": self._failed,
                    "remote_enabled": self._remote_enabled,
                    "cached_blocks": self._valid_count,
                    "valid_bitmap_bytes": len(self._valid) if self._valid is not None else 0,
                    "integrity_bytes": len(self._digests) if self._digests is not None else 0,
                    "capacity_bytes": self._size,
                    "remote_errors": self._remote_errors,
                    "write_behind_bytes": self._write_behind,
                    "pending_mirror_blocks": len(self._pending),
                    "mirror_dropped_blocks": self._mirror_dropped}

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._remote_enabled = False
                self._clear_valid()
                self._stopping = True
                self._condition.notify_all()
                cancellation = None
                for storage in (self._local, self._remote):
                    if storage is not None:
                        try:
                            storage.close()
                        except BaseException as error:
                            self._close_failed = True
                            if not isinstance(error, Exception) and cancellation is None:
                                cancellation = error
                if cancellation is not None:
                    raise cancellation
            if self._close_failed:
                raise BackendError("Storage cleanup failed") from None
        # The remote is closed, so the next mirror write fails and the thread
        # exits; an in-flight write first runs to its own deadline. Join
        # outside the lock the thread needs for that exit.
        if self._mirror_thread is not None:
            self._mirror_thread.join(timeout=30)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
