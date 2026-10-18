#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NATIVE="${TARZAN_NATIVE_TOOLS_ROOT:-$ROOT}"
BUILD="$ROOT/build/game-metal"
LLVM="$NATIVE/tools/build/llvm19"
PKGCONF="$ROOT/tools/pkgconf/bin"
FFMPEG="$ROOT/tools/ffmpeg/arm64"
CMAKE="$NATIVE/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
NINJA="$NATIVE/tools/build/ninja-1.13.1/ninja"
SDK="$(xcrun --show-sdk-path)"
GENERATED="$ROOT/generated/SLES-50350-symbolmap-entry"
LOG_DIR="$ROOT/logs"

for tool in "$CMAKE" "$NINJA" "$LLVM/bin/clang" "$LLVM/bin/clang++" "$PKGCONF/pkg-config"; do
  if [ ! -x "$tool" ]; then echo "Required build tool is not installed: $tool" >&2; exit 2; fi
done
for input in "$ROOT/extracted/disc/SLES_503.50" "$GENERATED/register_functions.cpp" \
    "$ROOT/build/metal-bridge/pcsx2/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib"; do
  if [ ! -f "$input" ]; then echo "Required Metal build input is missing: $input" >&2; exit 2; fi
done

mkdir -p "$BUILD" "$LOG_DIR"
export PATH="$PKGCONF:$PATH"
export PKG_CONFIG_PATH="$FFMPEG/lib/pkgconfig"
CXX_FLAGS="-nostdinc++ -isystem $LLVM/include/c++/v1 -stdlib=libc++"
LINK_FLAGS="-L$LLVM/lib -Wl,-rpath,$LLVM/lib -stdlib=libc++"

"$CMAKE" -S "$ROOT/integration/metal-root" -B "$BUILD" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="$LLVM/bin/clang" \
  -DCMAKE_CXX_COMPILER="$LLVM/bin/clang++" \
  -DCMAKE_OBJC_COMPILER="$LLVM/bin/clang" \
  -DCMAKE_OBJCXX_COMPILER="$LLVM/bin/clang++" \
  -DCMAKE_CXX_FLAGS="$CXX_FLAGS" \
  -DCMAKE_OBJCXX_FLAGS="$CXX_FLAGS" \
  -DCMAKE_EXE_LINKER_FLAGS="$LINK_FLAGS" \
  -DCMAKE_SHARED_LINKER_FLAGS="$LINK_FLAGS" \
  -DCMAKE_OSX_SYSROOT="$SDK" \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_PREFIX_PATH="$LLVM;$ROOT/tools/metal-deps/prefix" \
  -DTARZAN_NATIVE_TOOLS_ROOT="$NATIVE" \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DPKG_CONFIG_ARGN=--static \
  -DTARZAN_PS2RECOMP_SOURCE_DIR="$ROOT/deps/ps2recomp" \
  -DTARZAN_GENERATED_DIR="$GENERATED" \
  -DTARZAN_USE_METAL=ON \
  2>&1 | tee "$LOG_DIR/metal-game-configure.log"

"$NINJA" -C "$BUILD" -j2 tarzanMetalHostSmoke \
  2>&1 | tee "$LOG_DIR/metal-game-build.log"

"$NINJA" -C "$BUILD" -j2 ps2EntryRunner \
  2>&1 | tee -a "$LOG_DIR/metal-game-build.log"
