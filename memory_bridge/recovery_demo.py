"""Demonstrate donor failure with real temporary-file backing, without a NAS."""

import json

from .local_disk import TemporaryFileStorage
from .resilient import ResilientStorage
from .simulation import SimulatedMemory


def recovery_demo(directory=None):
    donor = SimulatedMemory(8192)
    local = TemporaryFileStorage(8192, directory)
    store = ResilientStorage(local, donor)
    try:
        payload = b"survives donor loss"
        # Cross a block boundary to exercise partial-block cache population.
        store.write(4090, payload)
        if store.read(4090, len(payload)) != payload:
            raise RuntimeError("Healthy read failed")
        donor.fail_reads = True
        if store.read(4090, len(payload)) != payload:
            raise RuntimeError("Local fallback failed")
        store.write(0, b"still writable")
        if store.read(0, 14) != b"still writable":
            raise RuntimeError("Degraded write failed")
        print(json.dumps({"mode": "local file and simulated donor; no network",
                          "donor_loss_recovery": "passed",
                          "degraded_write": "passed",
                          "metrics": store.snapshot()}, indent=2))
    finally:
        store.close()
