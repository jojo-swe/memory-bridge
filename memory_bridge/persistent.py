"""Private, durable sparse page storage for Linux experiments.

The parent directory and path must remain trusted and stable: SQLite reopens
paths for its journal, while our descriptor lock protects cooperating openers.
Durability depends on the OS and disk honoring sync; this is not protection
against bitrot or hostile storage. Failed creation can leave a private artifact
for inspection. We never unlink a database or a possibly active journal.
"""

import fcntl
import os
from pathlib import Path
import sqlite3
import stat
import threading

from .core import BackendError

_PAGE = 4096
_APP_ID = 0x4D425231
_VERSION = 1
_META_SQL = 'CREATE TABLE meta (version INTEGER NOT NULL, size INTEGER NOT NULL)'


def _pages_sql(size):
    return ('CREATE TABLE pages ("index" INTEGER PRIMARY KEY CHECK ("index" >= 0 AND '
            f'"index" < {size // _PAGE}), data BLOB NOT NULL '
            'CHECK (typeof(data) = \'blob\' AND length(data) = 4096))')


class PersistentStorage:
    """Atomically commit each write, including writes spanning page boundaries.

    Uncertain cleanup retains the ownership lock until SQLite closes
    successfully. Call close() again to retry interrupted cleanup.
    """

    def __init__(self, path, size, *, create=False, max_request_bytes=1048576):
        if type(size) is not int or not 0 < size <= 64 * 1024**3 or size % _PAGE:
            raise ValueError("Size must be page aligned and at most 64 GiB")
        if type(create) is not bool:
            raise ValueError("Create must be boolean")
        if type(max_request_bytes) is not int or not 0 < max_request_bytes <= 32 * 1024**2:
            raise ValueError("Request maximum must be 1..33554432")
        path = Path(path).absolute()
        self.size = size
        self._size = size
        self._maximum = max_request_bytes
        self._lock = threading.RLock()
        self._connection = None
        self._fd = None
        self._closed = False
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            if create:
                flags |= os.O_CREAT | os.O_EXCL
            self._fd = os.open(path, flags, 0o600)
            info = os.fstat(self._fd)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid() or info.st_nlink != 1):
                raise ValueError("Backing must be a private, owned, single-link regular file")
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True,
                                               isolation_level=None, timeout=0,
                                               check_same_thread=False)
            connection = self._connection
            if not create:
                # Inspect format before changing journal mode or any content.
                self._validate_format()
            self._configure()
            if create:
                connection.execute('BEGIN IMMEDIATE')
                try:
                    connection.execute(f'PRAGMA application_id={_APP_ID}')
                    connection.execute(_META_SQL)
                    connection.execute(_pages_sql(size))
                    connection.execute('INSERT INTO meta VALUES (?, ?)', (_VERSION, size))
                    connection.execute('COMMIT')
                except BaseException:
                    self._rollback()
                    raise
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except BaseException:
            try:
                self.close()
            except BaseException:
                pass
            raise

    def _configure(self):
        connection = self._connection
        if connection.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower() != 'delete':
            raise BackendError("Could not enable durable journal mode")
        for name, value in (('synchronous', 3), ('cache_size', -1024), ('mmap_size', 0)):
            connection.execute(f'PRAGMA {name}={value}')
            if connection.execute(f'PRAGMA {name}').fetchone()[0] != value:
                raise BackendError("Could not apply bounded durable storage settings")

    def _validate_format(self):
        connection = self._connection
        if connection.execute('PRAGMA application_id').fetchone()[0] != _APP_ID:
            raise ValueError("Unknown persistent storage format")
        rows = connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table' LIMIT 3").fetchall()
        if dict(rows) != {'meta': _META_SQL, 'pages': _pages_sql(self._size)}:
            raise ValueError("Persistent storage schema or size mismatch")
        if connection.execute('SELECT version, size FROM meta LIMIT 2').fetchall() != [(_VERSION, self._size)]:
            raise ValueError("Persistent storage metadata or size mismatch")

    def _validate(self, offset, count):
        if type(offset) is not int or type(count) is not int:
            raise ValueError("Offset and length must be integers")
        if offset < 0 or count <= 0 or offset + count > self._size or count > self._maximum:
            raise ValueError("I/O exceeds allocation or request maximum")
        if self._closed:
            raise BackendError("Persistent storage is closed")

    def _page(self, index):
        # Bound extraction even when a file was externally modified with
        # CHECK constraints disabled. Never fetch an arbitrarily large blob.
        row = self._connection.execute(
            'SELECT typeof(data), length(data), substr(data, 1, 4097) '
            'FROM pages WHERE "index"=?', (index,)).fetchone()
        if row is None:
            return bytes(_PAGE)
        kind, length, data = row
        if kind != 'blob' or length != _PAGE or not isinstance(data, bytes) or len(data) != _PAGE:
            raise BackendError("Malformed persistent page")
        return data

    def read(self, offset, count):
        with self._lock:
            self._validate(offset, count)
            result = bytearray()
            end = offset + count
            while offset < end:
                index, within = divmod(offset, _PAGE)
                length = min(end - offset, _PAGE - within)
                result.extend(self._page(index)[within:within + length])
                offset += length
            return bytes(result)

    def _rollback(self):
        try:
            if self._connection.in_transaction:
                self._connection.execute('ROLLBACK')
        except BaseException:
            # A connection with uncertain transaction state must never serve
            # further I/O. close() releases both SQLite and the ownership lock.
            try:
                self.close()
            except BaseException:
                pass

    def write(self, offset, data):
        if not isinstance(data, bytes):
            raise ValueError("Writes require immutable bytes")
        with self._lock:
            self._validate(offset, len(data))
            connection = self._connection
            try:
                connection.execute('BEGIN IMMEDIATE')
                end = offset + len(data)
                cursor = 0
                while offset < end:
                    index, within = divmod(offset, _PAGE)
                    length = min(end - offset, _PAGE - within)
                    if within == 0 and length == _PAGE:
                        page = data[cursor:cursor + length]
                    else:
                        page = bytearray(self._page(index))
                        page[within:within + length] = data[cursor:cursor + length]
                    connection.execute('INSERT INTO pages ("index", data) VALUES (?, ?) '
                                       'ON CONFLICT("index") DO UPDATE SET data=excluded.data',
                                       (index, page))
                    cursor += length
                    offset += length
                connection.execute('COMMIT')
            except BaseException:
                self._rollback()
                raise

    def close(self):
        """Close SQLite before releasing its ownership lock; retry on failure."""
        with self._lock:
            self._closed = True
            failure = None
            if self._connection is not None:
                try:
                    self._connection.close()
                    self._connection = None
                except BaseException as error:
                    failure = error
            if self._connection is None and self._fd is not None:
                descriptor, self._fd = self._fd, None
                try:
                    os.close(descriptor)
                except BaseException as error:
                    if failure is None:
                        failure = error
            if failure is not None:
                raise failure

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
