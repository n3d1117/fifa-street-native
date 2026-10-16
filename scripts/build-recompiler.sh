#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NATIVE="${TARZAN_NATIVE_TOOLS_ROOT:-$(dirname "$ROOT")/tarzan-native}"
SRC="$ROOT/source/PS2Recomp"
BUILD="$ROOT/build/recompiler"
PREFIX="$NATIVE/tools/build/llvm19"
CMAKE="$NATIVE/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
NINJA="$NATIVE/tools/build/ninja-1.13.1/ninja"
LOG="$ROOT/logs/recompiler-build.log"
SDK="$(xcrun --show-sdk-path)"

for tool in "$CMAKE" "$NINJA" "$PREFIX/bin/clang" "$PREFIX/bin/clang++"; do
  if [ ! -x "$tool" ]; then
    echo "Required build tool is not installed: $tool" >&2
    exit 2
  fi
done
if [ ! -d "$PREFIX/include/c++/v1" ] || [ ! -d "$PREFIX/lib" ]; then
  echo "LLVM 19 libc++ headers or libraries are missing under $PREFIX" >&2
  exit 2
fi

mkdir -p "$BUILD" "$ROOT/logs"
CXX_FLAGS="-nostdinc++ -isystem $PREFIX/include/c++/v1 -stdlib=libc++"
LINK_FLAGS="-L$PREFIX/lib -Wl,-rpath,$PREFIX/lib -stdlib=libc++"

"$CMAKE" -S "$SRC" -B "$BUILD" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_C_COMPILER="$PREFIX/bin/clang" \
  -DCMAKE_CXX_COMPILER="$PREFIX/bin/clang++" \
  -DCMAKE_CXX_FLAGS="$CXX_FLAGS" \
  -DCMAKE_EXE_LINKER_FLAGS="$LINK_FLAGS" \
  -DCMAKE_SHARED_LINKER_FLAGS="$LINK_FLAGS" \
  -DCMAKE_OSX_SYSROOT="$SDK" \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_PREFIX_PATH="$PREFIX" \
  -DPS2X_BUILD_RECOMP=ON \
  -DPS2X_BUILD_RUNTIME=OFF \
  -DPS2X_BUILD_ANALYZER=OFF \
  -DPS2X_BUILD_TEST=OFF \
  -DPS2X_BUILD_STUDIO=OFF \
  2>&1 | tee "$ROOT/logs/recompiler-configure.log"

"$NINJA" -C "$BUILD" -j2 ps2_recomp 2>&1 | tee "$LOG"
