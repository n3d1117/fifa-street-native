#!/usr/bin/env python3
"""Verify files that must stay identical in the original and Metal runtime trees.

Platform CMake files and runtime implementation files with private Metal tracing
are intentionally excluded. This is a consistency gate, not a functional test.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
original = ROOT / "source/PS2Recomp"
metal = ROOT / "source/PS2Recomp-metal"
relative = set()
for folder in ("ps2xIOP/src", "ps2xIOP/include", "ps2xIOP/tests"):
    for tree in (original, metal):
        relative.update(path.relative_to(tree) for path in (tree / folder).rglob("*")
                        if path.is_file() and path.suffix in (".cpp", ".h", ".hpp"))
relative.update(Path(name) for name in (
    "ps2xRuntime/include/ps2_runtime.h",
    "ps2xRuntime/include/runtime/ps2_host_arena.h",
    "ps2xRuntime/src/lib/Kernel/Syscalls/System.cpp",
    "ps2xTest/src/ps2_runtime_kernel_tests.cpp"))
different = []
for name in sorted(relative):
    left, right = original / name, metal / name
    if not left.is_file() or not right.is_file() or left.read_bytes() != right.read_bytes():
        different.append(str(name))
if different:
    print("Runtime mirror mismatch:\n" + "\n".join(different))
    sys.exit(1)
print(f"Runtime mirror consistency: {len(relative)} files match.")
