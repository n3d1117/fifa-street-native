# Offline VU1 differential replay

This replays MSCAL snapshots through Tarzan's VU1 interpreter and both generated native variants. It compares GIF packets, VF/VI/ACC/Q/P/I/R/PC, selected flags, all VU1 data memory, and cycle counts. Generated sources and snapshots are game-derived local artifacts; keep them out of version control.

The macOS build script defaults to the active Metal runtime at `source/PS2Recomp-metal/ps2xRuntime`, the local LLVM 19 toolchain at `tools/build/llvm19`, SSE2NEON from `build/game-metal/_deps/sse2neon-src`, generated code in `generated/vu1-native`, and its executable in `build/vu1-replay`. Override these with `PS2X_VU1_RUNTIME`, `PS2X_LLVM19_ROOT`, `PS2X_LIBCXX_INCLUDE`, `PS2X_SSE2NEON_DIR`, `PS2X_VU1GEN_DIR`, and `PS2X_VU1TEST_OUT` as needed.

Build after CMake has generated at least one `vu1rec_*.cpp`:

```sh
tools/vu1recomp/test/build.sh
```

Capture snapshots from the runtime with `PS2X_VU1_CAPTURE=runtime/vu1-snapshots`. The catalog goes in `runtime/vu1-catalog` via `PS2X_VU1_CATALOG`; generated code should use `generated/vu1-native`. Then replay:

```sh
build/vu1-replay/vu1_replay runtime/vu1-catalog runtime/vu1-snapshots
```

If a generated JR/JALR reaches a destination the static decoder could not infer, collect the observed destinations by stepping the interpreter over the snapshots, then regenerate:

```sh
build/vu1-replay/vu1_replay runtime/vu1-catalog runtime/vu1-snapshots \
  --dump-jr-targets runtime/vu1-catalog/jr_targets.txt
python3 tools/vu1recomp/vu1recomp.py --catalog runtime/vu1-catalog \
  --out generated/vu1-native --jr-targets runtime/vu1-catalog/jr_targets.txt
tools/vu1recomp/test/build.sh
```

The build is incremental, so unchanged runtime and generated translation units are reused. For additional edge coverage, pass `--fuzz 20`; pass `--no-ftz` to disable flush-to-zero. The interpreter/oracle uses `-O2 -fno-fast-math -ffp-contract=off`; generated native code uses `-O2 -ffp-contract=off -frounding-math`. Neither path enables compiler contraction, so the comparison checks the interpreter's explicit arithmetic behavior rather than hiding mismatches behind forced FMA.
