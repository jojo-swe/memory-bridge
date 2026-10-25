"""Opt-in address-space limits for disposable Linux experiment workers.

RLIMIT_AS limits each process's virtual address space, not resident memory,
not the sum of child processes, and not the kernel file cache. It is a bounded
allocation test, not a substitute for cgroup memory-pressure measurements.
"""

import resource


def apply_address_limit(limit_mib):
    """Lower this worker's limit, inherited by future exec'd NBD children.

    Never raise an existing soft or hard limit. Call only inside a disposable
    worker, before creating backings. Failure must abort the experiment.
    """
    if limit_mib is None:
        return None
    if type(limit_mib) is not int or not 32 <= limit_mib <= 65536:
        raise ValueError("Address-space limit must be 32..65536 MiB")
    target = limit_mib * 1024 * 1024
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if any(value != resource.RLIM_INFINITY and target > value for value in (soft, hard)):
        raise ValueError("Requested limit exceeds an existing address-space limit")
    resource.setrlimit(resource.RLIMIT_AS, (target, hard))
    return target
