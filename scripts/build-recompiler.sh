#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMAKE="$ROOT/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
mkdir -p "$ROOT/logs"
cd "$ROOT"
"$CMAKE" --preset recompiler 2>&1 | tee "$ROOT/logs/recompiler-configure.log"
"$CMAKE" --build --preset recompiler 2>&1 | tee "$ROOT/logs/recompiler-build.log"
