#!/usr/bin/env bash
# Build only the pinned PCSX2 GS + Fukami's native Metal bridge, in an isolated copy.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
native_root="${TARZAN_NATIVE_TOOLS_ROOT:-$(dirname "$root")/tarzan-native}"
pcsx2="$root/source/deps/pcsx2-2.8.2"
patched="$root/source/deps/pcsx2-2.8.2-bridge"
build="$root/build/metal-bridge/pcsx2"
resources="$root/build/metal-bridge/pcsx2-2.8.2-resources"
bridge_upstream="$root/source/fukami/tools/pcsx2-gs-bridge"
bridge="$root/source/deps/pcsx2-gs-bridge-metal"
csr_patch="$root/tools/patches/bridge-csr-ack.patch"
depth_patch="$root/tools/patches/pcsx2-null-depth-range.patch"
patchfile="$root/source/fukami/tools/patches/pcsx2-gs-bridge-target.patch"
sdk_patch="$root/tools/patches/pcsx2-sdk11.patch"
pin=fd9d310ccbb6b8b62c976da8886a3c8fd3a10ff3
expected_patch=a5d0088d69219ef4703863d38c8abb81d1fdb5b7ae7bbdcea3d9a1b0eb5ff281
expected_sdk_patch=47efc75a9303b324394fad4aa084bb7b1cf0579749e6f9d0158256bfc3f694b2
expected_csr_patch=263fefe93aa169ef7f6eabfb02ffd0a1c690d2b8d7c44320b16114725abd8e91
expected_depth_patch=dba40a78b743e57cfb85bf72f616aa95a33725fef52d473fcf4ae56fa281114a
tools="$native_root/tools/build"
cmake="$tools/cmake-3.31.12/CMake.app/Contents/bin/cmake"
ninja="$tools/ninja-1.13.1/ninja"
llvm="$tools/llvm19/bin"
llvm_lib="$tools/llvm19/lib"
deps_prefix="$root/tools/metal-deps/prefix"

[[ "$(shasum -a 256 "$patchfile" | awk '{print $1}')" == "$expected_patch" ]] || { echo 'Fukami bridge patch hash mismatch' >&2; exit 1; }
[[ "$(shasum -a 256 "$sdk_patch" | awk '{print $1}')" == "$expected_sdk_patch" ]] || { echo 'macOS 11 SDK compatibility patch hash mismatch' >&2; exit 1; }
[[ "$(shasum -a 256 "$csr_patch" | awk '{print $1}')" == "$expected_csr_patch" ]] || { echo 'Metal CSR acknowledgement patch hash mismatch' >&2; exit 1; }
[[ "$(shasum -a 256 "$depth_patch" | awk '{print $1}')" == "$expected_depth_patch" ]] || { echo 'GS depth range patch hash mismatch' >&2; exit 1; }
[[ "$(git -C "$pcsx2" rev-parse HEAD)" == "$pin" ]] || { echo "Source checkout is not $pin" >&2; exit 1; }
[[ -z "$(git -C "$pcsx2" status --porcelain)" ]] || { echo 'Pinned source checkout is dirty' >&2; exit 1; }
for f in default.metallib Metal22.metallib Metal23.metallib; do [[ -s "$resources/$f" ]] || { echo "Missing verified $resources/$f; run scripts/prepare-metal-bridge.py" >&2; exit 1; }; done

# Keep the upstream Fukami bridge checkout read-only. The game needs one small
# owner-thread CSR synchronization entry point, so build from a private source
# copy with this content-hashed patch applied.
if [[ ! -f "$bridge/bridge.cpp" ]]; then
    mkdir -p "$bridge"
    cp -R "$bridge_upstream/." "$bridge/"
fi
if git -C "$bridge" apply --reverse --check "$csr_patch" >/dev/null 2>&1; then
    : # the retained private copy already contains the reviewed extension
elif git -C "$bridge" apply --check "$csr_patch"; then
    git -C "$bridge" apply "$csr_patch"
else
    echo 'Private bridge source differs from the reviewed CSR patch base' >&2
    exit 1
fi

if [[ ! -d "$patched/.git" ]]; then
    git clone --no-local --no-checkout "$pcsx2" "$patched"
    git -C "$patched" checkout --detach "$pin"
    git -C "$patched" apply --check "$patchfile"
    git -C "$patched" apply --index "$patchfile"
    git -C "$patched" diff --cached --check
    tree=$(git -C "$patched" write-tree)
    [[ "$tree" == 6bf1a4edd0a772afaee775b772a9d148a46b58a3 ]] || { echo "Unexpected patched tree $tree" >&2; exit 1; }
else
    [[ "$(git -C "$patched" rev-parse HEAD)" == "$pin" ]] || { echo 'Existing patched copy has wrong base revision' >&2; exit 1; }
    if [[ -f "$root/patches/pcsx2-metal.patch" ]] && git -C "$patched" apply --reverse --check "$root/patches/pcsx2-metal.patch" >/dev/null 2>&1; then
        : # Full source snapshot restored without altering the upstream index.
    else
        [[ "$(git -C "$patched" write-tree)" == 6bf1a4edd0a772afaee775b772a9d148a46b58a3 ]] || { echo 'Existing patched copy differs from reviewed patch tree' >&2; exit 1; }
    fi
fi
if git -C "$patched" apply --reverse --check "$sdk_patch" >/dev/null 2>&1; then
    : # already applied in the retained private build copy
elif git -C "$patched" apply --check "$sdk_patch"; then
    git -C "$patched" apply "$sdk_patch"
else
    echo 'Cannot apply the reviewed macOS 11 SDK compatibility patch' >&2
    exit 1
fi

if git -C "$patched" apply --reverse --check "$depth_patch" >/dev/null 2>&1; then
    : # already applied in the retained private build copy
elif git -C "$patched" apply --check "$depth_patch"; then
    git -C "$patched" apply "$depth_patch"
else
    echo 'Cannot apply the reviewed GS depth range patch' >&2
    exit 1
fi

export PATH="$llvm:$tools/ninja-1.13.1:$PATH"
export CC="${CC:-$llvm/clang}"
export CXX="${CXX:-$llvm/clang++}"
export OBJC="${OBJC:-$llvm/clang}"
export OBJCXX="${OBJCXX:-$llvm/clang++}"
export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-$(sysctl -n hw.ncpu)}"
export PKG_CONFIG_PATH="$deps_prefix/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
"$cmake" -S "$patched" -B "$build" -G Ninja \
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
