"""Address-space tests run in disposable processes; never limit the test runner."""

import json
import resource
import subprocess
import sys
import unittest
from unittest.mock import patch

from memory_bridge.memory_budget import apply_address_limit


class MemoryBudgetTests(unittest.TestCase):
    def test_requested_limit_never_raises_existing_limit(self):
        with patch('memory_bridge.memory_budget.resource.getrlimit',
                   return_value=(64 * 1024 * 1024, resource.RLIM_INFINITY)), \
                patch('memory_bridge.memory_budget.resource.setrlimit') as change:
            with self.assertRaises(ValueError):
                apply_address_limit(128)
            change.assert_not_called()

    def test_limit_rejects_large_allocation_in_child(self):
        code = '''
from memory_bridge.memory_budget import apply_address_limit
apply_address_limit(64)
try:
    bytearray(128 * 1024 * 1024)
except MemoryError:
    print('bounded')
else:
    raise RuntimeError('Limit was not enforced')
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'bounded')

    def test_file_workload_under_limit_with_large_requests(self):
        result = subprocess.run([sys.executable, 'compare_benchmark.py', '--mode', 'local',
                                 '--size-mib', '8', '--operations', '16', '--request-kib', '64',
                                 '--memory-limit-mib', '64'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)['results'][0]
        self.assertEqual(report['address_space_limit_bytes_per_process'], 64 * 1024 * 1024)
        self.assertEqual(report['page_size'], 65536)
        self.assertEqual(report['write']['verification_reads'], 16)
