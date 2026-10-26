"""Build and exercise the C swap workload experiments/swap/workloads/pagestress.c.

The binary is compiled into a temporary directory with the exact production
flags (-Werror), so a new compiler warning fails the suite.
"""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "experiments" / "swap" / "workloads" / "pagestress.c"
CFLAGS = ["-O2", "-Wall", "-Wextra", "-Werror", "-std=c11", "-D_GNU_SOURCE"]

EXPECTED_KEYS = {
    "tool", "version", "params", "page_count", "populate_s", "request_phase_s",
    "requests_completed", "requests_per_s", "latency_ms", "latency_truncated",
    "verify_errors", "final_sweep_errors", "final_sweep_s", "total_s",
    "max_rss_kib", "major_faults", "minor_faults", "voluntary_ctx_switches",
    "involuntary_ctx_switches", "interrupted",
}


class PageStressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gcc = shutil.which("gcc")
        if gcc is None:
            raise unittest.SkipTest("gcc not installed")
        cls._tmp = tempfile.TemporaryDirectory(prefix="pagestress-")
        cls.binary = Path(cls._tmp.name) / "pagestress"
        proc = subprocess.run(
            [gcc, *CFLAGS, "-o", str(cls.binary), str(SOURCE)],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0 or proc.stderr.strip():
            cls._tmp.cleanup()
            raise AssertionError(f"compile failed or warned:\n{proc.stderr}")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_tool(self, *args, timeout=20):
        return subprocess.run([str(self.binary), *args], capture_output=True,
                              text=True, timeout=timeout, cwd=self._tmp.name)

    def parse(self, proc):
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, f"stdout must be one JSON line: {proc.stdout!r}")
        return json.loads(lines[0])

    def test_small_run_report(self):
        out_path = Path(self._tmp.name) / "small.json"
        proc = self.run_tool("--size-mib", "8", "--requests", "2000",
                             "--json-out", str(out_path))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = self.parse(proc)
        self.assertTrue(EXPECTED_KEYS <= data.keys(), EXPECTED_KEYS - data.keys())
        self.assertEqual(data["tool"], "pagestress")
        self.assertEqual(data["version"], "1")
        self.assertEqual(data["page_count"], 8 * 256)
        self.assertEqual(data["requests_completed"], 2000)
        self.assertEqual(data["verify_errors"], 0)
        self.assertEqual(data["final_sweep_errors"], 0)
        self.assertFalse(data["interrupted"])
        self.assertFalse(data["latency_truncated"])
        params = data["params"]
        self.assertEqual(params["size_mib"], 8)
        self.assertEqual(params["pages_per_request"], 8)
        self.assertAlmostEqual(params["hot_fraction"], 0.2)
        self.assertAlmostEqual(params["hot_probability"], 0.8)
        self.assertAlmostEqual(params["write_fraction"], 0.1)
        self.assertEqual(params["seed"], 1)
        lat = data["latency_ms"]
        self.assertLessEqual(lat["p50"], lat["p90"])
        self.assertLessEqual(lat["p90"], lat["p99"])
        self.assertLessEqual(lat["p99"], lat["p999"])
        self.assertLessEqual(lat["p999"], lat["max"])
        self.assertGreater(lat["max"], 0)
        self.assertGreater(data["max_rss_kib"], 8 * 1024)
        self.assertEqual(json.loads(out_path.read_text()), data)

    def test_duration_mode(self):
        proc = self.run_tool("--size-mib", "4", "--duration-s", "0.5")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = self.parse(proc)
        self.assertGreater(data["requests_completed"], 0)
        self.assertGreaterEqual(data["request_phase_s"], 0.5)
        self.assertEqual(data["verify_errors"] + data["final_sweep_errors"], 0)

    def test_corruption_self_test_detected(self):
        proc = self.run_tool("--size-mib", "4", "--requests", "200",
                             "--corrupt-for-self-test")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        data = self.parse(proc)
        self.assertGreater(data["verify_errors"] + data["final_sweep_errors"], 0)
        self.assertGreaterEqual(data["final_sweep_errors"], 1)
        self.assertIn("MISMATCH", proc.stderr)

    def test_invalid_arguments(self):
        cases = [
            [],
            ["--requests", "10"],                                  # missing size
            ["--size-mib", "0", "--requests", "10"],
            ["--size-mib", "4"],                                   # no limit
            ["--size-mib", "4", "--requests", "10", "--hot-fraction", "1.5"],
            ["--size-mib", "4", "--requests", "10", "--write-fraction", "-0.1"],
            ["--size-mib", "4", "--requests", "10", "--hot-probability", "abc"],
            ["--size-mib", "4", "--requests", "-5"],
            ["--size-mib", "4", "--duration-s", "nan"],
            ["--size-mib", "4", "--requests", "10", "--pages-per-request", "0"],
            ["--size-mib", "99999999999999", "--requests", "10"],
            ["--size-mib", "4", "--requests", "10", "--bogus"],
            ["--size-mib", "4", "--requests", "10", "extra"],
        ]
        for args in cases:
            with self.subTest(args=args):
                proc = self.run_tool(*args)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(proc.stdout, "")

    def test_determinism_digest(self):
        def digest(seed):
            proc = self.run_tool("--size-mib", "4", "--requests", "1",
                                 "--seed", str(seed), "--print-digest")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return self.parse(proc)["populate_digest"]

        a, b, c = digest(42), digest(42), digest(43)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_sigterm_finishes_cleanly(self):
        proc = subprocess.Popen(
            [str(self.binary), "--size-mib", "8", "--duration-s", "60"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            time.sleep(0.5)
            self.assertIsNone(proc.poll(), "workload exited before SIGTERM")
            os.kill(proc.pid, signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertEqual(proc.returncode, 0, stderr)
        data = json.loads(stdout.strip())
        self.assertTrue(data["interrupted"])
        self.assertEqual(data["final_sweep_errors"], 0)
        self.assertEqual(data["verify_errors"], 0)
        self.assertGreater(data["requests_completed"], 0)
        self.assertLess(data["request_phase_s"], 30)


if __name__ == "__main__":
    unittest.main()
