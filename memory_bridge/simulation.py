"""Explicit test backend. Unlocked memory must never serve production exports."""


class SimulatedMemory:
    """Deterministic in-process backend with opt-in failure injection."""

    def __init__(self, size):
        self.size = size
        self.data = bytearray(size)
        self.closed = False
        self.fail_reads = False
        self.fail_writes = False
        self.fail_close = False

    def read(self, offset, count):
        if self.closed or self.fail_reads:
            raise OSError("Injected donor failure")
        return bytes(self.data[offset:offset + count])

    def write(self, offset, data):
        if self.closed or self.fail_writes:
            raise OSError("Injected donor failure")
        self.data[offset:offset + len(data)] = data

    def close(self):
        if self.fail_close:
            raise OSError("Injected cleanup failure")
        if not self.closed:
            self.data[:] = bytes(self.size)
            self.data.clear()
            self.closed = True
