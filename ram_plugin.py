"""Compatibility nbdkit plugin; callbacks live in the installed package."""

import sys
from pathlib import Path

# nbdkit embeds Python without adding this compatibility file's directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from memory_bridge import nbd_plugin as _implementation
from memory_bridge.nbd_plugin import *  # nbdkit loads callbacks from this namespace.

# Normal imports share implementation state, including private test hooks.
sys.modules[__name__] = _implementation
