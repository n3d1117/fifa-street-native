#!/usr/bin/env bash
# Build LLVM 19 libc++/libc++abi for one Apple embedded platform.
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
SRC="$ROOT/tools/embedded/src/llvm-project"
BUILD="$ROOT/build/embedded/$PLATFORM/libcxx"
PREFIX="$ROOT/tools/embedded/$PLATFORM/libcxx"
LOG="$ROOT/build/embedded/logs"
source "$ROOT/tools/embedded-cmake.sh"
mkdir -p "$ROOT/tools/embedded/src" "$BUILD" "$PREFIX" "$LOG"
for tool in "$LLVM/bin/clang++" "$CMAKE" "$NINJA"; do
  [[ -x "$tool" ]] || { echo "Missing $tool" >&2; exit 2; }
done
if [[ ! -d "$SRC/llvm" ]]; then
  git clone --depth 1 --branch llvmorg-19.1.7 https://github.com/llvm/llvm-project.git "$SRC"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == cd708029e0b2869e80abe31ddb175f7c35361f90 ]] || {
  echo "llvm-project is not at pinned LLVM 19.1.7 commit" >&2; exit 1;
}
  embedded_cmake_configure "$CMAKE" "$ROOT/cmake/toolchain-llvm19-embedded.cmake" "$PLATFORM" "$LLVM" \
    -U CMAKE_C_FLAGS -U CMAKE_CXX_FLAGS -S "$SRC/runtimes" -B "$BUILD" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$NINJA" \
    -DCMAKE_TOOLCHAIN_FILE="$ROOT/cmake/toolchain-llvm19-embedded.cmake" \
    -DCMAKE_SYSTEM_NAME:STRING=Darwin \
    -DRX_APPLE_PLATFORM="$PLATFORM" \
    -DAPPLE=ON \
    -DRX_BUILDING_LIBCXX=ON \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="$LLVM/bin/clang" \
    -DCMAKE_CXX_COMPILER="$LLVM/bin/clang++" \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_INSTALL_NAME_DIR=@rpath \
    -DLLVM_ENABLE_RUNTIMES='libcxx;libcxxabi' \
    -DLLVM_ENABLE_PER_TARGET_RUNTIME_DIR=OFF \
    -DLIBCXX_ENABLE_SHARED=ON -DLIBCXX_ENABLE_STATIC=ON \
    -DLIBCXXABI_ENABLE_SHARED=ON -DLIBCXXABI_ENABLE_STATIC=ON \
    -DLIBCXX_INSTALL_LIBRARY=ON -DLIBCXX_INSTALL_HEADERS=ON \
    -DLIBCXX_INSTALL_LIBRARY_DIR=lib -DLIBCXXABI_INSTALL_LIBRARY_DIR=lib \
    -DLIBCXXABI_USE_LLVM_UNWINDER=OFF \
    -DLIBCXX_USE_COMPILER_RT=ON -DLIBCXXABI_USE_COMPILER_RT=ON \
    -DLIBCXX_INCLUDE_TESTS=OFF -DLIBCXXABI_INCLUDE_TESTS=OFF \
    2>&1 | tee "$LOG/$PLATFORM-libcxx-configure.log"
"$NINJA" -C "$BUILD" -j"$JOBS" install-cxx install-cxxabi \
  2>&1 | tee "$LOG/$PLATFORM-libcxx-build.log"
[[ -f "$PREFIX/lib/libc++.a" && -f "$PREFIX/lib/libc++abi.a" && \
   -f "$PREFIX/lib/libc++.1.dylib" ]] || {
  echo "Expected static runtimes and shared libc++ were not installed" >&2; exit 1;
}
echo "== libc++ ready: $PREFIX"
