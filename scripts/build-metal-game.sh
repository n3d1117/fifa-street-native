#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CMAKE="$ROOT/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
mkdir -p "$ROOT/logs"
cd "$ROOT"
"$CMAKE" --preset release 2>&1 | tee "$ROOT/logs/metal-game-configure.log"
"$CMAKE" --build --preset release 2>&1 | tee "$ROOT/logs/metal-game-build.log"
