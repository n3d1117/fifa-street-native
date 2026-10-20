#!/usr/bin/env python3
"""Inline PCSX2's Metal shader sources into one translation unit.

MTLDevice newLibraryWithSource: compiles a single source string and cannot
resolve quoted #include files, so every quoted include is expanded in place
(honouring #pragma once) and the shaders are concatenated in PCSX2's CMake
order. System includes such as <metal_stdlib> are left for the compiler.

usage: inline_metal_sources.py OUTPUT DEPFILE SHADER...
"""
import pathlib
import re
import sys

INCLUDE = re.compile(r'\s*#\s*include\s+"([^"]+)"')


def main() -> int:
    output, depfile, *shaders = sys.argv[1:]
    if not shaders:
        raise SystemExit(__doc__)
    once: set[pathlib.Path] = set()
    inputs: list[pathlib.Path] = []

    def expand(path: pathlib.Path) -> str:
        path = path.resolve()
        inputs.append(path)
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() == "#pragma once":
                if path in once:
                    return ""
                once.add(path)
                continue
            match = INCLUDE.match(line)
            lines.append(expand(path.parent / match.group(1)) if match else line)
        return "\n".join(lines)

    text = "\n".join(expand(pathlib.Path(shader)) for shader in shaders) + "\n"
    out = pathlib.Path(output)
    if not out.exists() or out.read_text(encoding="utf-8") != text:
        out.write_text(text, encoding="utf-8")
    deps = " ".join(str(p).replace(" ", "\\ ") for p in dict.fromkeys(inputs))
    pathlib.Path(depfile).write_text(f"{str(out).replace(' ', chr(92) + ' ')}: {deps}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
