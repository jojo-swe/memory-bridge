"""Process-wide protections for daemons that sit in a swap device's I/O path.

A process that serves swap I/O must not itself be swapped out to the device it
serves (recursive swap), and its own allocations should not recurse into I/O
that waits for itself (deadlock under memory pressure). Linux offers two tools:

* mlockall(MCL_CURRENT | MCL_FUTURE | MCL_ONFAULT): pages become locked as
  they are faulted in, instead of pre-faulting the whole address space.
  Without MCL_ONFAULT a Python/nbdkit process grows from ~25 MiB to ~250 MiB
  resident (thread stacks, glibc arenas, mapped libraries), which defeats the
  point of borrowing RAM. Locked pages still need free RAM to be faulted in;
  mlock only prevents eviction.
* prctl(PR_SET_IO_FLUSHER): mark the process as an I/O flusher, which makes
  its allocations behave as GFP_NOIO and exempts it from some throttling.
  This needs CAP_SYS_RESOURCE and is inherited across fork and execve.

Neither makes a Python daemon allocation-free; they reduce, not eliminate,
deadlock risk. Callers report exactly which protections were obtained.
"""

import ctypes
import ctypes.util
import os

MCL_CURRENT = 1
MCL_FUTURE = 2
MCL_ONFAULT = 4  # Linux 4.4+
PR_SET_IO_FLUSHER = 57
PR_GET_IO_FLUSHER = 58


def _libc():
    return ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


def lock_all_memory():
    """mlockall(MCL_CURRENT|MCL_FUTURE|MCL_ONFAULT); raises OSError on failure."""
    libc = _libc()
    if libc.mlockall(MCL_CURRENT | MCL_FUTURE | MCL_ONFAULT) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "mlockall failed: " + os.strerror(error))


def set_io_flusher():
    """Try PR_SET_IO_FLUSHER; return True on success, False if not permitted."""
    libc = _libc()
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    if libc.prctl(PR_SET_IO_FLUSHER, 1, 0, 0, 0) == 0:
        return True
    error = ctypes.get_errno()
    if error in (1, 22):  # EPERM: missing CAP_SYS_RESOURCE; EINVAL: old kernel.
        return False
    raise OSError(error, "prctl(PR_SET_IO_FLUSHER) failed: " + os.strerror(error))


def io_flusher_enabled():
    libc = _libc()
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    return libc.prctl(PR_GET_IO_FLUSHER, 0, 0, 0, 0) == 1


CAP_IPC_LOCK = 14


def has_cap_ipc_lock(status_path="/proc/self/status"):
    """True when the effective capability set contains CAP_IPC_LOCK.

    Linux does not apply RLIMIT_MEMLOCK to such processes, so a limit check
    that ignores the capability would reject valid locked-memory budgets.
    Returns False when the status file is unavailable (non-Linux).
    """
    try:
        with open(status_path) as status:
            for line in status:
                if line.startswith("CapEff:"):
                    return bool(int(line.split()[1], 16) >> CAP_IPC_LOCK & 1)
    except (OSError, ValueError, IndexError):
        return False
    return False
