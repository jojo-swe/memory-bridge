"""Attribute remote read latency hop by hop on loopback (4 KiB and 32 KiB).

One-off microbenchmark; run with the system Python that has libnbd:
    /usr/bin/python3 docs/experiments/latency_probe.py /var/tmp
Output is saved next to it as microbench-latency.txt. Requires nbdkit.
"""
import os, secrets, socket, subprocess, sys, tempfile, time, statistics
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from memory_bridge.remote_nbd import RemoteNBDStorage
from memory_bridge.direct_file import DirectFileStorage
from memory_bridge.resilient import ResilientStorage
import nbd

ROOT = str(__import__("pathlib").Path(__file__).resolve().parents[2])
SIZE = 64 * 1024 * 1024

def wait(port):
    for _ in range(200):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise SystemExit("donor did not start")

def bench(label, fn, n=2000, size=4096):
    lat = []
    for i in range(n):
        off = (i * 7919 * 4096) % (SIZE - size)
        t = time.perf_counter(); fn(off, size); lat.append(time.perf_counter() - t)
    lat.sort()
    print(f"{label:40s} n={n} size={size:6d} median={lat[n//2]*1e6:7.1f}us p99={lat[int(n*.99)]*1e6:7.1f}us")

with tempfile.TemporaryDirectory() as d:
    key = os.path.join(d, "s.psk")
    fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("borrower:" + secrets.token_hex(32) + "\n")
    port = 10911
    donor = subprocess.Popen([sys.executable, "-m", "memory_bridge.launcher", "donor", "--key", key,
                              "--port", str(port), "--size-mib", "64"], cwd=ROOT,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait(port)
    # (a) libnbd in-process, TLS, against the project's Python plugin donor
    h = nbd.NBD(); h.set_tls(nbd.TLS_REQUIRE); h.set_tls_username("borrower"); h.set_tls_psk_file(key)
    h.connect_tcp("127.0.0.1", str(port))
    buf = bytearray(32768)
    for size in (4096, 32768):
        bench("libnbd direct -> python plugin donor", lambda o, s: h.pread(s, o), size=size)
    h.close(); donor.kill(); donor.wait()

    # (b) reference lower bound: nbdkit memory plugin (no project core, no python)
    donor = subprocess.Popen(["nbdkit", "--foreground", "--threads=1", "--tls=require",
                              f"--tls-psk={key}", "--ipaddr=127.0.0.1", f"--port={port}",
                              "memory", "64M"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait(port)
    h = nbd.NBD(); h.set_tls(nbd.TLS_REQUIRE); h.set_tls_username("borrower"); h.set_tls_psk_file(key)
    h.connect_tcp("127.0.0.1", str(port))
    for size in (4096, 32768):
        bench("libnbd direct -> nbdkit memory plugin", lambda o, s: h.pread(s, o), size=size)
    h.close(); donor.kill(); donor.wait()

    # (c) full borrower stack against the project donor
    donor = subprocess.Popen([sys.executable, "-m", "memory_bridge.launcher", "donor", "--key", key,
                              "--port", str(port), "--size-mib", "64"], cwd=ROOT,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait(port)
    remote = RemoteNBDStorage("127.0.0.1", port, key, SIZE, timeout=2)
    for size in (4096, 32768):
        bench("RemoteNBDStorage (worker hop)", remote.read, size=size)
    local = DirectFileStorage(SIZE, sys.argv[1] if len(sys.argv) > 1 else "/var/tmp")
    res = ResilientStorage(local, remote, transfer_bytes=32768)
    # populate cache
    for off in range(0, SIZE, 32768):
        res.write(off, os.urandom(32768))
    for size in (4096, 32768):
        bench("ResilientStorage cached read (+sha256)", res.read, size=size)
    bench("DirectFileStorage O_DIRECT read", local.read, size=4096)
    bench("DirectFileStorage O_DIRECT read", local.read, size=32768)
    bench("DirectFileStorage O_DIRECT write", lambda o, s: local.write(o, bytes(s)), size=4096)
    bench("ResilientStorage write (local+mirror)", lambda o, s: res.write(o, bytes(s)), size=4096)
    bench("ResilientStorage write (local+mirror)", lambda o, s: res.write(o, bytes(s)), size=32768)
    res.close(); donor.kill(); donor.wait()

# Pure socketpair request/response round trip between two Python processes,
# with the same framing as remote_nbd, to separate scheduling cost from logic.
import struct
_H = struct.Struct("!I")
_a, _b = socket.socketpair()
_pid = os.fork()
if _pid == 0:
    _a.close()
    while True:
        hdr = _b.recv(4)
        if not hdr:
            os._exit(0)
        n, = _H.unpack(hdr)
        got = bytearray()
        while len(got) < n:
            got += _b.recv(n - len(got))
        resp = b"K" + bytes(4096)
        _b.sendall(_H.pack(len(resp)) + resp)
_b.close()

def _roundtrip():
    _a.sendall(_H.pack(17) + b"R" + bytes(16))
    n, = _H.unpack(_a.recv(4))
    got = bytearray()
    while len(got) < n:
        got += _a.recv(n - len(got))

for _ in range(500):
    _roundtrip()
lat = []
for _ in range(3000):
    t = time.perf_counter(); _roundtrip(); lat.append(time.perf_counter() - t)
lat.sort()
print(f"{'socketpair echo, two Python processes':40s} n=3000 size=  4096 median={lat[1500]*1e6:7.1f}us p99={lat[2970]*1e6:7.1f}us")
_a.close()
