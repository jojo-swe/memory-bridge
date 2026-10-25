"""Building blocks for swap experiments in a DISPOSABLE Linux VM.

Everything here changes system state (swap devices, loop devices, FUSE
mounts, cgroups, nftables). It refuses to run unless the operator explicitly
declares the machine disposable, and it only ever tears down objects it
created itself, tracked by path or PID. It never pattern-kills processes.

Design notes (see docs/SWAP-EXPERIMENT-KRITERIER.md):

* Memory pressure: a memory cgroup limit for the workload. This is real
  reclaim and swap-out of the workload's pages, not RLIMIT_AS.
* Local swap ("L"): a swap file on the same filesystem as the bridge's local
  copy. The kernel writes it directly (no page cache).
* Bridge swap ("P"/"R"): loop (direct I/O) -> nbdfuse file -> NBD over a
  private Unix socket -> memory-bridge export (O_DIRECT local copy, optional
  verified donor cache). With a kernel that has the nbd module, nbd-client
  would replace loop + nbdfuse; that path is not exercised here.
"""

import json
import os
from pathlib import Path
import random
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

REPO = Path(__file__).resolve().parents[2]
PYTHON = os.environ.get("MB_PYTHON", "/usr/bin/python3.12")
CLK_TCK = os.sysconf("SC_CLK_TCK")


class SafetyError(RuntimeError):
    """The environment does not look like an acknowledged disposable VM."""


# --------------------------------------------------------------------------- utilities

def sh(command, *, check=True, timeout=120, capture=True):
    """Run a command (list form only, never a shell string)."""
    result = subprocess.run(command, check=False, timeout=timeout, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if check and result.returncode != 0:
        raise RuntimeError(f"{command!r} failed ({result.returncode}): "
                           f"{(result.stderr or '').strip()[-500:]}")
    return result


def read_text(path, default=None):
    try:
        return Path(path).read_text()
    except OSError:
        return default


def mounted(path):
    """True if path is a mount point; works for dead FUSE mounts (ENOTCONN)."""
    target = str(Path(path).absolute())
    for line in (read_text("/proc/mounts", "") or "").splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[1].replace("\\040", " ") == target:
            return True
    return False


def active_swaps():
    lines = (read_text("/proc/swaps", "") or "").splitlines()[1:]
    return [line.split()[0] for line in lines if line.strip()]


def meminfo():
    values = {}
    for line in (read_text("/proc/meminfo", "") or "").splitlines():
        key, _, rest = line.partition(":")
        values[key] = int(rest.split()[0])  # kB
    return values


def vmstat(keys=("pswpin", "pswpout", "pgmajfault", "pgfault", "oom_kill")):
    values = {}
    for line in (read_text("/proc/vmstat", "") or "").splitlines():
        name, _, value = line.partition(" ")
        if name in keys:
            values[name] = int(value)
    return values


def psi_memory():
    report = {}
    for line in (read_text("/proc/pressure/memory", "") or "").splitlines():
        kind, *fields = line.split()
        for field in fields:
            name, _, value = field.partition("=")
            report[f"{kind}_{name}"] = float(value)
    return report


def process_status(pid):
    """VmRSS/VmLck/VmSwap in kB, or None if the process is gone."""
    text = read_text(f"/proc/{pid}/status")
    if text is None:
        return None
    values = {}
    for line in text.splitlines():
        if line.startswith(("VmRSS:", "VmLck:", "VmSwap:", "State:")):
            key, _, rest = line.partition(":")
            values[key] = rest.split()[0] if key == "State" else int(rest.split()[0])
    return values


def cpu_seconds(pid):
    text = read_text(f"/proc/{pid}/stat")
    if text is None:
        return None
    fields = text.rpartition(")")[2].split()
    # utime and stime are fields 14 and 15 (1-based) of /proc/PID/stat.
    return (int(fields[11]) + int(fields[12])) / CLK_TCK


def drop_caches():
    os.sync()
    Path("/proc/sys/vm/drop_caches").write_text("3\n")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_for(predicate, timeout, interval=0.05, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {what}")


def require_disposable_vm(acknowledged):
    """Refuse to touch swap unless the operator declared the VM disposable."""
    if not acknowledged or os.environ.get("MB_DISPOSABLE_VM") != "1":
        raise SafetyError("Refusing: pass --i-am-in-a-disposable-vm and set MB_DISPOSABLE_VM=1. "
                          "Never run this on a daily-use computer.")
    if os.geteuid() != 0:
        raise SafetyError("Root is required for swap, loop and cgroup setup")
    existing = active_swaps()
    if existing:
        raise SafetyError(f"Refusing: swap already active ({existing}); "
                          "this harness only manages swap it creates itself")
    zswap = (read_text("/sys/module/zswap/parameters/enabled", "N") or "N").strip()
    if zswap not in ("N", "0"):
        raise SafetyError("zswap is enabled; disable it so no variant gets compression")


# --------------------------------------------------------------------------- cgroups

class Cgroup:
    """Memory cgroup for the workload; v1 and v2 supported."""

    def __init__(self, name, limit_bytes, swappiness=60):
        self.limit = limit_bytes
        v1 = Path("/sys/fs/cgroup/memory")
        if (v1 / "memory.limit_in_bytes").exists():
            self.version = 1
            self.path = v1 / name
        elif Path("/sys/fs/cgroup/cgroup.controllers").exists():
            self.version = 2
            self.path = Path("/sys/fs/cgroup") / name
        else:
            raise SafetyError("No usable memory cgroup controller")
        self.path.mkdir()
        try:
            if self.version == 1:
                (self.path / "memory.limit_in_bytes").write_text(str(limit_bytes))
                (self.path / "memory.swappiness").write_text(str(swappiness))
            else:
                (self.path / "memory.max").write_text(str(limit_bytes))
                (self.path / "memory.swap.max").write_text("max")
        except BaseException:
            self.path.rmdir()
            raise
        self.procs = self.path / "cgroup.procs"

    def wrap(self, command):
        # Join the cgroup in the child itself before exec; avoids preexec_fn
        # (unsafe with threads) and races where the workload starts outside.
        return ["/bin/sh", "-c", 'echo $$ > "$0" && exec "$@"', str(self.procs), *command]

    def _value(self, name, default=0):
        text = read_text(self.path / name)
        return int(text.split()[0]) if text and text.split() else default

    def usage(self):
        return self._value("memory.usage_in_bytes" if self.version == 1 else "memory.current")

    def stats(self):
        report = {"version": self.version, "limit_bytes": self.limit}
        if self.version == 1:
            report["peak_bytes"] = self._value("memory.max_usage_in_bytes")
            report["failcnt"] = self._value("memory.failcnt")
            control = read_text(self.path / "memory.oom_control", "") or ""
            for line in control.splitlines():
                key, _, value = line.partition(" ")
                if key == "oom_kill":
                    report["oom_kills"] = int(value)
        else:
            report["peak_bytes"] = self._value("memory.peak")
            for line in (read_text(self.path / "memory.events", "") or "").splitlines():
                key, _, value = line.partition(" ")
                report[f"events_{key}"] = int(value)
            report["oom_kills"] = report.get("events_oom_kill", 0)
        stat = {}
        for line in (read_text(self.path / "memory.stat", "") or "").splitlines():
            key, _, value = line.partition(" ")
            if key in ("cache", "rss", "swap", "file", "anon", "pswpin", "pswpout",
                       "total_swap", "pgmajfault", "workingset_refault_anon"):
                stat[key] = int(value)
        report["memory_stat"] = stat
        return report

    def pids(self):
        return [int(p) for p in (read_text(self.procs, "") or "").split()]

    def kill_all(self, timeout=30):
        if self.version == 2 and (self.path / "cgroup.kill").exists():
            (self.path / "cgroup.kill").write_text("1")
        deadline = time.monotonic() + timeout
        while self.pids():
            for pid in self.pids():
                try:
                    os.kill(pid, signal.SIGCONT)
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if time.monotonic() > deadline:
                return False
            time.sleep(0.1)
        return True

    def destroy(self):
        killed = self.kill_all()
        for _ in range(50):
            try:
                self.path.rmdir()
                return killed
            except OSError:
                time.sleep(0.1)
        return False


# --------------------------------------------------------------------------- processes

class Managed:
    """A child process tracked by PID, with its output in log files."""

    def __init__(self, name, command, log_dir, **kwargs):
        self.name = name
        self.command = command
        self.stdout_path = Path(log_dir) / f"{name}.out"
        self.stderr_path = Path(log_dir) / f"{name}.err"
        self._stdout = open(self.stdout_path, "wb")
        self._stderr = open(self.stderr_path, "wb")
        self.process = subprocess.Popen(command, stdout=self._stdout, stderr=self._stderr,
                                        stdin=subprocess.DEVNULL, **kwargs)
        self.pid = self.process.pid
        self.started = time.monotonic()

    def alive(self):
        return self.process.poll() is None

    def signal(self, number):
        if self.alive():
            os.kill(self.pid, number)

    def stop(self, timeout=10):
        if self.alive():
            try:
                os.kill(self.pid, signal.SIGCONT)  # A stopped process cannot exit.
            except ProcessLookupError:
                pass
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=timeout)
        self._stdout.close()
        self._stderr.close()
        return self.process.returncode


class Donor:
    """Single-use TLS donor; optionally as an unprivileged user with CAP_IPC_LOCK."""

    def __init__(self, workdir, size_mib, key, user=None, delay_ms=0):
        self.port = free_port()
        donor_key = Path(workdir) / "donor.psk"
        shutil.copyfile(key, donor_key)
        prefix = []
        if user:
            sh(["chown", f"{user}:{user}", str(donor_key)])
            prefix = ["setpriv", "--reuid", user, "--regid", user, "--init-groups",
                      "--inh-caps", "+ipc_lock", "--ambient-caps", "+ipc_lock", "--"]
        os.chmod(donor_key, 0o600)
        command = [*prefix, PYTHON, str(REPO / "bridge.py"), "donor", "--key", str(donor_key),
                   "--size-mib", str(size_mib), "--port", str(self.port)]
        if delay_ms:
            command += ["--lab-delay-ms", str(delay_ms)]
        self.proc = Managed("donor", command, workdir, cwd=REPO)

        def listening():
            if not self.proc.alive():
                raise RuntimeError("donor exited: " + read_text(self.proc.stderr_path, "")[-1000:])
            with socket.socket() as client:
                return client.connect_ex(("127.0.0.1", self.port)) == 0
        wait_for(listening, 60, what="donor port")
        self.nbdkit_pid = self._find_nbdkit()

    def _find_nbdkit(self):
        # bridge.py execs nbdkit (same PID); setpriv also execs. Verify.
        comm = (read_text(f"/proc/{self.proc.pid}/comm", "") or "").strip()
        return self.proc.pid if comm == "nbdkit" else None

    def stop(self):
        return self.proc.stop()


class Export:
    """The memory-bridge block export (nbdkit + block_export plugin)."""

    def __init__(self, workdir, size_mib, local_dir, donor=None, key=None, timeout=2.0,
                 wrap=None, transfer_kib=128, lock_memory=True):
        self.run_dir = Path(workdir) / "run"
        self.run_dir.mkdir(mode=0o700)
        self.socket = self.run_dir / "export.sock"
        self.stats_path = Path(workdir) / "export-stats.json"
        command = [PYTHON, "-m", "memory_bridge", "export", "--socket", str(self.socket),
                   "--size-mib", str(size_mib), "--local-dir", str(local_dir),
                   "--timeout", str(timeout), "--transfer-kib", str(transfer_kib),
                   "--stats", str(self.stats_path)]
        if not lock_memory:
            command.append("--no-lock-memory")
        if donor is not None:
            command += ["--donor-host", "127.0.0.1", "--donor-port", str(donor.port),
                        "--key", str(key)]
        if wrap is not None:
            command = wrap(command)
        self.proc = Managed("export", command, workdir, cwd=REPO)

        def ready():
            if not self.proc.alive():
                raise RuntimeError("export exited: " + read_text(self.proc.stderr_path, "")[-2000:])
            return self.socket.exists() and self.stats() is not None
        wait_for(ready, 60, what="export socket")

    def stats(self):
        try:
            return json.loads(self.stats_path.read_text())
        except (OSError, ValueError):
            return None

    def worker_pid(self):
        report = self.stats()
        return report.get("worker_pid") if report else None

    def stop(self):
        return self.proc.stop()


# --------------------------------------------------------------------------- swap backends

class NoSwap:
    kind = "none"
    device = None

    def components(self):
        return {}

    def teardown(self):
        return []


class LocalSwapFile:
    kind = "local-file"

    def __init__(self, directory, size_mib):
        self.path = Path(directory) / "swapfile"
        sh(["fallocate", "-l", f"{size_mib}M", str(self.path)])
        os.chmod(self.path, 0o600)
        sh(["mkswap", str(self.path)])
        sh(["swapon", str(self.path)])
        self.device = str(self.path)

    def components(self):
        return {}

    def teardown(self):
        errors = []
        if str(self.path) in active_swaps():
            result = sh(["swapoff", str(self.path)], check=False, timeout=900)
            if result.returncode:
                errors.append("swapoff: " + result.stderr.strip())
        try:
            self.path.unlink()
        except OSError as error:
            errors.append(f"unlink: {error}")
        return errors


class BridgeSwap:
    """loop(direct-io) -> nbdfuse -> export. Tracks every object it creates."""

    kind = "bridge"

    def __init__(self, workdir, export, wrap=None):
        self.export = export
        self.mount = Path(workdir) / "fuse"
        self.mount.mkdir()
        self.file = self.mount / "swapdev"
        self.loop = None
        self.device = None
        command = ["nbdfuse", str(self.file), "--unix", str(export.socket)]
        if wrap is not None:
            command = wrap(command)
        self.fuse = Managed("nbdfuse", command, workdir)
        try:
            wait_for(lambda: self.file.exists() or not self.fuse.alive(), 30, what="nbdfuse file")
            if not self.fuse.alive():
                raise RuntimeError("nbdfuse exited: " + read_text(self.fuse.stderr_path, "")[-1000:])
            self.loop = sh(["losetup", "--direct-io=on", "--sector-size", "4096", "--show",
                            "-f", str(self.file)]).stdout.strip()
            info = sh(["losetup", "-l", "-n", "-O", "DIO", self.loop]).stdout.strip()
            if info != "1":
                raise RuntimeError(f"loop device without direct I/O: {info!r}")
            sh(["mkswap", self.loop])
            sh(["swapon", self.loop])
            self.device = self.loop
        except BaseException:
            self.teardown()
            raise

    def components(self):
        found = {"export": self.export.proc.pid, "nbdfuse": self.fuse.pid}
        worker = self.export.worker_pid()
        if worker:
            found["worker"] = worker
        return found

    def teardown(self):
        errors = []
        if self.loop and self.loop in active_swaps():
            try:
                result = sh(["swapoff", self.loop], check=False, timeout=900)
                if result.returncode:
                    errors.append("swapoff: " + result.stderr.strip())
            except subprocess.TimeoutExpired:
                errors.append("swapoff: timed out after 900 s")
        if self.loop:
            result = sh(["losetup", "-d", self.loop], check=False)
            if result.returncode:
                errors.append("losetup -d: " + result.stderr.strip())
        # A dead nbdfuse leaves a mount that raises ENOTCONN on stat; consult
        # /proc/mounts instead of touching the mount point.
        if mounted(self.mount):
            result = sh(["fusermount3", "-u", str(self.mount)], check=False)
            if result.returncode:
                # Lazy unmount detaches a mount whose server already died.
                result = sh(["fusermount3", "-uz", str(self.mount)], check=False)
                if result.returncode:
                    errors.append("fusermount3: " + result.stderr.strip())
        self.fuse.stop()
        self.export.stop()
        return errors


# --------------------------------------------------------------------------- sampling

class Sampler(threading.Thread):
    """Time-aligned samples: cgroup, components, system memory, swap I/O, PSI."""

    def __init__(self, cgroup, components, path, interval=0.25):
        super().__init__(daemon=True)
        self.cgroup = cgroup
        self.components = components  # callable -> {name: pid}
        self.path = Path(path)
        self.interval = interval
        self.halt = threading.Event()
        self.peaks = {"cgroup_bytes": 0, "bridge_rss_kib": 0, "donor_rss_kib": 0,
                      "system_used_kib": 0}
        self.count = 0
        self.t0 = time.monotonic()

    def run(self):
        with open(self.path, "w") as output:
            while not self.halt.is_set():
                sample = self.sample()
                output.write(json.dumps(sample, separators=(",", ":")) + "\n")
                self.count += 1
                self.halt.wait(self.interval)

    def sample(self):
        info = meminfo()
        sample = {"t": round(time.monotonic() - self.t0, 3), "cgroup_bytes": self.cgroup.usage(),
                  "mem_free_kib": info.get("MemFree"), "mem_available_kib": info.get("MemAvailable"),
                  "cached_kib": info.get("Cached"), "swap_used_kib":
                  info.get("SwapTotal", 0) - info.get("SwapFree", 0),
                  "shmem_kib": info.get("Shmem"), **vmstat(), "psi": psi_memory(), "rss_kib": {}}
        for name, pid in self.components().items():
            status = process_status(pid)
            if status is not None:
                sample["rss_kib"][name] = status.get("VmRSS", 0)
        bridge = sum(v for k, v in sample["rss_kib"].items() if k in ("export", "nbdfuse", "worker"))
        self.peaks["bridge_rss_kib"] = max(self.peaks["bridge_rss_kib"], bridge)
        self.peaks["donor_rss_kib"] = max(self.peaks["donor_rss_kib"], sample["rss_kib"].get("donor", 0))
        self.peaks["cgroup_bytes"] = max(self.peaks["cgroup_bytes"], sample["cgroup_bytes"])
        used = info.get("MemTotal", 0) - info.get("MemAvailable", 0)
        self.peaks["system_used_kib"] = max(self.peaks["system_used_kib"], used)
        return sample

    def stop(self):
        self.halt.set()
        self.join(timeout=10)
        return dict(self.peaks, samples=self.count)


# --------------------------------------------------------------------------- faults

class NetworkBlackhole:
    """Drop TCP packets to/from one local port with a dedicated nftables table."""

    TABLE = "mb_fault"

    def __init__(self, port):
        self.port = port
        self.active = False

    def apply(self):
        rules = (f"table inet {self.TABLE} {{\n"
                 f" chain out {{ type filter hook output priority 0; tcp dport {self.port} drop; "
                 f"tcp sport {self.port} drop; }}\n"
                 f" chain in {{ type filter hook input priority 0; tcp dport {self.port} drop; "
                 f"tcp sport {self.port} drop; }}\n}}\n")
        subprocess.run(["nft", "-f", "-"], input=rules, text=True, check=True, timeout=30)
        self.active = True

    def remove(self):
        if self.active:
            sh(["nft", "delete", "table", "inet", self.TABLE], check=False)
            self.active = False


def wait_for_marker(path, marker, timeout, alive):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker in (read_text(path, "") or ""):
            return time.monotonic()
        if not alive():
            return None
        time.sleep(0.05)
    return None


def shuffled(items, seed):
    order = list(items)
    random.Random(seed).shuffle(order)
    return order


def environment_report():
    def out(command):
        try:
            return sh(command, check=False, timeout=30).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
    cpu = next((line.split(":", 1)[1].strip() for line in
                (read_text("/proc/cpuinfo", "") or "").splitlines() if line.startswith("model name")), None)
    return {
        "kernel": out(["uname", "-a"]), "os_release": read_text("/etc/os-release"),
        "cpu_model": cpu, "nproc": os.cpu_count(), "mem_total_kib": meminfo().get("MemTotal"),
        "python": sys.version, "python_for_bridge": PYTHON,
        "nbdkit": out(["nbdkit", "--version"]), "nbdfuse": out(["nbdfuse", "--version"]),
        "xz": out(["xz", "--version"]), "gcc": out(["gcc", "--version"]),
        "libnbd_python": out([PYTHON, "-c", "import nbd; print(nbd.NBD().get_version())"]),
        "git_commit": out(["git", "-C", str(REPO), "rev-parse", "HEAD"]),
        "git_dirty": bool(out(["git", "-C", str(REPO), "status", "--porcelain"])),
        "block_devices": out(["lsblk", "-o", "NAME,SIZE,ROTA,TYPE,MOUNTPOINTS"]),
        "filesystem": out(["df", "-hT", str(REPO)]),
        "vm": {name: (read_text(f"/proc/sys/vm/{name}", "") or "").strip()
               for name in ("swappiness", "page-cluster", "overcommit_memory", "min_free_kbytes")},
        "zswap_enabled": (read_text("/sys/module/zswap/parameters/enabled", "") or "").strip(),
        "thp": (read_text("/sys/kernel/mm/transparent_hugepage/enabled", "") or "").strip(),
        "cgroup_v1_memory": Path("/sys/fs/cgroup/memory/memory.limit_in_bytes").exists(),
    }
