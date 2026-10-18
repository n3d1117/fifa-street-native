#!/usr/bin/env bash
# Install the pinned build toolchain under tools/build: CMake, Ninja and LLVM 19.
# Idempotent. If TARZAN_LLVM points at an existing LLVM 19 install (with
# bin/clang++), the LLVM build is skipped; CMake and Ninja are still fetched.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
B="$ROOT/tools/build"
LOGS="$ROOT/logs"
JOBS="${TZ_JOBS:-$(python3 "$ROOT/tools/jobs.py")}"
CMAKE_VER=3.31.12
CMAKE_URL="https://github.com/Kitware/CMake/releases/download/v$CMAKE_VER/cmake-$CMAKE_VER-macos-universal.tar.gz"
CMAKE_SHA=799af7fd545db9bf1b9cfe72f8095880e727a2d4e0df0e3dffc3bc7b95c2d3b0
NINJA_VER=1.13.1
NINJA_URL="https://github.com/ninja-build/ninja/releases/download/v$NINJA_VER/ninja-mac.zip"
NINJA_SHA=da7797794153629aca5570ef7c813342d0be214ba84632af886856e8f0063dd9
LLVM_TAG=llvmorg-19.1.7
LLVM_COMMIT=cd708029e0b2869e80abe31ddb175f7c35361f90
CMAKE="$B/cmake-$CMAKE_VER/CMake.app/Contents/bin/cmake"
NINJA="$B/ninja-$NINJA_VER/ninja"
mkdir -p "$B" "$LOGS"

fetch() { # url sha256 dest
  if [[ -f "$3" && "$(shasum -a 256 "$3" | awk '{print $1}')" == "$2" ]]; then return; fi
  curl -fL --retry 3 "$1" -o "$3.part"
  [[ "$(shasum -a 256 "$3.part" | awk '{print $1}')" == "$2" ]] || { echo "SHA-256 mismatch: $1" >&2; rm -f "$3.part"; exit 1; }
  mv "$3.part" "$3"
}

if [[ ! -x "$CMAKE" ]]; then
  echo "== CMake $CMAKE_VER"
  fetch "$CMAKE_URL" "$CMAKE_SHA" "$B/cmake-$CMAKE_VER.tar.gz"
  tmp="$(mktemp -d "$B/cmake-extract.XXXX")"
  tar -xzf "$B/cmake-$CMAKE_VER.tar.gz" -C "$tmp"
  mkdir -p "$B/cmake-$CMAKE_VER"
  mv "$tmp/cmake-$CMAKE_VER-macos-universal/CMake.app" "$B/cmake-$CMAKE_VER/"
  rm -rf "$tmp"
fi

if [[ ! -x "$NINJA" ]]; then
  echo "== Ninja $NINJA_VER"
  fetch "$NINJA_URL" "$NINJA_SHA" "$B/ninja-$NINJA_VER.zip"
  mkdir -p "$B/ninja-$NINJA_VER"
  unzip -oq "$B/ninja-$NINJA_VER.zip" -d "$B/ninja-$NINJA_VER"
  chmod +x "$NINJA"
fi

if [[ -n "${TARZAN_LLVM:-}" && -x "$TARZAN_LLVM/bin/clang++" ]]; then
  echo "== LLVM: using TARZAN_LLVM=$TARZAN_LLVM"
  exit 0
fi

PREFIX="$B/llvm19"
if [[ -x "$PREFIX/bin/clang++" && -f "$PREFIX/lib/libc++.1.dylib" ]]; then
  echo "== LLVM 19 already installed: $PREFIX"
  exit 0
fi

SRC="$B/src/llvm-project"
SDK="$(xcrun --show-sdk-path)"
if [[ ! -d "$SRC/llvm" ]]; then
  echo "== Fetching llvm-project $LLVM_TAG"
  mkdir -p "$B/src"
  rm -rf "$SRC"
  git clone -q --depth 1 --branch "$LLVM_TAG" https://github.com/llvm/llvm-project.git "$SRC"
fi
[[ "$(git -C "$SRC" rev-parse HEAD)" == "$LLVM_COMMIT" ]] || { echo "llvm-project is not at $LLVM_COMMIT" >&2; exit 1; }

# Stage 1: clang with the system compiler.
BUILD="$B/src/build-llvm19"
mkdir -p "$BUILD" "$PREFIX"
if [[ ! -f "$BUILD/build.ninja" ]]; then
  "$CMAKE" -S "$SRC/llvm" -B "$BUILD" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$NINJA" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_OSX_SYSROOT="$SDK" \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DLLVM_ENABLE_PROJECTS=clang \
    -DLLVM_TARGETS_TO_BUILD=AArch64 \
    -DLLVM_INCLUDE_TESTS=OFF \
    -DLLVM_INCLUDE_EXAMPLES=OFF \
    -DLLVM_INCLUDE_BENCHMARKS=OFF \
    -DLLVM_ENABLE_DOXYGEN=OFF \
    -DLLVM_ENABLE_SPHINX=OFF \
    -DLLVM_ENABLE_TERMINFO=OFF \
    -DLLVM_ENABLE_Z3_SOLVER=OFF \
    -DCLANG_ENABLE_STATIC_ANALYZER=OFF \
    -DCLANG_ENABLE_ARCMT=OFF \
    -DLLVM_PARALLEL_LINK_JOBS=1 2>&1 | tee "$LOGS/llvm19-configure.log"
fi
"$NINJA" -C "$BUILD" -j"$JOBS" install-llvm-headers install-llvm-libraries install-llvm-config install-llvm-tblgen install-cmake-exports install-clang-cmake-exports install-clang install-clang-resource-headers 2>&1 | tee -a "$LOGS/llvm19-build.log"

# Stage 2: libc++/libc++abi built with the new clang.
RBUILD="$B/src/build-llvm19-runtimes"
mkdir -p "$RBUILD"
if [[ ! -f "$RBUILD/build.ninja" ]]; then
  "$CMAKE" -S "$SRC/runtimes" -B "$RBUILD" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$NINJA" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="$PREFIX/bin/clang" \
    -DCMAKE_CXX_COMPILER="$PREFIX/bin/clang++" \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_OSX_SYSROOT="$SDK" \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_INSTALL_NAME_DIR=@rpath \
    -DCMAKE_INSTALL_RPATH="$PREFIX/lib" \
    -DLLVM_ENABLE_RUNTIMES='libcxx;libcxxabi' \
    -DLLVM_ENABLE_PER_TARGET_RUNTIME_DIR=OFF \
    -DLIBCXX_INSTALL_LIBRARY_DIR=lib \
    -DLIBCXXABI_INSTALL_LIBRARY_DIR=lib \
    -DLIBCXXABI_USE_LLVM_UNWINDER=OFF \
    -DLIBCXX_INCLUDE_TESTS=OFF \
    -DLIBCXXABI_INCLUDE_TESTS=OFF \
    2>&1 | tee "$LOGS/llvm19-runtimes-configure.log"
fi
"$NINJA" -C "$RBUILD" -j"$JOBS" install-cxx install-cxxabi 2>&1 | tee -a "$LOGS/llvm19-runtimes-build.log"
echo "== LLVM 19 installed: $PREFIX"
