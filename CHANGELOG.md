## 0.11.0

- Add a FUSE block export (`block-export`) exposing the borrower stack as one file for loop/swap use, with direct_io, mlockall, EIO mapping and a stats file.
- Add an O_DIRECT temporary backing so local disk cost is never hidden by the page cache.
- Add bounded write-behind mirroring to ResilientStorage; cache validity is still gated on the verified remote ACK.
- Add a verified anonymous-memory pressure workload, a disposable-VM swap lab with cgroup limits, rotated order, fault injection and raw JSON, and a swap-report aggregator.
- Respect CAP_IPC_LOCK in memlock pre-checks; make tests independent of running as root.
- Document the swap experiment design, acceptance criteria and measured results.

## 0.10.0

- Package all experiment commands and the NBD plugin.
- Expose commands through the installed memory-bridge entry point.
- Invoke worker modules without relying on checkout-relative scripts.
- Preserve root script compatibility and test package-only execution.

## 0.9.0

- Verify every remote cache block against a locally retained SHA-256 digest before exposing data.
- Verify complete blocks for partial reads; discard the cache and use local backing on mismatch.
- Bound integrity metadata and release it after failure or shutdown.

## 0.8.0

- Add opt-in persistent SQLite page backing with atomic writes and exclusive ownership.
- Add restart verification with optional TLS/NBD mirroring and real SIGKILL tests.
- Preserve the ownership lock after interrupted database cleanup.
- Synchronize library/distribution version metadata and test the invariant.
- Existing benchmarks retain temporary backing; no automatic data migration.

## 0.7.0

- Replace per-block Python sets with one-bit validity maps.
- Batch adjacent cached reads and mirrored writes; avoid redundant local readback for complete writes.
- Add configurable 4 KiB–1 MiB verified workloads and optional per-process RLIMIT_AS limits.
- Add a one-command loopback lab with temporary credentials and bounded lifecycle cleanup.
- Clean up interrupted NBD calls and invalidate uncertain local writes on cancellation.
- Launch NBD workers from the package directory so scripts also work outside the checkout.

## 0.6.0

- Add repeatable local-file versus TLS/NBD cache comparison in isolated processes.
- Verify deterministic reads and changing writes without retaining a shadow dataset.
- Report latency, IOPS, initialization, separate process memory peaks and scope limitations.
- Reject degraded-cache results and kill the experiment process group on a whole-run timeout.

## 0.5.0

- Connect local-first recovery to TLS-required NBD through an isolated worker.
- Bound remote connection and I/O waits; permanently abandon a failed worker.
- Add a CLI experiment and integration checks for killed/frozen donors, wrong keys and size mismatch.
- Local disk operations and whole multi-block requests have no global deadline.

## 0.4.0

- Add local-first storage with a best-effort block cache and permanent fallback on donor errors.
- Add private temporary-file backing and a runnable donor-loss recovery demo.
- Fail closed after local backing errors; serialize overlapping reads and writes.
- This transport-independent prototype is not yet connected to NBD or safe for system swap.

# Changelog

## 0.3.1 — 2026-10-25

- Prevent retired capability reuse with a generation prefix, without retaining token history.
- Check memlock and NBD limits before opening the key or starting the donor.
- Generate key entropy before creating the output file.
- Add a strict test runner: CI fails on skipped tests.
- Disable persisted checkout credentials in CI.


## 0.3.0 — 2026-10-25

- Enforce TOML quotas and request limits in the donor and NBD adapter.
- Validate NBD block limits before allocating memory.
- Add irreversible core draining: existing data remains accessible while new allocations are rejected.
- Add adapter, lifecycle and CLI regression tests; exercise config in real NBD tests.
- Publish contributor guidance.

## 0.2.1 — 2026-10-25

- Enforce read-only exports at the plugin callback boundary.
- Account for allocations using immutable session sizes, independent of backend mutation.
- Generate capabilities before allocating RAM; reject repeated live-token collisions.
- Validate and read key files using the same descriptor; reject symlinks,
  non-regular files and oversized contents without blocking on FIFOs.
- Exclude build artifacts, virtual environments and common key files from Git.
- Add seven regression tests: 36 passed, four transport tests skipped locally.

Key validation does not freeze the file after it is handed to nbdkit/libnbd.
The key's parent directory must be trusted; the downstream library reopens its
path. This change is not a claim of protection against a malicious local owner.

## 0.2.0

Shared allocation engine, isolated sessions, quotas, failure injection,
strict TOML validation, local demo, diagnostic CLI and GitHub Actions workflow.

## 0.1.0

Initial locked-RAM NBD plugin and synthetic read/write client.
