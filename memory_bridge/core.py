"""Transport-independent allocation engine with explicit resource ownership.

All mutations are serialized. This deliberately favors understandable failure
semantics over parallel throughput. Tokens are capabilities: transports must
authenticate peers before distributing them and must never log them.
"""

from dataclasses import dataclass
import secrets
import threading
from typing import Protocol


class BridgeError(RuntimeError):
    """Base class for failures a transport should translate to a bounded error."""


class CapacityError(BridgeError):
    """The donor or borrower quota would be exceeded."""


class SessionError(BridgeError):
    """The capability is unknown, released or invalidated by shutdown."""


class BackendError(BridgeError):
    """A backend failed; the affected session has been invalidated."""


class Storage(Protocol):
    """Minimal interface implemented by locked RAM and test backends."""

    size: int

    def read(self, offset: int, count: int) -> bytes: ...
    def write(self, offset: int, data: bytes) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class Limits:
    """Data budgets, excluding interpreter, TLS and request-buffer overhead."""

    total_bytes: int = 4 * 1024 * 1024
    per_owner_bytes: int = 4 * 1024 * 1024
    max_sessions: int = 8
    max_request_bytes: int = 1024 * 1024

    def __post_init__(self):
        for name in ("total_bytes", "per_owner_bytes", "max_sessions", "max_request_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.per_owner_bytes > self.total_bytes:
            raise ValueError("per_owner_bytes exceeds total_bytes")
        if self.total_bytes % 4096 or self.per_owner_bytes % 4096:
            raise ValueError("Memory budgets must be page aligned")


@dataclass(frozen=True)
class _Session:
    owner: str
    storage: Storage
    size: int


class MemoryPool:
    """Own allocations until explicit release, failure or shutdown.

    There is no automatic expiry: reclaiming pages underneath active swap is
    unsafe. A future lease protocol must drain/replicate before releasing RAM.
    A failed close quarantines capacity instead of claiming it was reclaimed.
    """

    def __init__(self, limits: Limits, factory):
        self.limits = limits
        self._factory = factory
        self._sessions = {}
        self._generation = 0
        self._quarantine = []
        self._lock = threading.RLock()
        self._closed = False
        self._draining = False
        self._reads = self._writes = self._errors = 0

    def _allocated(self):
        return sum(s.size for s in self._sessions.values()) + sum(
            s.size for s in self._quarantine)

    def allocate(self, owner: str, size: int) -> str:
        if not isinstance(owner, str) or not owner or len(owner) > 128:
            raise ValueError("Owner must be a nonempty identifier of at most 128 characters")
        if type(size) is not int or size <= 0 or size % 4096:
            raise ValueError("Allocation must be a positive multiple of 4096")
        with self._lock:
            if self._closed:
                raise SessionError("Pool is closed")
            if self._draining:
                raise SessionError("Pool is draining; new allocations are disabled")
            all_sessions = list(self._sessions.values()) + self._quarantine
            owned = sum(s.size for s in all_sessions if s.owner == owner)
            if (self._allocated() + size > self.limits.total_bytes
                    or owned + size > self.limits.per_owner_bytes
                    or len(all_sessions) >= self.limits.max_sessions):
                raise CapacityError("Allocation exceeds configured quota")
            # Allocation is serialized and only committed after successful
            # construction. Factories must clean up if construction fails.
            # Generate before allocating so entropy-source failures cannot
            # strand locked memory. A monotonically increasing prefix prevents
            # retired capabilities from becoming valid again even if entropy
            # repeats, without retaining unbounded capability tombstones.
            # The random component retains 256 bits of cross-pool entropy.
            secret = secrets.token_urlsafe(32)
            self._generation += 1
            token = f"{self._generation:x}.{secret}"
            storage = self._factory(size)
            self._sessions[token] = _Session(owner, storage, size)
            return token

    def _get(self, token):
        if not isinstance(token, str) or token not in self._sessions:
            raise SessionError("Unknown or released session")
        return self._sessions[token]

    def _validate_io(self, session, offset, count):
        if type(offset) is not int or type(count) is not int:
            raise ValueError("Offset and length must be integers")
        if offset < 0 or count <= 0 or offset + count > session.size:
            raise ValueError("I/O is outside allocation")
        if count > self.limits.max_request_bytes:
            raise ValueError("Request exceeds configured maximum")

    def _retire(self, token):
        session = self._sessions.pop(token)
        try:
            session.storage.close()
        except BaseException as error:
            # Keep a strong reference and account for resources that could not
            # be released. Do not leak backend exception text into telemetry.
            self._quarantine.append(session)
            self._errors += 1
            if not isinstance(error, Exception):
                raise
            raise BackendError("Cleanup failed; capacity quarantined") from None

    def _failed_io(self, token, error, message):
        self._errors += 1
        try:
            self._retire(token)
        except BaseException:
            # Preserve the original cancellation even if cleanup also fails.
            # _retire has already retained uncertain resources in quarantine.
            if isinstance(error, Exception):
                raise
        if not isinstance(error, Exception):
            raise error
        raise BackendError(message) from None

    def read(self, token: str, offset: int, count: int) -> bytes:
        with self._lock:
            session = self._get(token)
            self._validate_io(session, offset, count)
            try:
                data = session.storage.read(offset, count)
                if not isinstance(data, bytes) or len(data) != count:
                    raise OSError("Short or malformed read")
            except BaseException as error:
                self._failed_io(token, error, "Read failed; session invalidated")
            self._reads += count
            return data

    def write(self, token: str, offset: int, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise ValueError("Writes require immutable bytes")
        with self._lock:
            session = self._get(token)
            self._validate_io(session, offset, len(data))
            try:
                session.storage.write(offset, data)
            except BaseException as error:
                # A failed write may have partially changed memory. Retrying
                # silently would hide an ambiguous write, so revoke the token.
                self._failed_io(token, error, "Write failed; session invalidated")
            self._writes += len(data)

    def release(self, token: str) -> None:
        with self._lock:
            self._get(token)
            self._retire(token)

    def snapshot(self) -> dict:
        """Aggregate telemetry only: no contents, keys, owners or tokens."""
        with self._lock:
            return {"closed": self._closed, "draining": self._draining,
                    "sessions": len(self._sessions),
                    "allocated_bytes": self._allocated(),
                    "capacity_bytes": self.limits.total_bytes,
                    "quarantined_sessions": len(self._quarantine),
                    "read_bytes": self._reads, "written_bytes": self._writes,
                    "errors": self._errors}

    def drain(self) -> None:
        """Stop admission without deleting live data or waiting indefinitely.

        Existing capabilities continue to work until explicitly released.
        Draining is irreversible. The caller observes snapshot() and chooses
        when to close; this method never silently discards an allocation.
        """
        with self._lock:
            if self._closed:
                raise SessionError("Pool is closed")
            self._draining = True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            # Attempt every cleanup even when one backend fails. Quarantined
            # allocations are retained until process exit; restart is required.
            failed = False
            cancellation = None
            for token in list(self._sessions):
                try:
                    self._retire(token)
                except BackendError:
                    failed = True
                except BaseException as error:
                    if cancellation is None:
                        cancellation = error
            if cancellation is not None:
                raise cancellation
            if failed or self._quarantine:
                raise BackendError("Pool closed with quarantined capacity")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
