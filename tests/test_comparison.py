"""The benchmark must fail honestly, validate input and contain timed-out workers."""

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import compare_benchmark


class ComparisonTests(unittest.TestCase):
    def test_local_cli_reports_verified_workload_and_memory_scope(self):
        completed = subprocess.run([sys.executable, 'compare_benchmark.py', '--mode', 'local',
                                    '--operations', '8'], capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report['order'], ['local'])
        self.assertIsNone(report['duration_ratios'])
        result = report['results'][0]
        self.assertEqual(result['operations'], 8)
        self.assertEqual(result['export_bytes'], 1024 * 1024)
        self.assertGreater(result['process_peak_rss_kib'], 0)
        self.assertEqual(result['child_peak_rss_kib'], 0)
        self.assertIsNone(result['cache'])
        self.assertIn('Not a cold SSD or memory-pressure benchmark', report['limitations'])

    def test_invalid_values_do_not_start_workers(self):
        for args in (['--operations', '0'], ['--size-mib', '0'], ['--seed', '-1'],
                     ['--io-timeout', 'nan'], ['--run-timeout', 'inf'],
                     ['--mode', 'compare']):
            with self.subTest(args=args), patch.object(sys, 'argv', ['compare_benchmark.py', *args]), \
                    patch.object(compare_benchmark, 'run_mode') as run:
                with self.assertRaises(SystemExit) as error:
                    compare_benchmark.main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()

    def test_deadline_kills_entire_experiment_process_group(self):
        args = SimpleNamespace(size_mib=1, operations=8, seed=42, host='localhost',
                               port=10809, io_timeout=2, key=None, directory=None, run_timeout=1)
        process = Mock(pid=12345)
        process.communicate.side_effect = [subprocess.TimeoutExpired('experiment', 1), ('', '')]
        with patch.object(compare_benchmark.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(compare_benchmark.os, 'killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                compare_benchmark.run_mode(args, 'local')
            self.assertTrue(spawn.call_args.kwargs['start_new_session'])
            kill.assert_called_once_with(12345, compare_benchmark.signal.SIGKILL)
            self.assertEqual(process.communicate.call_count, 2)

    def test_failed_cache_is_never_reported_as_remote_performance(self):
        args = SimpleNamespace(size_mib=1, worker_mode='remote', key=Path('/key'),
                               directory=None, host='localhost', port=10809,
                               io_timeout=2, operations=8, seed=42)
        with patch.object(compare_benchmark, 'private_key'), \
                patch.object(compare_benchmark, 'TemporaryFileStorage'), \
                patch.object(compare_benchmark, 'RemoteNBDStorage'), \
                patch.object(compare_benchmark, 'ResilientStorage') as cache, \
                patch.object(compare_benchmark, 'run_workload', return_value={}):
            cache.return_value.snapshot.return_value = {'remote_enabled': False}
            with self.assertRaisesRegex(RuntimeError, 'comparison is invalid'):
                compare_benchmark.worker(args)
