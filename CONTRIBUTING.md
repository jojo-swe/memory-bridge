# Contributing

Memory Bridge is an experimental research prototype. Help is especially useful
for reproducible benchmarks, failure handling and Linux integration.

## Local development

Python 3.11+ is required. The core and simulator have no third-party Python
dependencies. Run commands from the repository root:

```bash
python3 -m memory_bridge demo
python3 -m unittest discover -s tests -v
```

On Ubuntu, install `nbdkit`, `nbdkit-plugin-python` and `python3-libnbd`, then
use `/usr/bin/python3` for the tests to include the real TLS/NBD cases.

## Changes

- Include regression tests for behavioral changes and failure paths.
- Preserve comments; explain invariants, not just individual statements.
- Never commit PSKs, private keys, memory dumps or private datasets.
- Keep benchmarks reproducible and distinguish loopback from LAN results.
- Do not claim additional physical RAM, crash recovery or SSD improvements
  without evidence. No live host swap is configured by this project.
- Submit small pull requests with the problem, behavior and test results.

## Current boundaries

The NBD export serves one borrower per process. The core supports multiple
isolated allocations. Donors are trusted; transport encryption does not hide
contents from the donor. Drain is currently a core API, not a remote command.

Repository visibility is public. A redistribution license has not yet been
selected by the project owner; do not describe this release as MIT-licensed
or assume a license grant from visibility alone.
