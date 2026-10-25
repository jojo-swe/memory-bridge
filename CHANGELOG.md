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
