#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
RUNTIME="${PS2X_VU1_RUNTIME:-$ROOT/deps/ps2recomp/ps2xRuntime}"
GEN="${PS2X_VU1GEN_DIR:-$ROOT/generated/vu1-native}"
OUT="${PS2X_VU1TEST_OUT:-$ROOT/build/vu1-replay}"
SSE2NEON="${PS2X_SSE2NEON_DIR:-$ROOT/build/game-metal/_deps/sse2neon-src}"
LLVM_ROOT="${PS2X_LLVM19_ROOT:-$ROOT/tools/build/llvm19}"
CXX="${CXX:-$LLVM_ROOT/bin/clang++}"
LIBCXX_INCLUDE="${PS2X_LIBCXX_INCLUDE:-$LLVM_ROOT/include/c++/v1}"
SDKROOT="${SDKROOT:-$(xcrun --sdk macosx --show-sdk-path)}"

if [[ "$(uname -s)" != Darwin ]]; then
    echo "This replay harness currently uses the local macOS LLVM 19 toolchain." >&2
    exit 2
fi
for required in "$CXX" "$LIBCXX_INCLUDE"/vector "$SSE2NEON"/sse2neon.h \
                "$RUNTIME/src/lib/vu/ps2_vu1_core.cpp"; do
    if [[ ! -e "$required" ]]; then
        echo "Missing required VU1 replay input: $required" >&2
        exit 2
    fi
done
shopt -s nullglob
generated=("$GEN"/vu1rec_*.cpp)
if (( ${#generated[@]} == 0 )); then
    echo "No generated VU1 sources in $GEN. Capture a catalog and run CMake generation first." >&2
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

compile_if_stale() {
    local src="$1" obj="$2"; shift 2
    if [[ ! -e "$obj" || "$src" -nt "$obj" ]]; then
        "$CXX" "${common[@]}" "$@" -c "$src" -o "$obj"
    fi
}

for src in "$RUNTIME/src/lib/vu/ps2_vu1_core.cpp" \
           "$RUNTIME/src/lib/vu/ps2_vu1_upper.cpp" \
           "$RUNTIME/src/lib/vu/ps2_vu1_lower.cpp" \
           "$RUNTIME/src/lib/vu/ps2_vu1_recomp.cpp" \
           "$HERE/vu1_replay.cpp"; do
    obj="$OUT/$(basename "${src%.cpp}").o"
    compile_if_stale "$src" "$obj" "${oracle[@]}"
    objects+=("$obj")
done

for src in "${generated[@]}"; do
    obj="$OUT/$(basename "${src%.cpp}").o"
    compile_if_stale "$src" "$obj" "${native[@]}"
    objects+=("$obj")
done

compile_if_stale "$HERE/vu1_stubs.cpp" "$OUT/stubs.o" -O2
"$CXX" -arch arm64 -isysroot "$SDKROOT" -stdlib=libc++ -L"$LLVM_ROOT/lib" -Wl,-rpath,"$LLVM_ROOT/lib" "${objects[@]}" "$OUT/stubs.o" -o "$OUT/vu1_replay"
echo "Built $OUT/vu1_replay"
echo "Run: $OUT/vu1_replay $ROOT/runtime/vu1-catalog $ROOT/runtime/vu1-snapshots"
