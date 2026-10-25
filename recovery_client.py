#!/usr/bin/env python3
"""Compatibility entry point; implementation lives in the installed package."""

import sys
from memory_bridge import recovery_cli as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    # Preserve legacy imports and monkeypatches on the implementation module.
    sys.modules[__name__] = _implementation
