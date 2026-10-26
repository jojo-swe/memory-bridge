#!/usr/bin/env python3
"""Write deterministic, partly compressible synthetic text for the xz workload.

The data imitates application logs: repeated vocabulary (compressible) mixed
with pseudo-random identifiers and numbers (not compressible), so xz's match
finder has real work to do. Same seed and size always give identical bytes;
the SHA-256 is printed so runs can prove they used the same input.
"""

import argparse
import hashlib
import random
import sys

WORDS = ("request", "response", "cache", "miss", "hit", "user", "session", "token",
         "render", "frame", "texture", "upload", "download", "query", "index", "table",
         "commit", "rollback", "retry", "timeout", "socket", "buffer", "worker", "thread",
         "allocate", "release", "swap", "page", "fault", "memory", "disk", "network")
LEVELS = ("DEBUG", "INFO", "INFO", "INFO", "WARN", "ERROR")


def generate(path, size_bytes, seed):
    generator = random.Random(seed)
    digest = hashlib.sha256()
    written = 0
    with open(path, "xb") as output:  # Never overwrite an existing input.
        while written < size_bytes:
            lines = []
            for _ in range(512):
                words = " ".join(generator.choice(WORDS) for _ in range(generator.randint(4, 12)))
                lines.append(f"{generator.randint(10**9, 2 * 10**9)} "
                             f"{generator.choice(LEVELS)} id={generator.getrandbits(48):012x} "
                             f"{words} n={generator.randint(0, 99999)}\n")
            chunk = "".join(lines).encode()[:size_bytes - written]
            output.write(chunk)
            digest.update(chunk)
            written += len(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--size-mib", type=int, required=True)
    parser.add_argument("--seed", type=int, default=20261025)
    args = parser.parse_args()
    if not 1 <= args.size_mib <= 4096:
        parser.error("--size-mib must be 1..4096")
    print(generate(args.path, args.size_mib * 1024 * 1024, args.seed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
