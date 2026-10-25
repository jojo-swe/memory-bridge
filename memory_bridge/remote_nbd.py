"""Deadline-bounded NBD storage, isolating blocking libnbd calls in a worker.

The key path must already have been validated as a private PSK file. The caller
owns path stability until the worker has connected. No operation is retried.
"""

import json
import math
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time

from .core import BackendError

_HEADER = struct.Struct("!I")
_IO = struct.Struct("!QQ")
_MAX_REQUEST = 32 * 1024 * 1024


class RemoteNBDStorage:
    def __init__(self, host, port, key, size, timeout=2.0,
                 max_request_bytes=1048576):
        if not isinstance(host, str) or not host or len(host) > 253 or "\0" in host:
            raise ValueError("Host must be a nonempty bounded hostname or address")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Port must be 1..65535")
        if type(size) is not int or not 0 < size <= (1 << 63) - 1 or size % 4096:
            raise ValueError("Expected size must be positive and page aligned")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be positive and finite")
        if type(max_request_bytes) is not int or not 4096 <= max_request_bytes <= _MAX_REQUEST:
            raise ValueError("Request maximum must be 4096..33554432")
        if not isinstance(key, (str, os.PathLike)):
            raise ValueError("Key must be a prevalidated private file path")
        key = str(Path(key).absolute())
        if len(key) > 2048 or "\0" in key:
            raise ValueError("Key path is invalid")
        self.size = size
        self._size = size
        self._timeout = float(timeout)
        self._maximum = max_request_bytes
        self._lock = threading.RLock()
        self._closed = False
        self._process = None
        self._socket, child = socket.socketpair()
        try:
            self._process = self._launch(child.fileno())
            child.close()
            request = json.dumps({"host": host, "port": port, "key": key,
                                  "size": size, "maximum": max_request_bytes}).encode()
            response = self._exchange(b"H" + request, 1)
            if response != b"K":
                raise ValueError("Invalid handshake")
        except Exception:
            child.close()
            self._stop()
            raise BackendError("Remote connection failed") from None

    @staticmethod
    def _launch(fd):
        return subprocess.Popen([sys.executable, "-m", "memory_bridge.nbd_worker", str(fd)],
                                pass_fds=(fd,), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _remaining(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self._socket.settimeout(remaining)

    def _receive(self, count, deadline):
        result = bytearray()
        while len(result) < count:
            self._remaining(deadline)
            part = self._socket.recv(count - len(result))
            if not part:
                raise EOFError
            result.extend(part)
        return bytes(result)

    def _exchange(self, request, response_limit):
        deadline = time.monotonic() + self._timeout
        # sendall has one total socket timeout; the same deadline also covers
        # every receive fragment, including the frame header.
        self._remaining(deadline)
        self._socket.sendall(_HEADER.pack(len(request)) + request)
        length, = _HEADER.unpack(self._receive(_HEADER.size, deadline))
        if not 1 <= length <= response_limit:
            raise ValueError("Invalid response size")
        response = self._receive(length, deadline)
        if response[:1] != b"K":
            raise ValueError("Worker failure")
        return response

    def _validate(self, offset, count):
        if type(offset) is not int or type(count) is not int:
            raise ValueError("Offset and length must be integers")
        if offset < 0 or count <= 0 or offset + count > self._size or count > self._maximum:
            raise ValueError("I/O exceeds allocation or request maximum")
        if self._closed:
            raise BackendError("Remote storage is closed or invalidated")

    def _operation(self, request, response_length):
        try:
            response = self._exchange(request, response_length)
            if len(response) != response_length:
                raise ValueError("Malformed response")
            return response[1:]
        except Exception:
            self._stop()
            raise BackendError("Remote operation failed; storage invalidated") from None

    def read(self, offset, count):
        with self._lock:
            self._validate(offset, count)
            return self._operation(b"R" + _IO.pack(offset, count), count + 1)

    def write(self, offset, data):
        if not isinstance(data, bytes):
            raise ValueError("Writes require immutable bytes")
        with self._lock:
            self._validate(offset, len(data))
            self._operation(b"W" + _IO.pack(offset, len(data)) + data, 1)

    def _stop(self):
        self._closed = True
        self._socket.close()
        if self._process is not None:
            try:
                if self._process.poll() is None:
                    self._process.kill()
                self._process.wait(timeout=0.25)
            except (OSError, subprocess.TimeoutExpired):
                # Keep the process reference; close can retry bounded reaping.
                raise BackendError("Remote worker cleanup failed") from None

    def close(self):
        with self._lock:
            self._stop()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
