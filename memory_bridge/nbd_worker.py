"""Private subprocess protocol: blocking libnbd never runs in the borrower."""

import json
import socket
import sys

from .remote_nbd import _HEADER, _IO, _MAX_REQUEST


def _receive(sock, count):
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise EOFError
        data.extend(part)
    return bytes(data)


def _frame(sock, maximum):
    length, = _HEADER.unpack(_receive(sock, _HEADER.size))
    if not 1 <= length <= maximum:
        raise ValueError("Invalid request size")
    return _receive(sock, length)


def _send(sock, data):
    sock.sendall(_HEADER.pack(len(data)) + data)


def serve(sock):
    try:
        request = _frame(sock, 4096)
        if request[:1] != b"H":
            raise ValueError("Expected handshake")
        config = json.loads(request[1:])
        maximum = config["maximum"]
        size = config["size"]
        if type(maximum) is not int or not 4096 <= maximum <= _MAX_REQUEST:
            raise ValueError("Invalid maximum")
        if type(size) is not int or size <= 0 or size % 4096:
            raise ValueError("Invalid size")
        lock_memory = config.get("lock_memory", False)
        if type(lock_memory) is not bool:
            raise ValueError("Invalid lock_memory")
        protections = 0
        if lock_memory:
            # Serving swap: keep this worker resident before any connection
            # exists. Failure aborts the handshake instead of running unlocked.
            from .memlock import lock_all_memory, set_io_flusher
            lock_all_memory()
            protections |= 1
            if set_io_flusher():
                protections |= 2
        import nbd
        handle = nbd.NBD()
        handle.set_tls(nbd.TLS_REQUIRE)
        handle.set_tls_username("borrower")
        handle.set_tls_psk_file(config["key"])
        handle.connect_tcp(config["host"], str(config["port"]))
        if handle.get_size() != size:
            raise ValueError("Unexpected export size")
        # Report which protections were obtained; the parent publishes them.
        _send(sock, b"K" + bytes([protections]))
        while True:
            request = _frame(sock, maximum + 1 + _IO.size)
            operation = request[:1]
            if len(request) < 1 + _IO.size:
                raise ValueError("Short command")
            offset, count = _IO.unpack(request[1:1 + _IO.size])
            if count <= 0 or count > maximum or offset + count > size:
                raise ValueError("Invalid range")
            if operation == b"R" and len(request) == 1 + _IO.size:
                data = handle.pread(count, offset)
                # libnbd's Python binding returns bytearray buffers.
                if not isinstance(data, (bytes, bytearray)) or len(data) != count:
                    raise ValueError("Malformed read")
                _send(sock, b"K" + bytes(data))
            elif operation == b"W" and len(request) == 1 + _IO.size + count:
                handle.pwrite(request[1 + _IO.size:], offset)
                _send(sock, b"K")
            else:
                raise ValueError("Invalid command")
    except Exception:
        try:
            _send(sock, b"E")
        except Exception:
            pass
        # No exception text, tracebacks, or keys cross the process boundary.
        # Process exit closes libnbd without a potentially blocking shutdown.


def main():
    with socket.socket(fileno=int(sys.argv[1])) as sock:
        serve(sock)


if __name__ == "__main__":
    main()
