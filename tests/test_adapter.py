"""Exercise real plugin callbacks without pretending to test NBD transport."""

import unittest
import ram_plugin as plugin
from memory_bridge.core import SessionError


class AdapterTests(unittest.TestCase):
    def setUp(self):
        plugin.config("size_mib", "1")
        plugin.config_complete()

    def tearDown(self):
        plugin.cleanup()

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
