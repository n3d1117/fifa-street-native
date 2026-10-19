#!/usr/bin/env python3
"""Build parallelism derived from the host; the single place the formula lives.

jobs = min(cpu_count, max(1, floor(RAM_GB / GB_PER_JOB)))
GB_PER_JOB is the RSS budget of one heavy clang job (largest single TU peak
footprint measured ~2 GB, ordinary unity batches ~0.6 GB). RX_JOBS overrides.
The heaviest translation units additionally share a Ninja pool of size 1
(CMakeLists.txt, `heavy`), so they never compile concurrently.
"""
import os

GB_PER_JOB = 1.5


def jobs():
    override = os.environ.get("RX_JOBS", "")
    if override.isdigit() and int(override) > 0:
        return int(override)
    ram_gb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    return min(os.cpu_count() or 1, max(1, int(ram_gb // GB_PER_JOB)))


if __name__ == "__main__":
    print(jobs())
