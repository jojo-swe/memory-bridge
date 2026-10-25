"""Durable sparse-page correctness, transaction failure and ownership checks."""

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from memory_bridge.core import BackendError
from memory_bridge.persistent import PersistentStorage


class PersistentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'pages.db'

    def create(self, size=16384, **kwargs):
        storage = PersistentStorage(self.path, size, create=True, **kwargs)
        self.addCleanup(storage.close)
        return storage

    def test_sparse_zeros_cross_page_overwrite_and_reopen(self):
        storage = self.create()
        self.assertEqual(storage.read(0, 16384), bytes(16384))
        expected = bytearray(16384)
        for offset, payload in ((4093, b'a' * 8200), (8190, b'updated'), (0, b'first')):
            storage.write(offset, payload)
            expected[offset:offset + len(payload)] = payload
        storage.close()
        storage.close()
        with PersistentStorage(self.path, 16384) as resumed:
            self.assertEqual(resumed.read(0, 16384), bytes(expected))
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_durable_bounded_pragmas(self):
        storage = self.create()
        for name, value in (('journal_mode', 'delete'), ('synchronous', 3),
                            ('cache_size', -1024), ('mmap_size', 0)):
            self.assertEqual(storage._connection.execute(f'PRAGMA {name}').fetchone()[0], value)

    def test_cross_page_write_rolls_back_atomically(self):
        storage = self.create()
        original = b'a' * 12288
        storage.write(0, original)
        storage.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute('CREATE TRIGGER fail_second BEFORE UPDATE ON pages '
                               'WHEN NEW."index" = 1 BEGIN SELECT RAISE(ABORT, \'injected\'); END')
        with PersistentStorage(self.path, 16384) as storage:
            with self.assertRaises(sqlite3.DatabaseError):
                storage.write(0, b'b' * 12288)
            self.assertFalse(storage._connection.in_transaction)
            self.assertEqual(storage.read(0, 12288), original)
        with PersistentStorage(self.path, 16384) as storage:
            self.assertEqual(storage.read(0, 12288), original)

    def test_cancellation_rolls_back_and_connection_remains_usable(self):
        storage = self.create()
        storage.write(0, b'a' * 8192)
        original_page = storage._page
        cancellation = KeyboardInterrupt()

        def interrupted(index):
            if index == 1:
                raise cancellation
            return original_page(index)

        with patch.object(storage, '_page', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt) as raised:
                storage.write(1, b'b' * 8190)
        self.assertIs(raised.exception, cancellation)
        self.assertFalse(storage._connection.in_transaction)
        self.assertEqual(storage.read(0, 8192), b'a' * 8192)

    def test_create_never_overwrites_and_resume_never_creates(self):
        storage = self.create()
        storage.write(0, b'safe')
        storage.close()
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            PersistentStorage(self.path, 16384, create=True)
        self.assertEqual(self.path.read_bytes(), before)
        missing = self.path.with_name('missing.db')
        with self.assertRaises(FileNotFoundError):
            PersistentStorage(missing, 4096)
        self.assertFalse(missing.exists())

    def test_exclusive_lock_and_release(self):
        storage = self.create()
        with self.assertRaises(BlockingIOError):
            PersistentStorage(self.path, 16384)
        storage.close()
        with PersistentStorage(self.path, 16384) as resumed:
            resumed.write(0, b'x')

    def test_size_or_schema_mismatch_does_not_modify_existing_file(self):
        storage = self.create()
        storage.close()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            PersistentStorage(self.path, 8192)
        self.assertEqual(self.path.read_bytes(), before)
        with sqlite3.connect(self.path) as connection:
            connection.execute('ALTER TABLE meta ADD COLUMN unexpected INTEGER')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            PersistentStorage(self.path, 16384)
        self.assertEqual(self.path.read_bytes(), before)

    def test_symlink_hardlink_and_insecure_mode_rejected(self):
        storage = self.create()
        storage.close()
        alias = self.path.with_name('alias.db')
        alias.symlink_to(self.path)
        with self.assertRaises(OSError):
            PersistentStorage(alias, 16384)
        alias.unlink()
        os.link(self.path, alias)
        with self.assertRaises(ValueError):
            PersistentStorage(self.path, 16384)
        alias.unlink()
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            PersistentStorage(self.path, 16384)

    def test_corrupt_file_is_not_overwritten(self):
        self.path.write_bytes(b'not sqlite storage')
        self.path.chmod(0o600)
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            PersistentStorage(self.path, 4096)
        self.assertEqual(self.path.read_bytes(), b'not sqlite storage')

    def test_externally_malformed_blob_is_rejected(self):
        storage = self.create()
        storage.close()
        with sqlite3.connect(self.path) as connection:
            connection.execute('PRAGMA ignore_check_constraints=ON')
            connection.execute('INSERT INTO pages VALUES (0, ?)', (b'x' * 8192,))
        with PersistentStorage(self.path, 16384) as resumed:
            with self.assertRaisesRegex(BackendError, 'Malformed'):
                resumed.read(0, 1)

    def test_invalid_io_has_no_transaction_or_content_effect(self):
        storage = self.create(max_request_bytes=4096)
        for offset, count in ((True, 1), (-1, 1), (0, 0), (16384, 1), (0, 4097)):
            with self.subTest(offset=offset, count=count), self.assertRaises(ValueError):
                storage.read(offset, count)
        for offset, data in ((False, b'x'), (0, b''), (16383, b'xx'),
                             (0, bytearray(b'x')), (0, b'x' * 4097)):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                storage.write(offset, data)
        self.assertFalse(storage._connection.in_transaction)
        self.assertEqual(storage._connection.execute('SELECT count(*) FROM pages').fetchone()[0], 0)
        storage.close()
        with self.assertRaises(BackendError):
            storage.read(0, 1)

    def test_invalid_constructor_arguments_do_not_create_artifacts(self):
        for size in (True, 0, -4096, 4097, 64 * 1024**3 + 4096):
            with self.subTest(size=size), self.assertRaises(ValueError):
                PersistentStorage(self.path, size, create=True)
        for kwargs in ({'create': 1}, {'max_request_bytes': True},
                       {'max_request_bytes': 0}, {'max_request_bytes': 33554433}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PersistentStorage(self.path, 4096, **kwargs)
        self.assertFalse(self.path.exists())

    def test_interrupted_sqlite_close_retains_lock_until_successful_retry(self):
        storage = self.create()
        descriptor = storage._fd
        actual = storage._connection

        class InterruptedConnection:
            attempts = 0

            def close(self):
                self.attempts += 1
                if self.attempts == 1:
                    raise KeyboardInterrupt()
                actual.close()

        interrupted = InterruptedConnection()
        storage._connection = interrupted
        try:
            with self.assertRaises(KeyboardInterrupt):
                storage.close()
            os.fstat(descriptor)  # Ownership descriptor remains live.
            with self.assertRaises(BlockingIOError):
                PersistentStorage(self.path, 16384)
            storage.close()
            self.assertEqual(interrupted.attempts, 2)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
            with PersistentStorage(self.path, 16384) as resumed:
                self.assertEqual(resumed.read(0, 1), b'\0')
        finally:
            # Restore the real connection if an assertion interrupted cleanup.
            if storage._connection is interrupted:
                storage._connection = actual
                storage.close()
