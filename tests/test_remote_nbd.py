"""Protocol deadlines and cleanup without a libnbd installation."""

import socket
import struct
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from memory_bridge.core import BackendError
from memory_bridge.remote_nbd import RemoteNBDStorage


# A real isolated process exercises OS socket deadlines and process cleanup.
_FAKE = r'''
import socket,struct,sys,time
s=socket.socket(fileno=int(sys.argv[1])); mode=sys.argv[2]
def receive(n):
 b=b''
 while len(b)<n:
  p=s.recv(n-len(b))
  if not p: sys.exit(0)
  b+=p
 return b
def frame(): return receive(struct.unpack('!I',receive(4))[0])
def send(b): s.sendall(struct.pack('!I',len(b))+b)
frame()
if mode=='handshake_hang': time.sleep(20)
send(b'K')
data=bytearray(8192)
while True:
 req=frame()
 if mode=='hang': time.sleep(20)
 if mode=='error': send(b'E'); continue
 if mode=='oversize': s.sendall(struct.pack('!I',0xffffffff)); time.sleep(20)
 if mode=='short': send(b'K'); continue
 if mode=='partial':
  s.sendall(struct.pack('!I',5)+b'K')
  for c in b'abcd':
   time.sleep(.12); s.sendall(bytes([c]))
  continue
 off,count=struct.unpack('!QQ',req[1:17])
 if req[:1]==b'W': data[off:off+count]=req[17:]; send(b'K')
 else: send(b'K'+data[off:off+count])
'''


class RemoteTests(unittest.TestCase):
    def launch(self, mode, timeout=.3):
        processes = []

        def fake(fd):
            process = subprocess.Popen([sys.executable, '-c', _FAKE, str(fd), mode],
                                       pass_fds=(fd,), stderr=subprocess.DEVNULL)
            processes.append(process)
            return process

        with patch.object(RemoteNBDStorage, '_launch', side_effect=fake):
            storage = RemoteNBDStorage('localhost', 10809, '/prevalidated.psk',
                                       8192, timeout=timeout, max_request_bytes=4096)
        self.addCleanup(storage.close)
        return storage, processes[0]

    def test_roundtrip_and_close_reaps_child(self):
        storage, process = self.launch('ok')
        storage.write(3, b'hello')
        self.assertEqual(storage.read(2, 7), b'\0hello\0')
        storage.close()
        storage.close()
        self.assertIsNotNone(process.poll())
        with self.assertRaises(BackendError):
            storage.read(0, 1)

    def test_invalid_io_never_invalidates_transport(self):
        storage, _ = self.launch('ok')
        for offset, count in ((True, 1), (-1, 1), (0, 0), (8192, 1), (0, 4097)):
            with self.subTest(offset=offset, count=count), self.assertRaises(ValueError):
                storage.read(offset, count)
        with self.assertRaises(ValueError):
            storage.write(0, bytearray(b'x'))
        storage.write(0, b'x')
        self.assertEqual(storage.read(0, 1), b'x')

    def test_protocol_errors_are_terminal_and_reap_child(self):
        for mode in ('error', 'oversize', 'short', 'hang', 'partial'):
            with self.subTest(mode=mode):
                storage, process = self.launch(mode)
                started = time.monotonic()
                with self.assertRaises(BackendError):
                    storage.read(0, 4)
                self.assertLess(time.monotonic() - started, 1.5)
                self.assertIsNotNone(process.poll())
                with self.assertRaises(BackendError):
                    storage.write(0, b'x')

    def test_handshake_timeout_reaps_child(self):
        processes = []

        def fake(fd):
            process = subprocess.Popen([sys.executable, '-c', _FAKE, str(fd),
                                        'handshake_hang'], pass_fds=(fd,),
                                       stderr=subprocess.DEVNULL)
            processes.append(process)
            return process

        with patch.object(RemoteNBDStorage, '_launch', side_effect=fake):
            with self.assertRaises(BackendError):
                RemoteNBDStorage('localhost', 10809, '/key', 8192, timeout=.3)
        self.assertIsNotNone(processes[0].poll())

    def test_constructor_validates_before_launch(self):
        invalid = ({'host': ''}, {'port': True}, {'port': 65536}, {'size': True},
                   {'size': 4097}, {'timeout': float('nan')},
                   {'timeout': float('inf')}, {'timeout': True}, {'timeout': 0},
                   {'max_request_bytes': 0}, {'max_request_bytes': 4095},
                   {'max_request_bytes': 33554433},
                   {'key': None})
        with patch.object(RemoteNBDStorage, '_launch') as launch:
            for change in invalid:
                values = dict(host='localhost', port=10809, key='/key', size=8192)
                values.update(change)
                with self.subTest(change=change), self.assertRaises(ValueError):
                    RemoteNBDStorage(**values)
            launch.assert_not_called()


class WorkerTests(unittest.TestCase):
    @staticmethod
    def receive_frame(sock):
        from memory_bridge.nbd_worker import _frame
        return _frame(sock, 8192)

    def test_healthy_worker_accepts_libnbd_bytearray_buffers(self):
        from memory_bridge.nbd_worker import serve
        import json
        import threading
        from unittest.mock import Mock
        handle = Mock()
        handle.get_size.return_value = 8192
        handle.pread.return_value = bytearray(b'hello')
        module = Mock(TLS_REQUIRE=2)
        module.NBD.return_value = handle
        parent, child = socket.socketpair()
        parent.settimeout(1)
        self.addCleanup(parent.close)
        self.addCleanup(child.close)

        def send(payload):
            parent.sendall(struct.pack('!I', len(payload)) + payload)

        with patch.dict(sys.modules, {'nbd': module}):
            thread = threading.Thread(target=serve, args=(child,))
            thread.start()
            try:
                send(b'H' + json.dumps(dict(host='localhost', port=10809,
                     key='/key', size=8192, maximum=4096)).encode())
                self.assertEqual(self.receive_frame(parent), b'K')
                send(b'W' + struct.pack('!QQ', 3, 5) + b'hello')
                self.assertEqual(self.receive_frame(parent), b'K')
                send(b'R' + struct.pack('!QQ', 3, 5))
                self.assertEqual(self.receive_frame(parent), b'Khello')
                handle.pwrite.assert_called_once_with(b'hello', 3)
                handle.pread.assert_called_once_with(5, 3)
            finally:
                parent.shutdown(socket.SHUT_RDWR)
                thread.join(1)
        self.assertFalse(thread.is_alive())

    def test_worker_requires_tls_and_checks_size(self):
        from memory_bridge.nbd_worker import serve
        import json
        import threading
        from unittest.mock import Mock
        handle = Mock()
        handle.get_size.return_value = 4096
        module = Mock(TLS_REQUIRE=2)
        module.NBD.return_value = handle
        parent, child = socket.socketpair()
        self.addCleanup(parent.close)
        self.addCleanup(child.close)
        config = json.dumps(dict(host='localhost', port=10809, key='/key',
                                 size=8192, maximum=4096)).encode()
        with patch.dict(sys.modules, {'nbd': module}):
            thread = threading.Thread(target=serve, args=(child,))
            thread.start()
            payload = b'H' + config
            parent.sendall(struct.pack('!I', len(payload)) + payload)
            parent.settimeout(1)
            self.assertEqual(self.receive_frame(parent), b'E')
            thread.join(1)
        self.assertFalse(thread.is_alive())
        handle.set_tls.assert_called_once_with(2)
        handle.set_tls_username.assert_called_once_with('borrower')
        handle.set_tls_psk_file.assert_called_once_with('/key')
        handle.connect_tcp.assert_called_once_with('localhost', '10809')
        handle.pread.assert_not_called()
