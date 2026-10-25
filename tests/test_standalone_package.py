"""Verify command modules operate with only the installed package available."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class StandalonePackageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        source = Path(__file__).resolve().parents[1] / 'memory_bridge'
        shutil.copytree(source, self.root / 'memory_bridge', ignore=shutil.ignore_patterns('__pycache__'))
        self.environment = dict(os.environ)
        self.environment.pop('PYTHONPATH', None)

    def command(self, *args, timeout=8):
        return subprocess.run([sys.executable, '-m', 'memory_bridge', *args], cwd=self.root,
                              env=self.environment, capture_output=True, text=True, timeout=timeout)

    def test_all_installed_commands_have_help(self):
        for command in ('lab', 'compare', 'benchmark', 'recover', 'checkpoint', 'donor', 'keygen'):
            with self.subTest(command=command):
                result = self.command(command, '--help')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)

    def test_persistent_checkpoint_without_checkout(self):
        for action in ('create', 'verify'):
            result = self.command('checkpoint', action, 'checkpoint.db')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['verified'])

    def test_isolated_comparison_subprocess_without_checkout(self):
        result = self.command('compare', '--mode', 'local', '--operations', '8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['results'][0]['operations'], 8)

    @unittest.skipUnless(shutil.which('nbdkit') and importlib.util.find_spec('nbd'),
                         'Requires nbdkit and python3-libnbd')
    def test_whole_installed_lab_without_checkout(self):
        result = self.command('lab', '--operations', '8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['results'][1]['cache']['remote_enabled'])
