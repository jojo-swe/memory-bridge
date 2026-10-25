"""Exercise real plugin callbacks without pretending to test NBD transport."""

import unittest
import tempfile
from pathlib import Path
import ram_plugin as plugin
from memory_bridge.core import SessionError


class AdapterTests(unittest.TestCase):
    def setUp(self):
        plugin._config_file = None
        plugin.config("size_mib", "1")
        plugin.config_complete()

    def tearDown(self):
        plugin.cleanup()
        plugin._config_file = None

    def test_callbacks_use_pool_and_roundtrip(self):
        handle = plugin.open(False)
        plugin.pwrite(handle, memoryview(b"hello"), 4094, 0)
        output = bytearray(5)
        plugin.pread(handle, output, 4094, 0)
        self.assertEqual(output, b"hello")
        self.assertEqual(plugin._pool.snapshot()["written_bytes"], 5)
        self.assertEqual(plugin.get_size(handle), 1048576)
        self.assertEqual(plugin.block_size(handle), (1, 4096, 1048576))
        self.assertFalse(plugin.can_multi_conn(handle))

    def test_disconnect_invalidates_handle(self):
        handle = plugin.open(False)
        plugin.close(handle)
        with self.assertRaises(SessionError):
            plugin.pread(handle, bytearray(1), 0, 0)
        with self.assertRaises(RuntimeError):
            plugin.open(False)

    def test_unknown_configuration_rejected(self):
        with self.assertRaises(ValueError):
            plugin.config("unknown", "1")

    def test_readonly_rejects_write_at_callback_boundary(self):
        handle = plugin.open(True)
        self.assertFalse(plugin.can_write(handle))
        with self.assertRaises(PermissionError):
            plugin.pwrite(handle, b"x", 0, 0)
        output = bytearray(1)
        plugin.pread(handle, output, 0, 0)
        self.assertEqual(output, b"\0")

    def test_toml_quota_applies_to_real_adapter(self):
        plugin.cleanup()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.toml"
            path.write_text('[memory]\ntotal_bytes=1048576\nper_owner_bytes=4096\n')
            plugin.config("config_file", str(path))
            from memory_bridge.core import CapacityError
            with self.assertRaises(CapacityError):
                plugin.config_complete()

    def test_toml_request_limit_is_advertised_and_enforced(self):
        plugin.cleanup()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.toml"
            path.write_text('[memory]\nmax_request_bytes=4096\n')
            plugin.config("config_file", str(path))
            plugin.config_complete()
            handle = plugin.open(False)
            self.assertEqual(plugin.block_size(handle), (1, 4096, 4096))
            with self.assertRaises(ValueError):
                plugin.pwrite(handle, bytes(4097), 0, 0)

    def test_invalid_nbd_block_limit_fails_before_allocation(self):
        plugin.cleanup()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "limits.toml"
            path.write_text('[memory]\nmax_request_bytes=6000\n')
            plugin.config("config_file", str(path))
            with self.assertRaises(ValueError):
                plugin.config_complete()
