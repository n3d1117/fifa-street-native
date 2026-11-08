#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
export PWD="$ROOT"
PLATFORM="${1:-}"
case "$PLATFORM" in
  iphonesimulator|iphoneos|appletvos|appletvsimulator) ;;
  *) echo "usage: $0 <iphonesimulator|iphoneos|appletvos|appletvsimulator>" >&2; exit 2 ;;
esac

export DEVELOPER_DIR="${DEVELOPER_DIR:-$(xcode-select -p)}"
export RX_JOBS="${RX_JOBS:-3}"
BUILD="$ROOT/build/embedded/$PLATFORM/game"
LLVM="${RX_LLVM:-$ROOT/tools/build/llvm19}"
NINJA="$ROOT/tools/build/ninja-1.13.1/ninja"
CMAKE="$ROOT/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
export ZERO_AR_DATE=1
source "$ROOT/tools/embedded-cmake.sh"

embedded_cmake_configure "$CMAKE" "$ROOT/cmake/toolchain-llvm19-embedded.cmake" "$PLATFORM" "$LLVM" \
  -S "$ROOT" -B "$BUILD" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchain-llvm19-embedded.cmake" \
  -DRX_APPLE_PLATFORM="$PLATFORM" -DRX_LLVM="$LLVM" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$LLVM;$ROOT/tools/embedded/$PLATFORM/prefix" \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
"$CMAKE" --build "$BUILD" --target ps2EntryRunner --parallel "$RX_JOBS"
