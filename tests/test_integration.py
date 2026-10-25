"""Real TLS/NBD tests, skipped explicitly when system dependencies are absent."""

import importlib.util
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

AVAILABLE = bool(shutil.which("nbdkit") and importlib.util.find_spec("nbd"))


@unittest.skipUnless(AVAILABLE, "Requires nbdkit, its Python plugin and python3-libnbd")
class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.key = Path(self.directory.name) / "key.psk"
        subprocess.run([sys.executable, "bridge.py", "keygen", str(self.key)],
                       check=True, capture_output=True)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.donor = subprocess.Popen([sys.executable, "bridge.py", "donor",
                                       "--key", str(self.key), "--port", str(self.port),
                                       "--size-mib", "1", "--config", "config/local.toml"],
                                      stdout=subprocess.DEVNULL)
        self.addCleanup(self.stop_donor)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.donor.poll() is not None:
                self.fail("Donor failed to start; inspect stderr")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return  # TCP probe does not authenticate or claim the export.
            except OSError:
                time.sleep(0.02)
        self.fail("Donor startup timed out")

    def stop_donor(self):
        if self.donor.poll() is None:
            self.donor.kill()
        self.donor.wait(timeout=5)

    def benchmark(self, key=None):
        return subprocess.run([sys.executable, "benchmark.py", "--key", str(key or self.key),
                               "--port", str(self.port), "--pages", "32", "--timeout", "5"],
                              capture_output=True, text=True, timeout=8)

    def test_roundtrip_and_no_reconnect(self):
        result = self.benchmark()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unique_pages_verified", result.stdout)
        self.assertNotEqual(self.benchmark().returncode, 0)

    def test_wrong_key_does_not_consume_export(self):
        wrong = Path(self.directory.name) / "wrong.psk"
        subprocess.run([sys.executable, "bridge.py", "keygen", str(wrong)],
                       check=True, capture_output=True)
        self.assertNotEqual(self.benchmark(wrong).returncode, 0)
        self.assertEqual(self.benchmark().returncode, 0)

    def test_plaintext_rejected(self):
        code = ("import nbd; h=nbd.NBD(); h.set_tls(nbd.TLS_DISABLE); "
                f"h.connect_tcp('127.0.0.1', '{self.port}')")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.benchmark().returncode, 0)

    def test_donor_loss_returns_error(self):
        # A child owns the blocking client calls so a broken read has a hard
        # external deadline. There is deliberately no transparent reconnect.
        code = "\n".join([
            "import nbd, sys", "h=nbd.NBD()", "h.set_tls(nbd.TLS_REQUIRE)",
            "h.set_tls_username('borrower')", f"h.set_tls_psk_file({str(self.key)!r})",
            f"h.connect_tcp('127.0.0.1', '{self.port}')", "h.pwrite(b'x'*4096, 0)",
            "print('READY', flush=True)", "sys.stdin.readline()", "h.pread(4096, 0)"])
        with tempfile.TemporaryFile(mode="w+") as output:
            child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                     stdout=output, stderr=subprocess.PIPE, text=True)
            try:
                # Generous: a loaded CI runner can take seconds to start a TLS
                # client; the test checks the outcome, not connection speed.
                deadline = time.monotonic() + 20
                ready = False
                while time.monotonic() < deadline:
                    output.seek(0)
                    if "READY" in output.read():
                        ready = True
                        break
                    if child.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(ready, "Client did not reach connected state")
                self.stop_donor()
                child.communicate("read\n", timeout=5)
                self.assertNotEqual(child.returncode, 0)
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate()

    def _recovery_after_fault(self, freeze):
        from memory_bridge.local_disk import TemporaryFileStorage
        from memory_bridge.remote_nbd import RemoteNBDStorage
        from memory_bridge.resilient import ResilientStorage
        local = TemporaryFileStorage(1024 * 1024, self.directory.name)
        self.addCleanup(local.close)
        remote = RemoteNBDStorage("127.0.0.1", self.port, self.key,
                                  1024 * 1024, timeout=0.75)
        self.addCleanup(remote.close)
        with ResilientStorage(local, remote) as storage:
            payload = b"across blocks" * 500
            storage.write(4090, payload)
            self.assertTrue(storage.snapshot()["remote_enabled"])
            self.assertEqual(storage.read(4090, len(payload)), payload)
            self.assertTrue(storage.snapshot()["remote_enabled"])
            if freeze:
                # A live TCP connection with an unresponsive server must also
                # trigger fallback; connection-reset-only coverage is insufficient.
                self.donor.send_signal(signal.SIGSTOP)
            else:
                self.stop_donor()
            start = time.monotonic()
            self.assertEqual(storage.read(4090, len(payload)), payload)
            self.assertLess(time.monotonic() - start, 3.0)
            self.assertFalse(storage.snapshot()["remote_enabled"])
            storage.write(0, b"new")
            self.assertEqual(storage.read(0, 3), b"new")

    def test_recovery_after_killed_donor(self):
        self._recovery_after_fault(False)

    def test_recovery_after_frozen_donor(self):
        self._recovery_after_fault(True)

    def test_recovery_transport_rejects_size_mismatch(self):
        from memory_bridge.remote_nbd import RemoteNBDStorage
        from memory_bridge.core import BackendError
        with self.assertRaises(BackendError):
            RemoteNBDStorage("127.0.0.1", self.port, self.key, 4096, timeout=2)

    def test_recovery_transport_rejects_wrong_key(self):
        from memory_bridge.remote_nbd import RemoteNBDStorage
        from memory_bridge.core import BackendError
        wrong = Path(self.directory.name) / "wrong.psk"
        subprocess.run([sys.executable, "bridge.py", "keygen", str(wrong)],
                       check=True, capture_output=True)
        with self.assertRaises(BackendError):
            RemoteNBDStorage("127.0.0.1", self.port, wrong, 1024 * 1024, timeout=2)
        self.assertEqual(self.benchmark().returncode, 0)

    def test_comparison_benchmark_uses_live_remote_cache(self):
        import json
        result = subprocess.run([sys.executable, 'compare_benchmark.py', '--key', str(self.key),
                                 '--port', str(self.port), '--size-mib', '1',
                                 '--operations', '16', '--run-timeout', '10',
                                 '--directory', self.directory.name],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['order'], ['local', 'remote'])
        local, remote = report['results']
        self.assertEqual(local['operations'], remote['operations'])
        self.assertEqual(local['seed'], remote['seed'])
        self.assertTrue(remote['cache']['remote_enabled'])
        self.assertEqual(remote['cache']['remote_errors'], 0)
        self.assertEqual(remote['cache']['cached_blocks'], 256)
        self.assertGreater(remote['child_peak_rss_kib'], 0)
        self.assertGreater(report['duration_ratios']['read_remote_over_local'], 0)
        # No performance threshold: CI proves correctness, not a speed benefit.
        print('COMPARISON_RESULT=' + json.dumps(report), flush=True)

    def test_batched_comparison_under_address_limit(self):
        import json
        result = subprocess.run([sys.executable, 'compare_benchmark.py', '--key', str(self.key),
                                 '--port', str(self.port), '--operations', '16',
                                 '--request-kib', '64', '--memory-limit-mib', '128'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        remote = report['results'][1]
        self.assertEqual(remote['page_size'], 65536)
        self.assertEqual(remote['address_space_limit_bytes_per_process'], 128 * 1024 * 1024)
        self.assertEqual(remote['cache']['valid_bitmap_bytes'], 32)
        self.assertTrue(remote['cache']['remote_enabled'])
        print('BATCHED_COMPARISON_RESULT=' + json.dumps(report), flush=True)

    def test_persistent_checkpoint_survives_donor_and_client_restart(self):
        path = Path(self.directory.name) / 'checkpoint.db'
        created = subprocess.run([sys.executable, 'checkpoint_demo.py', 'create', str(path),
                                  '--key', str(self.key), '--port', str(self.port)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(created.returncode, 0, created.stderr)
        self.stop_donor()
        verified = subprocess.run([sys.executable, 'checkpoint_demo.py', 'verify', str(path)],
                                  capture_output=True, text=True, timeout=8)
        self.assertEqual(verified.returncode, 0, verified.stderr)
