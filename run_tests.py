#!/usr/bin/env python3
"""Run the suite from any directory; strict CI rejects all skipped tests."""

import argparse
import os
from pathlib import Path
import sys
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-integration", action="store_true",
                        help="Fail if any test is skipped (use Linux with NBD dependencies)")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    # Existing test fixtures resolve repository files relative to this root.
    os.chdir(root)
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if args.require_integration and result.skipped:
        print("FAIL: strict integration run contains skipped tests", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
