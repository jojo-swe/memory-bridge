"""Swap lab guard rails and the verified pressure workload, without root or swap."""

import json
import os
import subprocess
import sys
import time
import unittest

from memory_bridge import swap_lab


class WorkloadTests(unittest.TestCase):
    def test_small_workload_verifies_every_page(self):
        result = subprocess.run([sys.executable, "-m", "memory_bridge.pressure_workload",
                                 "--dataset-mib", "2", "--rounds", "1", "--pattern", "hotset"],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report["verification_errors"], 0)
        self.assertEqual(report["rounds_completed"], 1)
        self.assertEqual(report["pages"], 512)
        self.assertEqual(report["phases"]["random"]["touches"], 512)
        self.assertEqual(report["phases"]["random_writes"]["count"], 64)
        for phase in ("fill", "sweep", "random_reads"):
            self.assertIn("p99_seconds", report["phases"][phase])

    def test_deadline_stops_random_phase(self):
        result = subprocess.run([sys.executable, "-m", "memory_bridge.pressure_workload",
                                 "--dataset-mib", "4", "--rounds", "1000", "--deadline", "0.05"],
                                capture_output=True, text=True, timeout=120)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(report["deadline_hit"])
        self.assertLess(report["rounds_completed"], 1000)


class SamplerTests(unittest.TestCase):
    def test_sampler_keeps_exact_count_and_maximum_when_decimating(self):
        from memory_bridge import pressure_workload
        sampler = pressure_workload.Sampler()
        for value in range(pressure_workload.MAX_SAMPLES * 2 + 3):
            sampler.add(value / 1e6)
        report = sampler.report()
        self.assertEqual(report["count"], pressure_workload.MAX_SAMPLES * 2 + 3)
        self.assertAlmostEqual(report["max_seconds"], (pressure_workload.MAX_SAMPLES * 2 + 2) / 1e6)
        self.assertLessEqual(report["retained_samples"], pressure_workload.MAX_SAMPLES)
        self.assertGreater(report["sample_stride"], 1)


class GuardRailTests(unittest.TestCase):
    def test_refuses_without_disposable_flag_and_sane_sizes(self):
        result = subprocess.run([sys.executable, "-m", "memory_bridge.swap_lab", "--arms",
                                 "local-swap", "--ram-mib", "64", "--dataset-mib", "32",
                                 "--output", os.devnull],
                                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Preflight failed", result.stderr)
        self.assertIn("--disposable-vm", result.stderr)
        self.assertIn("dataset must exceed", result.stderr)

    def test_unknown_arm_rejected(self):
        result = subprocess.run([sys.executable, "-m", "memory_bridge.swap_lab", "--arms",
                                 "ramdisk", "--output", os.devnull],
                                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown arm", result.stderr)

    def test_monitor_excludes_workload_from_bridge_total(self):
        monitor = swap_lab.Monitor({"export": os.getpid(), "workload": os.getpid()}, interval=0.05)
        monitor.start()
        deadline = time.monotonic() + 5
        while monitor.samples < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        monitor.stop.set()
        monitor.join(timeout=2)
        own = swap_lab.process_memory(os.getpid())["vmrss_kib"]
        self.assertGreater(monitor.peak_concurrent_rss_kib, 0)
        self.assertLessEqual(monitor.peak_concurrent_rss_kib, own * 1.5)
        self.assertIn("workload", monitor.peak_by_process)

    def test_summary_ignores_fault_runs_and_counts_oom(self):
        def run(arm, wall, p99, oom=False, fault=None):
            workload = None if oom else {
                "verification_errors": 0, "total_wall_seconds": wall + 1, "rounds_completed": 2,
                "phases": {"random": {"wall_seconds": wall}, "random_reads": {"p99_seconds": p99}}}
            return {"arm": arm, "workload": workload, "oom_killed": oom, "fault": fault,
                    "swap_io": {"pswpin": 10, "pswpout": 20, "pgmajfault": 5}}
        summary = swap_lab.summarize([
            run("local-swap", 2.0, 0.1), run("local-swap", 4.0, 0.3), run("local-swap", 3.0, 0.2),
            run("bridge", 9.0, 0.9, fault={"kind": "dead"}), run("noswap", 0, 0, oom=True)])
        self.assertEqual(summary["local-swap"]["random_wall_median_seconds"], 3.0)
        self.assertEqual(summary["local-swap"]["random_read_p99_median_seconds"], 0.2)
        self.assertEqual(summary["noswap"]["oom_killed"], 1)
        self.assertEqual(summary["noswap"]["died_other"], 0)
        self.assertNotIn("bridge", summary)


if __name__ == "__main__":
    unittest.main()
