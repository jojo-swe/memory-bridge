"""Supporting changes for the swap adapter: flush, counters, worker launch, capabilities."""

import json
import tempfile
import unittest
from unittest.mock import patch

from memory_bridge.core import BackendError
from memory_bridge.memlock import has_cap_ipc_lock
from memory_bridge.remote_nbd import RemoteNBDStorage
from memory_bridge.resilient import ResilientStorage
from memory_bridge.simulation import SimulatedMemory


class FlushableMemory(SimulatedMemory):
    def __init__(self, size):
        super().__init__(size)
        self.flushes = 0
        self.fail_flush = False

    def flush(self):
        if self.fail_flush:
            raise OSError("injected fdatasync failure")
        self.flushes += 1


class ResilientFlushTests(unittest.TestCase):
    def test_flush_forwards_only_to_local_copy(self):
        local, remote = FlushableMemory(8192), FlushableMemory(8192)
        store = ResilientStorage(local, remote)
        self.assertTrue(store.can_flush)
        store.write(0, b"x" * 4096)
        store.flush()
        self.assertEqual((local.flushes, remote.flushes), (1, 0))
        self.assertEqual(store.snapshot()["flushes"], 1)

    def test_failed_local_flush_invalidates_store(self):
        local = FlushableMemory(8192)
        store = ResilientStorage(local, FlushableMemory(8192))
        local.fail_flush = True
        with self.assertRaises(BackendError):
            store.flush()
        local.fail_flush = False
        # Never allow a retry to report durability after an fsync error.
        with self.assertRaises(BackendError):
            store.flush()
        with self.assertRaises(BackendError):
            store.read(0, 1)
        self.assertFalse(store.snapshot()["remote_enabled"])

    def test_backend_without_flush_is_reported_not_faked(self):
        store = ResilientStorage(SimulatedMemory(8192))
        self.assertFalse(store.can_flush)
        with self.assertRaises(BackendError):
            store.flush()
        store.write(0, b"still usable")
        self.assertEqual(store.read(0, 12), b"still usable")

    def test_counters_show_data_source_and_fallback(self):
        local, remote = SimulatedMemory(16384), SimulatedMemory(16384)
        store = ResilientStorage(local, remote)
        store.write(0, b"a" * 8192)
        store.read(0, 8192)
        before = store.snapshot()
        self.assertEqual(before["remote_read_bytes"], 8192)
        self.assertEqual(before["remote_write_bytes"], 8192)
        self.assertEqual(before["local_read_bytes"], 0)
        remote.fail_reads = True
        self.assertEqual(store.read(0, 8192), b"a" * 8192)
        after = store.snapshot()
        self.assertEqual(after["local_read_bytes"], 8192)
        self.assertEqual(after["remote_errors"], 1)
        self.assertFalse(after["remote_enabled"])


class WorkerLaunchTests(unittest.TestCase):
    def test_explicit_interpreter_and_lock_flag_reach_worker(self):
        captured = {}

        def fake_popen(argv, **kwargs):
            captured["argv"] = argv
            raise OSError("stop after capturing the command")

        with patch("memory_bridge.remote_nbd.subprocess.Popen", side_effect=fake_popen):
            with self.assertRaises(BackendError):
                RemoteNBDStorage("localhost", 10809, "/prevalidated.psk", 8192,
                                 python="/opt/python-with-libnbd", lock_memory=True)
        # Embedded interpreters may report a wrong sys.executable.
        self.assertEqual(captured["argv"][0], "/opt/python-with-libnbd")

    def test_handshake_carries_lock_memory(self):
        sent = []

        def fake_exchange(self, request, limit):
            sent.append(request)
            return b"K"

        with patch.object(RemoteNBDStorage, "_launch"), \
             patch.object(RemoteNBDStorage, "_exchange", fake_exchange):
            storage = RemoteNBDStorage("localhost", 10809, "/k.psk", 8192, lock_memory=True)
            storage._process = None
            storage.close()
        self.assertTrue(json.loads(sent[0][1:])["lock_memory"])

    def test_invalid_launch_options_rejected_before_launch(self):
        with patch.object(RemoteNBDStorage, "_launch") as launch:
            for options in ({"python": ""}, {"python": 3}, {"lock_memory": "yes"}):
                with self.assertRaises(ValueError, msg=options):
                    RemoteNBDStorage("localhost", 10809, "/k.psk", 8192, **options)
            launch.assert_not_called()

    def test_worker_rejects_non_boolean_lock_flag(self):
        import socket
        from memory_bridge import nbd_worker
        from memory_bridge.remote_nbd import _HEADER
        parent, child = socket.socketpair()
        with parent, child:
            request = b"H" + json.dumps({"host": "h", "port": 1, "key": "/k", "size": 4096,
                                         "maximum": 4096, "lock_memory": "yes"}).encode()
            parent.sendall(_HEADER.pack(len(request)) + request)
            # A truthy non-boolean must be rejected before any locking.
            with patch("memory_bridge.memlock.lock_all_memory",
                       side_effect=AssertionError("locked despite invalid flag")):
                nbd_worker.serve(child)
            length, = _HEADER.unpack(parent.recv(4))
            self.assertEqual(parent.recv(length), b"E")


class CapabilityTests(unittest.TestCase):
    def check(self, line):
        with tempfile.NamedTemporaryFile("w", delete=False) as status:
            status.write("Name:\tx\n" + line + "\n")
        return has_cap_ipc_lock(status.name)

    def test_parses_effective_capability_bit(self):
        self.assertTrue(self.check("CapEff:\t0000000000004000"))
        self.assertFalse(self.check("CapEff:\t0000000000000000"))
        self.assertTrue(self.check("CapEff:\t000001fffeffffff"))
        self.assertFalse(self.check("CapEff:\tgarbage"))
        self.assertFalse(has_cap_ipc_lock("/nonexistent/status"))


if __name__ == "__main__":
    unittest.main()
