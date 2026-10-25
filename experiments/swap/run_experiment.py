#!/usr/bin/env python3
"""Run the pre-registered swap experiment matrix in a DISPOSABLE Linux VM.

Configurations (docs/SWAP-EXPERIMENT-KRITERIER.md):
  N   limited RAM, no swap          L   limited RAM, local swap file
  P   bridge swap, local copy only  R   bridge swap + donor cache (loopback)
  R1  R with 1 ms donor delay       Ref more RAM, no swap
  S   (stress) R with export/nbdfuse inside the workload's cgroup
Faults (config R, workload W2): donor_stop, donor_kill, net_drop, export_kill.

Writes plan.json (order fixed before the first run), environment.json,
runs.jsonl (one record per run, appended as runs finish) and per-run logs and
memory samples. Re-running with the same --out resumes unfinished runs.

Example (inside the disposable VM, as root):
  MB_DISPOSABLE_VM=1 /usr/bin/python3.12 experiments/swap/run_experiment.py \
      --i-am-in-a-disposable-vm --out /srv/mbx/exp1 --rounds 5
"""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import swaplab  # noqa: E402
from swaplab import (BridgeSwap, Cgroup, Donor, Export, LocalSwapFile, Managed,  # noqa: E402
                     NetworkBlackhole, NoSwap, Sampler, active_swaps, drop_caches, sh)

HERE = Path(__file__).resolve().parent
TIME_FORMAT = "wall_s=%e user_s=%U sys_s=%S max_rss_kib=%M major_faults=%F minor_faults=%R exit=%x"
MAIN_CONFIGS = ("N", "L", "P", "R", "Ref")
FAULTS = ("donor_stop", "donor_kill", "net_drop", "export_kill")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--i-am-in-a-disposable-vm", dest="acknowledged", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--rounds-r1", type=int, default=3)
    parser.add_argument("--workloads", default="w2,w1")
    parser.add_argument("--configs", default="N,L,P,R,R1,Ref")
    parser.add_argument("--faults", default="", help="Comma list or 'all'")
    parser.add_argument("--fault-reps", type=int, default=3)
    parser.add_argument("--fault-delay-s", type=float, default=20.0)
    parser.add_argument("--stress-reps", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20261025)
    parser.add_argument("--limit-mib", type=int, default=256)
    parser.add_argument("--ref-limit-mib", type=int, default=1024)
    parser.add_argument("--swap-mib", type=int, default=1024)
    parser.add_argument("--remote-timeout", type=float, default=2.0)
    parser.add_argument("--w2-size-mib", type=int, default=512)
    parser.add_argument("--w2-duration-s", type=float, default=60.0)
    parser.add_argument("--w1-level", type=int, default=8)
    parser.add_argument("--w1-input-mib", type=int, default=48)
    parser.add_argument("--timeout-s", type=float, default=900.0)
    parser.add_argument("--stress-timeout-s", type=float, default=300.0)
    parser.add_argument("--donor-user", default="mbtest",
                        help="Unprivileged donor user (gets only CAP_IPC_LOCK); '' = root")
    parser.add_argument("--only", help="Comma list of run ids to execute (debugging)")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------- planning

def make_plan(args):
    configs = [c for c in args.configs.split(",") if c]
    workloads = [w for w in args.workloads.split(",") if w]
    runs = []
    for round_number in range(1, args.rounds + 1):
        for workload_index, workload in enumerate(workloads):
            eligible = [c for c in configs if c != "R1" or round_number <= args.rounds_r1]
            order = swaplab.shuffled(eligible, args.seed * 1000 + round_number * 10 + workload_index)
            for position, config in enumerate(order):
                runs.append({"id": f"r{round_number}-{workload}-{config}", "phase": "matrix",
                             "round": round_number, "position": position, "workload": workload,
                             "config": config, "fault": None})
    faults = list(FAULTS) if args.faults == "all" else [f for f in args.faults.split(",") if f]
    for repetition in range(1, args.fault_reps + 1 if faults else 1):
        order = swaplab.shuffled(faults, args.seed + 7000 + repetition)
        for position, fault in enumerate(order):
            runs.append({"id": f"f{repetition}-{fault}", "phase": "fault", "round": repetition,
                         "position": position, "workload": "w2", "config": "R", "fault": fault})
    for repetition in range(1, args.stress_reps + 1):
        runs.append({"id": f"s{repetition}", "phase": "stress", "round": repetition,
                     "position": 0, "workload": "w2", "config": "S", "fault": None})
    return runs


# --------------------------------------------------------------------------- preparation

def prepare(args, out):
    bin_dir = out / "bin"
    bin_dir.mkdir(exist_ok=True)
    pagestress = bin_dir / "pagestress"
    if not pagestress.exists():
        sh(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-std=c11", "-D_GNU_SOURCE", "-o",
            str(pagestress), str(HERE / "workloads" / "pagestress.c")])
    prep = {"pagestress_sha256": hashlib.sha256(pagestress.read_bytes()).hexdigest()}
    data = out / "data"
    data.mkdir(exist_ok=True)
    os.chmod(data, 0o755)
    xz_input = data / f"xz-input-{args.w1_input_mib}mib.txt"
    if not xz_input.exists():
        prep["xz_input_sha256"] = sh([swaplab.PYTHON, str(HERE / "workloads" / "make_xz_input.py"),
                                      str(xz_input), "--size-mib", str(args.w1_input_mib)],
                                     timeout=600).stdout.strip()
    else:
        prep["xz_input_sha256"] = hashlib.sha256(xz_input.read_bytes()).hexdigest()
    reference = data / "xz-reference.txt"
    if not reference.exists():
        # Reference output hash without any memory limit or swap.
        result = sh(["/bin/bash", "-o", "pipefail", "-c",
                     f'xz -{args.w1_level} -T1 -c -- "$1" | sha256sum', "bash", str(xz_input)],
                    timeout=1800)
        reference.write_text(result.stdout.split()[0])
    prep["xz_reference_sha256"] = reference.read_text().strip()
    keys = out / "keys"
    keys.mkdir(mode=0o700, exist_ok=True)
    key = keys / "session.psk"
    if not key.exists():
        sh([swaplab.PYTHON, str(swaplab.REPO / "bridge.py"), "keygen", str(key)])
    return pagestress, xz_input, key, prep


# --------------------------------------------------------------------------- one run

class FaultInjector(threading.Thread):
    def __init__(self, fault, delay, marker_path, workload, donor, export, blackhole):
        super().__init__(daemon=True)
        self.fault, self.delay = fault, delay
        self.marker_path, self.workload = marker_path, workload
        self.donor, self.export, self.blackhole = donor, export, blackhole
        self.report = {"fault": fault, "injected": False}

    def run(self):
        marker = swaplab.wait_for_marker(self.marker_path, "request-phase-start", 1200,
                                         self.workload.alive)
        if marker is None:
            self.report["error"] = "request phase never started"
            return
        end = marker + self.delay
        while time.monotonic() < end:
            if not self.workload.alive():
                self.report["error"] = "workload ended before fault"
                return
            time.sleep(0.02)
        injected = time.monotonic()
        if self.fault == "donor_stop":
            self.donor.proc.signal(signal.SIGSTOP)
        elif self.fault == "donor_kill":
            self.donor.proc.signal(signal.SIGKILL)
        elif self.fault == "net_drop":
            self.blackhole.apply()
        elif self.fault == "export_kill":
            self.export.proc.signal(signal.SIGKILL)
        self.report.update(injected=True, inject_after_marker_s=round(injected - marker, 3))


def parse_time_file(path):
    text = swaplab.read_text(path, "") or ""
    report = {"terminated_by_signal": None}
    match = re.search(r"terminated by signal (\d+)", text)
    if match:
        report["terminated_by_signal"] = int(match.group(1))
    for line in reversed(text.splitlines()):
        if line.startswith("wall_s="):
            for field in line.split():
                key, _, value = field.partition("=")
                report[key] = float(value) if "." in value else int(value)
            break
    return report


def parse_slow_log(path):
    entries = []
    pattern = re.compile(r"slow request=(\d+) start_s=([\d.]+) latency_ms=([\d.]+)")
    for line in (swaplab.read_text(path, "") or "").splitlines():
        match = pattern.search(line)
        if match:
            entries.append((float(match.group(2)), float(match.group(3))))
    return entries


def run_one(spec, args, out, pagestress, xz_input, key, prep):
    workdir = out / "runs" / spec["id"]
    if workdir.exists():
        shutil.rmtree(workdir)  # A previous attempt that did not finish.
    workdir.mkdir(parents=True)
    os.chmod(workdir, 0o755)
    config = spec["config"]
    record = dict(spec, started_at=time.time(), errors=[])
    limit = (args.ref_limit_mib if config == "Ref" else args.limit_mib) * 1024 * 1024
    drop_caches()
    cgroup = Cgroup(f"mbx-{spec['id']}", limit)
    donor = export = backend = None
    blackhole = None
    try:
        if config in ("R", "R1", "S"):
            donor = Donor(workdir, args.swap_mib, key, user=args.donor_user or None,
                          delay_ms=1 if config == "R1" else 0)
            blackhole = NetworkBlackhole(donor.port)
        wrap = cgroup.wrap if config == "S" else None
        if config in ("N", "Ref"):
            backend = NoSwap()
        elif config == "L":
            backend = LocalSwapFile(workdir, args.swap_mib)
        else:
            export = Export(workdir, args.swap_mib, workdir, donor=donor, key=key,
                            timeout=args.remote_timeout, wrap=wrap)
            backend = BridgeSwap(workdir, export, wrap=wrap)
        record["swap_device"] = backend.device
        record["limit_bytes"] = limit

        def components():
            found = backend.components()
            if donor is not None:
                found["donor"] = donor.proc.pid
            return found

        if spec["workload"] == "w2":
            json_path = workdir / "pagestress.json"
            command = [str(pagestress), "--size-mib", str(args.w2_size_mib),
                       "--duration-s", str(args.w2_duration_s), "--seed", str(args.seed),
                       "--json-out", str(json_path), "--slow-ms", "100"]
        else:
            command = ["/bin/bash", "-o", "pipefail", "-c",
                       f'xz -{args.w1_level} -T1 -c -- "$1" | sha256sum', "bash", str(xz_input)]
        time_path = workdir / "time.txt"
        command = ["/usr/bin/time", "-o", str(time_path), "-f", TIME_FORMAT, *command]
        timeout = args.stress_timeout_s if config == "S" else args.timeout_s

        vm_before = swaplab.vmstat()
        sampler = Sampler(cgroup, components, workdir / "samples.jsonl")
        cpu_before = {name: swaplab.cpu_seconds(pid) for name, pid in components().items()}
        sampler.start()
        started = time.monotonic()
        workload = Managed("workload", cgroup.wrap(command), workdir)
        injector = None
        if spec["fault"]:
            injector = FaultInjector(spec["fault"], args.fault_delay_s, workload.stderr_path,
                                     workload, donor, export, blackhole)
            injector.start()
        try:
            workload.process.wait(timeout=timeout)
            record["timed_out"] = False
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
        elapsed = time.monotonic() - started
        if record["timed_out"]:
            record["errors"].append("workload timeout; killing cgroup")
            cgroup.kill_all()
            try:
                workload.process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                record["errors"].append("workload did not exit after SIGKILL (D state?)")
        if injector is not None:
            injector.join(timeout=5)
            record["fault_report"] = injector.report
        cpu_after = {name: swaplab.cpu_seconds(pid) for name, pid in components().items()}
        record["sampler"] = sampler.stop()
        vm_after = swaplab.vmstat()
        record["elapsed_s"] = round(elapsed, 3)
        record["exit_code"] = workload.process.returncode
        workload.stop()
        record["vmstat_delta"] = {k: vm_after.get(k, 0) - vm_before.get(k, 0) for k in vm_after}
        record["cgroup"] = cgroup.stats()
        record["cpu_s"] = {name: (round(cpu_after[name] - cpu_before[name], 3)
                                  if cpu_after.get(name) is not None and cpu_before.get(name) is not None
                                  else None) for name in cpu_before}
        record["time"] = parse_time_file(time_path)
        if export is not None:
            record["export_stats"] = export.stats()
        if spec["workload"] == "w2":
            try:
                record["pagestress"] = json.loads((workdir / "pagestress.json").read_text())
            except (OSError, ValueError):
                record["pagestress"] = None
            slow = parse_slow_log(workload.stderr_path)
            record["slow_requests_logged"] = len(slow)
            if injector is not None and injector.report.get("injected"):
                at = injector.report["inject_after_marker_s"]
                window = [lat for start, lat in slow if at - 1.0 <= start <= at + 30.0]
                record["fault_window_max_latency_ms"] = max(window) if window else 0.0
        else:
            output = (swaplab.read_text(workload.stdout_path, "") or "").split()
            record["xz_output_sha256"] = output[0] if output else None
            record["xz_output_ok"] = record["xz_output_sha256"] == prep["xz_reference_sha256"]
        record["status"] = classify(record)
    except Exception as error:  # Record and continue with teardown.
        record["status"] = "harness_error"
        record["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        def guarded(label, action):
            try:
                return action()
            except Exception as error:  # Keep tearing down the rest.
                record["errors"].append(f"teardown {label}: {type(error).__name__}: {error}")
                return None
        guarded("cgroup kill", lambda: cgroup.pids() and cgroup.kill_all())
        if blackhole is not None:
            guarded("nftables", blackhole.remove)
        if backend is not None:
            record["errors"].extend(guarded("backend", backend.teardown) or [])
        elif export is not None:
            guarded("export", export.stop)
        if donor is not None:
            record["donor_exit"] = guarded("donor", donor.stop)
        record["cgroup_removed"] = guarded("cgroup", cgroup.destroy)
        record["finished_at"] = time.time()
        leftover = active_swaps()
        record["leftover_swaps"] = leftover
        samples = workdir / "samples.jsonl"
        if samples.exists():
            with open(samples, "rb") as source, gzip.open(str(samples) + ".gz", "wb") as target:
                shutil.copyfileobj(source, target)
            samples.unlink()
    return record


def classify(record):
    oom = record["cgroup"].get("oom_kills", 0) > 0
    if record.get("timed_out"):
        return "timeout"
    if record["workload"] == "w2":
        result = record.get("pagestress")
        if result and record["exit_code"] == 0:
            return "ok"
        if result and (result.get("verify_errors") or result.get("final_sweep_errors")):
            return "data_error"
    else:
        if record["exit_code"] == 0 and record.get("xz_output_ok"):
            return "ok"
        if record["exit_code"] == 0 and not record.get("xz_output_ok"):
            return "data_error"
    return "oom" if oom else "failed"


# --------------------------------------------------------------------------- main

def main(argv=None):
    args = parse_args(argv)
    swaplab.require_disposable_vm(args.acknowledged)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o755)
    plan = make_plan(args)
    plan_path = out / "plan.json"
    plan_document = {"args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()
                              if k not in ("only",)}, "runs": plan}
    if plan_path.exists():
        previous = json.loads(plan_path.read_text())
        if previous["runs"] != plan:
            raise SystemExit("plan.json differs from the requested plan; use a new --out")
    else:
        plan_path.write_text(json.dumps(plan_document, indent=1))
    pagestress, xz_input, key, prep = prepare(args, out)
    environment = swaplab.environment_report()
    environment["preparation"] = prep
    environment["command"] = sys.argv
    (out / "environment.json").write_text(json.dumps(environment, indent=1))
    runs_path = out / "runs.jsonl"
    done = set()
    if runs_path.exists():
        done = {json.loads(line)["id"] for line in runs_path.read_text().splitlines() if line}
    selected = set(args.only.split(",")) if args.only else None
    for spec in plan:
        if spec["id"] in done or (selected and spec["id"] not in selected):
            continue
        print(f"[{time.strftime('%H:%M:%S')}] {spec['id']} ...", flush=True)
        record = run_one(spec, args, out, pagestress, xz_input, key, prep)
        with open(runs_path, "a") as output:
            output.write(json.dumps(record) + "\n")
        print(f"    -> {record['status']} in {record.get('elapsed_s')} s "
              f"errors={record['errors'][:2]}", flush=True)
        if record["leftover_swaps"]:
            raise SystemExit(f"ABORT: swap still active after teardown: {record['leftover_swaps']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
