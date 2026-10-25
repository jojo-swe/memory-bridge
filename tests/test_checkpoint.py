"""Real process crashes and restart recovery, without depending on a donor."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from memory_bridge.persistent import PersistentStorage
from memory_bridge.resilient import ResilientStorage
from memory_bridge.simulation import SimulatedMemory


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'checkpoint.db'

    def crash_child(self, code, sentinel):
        with tempfile.TemporaryFile(mode='w+') as output:
            child = subprocess.Popen([sys.executable, '-c', code, str(self.path)],
                                     stdout=output, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    output.seek(0)
                    if sentinel in output.read():
                        break
                    if child.poll() is not None:
                        self.fail('Child failed before crash point: ' + child.communicate()[1])
                    time.sleep(.01)
                else:
                    self.fail('Child failed to reach crash point')
                child.kill()
                child.communicate(timeout=3)
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=3)

    def test_acknowledged_write_survives_sigkill_and_new_process(self):
        self.crash_child('''
import sys,time
from memory_bridge.persistent import PersistentStorage
store=PersistentStorage(sys.argv[1],1048576,create=True)
store.write(4090,b'acknowledged'*1000)
print('ACKNOWLEDGED',flush=True)
time.sleep(20)
''', 'ACKNOWLEDGED')
        with PersistentStorage(self.path, 1048576) as store:
            self.assertEqual(store.read(4090, 12000), b'acknowledged' * 1000)

    def test_killed_uncommitted_cross_page_overwrite_rolls_back(self):
        with PersistentStorage(self.path, 1048576, create=True) as store:
            store.write(0, b'A' * 65536)
        self.crash_child('''
import sys,time
from memory_bridge.persistent import PersistentStorage
store=PersistentStorage(sys.argv[1],1048576)
store._connection.execute('PRAGMA cache_size=1')
def trace(sql):
    if sql.strip().upper()=='COMMIT':
        print('BEFORE_COMMIT',flush=True)
        time.sleep(20)
store._connection.set_trace_callback(trace)
store.write(0,b'B'*65536)
raise RuntimeError('Crash point missed')
''', 'BEFORE_COMMIT')
        with PersistentStorage(self.path, 1048576) as store:
            self.assertEqual(store.read(0, 65536), b'A' * 65536)

    def test_reopened_backing_never_trusts_old_remote_contents(self):
        with PersistentStorage(self.path, 8192, create=True) as store:
            store.write(0, b'saved')
        stale = SimulatedMemory(8192)
        stale.write(0, b'wrong')
        with ResilientStorage(PersistentStorage(self.path, 8192), stale) as storage:
            self.assertEqual(storage.snapshot()['cached_blocks'], 0)
            self.assertEqual(storage.read(0, 5), b'saved')
            storage.write(4095, b'edge')
            self.assertEqual(storage.read(4095, 4), b'edge')

    def test_checkpoint_cli_creates_once_and_verifies_after_restart(self):
        for action in ('create', 'verify'):
            result = subprocess.run([sys.executable, 'checkpoint_demo.py', action, str(self.path)],
                                    capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
        duplicate = subprocess.run([sys.executable, 'checkpoint_demo.py', 'create', str(self.path)],
                                   capture_output=True, text=True, timeout=8)
        self.assertNotEqual(duplicate.returncode, 0)
