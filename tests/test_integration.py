"""Real TLS/NBD tests, skipped explicitly when system dependencies are absent."""

import importlib.util
from pathlib import Path
import shutil
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
                deadline = time.monotonic() + 5
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
