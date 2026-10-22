#!/usr/bin/env bash
# Build PCSX2's Metal GS bridge, SoundTouch, and the SPU2 backend for one
# embedded Apple platform.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && /bin/pwd -P)"
export PWD="$ROOT"
PLATFORM="${1:-}"
case "$PLATFORM" in
  iphonesimulator|iphoneos|appletvos|appletvsimulator) ;;
  *) echo "Usage: $0 <iphonesimulator|iphoneos|appletvos|appletvsimulator>" >&2; exit 2 ;;
esac
export DEVELOPER_DIR="${DEVELOPER_DIR:-$HOME/Developer/Xcode-12.5.1/Xcode.app/Contents/Developer}"
LLVM="$ROOT/tools/build/llvm19"
CMAKE="$ROOT/tools/build/cmake-3.31.12/CMake.app/Contents/bin/cmake"
NINJA="$ROOT/tools/build/ninja-1.13.1/ninja"
JOBS="${RX_JOBS:-$(python3 "$ROOT/tools/jobs.py") }"
JOBS="${JOBS% }"
PCSX2="$ROOT/deps/pcsx2"
BRIDGE="$ROOT/bridge/gs"
PREFIX="$ROOT/tools/embedded/$PLATFORM/prefix"
PLATFORM_BUILD="$ROOT/build/embedded/$PLATFORM"
BRIDGE_BUILD="$PLATFORM_BUILD/metal-bridge"
SPU2_BUILD="$PLATFORM_BUILD/spu2-backend"
LOG="$ROOT/build/embedded/logs"
source "$ROOT/tools/embedded-cmake.sh"
mkdir -p "$PLATFORM_BUILD" "$LOG"
for tool in "$LLVM/bin/clang++" "$CMAKE" "$NINJA"; do
  [[ -x "$tool" ]] || { echo "Missing $tool" >&2; exit 2; }
done
git -C "$PCSX2" rev-parse --show-toplevel >/dev/null || { echo "Missing deps/pcsx2" >&2; exit 2; }

embedded_cmake_configure "$CMAKE" "$ROOT/cmake/toolchain-llvm19-embedded.cmake" "$PLATFORM" "$LLVM" \
  -S "$PCSX2" -B "$BRIDGE_BUILD" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchain-llvm19-embedded.cmake" \
  -DRX_APPLE_PLATFORM="$PLATFORM" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_PREFIX_PATH="$PREFIX" \
  -DFFMPEG_ROOT="$PREFIX" \
  -DENABLE_QT_UI=OFF -DENABLE_TESTS=OFF -DENABLE_GSRUNNER=OFF \
  -DUSE_VULKAN=OFF -DSKIP_POSTPROCESS_BUNDLE=ON \
  -DRRV_PCSX2_GS_BRIDGE_SOURCE_DIR="$BRIDGE" \
  -DPCSX2_COMPILER_RT_OS_VERSION_CHECK_SOURCE="$ROOT/tools/metal-deps/compat/llvm19-os_version_check.c" \
  -DCMAKE_SHARED_LINKER_FLAGS="-L$ROOT/tools/embedded/$PLATFORM/libcxx/lib -lc++ -lc++abi -Wl,-rpath,@loader_path" \
  2>&1 | tee "$LOG/$PLATFORM-bridge-configure.log"
CMAKE_BUILD_PARALLEL_LEVEL="$JOBS" "$CMAKE" --build "$BRIDGE_BUILD" --target rrv-pcsx2-gs-bridge \
  2>&1 | tee "$LOG/$PLATFORM-bridge-build.log"

embedded_cmake_configure "$CMAKE" "$ROOT/cmake/toolchain-llvm19-embedded.cmake" "$PLATFORM" "$LLVM" \
  -S "$ROOT/bridge/spu2" -B "$SPU2_BUILD" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchain-llvm19-embedded.cmake" \
  -DRX_APPLE_PLATFORM="$PLATFORM" \
  -DCMAKE_BUILD_TYPE=Release \
  -DRRV_PCSX2_SOURCE_DIR="$PCSX2" \
  -DRRV_SPU2_BUILD_TESTS=OFF -DRRV_SPU2_TSAN_TESTS=OFF \
  -DCMAKE_EXE_LINKER_FLAGS="-L$ROOT/tools/embedded/$PLATFORM/libcxx/lib -stdlib=libc++" \
  2>&1 | tee "$LOG/$PLATFORM-spu2-configure.log"
CMAKE_BUILD_PARALLEL_LEVEL="$JOBS" "$CMAKE" --build "$SPU2_BUILD" \
  2>&1 | tee "$LOG/$PLATFORM-spu2-build.log"

echo "== embedded bridge and SPU2 ready: $PLATFORM_BUILD"
