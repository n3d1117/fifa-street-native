#!/usr/bin/env python3
"""Keep generated translation units behind the stable guest ABI."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "generated" / "SLES-53064"
ALLOWED = {
    "ps2_guest_abi.h",
    "ps2_guest_macros.h",
    "ps2_recompiled_functions.h",
    "ps2_recompiled_stubs.h",
    "ps2_log.h",
}
INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')


def main():
    if not GENERATED.is_dir():
        print(f"missing generated directory: {GENERATED}", file=sys.stderr)
        return 1
    failures = []
    sources = sorted(GENERATED.glob("*.cpp"))
    for source in sources:
        for number, line in enumerate(source.read_text(errors="replace").splitlines(), 1):
            match = INCLUDE.match(line)
            if match and Path(match.group(1)).name not in ALLOWED:
                failures.append(f"{source.relative_to(ROOT)}:{number}: {match.group(1)}")
    emitters = [
        ROOT / "deps/ps2recomp/ps2xRecomp/src/lib/function_emitter.cpp",
        ROOT / "deps/ps2recomp/ps2xRecomp/src/lib/function_table_emitter.cpp",
        ROOT / "deps/ps2recomp/ps2xRecomp/src/lib/ps2_recompiler.cpp",
    ]
    forbidden = ("ps2_runtime.h", "ps2_runtime_macros.h", "ps2_syscalls.h", "ps2_stubs.h")
    for emitter in emitters:
        text = emitter.read_text()
        for header in forbidden:
            if f'#include \\"{header}\\"' in text or f'#include <{header}>' in text:
                failures.append(f"{emitter.relative_to(ROOT)} emits {header}")
    if failures:
        print("generated include boundary failed:", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"PASS: {len(sources)} generated C++ files use only guest ABI includes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
