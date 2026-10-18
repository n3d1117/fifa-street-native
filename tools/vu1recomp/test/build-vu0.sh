#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
RUNTIME="${PS2X_VU0_RUNTIME:-$ROOT/source/PS2Recomp-metal/ps2xRuntime}"
GEN="${PS2X_VU0GEN_DIR:-$ROOT/generated/vu0-native}"
OUT="${PS2X_VU0TEST_OUT:-$ROOT/build/vu0-replay}"
SSE2NEON="${PS2X_SSE2NEON_DIR:-$ROOT/build/game-metal/_deps/sse2neon-src}"
LLVM_ROOT="${PS2X_LLVM19_ROOT:-$ROOT/tools/build/llvm19}"
CXX="${CXX:-$LLVM_ROOT/bin/clang++}"
LIBCXX_INCLUDE="${PS2X_LIBCXX_INCLUDE:-$LLVM_ROOT/include/c++/v1}"

if [[ "$(uname -s)" != Darwin ]]; then
    echo "The VU0 replay harness requires the local macOS ARM64 LLVM 19 toolchain." >&2
    exit 2
fi
SDKROOT="${SDKROOT:-$(xcrun --sdk macosx --show-sdk-path)}"
for required in "$CXX" "$LIBCXX_INCLUDE"/vector "$SSE2NEON"/sse2neon.h \
                "$RUNTIME/src/lib/vu/ps2_vu1_core.cpp" \
                "$RUNTIME/src/lib/vu/ps2_vu0_recomp.cpp"; do
    if [[ ! -e "$required" ]]; then
        echo "Missing required VU0 replay input: $required" >&2
        exit 2
    fi
done
shopt -s nullglob
generated=("$GEN"/vu0rec_*.cpp)
if (( ${#generated[@]} == 0 )); then
    echo "No generated VU0 sources in $GEN. Generate a VU0 catalog first." >&2
    exit 2
fi

mkdir -p "$OUT"
common=(-std=gnu++20 -arch arm64 -isysroot "$SDKROOT" -mmacosx-version-min=11.0
        -nostdinc++ -stdlib=libc++ -isystem "$LIBCXX_INCLUDE"
        -DUSE_SSE2NEON -DNDEBUG -fno-lto
        -I"$SSE2NEON" -I"$RUNTIME/include" -I"$RUNTIME/src/lib/Kernel")
oracle=(-O2 -fno-fast-math -ffp-contract=off)
native=(-O2 -ffp-contract=off -frounding-math)
objects=()

compile() {
    local src="$1" obj="$2"; shift 2
    # Always compile: the harness is small, and this avoids stale standalone
    # objects when any transitive runtime/generated header changes.
    "$CXX" "${common[@]}" "$@" -c "$src" -o "$obj"
}

for src in "$RUNTIME/src/lib/vu/ps2_vu1_core.cpp" \
           "$RUNTIME/src/lib/vu/ps2_vu1_upper.cpp" \
           "$RUNTIME/src/lib/vu/ps2_vu1_lower.cpp" \
           "$RUNTIME/src/lib/vu/ps2_vu0_recomp.cpp" \
           "$HERE/vu0_replay.cpp"; do
    obj="$OUT/$(basename "${src%.cpp}").o"
    compile "$src" "$obj" "${oracle[@]}"
    objects+=("$obj")
done

for src in "${generated[@]}"; do
    obj="$OUT/$(basename "${src%.cpp}").o"
    compile "$src" "$obj" "${native[@]}"
    objects+=("$obj")
done

compile "$HERE/vu1_stubs.cpp" "$OUT/stubs.o" -O2
"$CXX" -arch arm64 -isysroot "$SDKROOT" -stdlib=libc++ -L"$LLVM_ROOT/lib" \
    -Wl,-rpath,"$LLVM_ROOT/lib" "${objects[@]}" "$OUT/stubs.o" -o "$OUT/vu0_replay"
echo "Built $OUT/vu0_replay"
echo "Run: $OUT/vu0_replay $ROOT/reports/vu0-skinning.code.bin $ROOT/reports/vu0-skinning.data.before.bin $ROOT/reports/vu0-skinning.state.before.txt"
