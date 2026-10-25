# Changelog

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
