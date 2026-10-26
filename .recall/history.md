# Recall History — memory-bridge


## Session 0aed359f — 2026-10-26 20:55

**You:** <system-reminder>
This session is now linked to one of the user's computers. The
`mcp__remote-devices__*` tools for working with that computer are in your
tool list, or will be shortly (possibly not until a later turn); until they
are, the computer is not yet reachable. Once they are, use them normally —
if earlier calls failed because no device was connected, try again; any
part of your system prompt saying this session has no linked computer no
longer applies.
</system-reminder>

**You:** @"/mnt/user-data/attachments/0aed359f-f5d7-545f-8be9-92accdc2fda5/053301d9-attachment.txt" Files from earlier in this conversation, delivered automatically.

**You:** The files from earlier in this conversation were delivered automatically, on the file-delivery messages just before this one.

The files from earlier in this conversation are available at these paths:
/mnt/user-data/uploads/attachment.txt
Read them there (those copies are read-only — copy a file elsewhere to modify it). Each is also attached, at an @-mentioned path, to this message or the file-delivery messages just before it; if a listed path is missing, use that @-mentioned copy instead.

**You:** [Attachment from earlier in this conversation: file 053301d9-3e1e-5748-bb66-8bbe31b346f5 — not embedded in this transcript; it arrives with one of the next messages.]
<system-reminder>The user's timezone is Europe/Stockholm (UTC+01:00). Message sent at Mon 2026-10-26 20:14 local time.</system-reminder>

**Claude:** Jag tar över Memory Bridge: hämtar senaste main, läser dokumentationen, kör befintliga tester och inventerar miljön innan jag bygger swap-experimentet. Jag rapporterar löpande och levererar en PR med mätresultat.
  - `mcp__claude-code-remote__add_repo: push`
  - `Bash: git clone --depth 1 https://github.com/jojo-swe/memory-bridge /home/claude/memory-bridge 2>&1 | tail -3 && git -C /home/claude/memory-bridge rev-parse HEAD && l`
  - `ToolSearch: select:register_repo_root`
  - `mcp__claude-code-remote__register_repo_root: jojo-swe`

**Claude:** Repot är klonat. Nu läser jag dokumentationen och kodstrukturen.
  - `Bash: cd /home/claude/memory-bridge && ls docs memory_bridge tests config; ls -a | grep -i -E "agents|claude"; cat pyproject.toml; wc -l memory_bridge/*.py *.py tests`
  - `Bash: cat docs/NULAGE-OCH-NASTA-STEG.md docs/ARKITEKTUR.md`
  - `Bash: cat docs/KORGUIDE.md docs/TESTRESULTAT.md docs/EXPERIMENTPLAN.md README.md CONTRIBUTING.md`
  - `Read: /root/.claude/projects/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/tool-results/b0l5kccpp.txt`
  - `Bash: cat memory_bridge/resilient.py memory_bridge/storage.py memory_bridge/local_disk.py`
  - `Bash: cat memory_bridge/remote_nbd.py memory_bridge/nbd_worker.py memory_bridge/nbd_plugin.py memory_bridge/launcher.py`
  - `Bash: cat memory_bridge/core.py memory_bridge/persistent.py memory_bridge/lab_cli.py memory_bridge/__main__.py memory_bridge/recovery_cli.py`
  - `Read: /root/.claude/projects/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/tool-results/bxwlvb6r1.txt`
  - `Bash: id; python3 --version; which nbdkit nbd-client qemu-system-x86_64 qemu-img kvm virsh docker podman unshare; ls -l /dev/kvm /dev/nbd0 2>&1; lsmod 2>/dev/null | h`
  - `Bash: ls /dev | head -50; ls /lib/modules 2>&1; which modprobe swapon mkswap losetup; cat /proc/swaps; zcat /proc/config.gz 2>/dev/null | grep -E "CONFIG_BLK_DEV_NBD|`
  - `Bash: zcat /proc/config.gz | grep -E "CONFIG_BLK_DEV_UBLK|CONFIG_FUSE_FS|CONFIG_CUSE|CONFIG_TCM|CONFIG_BLK_DEV_DM|CONFIG_DM_|CONFIG_NET_9P|CONFIG_VIRTIO_BLK|CONFIG_SC`
  - `Bash: ls .github/workflows && cat .github/workflows/*.yml && cat run_tests.py && sed -n 1,80p memory_bridge/compare_cli.py && ls -l /usr/bin/python3* && git log --one`

**Claude:** Nu installerar jag NBD- och FUSE-beroenden och kör den befintliga testsviten som utgångspunkt.
  - `Bash: export DEBIAN_FRONTEND=noninteractive; apt-get install -y -q nbdkit nbdkit-plugin-python python3-libnbd fuse3 libfuse2t64 2>&1 | tail -3; nbdkit --version; /usr`
  - `Bash: git checkout -b feature/swap-experiment-block-export -q && ulimit -l && /usr/bin/python3.12 run_tests.py --require-integration 2>&1 | tail -8`
  - `Bash: sed -n 40,70p tests/test_memory.py`
  - `Bash: /usr/bin/python3.12 -m pip install --break-system-packages -q fusepy 2>&1 | tail -2; /usr/bin/python3.12 -c "import fuse; print(fuse.__version__, fuse._libfuse)`
  - `Write: /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/proto/fuse_proto.py`
  - `Bash: cd /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/proto && mkdir -p mnt && /usr/bin/python3.12 fuse_proto.py mnt 64 2>fuse.err & sle`
  - `Bash: cd /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/proto && cat fuse.err; ls -la; mount | grep fuse; ps aux | grep fuse_proto | head`
  - `Bash: cd /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/proto && ls -l mnt && dd if=/dev/zero of=mnt/volume bs=64k count=4 conv=notrunc 2>`
  - `Write: /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/proto/hog.py`
  - `Bash: cd /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/proto && CG=/sys/fs/cgroup/memory/mb-proto && mkdir -p $CG && echo $((32*1024*1024`
  - `Bash: cd /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/proto && CG=/sys/fs/cgroup/memory/mb-proto && (echo $BASHPID > $CG/cgroup.procs; c`
  - `Bash: cd /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/proto && swapoff /dev/loop0 && losetup -d /dev/loop0 && fusermount3 -u mnt && slee`
  - `Bash: zcat /proc/config.gz | grep -E "INET_DIAG_DESTROY|CONFIG_NET_SCH_NETEM|CONFIG_NETFILTER_XT|CONFIG_NET_NS=|CONFIG_VETH"; which ss tc ip nft iptables; /usr/bin/py`
  - `TaskCreate: Write acceptance criteria and experiment design doc before results`
  - `TaskCreate: Implement DirectFileStorage and FUSE block export adapter`
  - `TaskCreate: Implement pressure workload and swap-lab orchestrator`
  - `TaskCreate: Run experiments: arms, repetitions, orders, faults`
  - `TaskCreate: Fix defects found, update docs, CI, tests; separate review agent`
  - `TaskCreate: Open PR with results and final report`
  - `TaskUpdate: 1`
  - `SendUserMessage: Lägesrapport: repot klonat, 149 tester körda (1 fel: `test_lock_failure_is_fatal` antar icke-root – fixas). Miljön är en disponibel Firecracker-VM (root, 8 GiB,`
  - `Bash: cat /sys/block/vda/queue/rotational /sys/block/vda/queue/logical_block_size; df -T /home/claude | tail -1; mount | grep " / "; cat /etc/os-release | head -2; ap`
  - `Write: /home/claude/memory-bridge/docs/SWAP-EXPERIMENT.md`
  - `Bash: cd /home/claude/memory-bridge && git add docs/SWAP-EXPERIMENT.md && git -c user.name="Claude" -c user.email="claude@anthropic.com" commit -q -m "docs: swap expe`
  - `TaskUpdate: 1`
  - `TaskUpdate: 2`
  - `Write: /home/claude/memory-bridge/memory_bridge/direct_file.py`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/local_disk.py"); s = p.read_text() s = s.replace(''' def close(self): with self._lock: self._`
  - `Write: /home/claude/memory-bridge/memory_bridge/block_export.py`
  - `Bash: cd /usr/lib/python3/dist-packages 2>/dev/null; /usr/bin/python3.12 -c "import fuse, inspect; src=inspect.getsource(fuse.FUSE.__init__); print(src[:3500])"; /usr`
  - `Bash: cd /home/claude/memory-bridge && python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/block_export.py"); s = p.read_text() s = s.replace(''' """fu`
  - `Write: /home/claude/memory-bridge/memory_bridge/block_export_cli.py`

**Claude:** Now I'll smoke-test the export against a real loop device.
  - `Bash: S=/tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad && mkdir -p $S/mnt1 && (/usr/bin/python3.12 -m memory_bridge.block_export_cli $S/mn`
  - `Bash: sed -i 's/self._started = time.time()/self._started = time.time_ns()/' memory_bridge/block_export.py && grep -n "time_ns" memory_bridge/block_export.py`
  - `Write: /home/claude/memory-bridge/memory_bridge/pressure_workload.py`
  - `Bash: time /usr/bin/python3.12 -m memory_bridge.pressure_workload --dataset-mib 128 --rounds 1 2>&1 | tail -4 | cut -c1-400`
  - `Write: /home/claude/memory-bridge/memory_bridge/swap_lab.py`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/swap_lab.py"); s = p.read_text() old = ''' def read_stderr(): for line in process.stderr: std`

**Claude:** Now a short smoke run of the lab with small sizes and one repetition across all arms.
  - `Bash: mkdir -p /var/tmp/swaplab && time /usr/bin/python3.12 -m memory_bridge.swap_lab --disposable-vm --arms noswap,local-swap,fuse-local,bridge,more-ram --repetition`
  - `Bash: cd /var/tmp/swaplab && python3 -c " import json; r=json.load(open('smoke.json')); print(r['cleanup_failures']); for run in r['runs']: print(run['arm'], run.get(`
  - `Bash: cd /home/claude/memory-bridge && mkdir -p /var/tmp/swaplab/k && chmod 700 /var/tmp/swaplab/k && /usr/bin/python3.12 -m memory_bridge.launcher keygen /var/tmp/sw`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/swap_lab.py"); s = p.read_text() old = ''' self.donor = subprocess.Popen([sys.executable, "-m`
  - `Bash: python3 -c " import resource; print(resource.getrlimit(resource.RLIMIT_MEMLOCK)) try: resource.setrlimit(resource.RLIMIT_MEMLOCK,(resource.RLIM_INFINITY,resourc`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/storage.py"); s = p.read_text() s = s.replace('''import ctypes import mmap import threading c`
  - `Bash: cd /var/tmp/swaplab && python3 -c " import json; r=json.load(open('smoke2.json')) b=r['runs'][0] print(json.dumps(b['after']['export'], indent=1)[:1500]); print`
  - `Write: /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/hops.py`
  - `Bash: cd /home/claude/memory-bridge && /usr/bin/python3.12 -I /tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/scratchpad/hops.py 2>&1 | tail -20`
  - `Bash: /usr/bin/python3.12 - <<'EOF' import socket, os, sys, time, struct, subprocess # pure socketpair request/response roundtrip between two python processes, same f`
  - `Read: /home/claude/memory-bridge/memory_bridge/resilient.py`
  - `Read: /home/claude/memory-bridge/memory_bridge/resilient.py`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/resilient.py"); s = p.read_text() # 1. docstring + constructor old = ''' def __init__(self, l`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/direct_file.py"); s = p.read_text() old = ''' def __init__(self, size, directory=None, *, max`
  - `Bash: cat memory_bridge/simulation.py && sed -n 1,60p tests/test_resilient.py && sed -n 1,40p tests/test_integration.py`

**Claude:** Now the tests for the new modules.
  - `Write: /home/claude/memory-bridge/tests/test_direct_file.py`
  - `Write: /home/claude/memory-bridge/tests/test_write_behind.py`
  - `Bash: /usr/bin/python3.12 -m unittest tests.test_write_behind tests.test_direct_file tests.test_resilient -v 2>&1 | tail -25`
  - `Write: /home/claude/memory-bridge/tests/test_block_export.py`
  - `Write: /home/claude/memory-bridge/tests/test_swap_lab.py`

**Claude:** Now the root-assumption fix in the existing memlock test, then the whole suite.
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("tests/test_memory.py"); s = p.read_text() old = ''' def test_lock_failure_is_fatal(self): # A separate proc`
  - `Bash: pgrep -af nbdkit; pkill -f "nbdkit.*10809"; cd /home/claude/memory-bridge && sed -n 1,60p tests/test_launcher.py`
  - `Bash: pgrep -a nbdkit || echo none; cd /home/claude/memory-bridge && sed -n 1,60p tests/test_launcher.py`
  - `Bash: cat bridge.py | tail -5 && python3 - <<'EOF' from pathlib import Path p = Path("tests/test_launcher.py"); s = p.read_text() old = ''' with patch("sys.argv", ["b`
  - `Bash: grep -n "memlock\|RLIMIT" tests/test_lab.py tests/test_standalone_package.py | head; /usr/bin/python3.12 run_tests.py --require-integration > /var/tmp/swaplab/t`
  - `Bash: git add -A && git -c user.name="Claude" -c user.email="claude@anthropic.com" commit -q -m "feat: FUSE block export, O_DIRECT local backing, write-behind mirror,`
  - `Bash: mkdir -p docs/experiments && /usr/bin/python3.12 -m memory_bridge.swap_lab --disposable-vm --arms noswap,local-swap,fuse-local,bridge,bridge-sync,more-ram --rep`
  - `TaskUpdate: 2`
  - `TaskUpdate: 3`
  - `TaskUpdate: 4`
  - `Agent: Review error handling and test assumptions`
  - `Bash: cat memory_bridge/__init__.py; cat CHANGELOG.md | head -30; cat tests/test_package.py; sed -n 1,52p tests/test_standalone_package.py`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/__main__.py"); s = p.read_text() old = ''' "donor": "launcher", "keygen": "launcher", }''' ne`
  - `Bash: cat /var/tmp/swaplab/main.log; cat /proc/swaps | tail -1; uptime`
  - `Bash: cat >> docs/ARKITEKTUR.md <<'EOF' ## Blockexport och write-behind i 0.11 `direct_file.py` är en lokal bakända som öppnar sin temporära fil med `O_DIRECT` och gö`
  - `Bash: cat >> docs/KORGUIDE.md <<'EOF' ## Blockexport och swap-experiment i 0.11 **Kör aldrig detta på din vardagsdator.** `swap-lab` skapar swap, loop-enheter, FUSE-m`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("README.md"); s = p.read_text() s = s.replace("# Memory Bridge — ramverk 0.10.0", "# Memory Bridge — ramverk`
  - `ToolSearch: select:Monitor`
  - `Monitor: tail -n +1 -f /var/tmp/swaplab/main.log | grep --line-buffered -E "repetition|EXIT|Traceback|Error|failed"`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("CHANGELOG.md"); s = p.read_text() s = '''## 0.11.0 - Add a FUSE block export (`block-export`) exposing the`
  - `Write: /home/claude/memory-bridge/docs/SWAP-RESULTAT.md`
  - `Bash: sleep 240; tail -2 /var/tmp/swaplab/main.log`
  - `Bash: until grep -q "EXIT=" /var/tmp/swaplab/main.log; do sleep 5; done; cat /var/tmp/swaplab/main.log | tail -3`

**Claude:** I'll wait for the experiment and the review agent to finish before continuing.

**You:** Stop hook feedback:
[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository. Please commit and push these changes to the remote branch.
  - `Bash: git add -A && git -c user.name="Claude" -c user.email="claude@anthropic.com" commit -q -m "docs: architecture, run guide, README, changelog and CI for block exp`

**Claude:** The experiment is still running (repetition 1 of 3, bridge arm). I'll continue when the background run and the review agent report back.

**You:** <task-notification>
<task-id>bpoxy52da</task-id>
<summary>Monitor event: "swap-lab main experiment progress"</summary>
<event>[swap-lab] repetition 1 arm bridge-sync</event>
</task-notification>

**Claude:** Still running (bridge-sync, repetition 1). Waiting for the next events.

**You:** Stop hook feedback:
[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository. Please commit and push these changes to the remote branch.
  - `Bash: git status --short | head`

**Claude:** The remaining changes are the in-progress experiment raw data (written after every sub-run) and the Recall session log; I'll commit them once the run has finished rather than mid-measurement.

**You:** <task-notification>
<task-id>bpoxy52da</task-id>
<summary>Monitor event: "swap-lab main experiment progress"</summary>
<event>[swap-lab] repetition 1 arm more-ram</event>
</task-notification>

**Claude:** Repetition 1 is almost complete. Still waiting on repetitions 2–3 and the review agent.

**You:** Stop hook feedback:
[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository. Please commit and push these changes to the remote branch.

**Claude:** Same in-progress files as before (raw data being appended during the run and the Recall log); they will be committed and pushed with the final results once the measurement finishes.

**You:** <task-notification>
<task-id>bpoxy52da</task-id>
<summary>Monitor event: "swap-lab main experiment progress"</summary>
<event>[swap-lab] repetition 2 arm local-swap</event>
</task-notification>

**Claude:** Repetition 2 has started. Waiting.

**You:** Stop hook feedback:
[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository. Please commit and push these changes to the remote branch.

**Claude:** Unchanged situation: the only uncommitted files are the raw-data JSON that the running experiment rewrites after each sub-run and the Recall log. Committing partial measurements mid-run would be misleading; they are committed and pushed once the run completes.

**You:** <task-notification>
<task-id>b5zqjxqzp</task-id>
<tool-use-id>toolu_01TADSPJ6Ep9UG3K6uPPvRhv</tool-use-id>
<output-file>/tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/tasks/b5zqjxqzp.output</output-file>
<status>completed</status>
<summary>Background command "Run the main uniform-pattern experiment (3 repetitions, 6 arms)" completed (exit code 0)</summary>
</task-notification>
  - `Bash: cat /var/tmp/swaplab/main.log | tail -5; cd /home/claude/memory-bridge && python3 - <<'EOF' import json, statistics r = json.load(open("docs/experiments/swap-un`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/swap_lab.py"); s = p.read_text() old = ''' self.mountpoint = self.workdir / f"mount-{self.nam`
  - `Bash: mv docs/experiments/swap-uniform-2026-10-26.json docs/experiments/swap-uniform-part1.json && /usr/bin/python3.12 -m memory_bridge.swap_lab --disposable-vm --arm`
  - `Bash: git add -A memory_bridge docs/experiments && git -c user.name="Claude" -c user.email="claude@anthropic.com" commit -q -m "fix(swap-lab): unique mountpoint per r`

**Claude:** Part 2 of the uniform run is in progress (two more rotated repetitions). Waiting for it and the review agent.

**You:** Stop hook feedback:
[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository. Please commit and push these changes to the remote branch.

**Claude:** Only the Recall session log and the part-2 raw data (rewritten by the running experiment) are uncommitted; they are committed and pushed when the run completes.

**You:** <task-notification>
<task-id>a62893539c69c453b</task-id>
<tool-use-id>toolu_011yAxDWVjqSKBzsM84rCJbZ</tool-use-id>
<output-file>/tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/tasks/a62893539c69c453b.output</output-file>
<status>completed</status>
<summary>Agent "Review error handling and test assumptions" finished</summary>
<note>A task-notification fires each time this agent stops with no live background children of its own. The user can send it another message and resume it, so the same task-id may notify more than once.</note>
<result>## Review: feature/swap-experiment-block-export

Read `docs/SWAP-EXPERIMENT.md` and all listed files (plus `remote_nbd.py`, `nbd_worker.py`, `local_disk.py`, `storage.py`, `launcher.py` for context). No tests or commands were run beyond `git show`/`git log`.

### Bugs

**1. `memory_bridge/swap_lab.py:377-384, 402` — "bridge" concurrent-RSS metric includes the workload's RSS (K6 violation).**
`Monitor.run` sums `vmrss_kib` over *every* pid in `self.pids`, and line 402 adds `"workload"` to that dict. So `bridge_peak_concurrent_rss_kib` (reported at 467 as the bridge processes' footprint) silently includes the ~256 MiB workload. The design doc's K6 requires reporting the bridge processes' actual concurrent RSS; this number overstates it by the RAM budget. Fix: in the loop, `if name == "workload": continue` for the `total`, or keep two totals.

**2. `memory_bridge/swap_lab.py:377, 402` — dict mutated while the monitor thread iterates it.**
`monitor.start()` runs before `Popen`, and `monitor.pids["workload"] = process.pid` is inserted while `run()` may be in `for name, pid in self.pids.items()`. That raises `RuntimeError: dictionary changed size during iteration` in the daemon thread, which dies silently; the run then reports `monitor_samples` of 0–1 and zero peaks with no error. Fix: build the pid dict (including the workload) before `monitor.start()`, or iterate over `list(self.pids.items())`.

**3. `memory_bridge/swap_lab.py:400-411, 440` — two readers on the workload's stderr pipe.**
`read_stderr` consumes `process.stderr` in a thread, then `process.communicate()` (which also selects on `stderr` when it is a PIPE) competes for the same fd. Lines are split arbitrarily between the two; `workload_stderr` in the report is unreliable, and if the sweep line is ever still unread when `communicate()` starts (no-fault path is unaffected; fault path is mostly safe only because injection precedes `communicate`) the phase marker is lost. Fix: send stderr to a temp file or read stdout in the thread as well and never call `communicate()`; alternatively set `process.stderr = None` before `communicate()`.

**4. `memory_bridge/resilient.py:139-157, 276-281` — cached reads serialize behind the in-flight mirror write while holding the storage lock.**
The comment at 279 ("reads keep being served locally while the remote write waits") is only true for blocks that are *not* cache-valid. A read of a valid block takes `self._lock` and then calls `self._remote.read`, which blocks on `RemoteNBDStorage._lock` held by the mirror thread for the duration of its write (up to `io_timeout` on a frozen donor). During that time *every* FUSE operation, including purely local ones, is stalled. In steady state this also means every cached read waits for any mirror batch currently on the single socket, which undercuts the write-behind's purpose and inflates p99 in the `bridge` arm relative to `bridge-sync`. This is not a deadlock (lock order is always storage→remote; the mirror never nests), but K4's "≤ io-timeout + 1 s" bound now applies to all I/O, not just remote. Fix options: (a) a second NBD connection/worker for mirror writes, or (b) check `remote._lock.acquire(blocking=False)` and fall back to local when the mirror holds it, or (c) at minimum correct the comment and document the stall in SWAP-EXPERIMENT risks.

**5. `memory_bridge/block_export_cli.py:78-98` — donor is consumed before `mlockall` can fail.**
The comment says the local backing is allocated first "so a failure never consumes the single-use donor", but `RemoteNBDStorage(...)` (which performs the one allowed NBD session) runs at 91, while `lock_all_memory()` runs inside `serve()` at 97. An `mlockall` failure (or any `serve` pre-mount failure) therefore burns the donor session. Fix: call `lock_all_memory()` (when `not args.allow_unlocked`) before creating `remote`, and pass `lock_memory=False` to `serve`.

### Risks

**6. `memory_bridge/nbd_worker.py` / `remote_nbd.py:67-71` — the NBD worker is not memory-locked, so the recursive-swap chain is only partially closed.**
`mlockall` covers the FUSE daemon only. `nbd_worker` (spawned before `mlockall`, and locks are not inherited across exec) and the donor's Python plugin can be swapped to the lab's own swap device under global pressure; a swap-in of a worker page then needs a FUSE read that may need the worker. The lab's cgroup confinement makes this unlikely, but `block_export.py`'s docstring overstates the guarantee. Cheap fix: `mlockall(MCL_CURRENT|MCL_FUTURE)` in `nbd_worker.main` (best effort), and mention the gap in the doc.

**7. `memory_bridge/swap_lab.py:257-258` — export's stderr pipe is never drained during the run.**
If the FUSE daemon logs more than the 64 KiB pipe buffer (fusepy prints a traceback on every unexpected exception), it blocks on `write(2)` inside a FUSE callback, which stalls swap I/O and hangs the workload until `run_timeout` (1800 s). Redirect stderr to a file in `workdir` or drain in a thread.

**8. `memory_bridge/swap_lab.py:186-188` — `wait_for_ready` can block forever.**
`process.stdout.readline()` is blocking; the deadline is only checked between lines. If the export neither prints nor exits (e.g. hangs in the NBD handshake or mount), the lab hangs. Use a selector/timeout on the fd or a reader thread with `join(timeout)`.

**9. `memory_bridge/swap_lab.py:400-401` — `preexec_fn` with live threads.**
`Monitor` and the stderr reader are running when `Popen(preexec_fn=cgroup.attach_self)` forks; `Path.write_text` in the child can deadlock on a lock held by another thread at fork time. Low probability but the failure mode is a silent hang. Prefer `os.open/os.write` in `attach_self`, or spawn via `sh -c 'echo $$ &gt; …/cgroup.procs &amp;&amp; exec "$@"'`.

**10. `memory_bridge/swap_lab.py:642-659` — any non-`LabError` exception in one arm aborts the whole lab.**
`losetup`/`mkswap`/`swapon` raise `CalledProcessError` (not `LabError`), and `run_workload` can raise `KeyError`/`OSError` while parsing cgroup files. Those propagate out of the `with ExitStack()` (cleanup still runs, which is good) and out of the repetition loop, so remaining arms/repetitions are skipped and the report ends with a traceback rather than an `error` field. Catch `Exception` around the arm body, record it, and continue.

**11. `memory_bridge/block_export.py:188-208` and design doc "Osäkra skrivningar" — EIO on a swap *write* is not a clean failure.**
The kernel re-dirties the page on a swap write error and retries; the workload does not die, it spins until `run_timeout` with the storage permanently `_failed`. Only swap *read* errors kill the faulting process (SIGBUS). The doc's "kärnan behandlar som swapfel" is accurate but the practical effect should be stated: a local failure during an experiment costs a full `run_timeout`. Consider making `run_workload` poll `stats.json` for `failed: true` and kill the workload early.

**12. `memory_bridge/resilient.py:357-358` — `join(timeout=5)` is shorter than a possible `io_timeout`.**
`RemoteNBDStorage.close()` cannot interrupt an in-flight `write` (it needs the same lock), so the mirror thread exits only after that write times out. With `--io-timeout &gt; 5`, `close()` returns with the thread alive; harmless for a daemon thread, but the comment ("fails any in-flight mirror write, so the thread exits promptly") is wrong and `test_close_joins_mirror_thread` would be timing-dependent. Join with `max(5, timeout)` or document.

**13. `memory_bridge/block_export.py:252-259` — `mlockall(MCL_FUTURE)` pins every libfuse worker thread's full 8 MiB stack.**
libfuse2's multithreaded loop spawns up to ~10 workers, each stack fully faulted in and locked: ~80 MiB of RSS attributed to the export process. It is real memory, but it is an artifact of Python+libfuse, not of the design; worth stating in K6 reporting so it is not read as inherent cost. Mitigation: lower `RLIMIT_STACK` for the export process in the lab.

**14. `memory_bridge/pressure_workload.py` + `swap_lab.summarize` — a swap read error does not count as a verification error.**
An EIO on swap-in kills the workload with SIGBUS; `workload` becomes `None`, `verification_errors` stays 0 and `completed &lt; runs`. K1 ("0 verifieringsfel") must be read together with `completed == runs`; the summary exposes both but the doc's wording invites misreading. Add a `died` count (returncode &lt; 0, not OOM) to the summary.

**15. Tests — assumptions that let real bugs pass or cause flakiness**
- `tests/test_write_behind.py:58-68` cannot detect the stale-valid bug it is named for: it only asserts final state after the *second* mirror has also been ACKed, which re-marks the block anyway. Make the remote fail (or stay gated) for the second write and assert `cached_blocks == 0` and `read(0,4) == b"new!"`.
- `tests/test_block_export.py:95-96` skips whenever stderr contains "mount" or "fuse", which also matches real failures ("Mountpoint must be an existing directory", any fusepy traceback). Match the specific permission/`/dev/fuse` error text instead.
- `tests/test_swap_lab.py:27-33` is timing-based: 25 600 touches vs a 50 ms deadline, checked every 256 touches. Probably fine (~3–8 µs per SHAKE-128 page) but marginal on fast hardware; use `--dataset-mib 4 --rounds 1000`.
- `tests/test_memory.py:57-62` on a root runner `setuid(65534)` and then imports `ram_plugin` from the checkout; a 0700 root-owned checkout makes the import fail and the assertion on "Cannot lock RAM" fails (loud, not silent). Also assumes uid 65534 is valid and that `setuid` is permitted in the container.
- `tests/test_swap_lab.py` tests none of the orchestration (`Arm`, `ExitStack` ordering, `Monitor`, `run_workload`); bugs 1–3 are untested. The Monitor and the vmstat/psi delta logic can be unit-tested without root by injecting a fake `/proc` reader.
- `tests/test_direct_file.py:15-18` skips on *any* `OSError` from `__init__` (ENOSPC, EACCES), not just EINVAL for O_DIRECT; narrow to `errno.EINVAL`.
- All of `test_swap_lab.py` assumes Linux (`/proc/swaps`); on another platform the guard tests fail with a traceback instead of "Preflight failed".

### Nits

- `memory_bridge/direct_file.py:110` — `bytes(self._buffer[...])` copies twice (mmap slicing already returns `bytes`).
- `memory_bridge/direct_file.py:121-125` — RMW reads the entire envelope even when only the edge pages are partial; for a 1 MiB write at a 512 B offset that is an extra 1 MiB read. Read only the first and last page.
- `memory_bridge/block_export_cli.py:96-100` — fusepy raises `RuntimeError` on mount failure and `_fuse_module()` raises `RuntimeError`; only `OSError` is caught, so those exit with a traceback instead of the one-line message.
- `memory_bridge/block_export.py:112-116` — `__call__` dispatches any attribute name, e.g. `"stats"` resolves to an `ExportStats` object and fails with `TypeError`. Restrict to a whitelist or check `callable`.
- `memory_bridge/swap_lab.py:567-572` — `p99_seconds` is `None` when the random phase recorded no reads (deadline hit immediately); `statistics.median` then raises `TypeError`.
- `memory_bridge/swap_lab.py:519-522` — `dataset == ram + swap` is allowed, but the workload also needs interpreter + `versions` array + sampler arrays (~20 MiB), so that boundary case will OOM; require a margin.
- `memory_bridge/resilient.py:255` — `min(self._pending)` is O(n) per batch (up to 4096 entries at 16 MiB budget); fine for the experiment, but a `heapq`/sorted structure is easy if write-behind survives.
- `memory_bridge/pressure_workload.py:54-61` — after decimation the sample that triggered it is dropped and retained samples alternate between odd/even multiples of the stride; statistically irrelevant but worth a comment.

### Correct but subtle (no action)

- `resilient.py:228-294` superseded-ACK logic is sound *because* there is exactly one mirror thread: an old batch's ACK is always processed before the next `_take_batch`, so `_inflight[block] = None` cannot be overwritten by newer content while the old write is still outstanding. The `is content` identity check is safe even when CPython returns the same object for a full-range slice, since identical `bytes` identity implies identical content. If a second mirror thread is ever added, this reasoning breaks.
- `resilient.py:291` the `self._digests is not None` guard is what prevents an ACK arriving after `close()`/`_disable_remote()` from marking a block valid on a cleared bitmap; `_mark` also no-ops when `_valid is None`.
- Lock ordering is acyclic (storage lock → remote lock; the mirror thread never holds both), so `close()` cannot deadlock; it can block up to `io_timeout` inside `self._lock`.
- `_queue_mirror` drops the *previous* pending version before applying the budget check, so a block never stays queued with stale content; budget overflow leaves it local-only, which is the invariant-preserving choice.
- `swap_lab` ExitStack order per arm is right: swapoff → losetup -d → (unlink) → fusermount -u → stop export → stop donor (with SIGCONT first) → rmtree keydir → cgroup rmdir; `_swapoff` raising `LabError` does not prevent the remaining callbacks.
- `DirectFileStorage` envelope buffer `max + 2*4096` covers the worst unaligned case (`offset % 4096 = 4095`, `count = max`).</result>
<usage><subagent_tokens>206304</subagent_tokens><tool_uses>22</tool_uses><duration_ms>867270</duration_ms></usage>
</task-notification>
  - `Bash: python3 - <<'EOF' from pathlib import Path import re # ---- resilient: mirror-busy flag so cached reads never wait behind an in-flight mirror write p = Path("me`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/direct_file.py"); s = p.read_text() old = ''' if relative: self._read_aligned(start, _ALIGN)`

**Claude:** Now the swap-lab fixes from the review (monitor accounting, stderr handling, ready-wait timeout, robust cgroup attach, error capture, summary).
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/swap_lab.py"); s = p.read_text() # Monitor: exclude workload from concurrent bridge total; it`
  - `Bash: grep -n "random_read_p99_median_seconds\|p99 = \[" memory_bridge/swap_lab.py; git diff --stat memory_bridge/swap_lab.py`
  - `Bash: sed -n 566,580p memory_bridge/swap_lab.py`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/swap_lab.py"); s = p.read_text() def rep(old, new): global s assert old in s, old[:60] s = s.`
  - `Bash: grep -n "monitor = Monitor" -A 16 memory_bridge/swap_lab.py | head -24`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("memory_bridge/swap_lab.py"); s = p.read_text() def rep(old, new): global s assert old in s, old[:60] s = s.`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("tests/test_write_behind.py"); s = p.read_text() old = ''' def test_superseded_inflight_block_is_never_marke`
  - `Bash: python3 - <<'EOF' from pathlib import Path p = Path("tests/test_swap_lab.py"); s = p.read_text() old = ''' monitor.start() self.assertTrue(any(monitor.samples f`
  - `Bash: git add -A memory_bridge tests && git -c user.name="Claude" -c user.email="claude@anthropic.com" commit -q -m "review fixes: no cached-read stall behind mirror`

**Claude:** Review fixes are committed. Part 2 of the uniform run is still going; once it finishes I'll rerun the `bridge` arm with the post-review code so its numbers reflect the current implementation, then run the hotset, fault and slow-disk scenarios.

**You:** Stop hook feedback:
[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository. Please commit and push these changes to the remote branch.

**Claude:** Same transient files (live raw data of the running experiment and the Recall log); committed and pushed when the run completes.

**You:** <task-notification>
<task-id>bjswdootk</task-id>
<tool-use-id>toolu_01HCmfNZPE4BEemnW9G4H9rt</tool-use-id>
<output-file>/tmp/claude-0/-home-claude/0aed359f-f5d7-545f-8be9-92accdc2fda5/tasks/bjswdootk.output</output-file>
<status>completed</status>
<summary>Background command "Run repetitions 2-3 of the uniform experiment with rotated order" completed (exit code 0)</summary>
</task-notification>
  - `Bash: tail -3 /var/tmp/swaplab/main2.log; cat /proc/swaps | wc -l; losetup -l | wc -l; python3 - <<'EOF' import json for f in ("docs/experiments/swap-uniform-part1.js`
  - `Bash: cat > /var/tmp/swaplab/phase3.sh <<'EOF' set -x P="/usr/bin/python3.12 -m memory_bridge.swap_lab --disposable-vm --directory /var/tmp/swaplab" $P --arms bridge`
  - `Bash: git add docs/experiments/swap-uniform-part2.json && git -c user.name="Claude" -c user.email="claude@anthropic.com" commit -q -m "data: uniform experiment repeti`
  - `SendUserMessage: Första resultat (uniform mönster, 3 upprepningar, roterad ordning, 256 MiB RAM-budget / 512 MiB dataset / 768 MiB swap, 0 verifieringsfel överallt): - noswap: O`
  - `Bash: until grep -q "PHASE3_DONE" /var/tmp/swaplab/phase3.log; do sleep 10; done; grep -E "^\+|repetition" /var/tmp/swaplab/phase3.log | tail -5`

**Claude:** Waiting for phase 3 (post-review bridge reruns, hotset, faults, slow-disk sensitivity) to finish.

**You:** Stop hook feedback:
[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository. Please commit and push these changes to the remote branch.

**Claude:** Only the live experiment output and the Recall log remain uncommitted; they go in with the results once phase 3 completes.

**You:** <task-notification>
<task-id>bpoxy52da</task-id>
<summary>Monitor event: "swap-lab main experiment progress"</summary>
<event>[Monitor expired after 30m with 6 events delivered. Re-arm it if you still need the watch.]</event>
</task-notification>

**Claude:** That monitor was for the first run, which is done; the phase-3 wait is tracked by its own background task.

**You:** Stop hook feedback:
[~/.claude/stop-hook-git-check.sh]: There are uncommitted changes in the repository. Please commit and push these changes to the remote branch.

**Claude:** Unchanged: the uncommitted files are being written by the running phase-3 experiment; they will be committed together with the results when it finishes.
