#!/usr/bin/env bash
# Configure and build the Metal GS bridge from deps/pcsx2 (deps/fukami supplies the bridge target).
# deps/ is created by `python3 tools/deps.py sync`.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
native_root="${TARZAN_NATIVE_TOOLS_ROOT:-$root}"
pcsx2="$root/deps/pcsx2"
bridge="$root/deps/fukami/tools/pcsx2-gs-bridge"
build="$root/build/metal-bridge/pcsx2"
resources="$root/build/metal-bridge/pcsx2-2.8.2-resources"
tools="$native_root/tools/build"
cmake="$tools/cmake-3.31.12/CMake.app/Contents/bin/cmake"
ninja="$tools/ninja-1.13.1/ninja"
llvm="$tools/llvm19/bin"
llvm_lib="$tools/llvm19/lib"
deps_prefix="$root/tools/metal-deps/prefix"

[[ -d "$pcsx2/.git" && -d "$bridge" ]] || { echo 'Missing deps/; run python3 tools/deps.py sync' >&2; exit 1; }
for f in default.metallib Metal22.metallib Metal23.metallib; do [[ -s "$resources/$f" ]] || { echo "Missing verified $resources/$f; run scripts/prepare-metal-bridge.py" >&2; exit 1; }; done

export PATH="$llvm:$tools/ninja-1.13.1:$PATH"
export CC="${CC:-$llvm/clang}"
export CXX="${CXX:-$llvm/clang++}"
export OBJC="${OBJC:-$llvm/clang}"
export OBJCXX="${OBJCXX:-$llvm/clang++}"
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-$(sysctl -n hw.ncpu)}"
export PKG_CONFIG_PATH="$deps_prefix/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
"$cmake" -S "$pcsx2" -B "$build" -G Ninja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_MAKE_PROGRAM="$ninja" \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 \
    -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
    -DCMAKE_OBJC_COMPILER="$OBJC" -DCMAKE_OBJCXX_COMPILER="$OBJCXX" \
    -DCMAKE_OSX_SYSROOT="$(xcrun --show-sdk-path)" \
    -DCMAKE_PREFIX_PATH="$deps_prefix" \
    -DENABLE_QT_UI=OFF -DENABLE_TESTS=OFF -DENABLE_GSRUNNER=OFF \
    -DUSE_VULKAN=OFF -DSKIP_POSTPROCESS_BUNDLE=ON \
    -DRRV_PCSX2_GS_BRIDGE_SOURCE_DIR="$bridge" \
    -DRRV_PCSX2_GS_METAL_RESOURCE_DIR="$resources" \
    -DPCSX2_COMPILER_RT_OS_VERSION_CHECK_SOURCE="$root/tools/metal-deps/compat/llvm19-os_version_check.c" \
    -DCMAKE_C_FLAGS='-mmacosx-version-min=11.0' \
    -DCMAKE_CXX_FLAGS='-mmacosx-version-min=11.0' \
    -DCMAKE_OBJC_FLAGS='-mmacosx-version-min=11.0' \
    -DCMAKE_OBJCXX_FLAGS='-mmacosx-version-min=11.0' \
    -DCMAKE_EXE_LINKER_FLAGS="-L$llvm_lib -Wl,-rpath,$llvm_lib -stdlib=libc++" \
    -DCMAKE_SHARED_LINKER_FLAGS="-L$llvm_lib -Wl,-rpath,$llvm_lib -stdlib=libc++" \
    -DCMAKE_MODULE_LINKER_FLAGS="-L$llvm_lib -Wl,-rpath,$llvm_lib -stdlib=libc++"
"$cmake" --build "$build" --target rrv-pcsx2-gs-bridge
echo "Bridge: $build/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib"
