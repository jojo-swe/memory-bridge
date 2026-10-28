#!/usr/bin/env python3
"""Reproducible swap experiment under cgroup memory pressure, disposable VM only.

Compares, with identical RAM budget, disk, workload and seed:
  noswap      no swap at all (shows the workload really does not fit)
  local-swap  loop device (direct-io) over a preallocated file on the disk
  fuse-local  loop device over the FUSE block export with O_DIRECT local file
  bridge      loop device over the FUSE block export with ResilientStorage
              (O_DIRECT local copy + TLS/NBD donor on loopback)
  more-ram    no swap, RAM budget raised by the swap size (reference)

Memory pressure is created with a cgroup v1 memory limit around the workload
only; the export daemon, donor and NBD worker stay outside that cgroup. The
lab refuses to run unless --disposable-vm is given, it runs as root, no swap
is active beforehand, and loop/FUSE/cgroup support is present. Everything it
creates is torn down before the report, also on failure. It never touches
existing swap, disks, partitions or firewall rules.

Optional fault injection during the bridge arm: freeze the donor (SIGSTOP),
kill it (SIGKILL) or destroy the borrower's TCP connection (ss -K), after the
workload's random phase has started.
"""

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

from memory_bridge.storage import memlock_limit_applies

ROOT = Path(__file__).resolve().parent.parent
ARMS = ("noswap", "local-swap", "fuse-local", "bridge", "bridge-sync", "more-ram")
CGROUP_ROOT = Path("/sys/fs/cgroup/memory")
MIB = 1024 * 1024


class LabError(RuntimeError):
    pass


def run(command, **kwargs):
    kwargs.setdefault("check", True)
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run(command, **kwargs)


def read_text(path):
    return Path(path).read_text()


def vmstat_swap():
    counters = {}
    for line in read_text("/proc/vmstat").splitlines():
        name, _, value = line.partition(" ")
        if name in ("pswpin", "pswpout", "pgmajfault"):
            counters[name] = int(value)
    return counters


def psi_memory():
    try:
        text = read_text("/proc/pressure/memory")
    except OSError:
        return None
    result = {}
    for line in text.splitlines():
        kind, *fields = line.split()
        for field in fields:
            key, _, value = field.partition("=")
            if key == "total":
                result[kind + "_total_us"] = int(value)
    return result


def process_memory(pid):
    try:
        status = read_text(f"/proc/{pid}/status")
    except OSError:
        return None
    result = {}
    for line in status.splitlines():
        if line.startswith(("VmRSS:", "VmHWM:", "VmLck:")):
            key, value = line.split(":", 1)
            result[key.lower() + "_kib"] = int(value.strip().split()[0])
    return result


def find_pids(fragment):
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            cmdline = Path(f"/proc/{entry}/cmdline").read_bytes()
        except OSError:
            continue
        if fragment.encode() in cmdline:
            pids.append(int(entry))
    return pids


class Cgroup:
    """One v1 memory cgroup for the workload; memsw limits swap usage too."""

    def __init__(self, name, limit_bytes, memsw_bytes):
        self.path = CGROUP_ROOT / name
        self.path.mkdir()
        try:
            (self.path / "memory.limit_in_bytes").write_text(str(limit_bytes))
            (self.path / "memory.memsw.limit_in_bytes").write_text(str(memsw_bytes))
            (self.path / "memory.swappiness").write_text("60")
            applied = int(read_text(self.path / "memory.limit_in_bytes"))
            if applied != limit_bytes:
                raise LabError("cgroup limit was not applied")
        except BaseException:
            self.remove()
            raise

    def value(self, name):
        return int(read_text(self.path / name).split()[0])

    def snapshot(self):
        oom = dict(line.split() for line in read_text(self.path / "memory.oom_control").splitlines())
        stat = dict((k, int(v)) for k, v in
                    (line.split() for line in read_text(self.path / "memory.stat").splitlines()))
        return {"limit_bytes": self.value("memory.limit_in_bytes"),
                "memsw_limit_bytes": self.value("memory.memsw.limit_in_bytes"),
                "max_usage_bytes": self.value("memory.max_usage_in_bytes"),
                "memsw_max_usage_bytes": self.value("memory.memsw.max_usage_in_bytes"),
                "failcnt": self.value("memory.failcnt"),
                "memsw_failcnt": self.value("memory.memsw.failcnt"),
                "oom_kill": int(oom.get("oom_kill", 0)),
                "stat_swap_bytes": stat.get("swap"), "stat_rss_bytes": stat.get("rss"),
                "stat_pgmajfault": stat.get("pgmajfault")}

    def attach_self(self):
        # Runs between fork and exec: use raw syscalls only, never Python
        # file objects whose locks another thread may hold at fork time.
        fd = os.open(self.path / "cgroup.procs", os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)

    def remove(self):
        for _ in range(50):
            try:
                self.path.rmdir()
                return
            except OSError:
                time.sleep(0.1)
        raise LabError(f"Could not remove cgroup {self.path}")


def reserve_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def wait_for_port(process, port, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LabError("Donor exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise LabError("Donor startup deadline exceeded")


def wait_for_ready(process, timeout=20):
    """The export prints one JSON line when the kernel accepted the mount."""
    result = {}

    def reader():
        for line in process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("ready"):
                result.update(message)
                return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if result:
        return result
    if process.poll() is not None:
        raise LabError("Block export exited during startup")
    process.kill()
    raise LabError("Block export startup deadline exceeded")


def stop_process(process, timeout=5):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout)


class Arm:
    """Set up and tear down the swap configuration of one arm, in one ExitStack."""

    def __init__(self, name, args, stack, workdir):
        self.name = name
        self.args = args
        self.stack = stack
        self.workdir = workdir
        self.swap_device = None
        self.export = None
        self.donor = None
        self.donor_port = None
        self.mountpoint = None
        self.notes = []
        self.setup()

    def _swapon(self, backing_file):
        device = run(["losetup", "--direct-io=on", "-f", "--show", str(backing_file)]).stdout.strip()
        self.stack.callback(self._detach_loop, device)
        run(["mkswap", device])
        run(["swapon", "-p", "100", device])
        self.stack.callback(self._swapoff, device)
        self.swap_device = device
        self.losetup = run(["losetup", "-l", device]).stdout

    def _swapoff(self, device):
        started = time.monotonic()
        try:
            run(["swapoff", device])
            self.notes.append({"swapoff_seconds": time.monotonic() - started})
        except subprocess.CalledProcessError as error:
            self.notes.append({"swapoff_failed": error.stderr[-300:]})
            raise LabError("swapoff failed; the VM is no longer in a clean state")

    @staticmethod
    def _detach_loop(device):
        run(["losetup", "-d", device])

    def _start_export(self, extra):
        self.mountpoint = Path(tempfile.mkdtemp(prefix=f"mount-{self.name}-", dir=self.workdir))
        self.stack.callback(self._remove_mountpoint)
        command = [sys.executable, "-m", "memory_bridge.block_export_cli", str(self.mountpoint),
                   "--size-mib", str(self.args.swap_mib), "--backing", "direct",
                   "--directory", str(self.args.directory), "--transfer-kib",
                   str(self.args.transfer_kib), "--io-timeout", str(self.args.io_timeout),
                   "--disk-latency-ms", str(self.args.disk_latency_ms)] + extra
        self.export_log = self.workdir / f"export-{self.mountpoint.name}.log"
        with open(self.export_log, "w") as log:
            self.export = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                           stderr=log, text=True)
        self.stack.callback(self._stop_export)
        wait_for_ready(self.export)
        self.stack.callback(self._unmount)

    def _remove_mountpoint(self):
        try:
            self.mountpoint.rmdir()
        except OSError as error:
            self.notes.append({"mountpoint_removal_failed": str(error)})

    def _unmount(self):
        tool = shutil.which("fusermount3") or shutil.which("fusermount")
        for attempt in range(20):
            result = subprocess.run([tool, "-u", str(self.mountpoint)], capture_output=True, text=True)
            if result.returncode == 0:
                break
            time.sleep(0.25)
        else:
            self.notes.append({"unmount_failed": result.stderr[-300:]})
        try:
            self.export.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def _stop_export(self):
        if self.export.poll() is None:
            stop_process(self.export)
        out, _ = self.export.communicate()
        self.export_output = out[-4000:]
        try:
            self.export_errors = self.export_log.read_text()[-2000:]
        except OSError:
            self.export_errors = None

    def _start_donor(self):
        keydir = Path(tempfile.mkdtemp(prefix="swap-lab-key-", dir=self.workdir))
        self.stack.callback(shutil.rmtree, keydir, True)
        self.key = keydir / "session.psk"
        fd = os.open(self.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write("borrower:" + secrets.token_hex(32) + "\n")
        self.donor_port = reserve_port()
        self.donor = subprocess.Popen([sys.executable, "-m", "memory_bridge.launcher", "donor",
                                       "--key", str(self.key), "--port", str(self.donor_port),
                                       "--size-mib", str(self.args.swap_mib)],
                                      cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.stack.callback(self._stop_donor)
        wait_for_port(self.donor, self.donor_port)

    def _stop_donor(self):
        if self.donor.poll() is None:
            try:
                self.donor.send_signal(signal.SIGCONT)  # a frozen donor must die too
            except OSError:
                pass
            self.donor.kill()
            self.donor.wait(timeout=5)

    def setup(self):
        swap_bytes = self.args.swap_mib * MIB
        if self.name == "local-swap":
            backing = self.workdir / "local-swap.img"
            with open(backing, "wb") as handle:
                os.posix_fallocate(handle.fileno(), 0, swap_bytes)
            self.stack.callback(backing.unlink)
            self._swapon(backing)
        elif self.name == "fuse-local":
            self._start_export([])
            self._swapon(self.mountpoint / "volume")
        elif self.name in ("bridge", "bridge-sync"):
            self._start_donor()
            extra = ["--key", str(self.key), "--port", str(self.donor_port)]
            if self.name == "bridge-sync":
                extra += ["--write-behind-mib", "0"]  # synchronous mirror, for attribution
            self._start_export(extra)
            self._swapon(self.mountpoint / "volume")
        elif self.name not in ("noswap", "more-ram"):
            raise LabError(f"Unknown arm {self.name}")

    def export_stats(self):
        if self.mountpoint is None:
            return None
        try:
            return json.loads(read_text(self.mountpoint / "stats.json"))
        except (OSError, ValueError):
            return None

    def bridge_pids(self):
        pids = {}
        if self.export is not None:
            pids["export"] = self.export.pid
        if self.donor is not None:
            pids["donor"] = self.donor.pid
            workers = find_pids("memory_bridge.nbd_worker")
            if workers:
                pids["nbd_worker"] = workers[0]
        return pids

    def inject_fault(self, kind):
        """Return the injection time; the donor is never resumed afterwards."""
        when = time.monotonic()
        if kind == "frozen":
            self.donor.send_signal(signal.SIGSTOP)
        elif kind == "dead":
            self.donor.kill()
        elif kind == "socket":
            run(["ss", "-K", "dst", "127.0.0.1", "dport", "=", str(self.donor_port)])
        else:
            raise LabError(f"Unknown fault {kind}")
        return when


class Monitor(threading.Thread):
    """Sample concurrent RSS of the bridge processes while the workload runs."""

    def __init__(self, pids, interval=0.5):
        super().__init__(daemon=True)
        self.pids = pids
        self.interval = interval
        self.stop = threading.Event()
        self.samples = 0
        self.peak_concurrent_rss_kib = 0
        self.peak_by_process = {}
        self.locked_by_process = {}  # VmLck peaks: the donor's locked budget, mlockall'd daemons

    def run(self):
        while not self.stop.is_set():
            total = 0  # bridge processes only; the workload is reported separately
            for name, pid in list(self.pids.items()):
                memory = process_memory(pid)
                if memory is None:
                    continue
                if name != "workload":
                    total += memory.get("vmrss_kib", 0)
                self.peak_by_process[name] = max(self.peak_by_process.get(name, 0),
                                                 memory.get("vmhwm_kib", 0))
                self.locked_by_process[name] = max(self.locked_by_process.get(name, 0),
                                                   memory.get("vmlck_kib", 0))
            self.peak_concurrent_rss_kib = max(self.peak_concurrent_rss_kib, total)
            self.samples += 1
            self.stop.wait(self.interval)


def run_workload(args, cgroup, arm, fault):
    command = [sys.executable, "-m", "memory_bridge.pressure_workload",
               "--dataset-mib", str(args.dataset_mib), "--rounds", str(args.rounds),
               "--pattern", args.pattern, "--seed", str(args.seed)]
    if args.deadline:
        command += ["--deadline", str(args.deadline)]
    before = {"vmstat": vmstat_swap(), "psi": psi_memory(), "cgroup": cgroup.snapshot(),
              "export": arm.export_stats()}
    pids = arm.bridge_pids()
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, preexec_fn=cgroup.attach_self)
    pids["workload"] = process.pid
    monitor = Monitor(pids)
    monitor.start()
    fault_record = None
    stderr_lines = []
    timed_out = False
    fault_done = threading.Event()
    # Exactly one reader per pipe: the thread owns stderr, communicate() stdout.
    stderr_pipe, process.stderr = process.stderr, None

    def read_stderr():
        with stderr_pipe:
            for line in stderr_pipe:
                stderr_lines.append(line.rstrip())
                if '"phase": "sweep"' in line:
                    fault_done.set()  # the random phase starts right after the sweep

    reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    if fault:
        # Inject once the random phase has started, after a short delay so
        # that the remote cache is in active use at the moment of failure.
        while not fault_done.wait(timeout=0.2):
            if process.poll() is not None or time.monotonic() - started > args.run_timeout:
                process.kill()
                raise LabError("Workload ended before the random phase; fault not injected")
        time.sleep(args.fault_delay)
        stats_before = arm.export_stats()
        injected = arm.inject_fault(fault)
        fallback_seconds = None
        poll_deadline = time.monotonic() + args.io_timeout * 4 + 5
        while time.monotonic() < poll_deadline:
            stats = arm.export_stats()
            storage = (stats or {}).get("storage") or {}
            if storage and not storage.get("remote_enabled", True):
                fallback_seconds = time.monotonic() - injected
                break
            time.sleep(0.05)
        fault_record = {"kind": fault, "injected_after_seconds": injected - started,
                        "fallback_observed_after_seconds": fallback_seconds,
                        "export_stats_before_fault": stats_before}
    try:
        stdout, _ = process.communicate(timeout=args.run_timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        stdout, _ = process.communicate()
    reader.join(timeout=5)
    wall = time.monotonic() - started
    monitor.stop.set()
    monitor.join(timeout=5)
    report = None
    if stdout.strip():
        try:
            report = json.loads(stdout.strip().splitlines()[-1])
        except ValueError:
            report = None
    after = {"vmstat": vmstat_swap(), "psi": psi_memory(), "cgroup": cgroup.snapshot(),
             "export": arm.export_stats()}
    result = {"arm": arm.name, "returncode": process.returncode, "timed_out": timed_out,
              "wall_seconds": wall, "workload": report, "workload_stderr": stderr_lines[-20:],
              "before": before, "after": after,
              "swap_io": {k: after["vmstat"][k] - before["vmstat"][k] for k in before["vmstat"]},
              "psi_delta_us": ({k: after["psi"][k] - before["psi"][k] for k in before["psi"]}
                               if before["psi"] and after["psi"] else None),
              "oom_killed": process.returncode == -signal.SIGKILL and not timed_out,
              "memory": {"workload_peak_rss_kib": monitor.peak_by_process.get("workload"),
                         "bridge_peak_by_process_kib": {k: v for k, v in monitor.peak_by_process.items()
                                                        if k != "workload"},
                         "bridge_peak_concurrent_rss_kib": monitor.peak_concurrent_rss_kib,
                         "locked_peak_by_process_kib": monitor.locked_by_process,
                         "monitor_samples": monitor.samples},
              "fault": fault_record}
    return result


def dmesg_tail(pattern_words):
    try:
        text = run(["dmesg", "-T"], check=False).stdout
    except OSError:
        return None
    lines = [line for line in text.splitlines() if any(w in line for w in pattern_words)]
    return lines[-20:]


def preflight(args):
    problems = []
    if os.geteuid() != 0:
        problems.append("must run as root (loop devices, swapon, cgroups)")
    if not args.disposable_vm:
        problems.append("pass --disposable-vm to confirm this is a throwaway machine")
    swaps = read_text("/proc/swaps").splitlines()[1:]
    if swaps:
        problems.append("swap is already active; this lab never touches existing swap")
    if not Path("/dev/loop-control").exists():
        problems.append("/dev/loop-control missing (CONFIG_BLK_DEV_LOOP)")
    if not Path("/dev/fuse").exists():
        problems.append("/dev/fuse missing")
    if not (CGROUP_ROOT / "memory.memsw.limit_in_bytes").exists():
        problems.append("cgroup v1 memory controller with swap accounting not mounted at " + str(CGROUP_ROOT))
    for tool in ("losetup", "mkswap", "swapon", "swapoff"):
        if not shutil.which(tool):
            problems.append(f"{tool} missing")
    if not (shutil.which("fusermount3") or shutil.which("fusermount")):
        problems.append("fusermount missing")
    if importlib.util.find_spec("fuse") is None:
        problems.append("fusepy missing for " + sys.executable)
    if "bridge" in args.arms or "bridge-sync" in args.arms:
        import resource
        soft, _ = resource.getrlimit(resource.RLIMIT_MEMLOCK)
        if memlock_limit_applies() and soft != resource.RLIM_INFINITY and args.swap_mib * MIB > soft:
            problems.append("donor size exceeds the memlock limit and CAP_IPC_LOCK is absent")
        if not shutil.which("nbdkit"):
            problems.append("nbdkit missing")
        if importlib.util.find_spec("nbd") is None:
            problems.append("python3-libnbd missing for " + sys.executable)
        if args.fault == "socket" and not shutil.which("ss"):
            problems.append("ss (iproute2) missing for the socket fault")
    if args.disk_latency_ms and "local-swap" in args.arms:
        problems.append("disk latency emulation only applies to FUSE arms; drop local-swap for that run")
    if args.fault and "bridge" not in args.arms:
        problems.append("faults are injected in the bridge arm only")
    if args.dataset_mib <= args.ram_mib:
        problems.append("dataset must exceed the RAM budget or there is no memory pressure")
    if args.dataset_mib + 32 > args.ram_mib + args.swap_mib:
        problems.append("dataset plus ~32 MiB interpreter overhead exceeds RAM + swap; every swap arm would OOM")
    meminfo = dict(line.split(":") for line in read_text("/proc/meminfo").splitlines())
    available_kib = int(meminfo["MemAvailable"].split()[0])
    need_kib = (args.swap_mib + args.ram_mib) * 1024 * 2
    if available_kib < need_kib:
        problems.append("not enough free RAM for the locked donor plus workload headroom")
    if problems:
        raise LabError("; ".join(problems))


def environment(args):
    def version(command):
        try:
            return run(command, check=False).stdout.strip().splitlines()[0]
        except (OSError, IndexError):
            return None

    meminfo = {}
    for line in read_text("/proc/meminfo").splitlines()[:3]:
        key, value = line.split(":")
        meminfo[key] = value.strip()
    return {"timestamp": datetime.now(timezone.utc).isoformat(),
            "kernel": platform.release(), "python": platform.python_version(),
            "executable": sys.executable, "nbdkit": version(["nbdkit", "--version"]),
            "fusermount": version([shutil.which("fusermount3") or "fusermount", "--version"]),
            "losetup": version(["losetup", "--version"]), "meminfo": meminfo,
            "cpus": os.cpu_count(), "directory_mount": version(["findmnt", "-n", "-o", "SOURCE,FSTYPE",
                                                                  "-T", str(args.directory)]),
            "active_swap_before": read_text("/proc/swaps"),
            "command": sys.argv}


def summarize(runs):
    summary = {}
    for arm in ARMS:
        selected = [r for r in runs if r["arm"] == arm and r.get("fault") is None]
        if not selected:
            continue
        completed = [r for r in selected if r.get("workload") is not None]
        entry = {"runs": len(selected), "completed": len(completed),
                 "oom_killed": sum(1 for r in selected if r.get("oom_killed")),
                 "died_other": sum(1 for r in selected if r.get("workload") is None
                                   and not r.get("oom_killed")),
                 "verification_errors": sum(r["workload"]["verification_errors"] for r in completed)}
        if completed:
            random_wall = [r["workload"]["phases"]["random"]["wall_seconds"] for r in completed]
            total_wall = [r["workload"]["total_wall_seconds"] for r in completed]
            p99 = [r["workload"]["phases"]["random_reads"]["p99_seconds"] for r in completed
                   if r["workload"]["phases"]["random_reads"]["p99_seconds"] is not None] or [None]
            entry.update({"random_wall_median_seconds": statistics.median(random_wall),
                          "random_wall_min_seconds": min(random_wall),
                          "random_wall_max_seconds": max(random_wall),
                          "total_wall_median_seconds": statistics.median(total_wall),
                          "random_read_p99_median_seconds": (statistics.median(p99)
                                                             if p99[0] is not None else None),
                          "rounds_completed": [r["workload"]["rounds_completed"] for r in completed],
                          "pswpin_median": statistics.median(r["swap_io"]["pswpin"] for r in completed),
                          "pswpout_median": statistics.median(r["swap_io"]["pswpout"] for r in completed)})
        summary[arm] = entry
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--disposable-vm", action="store_true",
                        help="Required: confirms this machine may get temporary swap and cgroups")
    parser.add_argument("--arms", default="local-swap,fuse-local,bridge",
                        help="Comma-separated subset of: " + ",".join(ARMS))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--order-offset", type=int, default=0,
                        help="Rotate the first repetition's arm order by this many positions")
    parser.add_argument("--ram-mib", type=int, default=256)
    parser.add_argument("--dataset-mib", type=int, default=512)
    parser.add_argument("--swap-mib", type=int, default=768)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--pattern", choices=("uniform", "hotset"), default="uniform")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--deadline", type=float, default=0, help="Workload random-phase deadline")
    parser.add_argument("--run-timeout", type=float, default=1800)
    parser.add_argument("--io-timeout", type=float, default=2.0)
    parser.add_argument("--transfer-kib", type=int, default=64)
    parser.add_argument("--directory", type=Path, default=Path("/var/tmp"),
                        help="Disk-backed directory for swap files and local copies")
    parser.add_argument("--disk-latency-ms", type=float, default=0.0,
                        help="Emulated extra local-disk latency in the FUSE arms (sensitivity only)")
    parser.add_argument("--fault", choices=("frozen", "dead", "socket"))
    parser.add_argument("--fault-delay", type=float, default=2.0,
                        help="Seconds after the random phase starts before injecting the fault")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    for arm in args.arms:
        if arm not in ARMS:
            parser.error(f"unknown arm {arm}")
    if not 1 <= args.repetitions <= 20:
        parser.error("repetitions must be 1..20")
    for name in ("ram_mib", "dataset_mib", "swap_mib"):
        if not 16 <= getattr(args, name) <= 65536:
            parser.error(f"{name} must be 16..65536")
    args.directory = args.directory.resolve()
    if not args.directory.is_dir():
        parser.error("--directory must exist")
    try:
        preflight(args)
    except LabError as error:
        parser.exit(1, f"Preflight failed: {error}\n")

    report = {"environment": environment(args), "parameters": vars(args) | {
        "directory": str(args.directory), "output": str(args.output)}, "runs": [],
        "cleanup_failures": []}
    report["parameters"]["arms"] = args.arms
    workdir = Path(tempfile.mkdtemp(prefix="memory-bridge-swap-lab-", dir=args.directory))
    try:
        for repetition in range(args.repetitions):
            # Rotate the order each repetition so order effects are visible.
            shift = (repetition + args.order_offset) % len(args.arms)
            order = args.arms[shift:] + args.arms[:shift]
            for arm_name in order:
                ram = args.ram_mib * MIB
                swap = args.swap_mib * MIB
                limit = ram + swap if arm_name == "more-ram" else ram
                memsw = limit + (swap if arm_name not in ("noswap", "more-ram") else 0)
                fault = args.fault if arm_name == "bridge" else None
                print(f"[swap-lab] repetition {repetition + 1} arm {arm_name}"
                      + (f" fault={fault}" if fault else ""), file=sys.stderr, flush=True)
                record = {"arm": arm_name, "repetition": repetition + 1, "order": order}
                with ExitStack() as stack:
                    cgroup = Cgroup(f"memory-bridge-swap-lab-{os.getpid()}-{repetition}-{arm_name}",
                                    limit, memsw)
                    stack.callback(cgroup.remove)
                    try:
                        arm = Arm(arm_name, args, stack, workdir)
                    except Exception as error:  # cleanup ran via ExitStack
                        record["error"] = f"setup {type(error).__name__}: {error}"
                        report["runs"].append(record)
                        continue
                    record["swap_device"] = arm.swap_device
                    record["losetup"] = getattr(arm, "losetup", None)
                    record["proc_swaps"] = read_text("/proc/swaps")
                    try:
                        record.update(run_workload(args, cgroup, arm, fault))
                    except Exception as error:  # record and continue with the next arm
                        record["error"] = f"{type(error).__name__}: {error}"
                    record["dmesg"] = dmesg_tail(("Out of memory", "oom", "swap", "loop", "fuse", "I/O error"))
                record["arm_notes"] = arm.notes
                record["export_final"] = getattr(arm, "export_output", None)
                record["export_errors"] = getattr(arm, "export_errors", None)
                report["runs"].append(record)
                args.output.write_text(json.dumps(report, indent=2))
    except LabError as error:
        report["cleanup_failures"].append(str(error))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        report["active_swap_after"] = read_text("/proc/swaps")
        report["loop_devices_after"] = run(["losetup", "-l"], check=False).stdout
        report["summary"] = summarize(report["runs"])
        args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))
    return 1 if report["cleanup_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
