#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NATIVE="${TARZAN_NATIVE_TOOLS_ROOT:-$(dirname "$ROOT")/tarzan-native}"
LLVM="$NATIVE/tools/build/llvm19"
CMAKE="$NATIVE/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
NINJA="$NATIVE/tools/build/ninja-1.13.1/ninja"
SDK="$(xcrun --show-sdk-path)"
BUILD="$ROOT/build/spu2-backend"
LOG="$ROOT/logs/spu2-backend-build.log"
PCSX2="$ROOT/source/deps/pcsx2-2.8.2"
SOURCE="$ROOT/source/fukami/tools/pcsx2-spu2"

for tool in "$CMAKE" "$NINJA" "$LLVM/bin/clang++"; do
    if [ ! -x "$tool" ]; then
        echo "Required build tool is not installed: $tool" >&2
        exit 2
    fi
done
if [ ! -f "$PCSX2/pcsx2/SPU2/spu2sys.cpp" ]; then
    echo "Pinned PCSX2 2.8.2 source is missing: $PCSX2" >&2
    exit 2
fi

mkdir -p "$BUILD" "$ROOT/logs"
CXX_FLAGS="-nostdinc++ -isystem $LLVM/include/c++/v1 -stdlib=libc++"
LINK_FLAGS="-L$LLVM/lib -Wl,-rpath,$LLVM/lib -stdlib=libc++"

{
    "$CMAKE" -S "$SOURCE" -B "$BUILD" -G Ninja \
        -DCMAKE_MAKE_PROGRAM="$NINJA" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER="$LLVM/bin/clang++" \
        -DCMAKE_CXX_FLAGS="$CXX_FLAGS" \
        -DCMAKE_EXE_LINKER_FLAGS="$LINK_FLAGS" \
        -DCMAKE_OSX_SYSROOT="$SDK" \
        -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 \
        -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DRRV_PCSX2_SOURCE_DIR="$PCSX2" \
        -DRRV_SPU2_BUILD_TESTS=ON \
        -DRRV_SPU2_TSAN_TESTS=OFF
    "$NINJA" -C "$BUILD" -j2
    "$CMAKE" -E chdir "$BUILD" \
        "$NATIVE/tools/build/cmake-3.31.12/CMake.app/Contents/bin/ctest" --output-on-failure
} 2>&1 | tee "$LOG"
