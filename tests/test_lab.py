"""Loopback launcher contracts; the real full lab runs when NBD is available."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from lab import wait_for_donor, stop_donor


class LabTests(unittest.TestCase):
    def test_donor_exit_fails_startup_promptly(self):
        process = Mock()
        process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, 'startup'):
            wait_for_donor(process, 1)

    def test_cleanup_reaps_live_donor(self):
        process = Mock()
        process.poll.return_value = None
        stop_donor(process)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=2)

    @unittest.skipUnless(shutil.which('nbdkit') and importlib.util.find_spec('nbd'),
                         'Requires nbdkit and python3-libnbd')
    def test_whole_lab_works_outside_repository(self):
        script = Path(__file__).resolve().parents[1] / 'lab.py'
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(script), '--operations', '8',
                                     '--memory-limit-mib', '128'], cwd=directory,
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['lab']['scope'], 'loopback only')
        self.assertTrue(report['results'][1]['cache']['remote_enabled'])
