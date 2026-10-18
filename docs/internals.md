# Internals

Mechanisms, invariants and constants. Read the section for the area you touch. Commands are in `AGENTS.md`; state and roadmap in `docs/STATUS.md`.

## Timing invariants

- PAL: gameplay 25 FPS, VBlank 50 Hz, movies 25 fps (PTS-driven). Nothing may assume 59.94/60 Hz unless an opt-in experiment set it.
- Interactive hardware time follows the monotonic host clock (`PS2X_WALLCLOCK=0` switches to deterministic estimated-cycle time). Estimated EE block costs never accelerate it.
- The EE clock, COP0 elapsed time and IOP/SPU clocks are never changed to raise FPS, and there is no EE overclock. The 60/120 FPS experiment changes only the guest VBlank period and the FPS gate (below).
- Synchronous guest IOP RPC instructions must not advance the SPU timeline on top of the EE-issued IOP budget. The IOP scheduler accumulates an absolute issued target and credits work already executed; reset clears the target and the fractional 1:8 carry; the SPU epoch stays monotonic.
- Movie presentation epoch is fixed. Late video frames present promptly without shifting the PTS epoch and without discarding decoded frames. Movie PTS and PTS-less frame intervals are converted with the effective VBlank period (regression coverage includes 50/60/120 Hz).
- Framebuffer changes (DISPFB2), guest updates and displayed presents are different metrics. `fps_dispfb2` in the `tz run` summary is the first; gameplay is 25/s, movies 50/s.
- Gameplay is CPU-bound (GPU about 3.5 ms per 20 ms field, no GPU readbacks). Guest render/culling (`HIE_fn_vSendBufferedCharacterToViewport` and its VIF/MFIFO/VU1 children) dominates samples. Metal drawable waits only matter at 120 Hz without the upstream display-rate throttle, which is enabled.

## Audio and movies

- The guest audio callback returns zero when its destination ring cannot take a complete PES payload. The MPEG shim must then leave the PES in the input ring and report only accepted bytes to the calling guest thread; erasing it shifts later data and corrupts ADPCM. Demux stops at the first registered audio PES; incomplete and wrapped packets are assembled from both ring sides before parsing; CD/EOF accounting follows accepted bytes. Never mask a symptom with mute, gain reduction or a limiter.
- Filter PSS audio by the selected private substream (for the story movie `FF A1 00 01`) before comparing against source. Source comparisons ignore each block's loop-flag byte, which the game driver rewrites.
- Healthy mixing is about 48k mixed frames/s with no accumulating PTS offset. At movie EOF the ring flush produces a burst of underrun/overrun frames; `tz run` classifies these by signature as movie-boundary, separate from steady-state underruns. A 64-multiple steady count on some title boots is open (STATUS B15).
- SPU host issues `Sync(t)` only (it processes through `t` and returns events); a preceding `Advance(t)` was redundant. An inline/threaded equivalence fixture compares events, core state, output PCM hash and frame count.
- `TARZAN_AUDIO_STRETCH=0` disables time stretching for SPU2 backend tests.

## VU0 and VU1 native paths

- **VU1.** `tools/vu/vu1recomp.py` generates guarded native code from the disc's VU1 microcode (extracted by `tools/vu/extract-vu-microcode.py`) plus validated light/material patch writers; no captured banks or jump traces are compiler inputs. All 2,048 instruction slots are represented. A native run executes only when the exact executed microcode words match (guards); unsupported pairs/entries and rejected runs fall back to the interpreter transactionally (state untouched). The guarded translation unit is the longest compile (own Ninja pool `heavy`); Ninja regenerates it incrementally when inputs change.
- **VU0.** Native paths exist for captured skinning programs, keyed by an FNV-1a 64-bit hash (offset `1469598103934665603`, multiplier `1099511628211`) of the full 4096-byte micro memory. `vu1recomp.py --unit vu0` accepts only reachable lower instructions LQI/NOP and supported FMAC/ACC/NOP upper instructions; anything else fails generation so the runtime interprets. Unknown programs always fall back.
- **MFIFO.** The tag limit follows configured ring capacity (a fixed 4096-tag limit once faulted on a valid chain).
- **XGKICK** scratch storage is reused across VU resets; a 64 KiB GS scratch clear per reset was removed (measured win).
- **FMAC range correction** is vectorized exactly (small measured gain).
- Compiler flags: interpreter/oracle `-O2 -fno-fast-math -ffp-contract=off`; native VU code `-O2 -ffp-contract=off -frounding-math`.
- **Capture and catalog (local, never committed).** VU1: run with `PS2X_VU1_CAPTURE=runtime/regression/vu1/snapshots` and `PS2X_VU1_CATALOG=runtime/regression/vu1/catalog`. VU0: run with `TARZAN_TRACE_VU0_SKINNING=<prefix>` and reach gameplay; the first matching deformation call writes `<prefix>.code.bin` (4096 bytes), `.data.before.bin`, `.state.before.txt` and appends to `.log`. Hash `.code.bin`, copy it to `<hash>.vu0`, and add `<hash> <entry-pc-hex> <count>` per observed `start_pc` (from `call=N ee_pc=0x.. start_pc=0x..` log lines) to `index.txt`. Then `python3 tools/vu/vu1recomp.py --unit vu0 --catalog <dir> --out <out>`.
- **Regeneration** happens inside `./tz build` (Ninja). Manual: `python3 tools/vu/vu1recomp.py --catalog runtime/regression/vu1/catalog --out generated/vu1-native [--jr-targets <file>]`. If a generated JR/JALR reaches a destination the static decoder could not infer, dump observed targets with `vu1_replay ... --dump-jr-targets <file>` and regenerate with `--jr-targets`.
- **Replay harness** (`tools/vu/test/`, built by `tools/vu/test/build.sh`, run by `./tz test --full`): replays MSCAL snapshots through the interpreter and both native variants and compares GIF packets, VF/VI/ACC/Q/P/I/R/PC, selected flags, all VU1 data memory and cycle counts. The corpus is `runtime/regression/vu1/` (`catalog/`, `snapshots/`, `live-20012*/snapshots/`); expected `runs=1106`, 0 mismatches, 0 rejected, plus edge/fuzz runs (`--fuzz N`, `--no-ftz`). Interpreter diff modes: `PS2X_VU1_DIFF[_LOG]`, `PS2X_VU0_DIFF[_COMMIT]` run each call three times; never use them for timing. Unit tests beside it: `test_extract_vu_microcode.py`, `test_guarded_pool.py`, `test_static_targets.py`, `test_vu0_generation.py` (and `build-vu0.sh`/`vu0_replay.cpp` for VU0).
- Overrides for the harness build: `PS2X_VU1_RUNTIME`, `PS2X_LLVM19_ROOT`, `PS2X_LIBCXX_INCLUDE`, `PS2X_SSE2NEON_DIR`, `PS2X_VU1GEN_DIR`, `PS2X_VU1TEST_OUT`.
- `tools/vu` is adapted from the Road-trip-Adventure VU1 tools (GPL-3.0-or-later; upstream and license in `tools/vu/LICENSE`).

## Game hooks and constants

PS2Recomp has no mid-function override, so our dep patch adds config `[[hooks]]` (address, `before`/`after`, optional `redirect`). The recompiler emits a call through `g_ps2InstructionHooks[]` at that instruction; `host/game-hooks.cpp` registers the handlers (`PS2_REGISTER_GAME_OVERRIDE`) and installs them only if every guest instruction word they depend on matches the retail ELF. `generated/` is pure recompiler output. Each hook does nothing when its setting is off.

| Hook (address) | Purpose | Setting |
|---|---|---|
| camera zoom `0x369a3c`, Y-FOV `0x369a7c` | PAL-M 16:9 camera constants (`0x3f210000`, `0x3f400000`) | `hdWidescreen` |
| FPS gate init `0x35ec74`, sync `0x3f8df4` | zero the min-sync byte `0x6B00B8` | `highFPS` |
| clock trace `0x360388` | game vs host milliseconds | `TARZAN_TRACE_GAME_CLOCK` |
| language `0x34fce0`, `0x350350`, `0x2cd48c`, `0x2c0e40` | persist accepted language IDs; restore through the original menu actions; hide the selector | `rememberLanguage`, `lastLanguage` |
| cheats `0x2dac2c` (redirect `0x2dada4`) | inject the original code strings into the cheat parser; HUD shows 99 lives; spears stay at five | `extraLives99`, `unlimitedSpears` |
| logo skip `0x35e2c4` (redirect `0x35e2cc`) | skip only the startup `Logo` call (verified by the `Logo` string); other movie call sites unchanged | `skipLogos` |

- **FPS gate `0x6B00B8`.** `GAM_InitEngineState_Playing` (0x35ec20) stores a min-sync byte there; `osrSyncCallback` reads it as an unsigned byte and compares it with `0x6B0690` before entering its sync path. The address lies in loader-zeroed BSS beyond the ELF's file-backed range. The published PCSX2 PAL patch for SLES-50350 writes zero there (`006B00B8=00000000`), which is the provenance of the constant; its note about EE overclock is not followed, no overclock is used. Zeroing the byte alone gave no swap-rate gain; the opt-in VBlank period change is what lets high-FPS targets move.
- **High FPS** also relies on the display-rate presentation throttle (upstream PCSX2) so a 60 Hz display does not block every 120 Hz field. Targets are not achieved-rate claims: first-mission gameplay reaches about 26-29 swaps/s; menus approach the target.
- **Widescreen camera scoping.** The title backdrop is a C3DWorldDlg with serialized dialog ID `0x107003b9`; first-mission gameplay uses `0x3e9`. The widescreen adjustment preserves the backdrop's authored camera and widens all other cameras. The hook checks the CAM frame's saved caller and dialog pointer (so guest yields do not lose context), with prologue, caller and SetId words verified against the ELF. Never select cameras by heap addresses. Logic: `host/experimental-clock.cpp`.
- **UI half-pixel coverage correction** (render scale above 1 only, `TARZAN_UPSCALE_UI_COVERAGE` set by `host/metal-host.mm`). World-selection and menu backgrounds are ST triangle-strip quads, not GS sprites, and showed a black grid when upscaled. The correction extends right/bottom coverage by half a guest pixel. Guard: clamped, 256-wide, axis-aligned four-vertex ST quads with constant Q=1, depth and color and full 0..1 texture mapping. It does not touch texture bytes, the native-scale path or perspective geometry. The Tarzan Normal half-pixel-offset (HPO=1) hint from PCSX2's game database alone did not remove the grid.
- **Sharper image:** upstream Metal contrast-adaptive sharpening at 50% plus sharp bilinear presentation; FXAA stays off; texture detail unchanged.
- **Resolution:** Native (default), 2x, 3x, 4x GS render scale, independent of widescreen and FPS. Original 4:3 menus and movies are not re-authored.
- **Diagnostics** `TARZAN_TRACE_CD_IO=1` reports host disc reads (loading is dominated by guest texture conversion, not reads). Probe `ready_elapsed_seconds` is not a load time.

## Settings and environment

One `tarzan::Settings` struct (`host/settings.hpp`) is loaded once at startup from
`<user dir>/experimental-settings.plist`, the file the Settings window writes
(format and keys unchanged: `highFPS`, `targetFPS`, `hdWidescreen`, `renderScale`,
`sharperImage`, `rememberLanguage`, `lastLanguage`, `skipLogos`, `extraLives99`,
`unlimitedSpears`; unknown keys are preserved on save). Debug overrides are applied
on top; the guest runtime gets its host configuration through `PS2HostConfig`
(`ps2SetHostConfig`), not through the environment. Keyboard bindings are a separate
file (`keyboard.plist`).

Environment variables still read, and why:

| Variable | Effect |
| --- | --- |
| `TARZAN_EXPERIMENT_HD=1`, `_FPS=60\|120`, `_SCALE=1..4`, `_SHARP=1` | override that Settings field for this process |
| `PS2X_WALLCLOCK=0` | deterministic estimated-cycle timing instead of host-clock pacing |
| `TARZAN_DIAGNOSTIC_INPUT_FIFO=<fifo>` | scripted input (`tz run --route`, `tools/probe`) |
| `TARZAN_TRACE_FAULTS=1`, `TARZAN_TRACE_SPU2=1` | fault and audio-health log lines (`tz run` sets both) |
| `TARZAN_CAPTURE_DIR`, `_FRAMES`, `_START_FIELD`, `_INTERVAL` | bounded Metal frame capture (`tz run --gpu-capture`) |
| `TARZAN_TRACE_GAME_CLOCK=1`, `TARZAN_TRACE_CD_IO=1` | game-clock vs host-clock, disc read trace |
| `TARZAN_DEBUG_SKIP_MOVIES=1`, `TARZAN_DEBUG_KEEP_STORY=1` | skip movie playback for diagnosis |
| `TARZAN_AUDIO_STRETCH=0` | disable audio time stretching (SPU2 backend tests) |
| `TARZAN_TRACE_VU0_SKINNING=<file>` | VU0 deformation trace for catalog capture |
| `PS2X_VU1_CAPTURE[_EVERY\|_SKIP]`, `PS2X_VU1_CATALOG`, `PS2X_VU1_INTERPRETER`, `PS2X_VU1_DIFF[_LOG]`, `PS2X_VU0_INTERPRETER`, `PS2X_VU0_DIFF[_COMMIT]` | VU catalog capture and differential tooling (`tools/vu`) |

Set by the host for the GS bridge library, not by users: `RRV_PCSX2_GS_*` and
`TARZAN_UPSCALE_UI_COVERAGE`, derived from Settings in `host/metal-host.mm`.

## Settings defaults

All experiments default off and apply after restart: `highFPS` off (`targetFPS` 60 or 120 when on), `hdWidescreen` off, `renderScale` Native, `sharperImage` off, `rememberLanguage` off, `skipLogos` off, `extraLives99` off, `unlimitedSpears` off. The normal path keeps original guest timing, 4:3 framing and native render scale. Window lifecycle: the Settings and Keyboard windows keep ARC ownership across close/reopen; keyboard capture monitors clean up on close. Routes: `./tz run --route first-mission --user-dir <empty-dir>` creates a separate slot, enters a new game via process-local FIFO input and refuses a nonempty save directory.

## Patch-stack workflow details

- `deps.lock` pins each upstream (`ps2recomp`, `pcsx2`, `fukami`) by SHA. `./tz deps sync` creates or refreshes `deps/<name>` at the pin on branch `tarzan` and applies `patches/<name>/*.patch` with `git am -3`. `./tz deps export` rewrites `patches/<name>/` with `git format-patch` (zero-commit, no signature, so output is byte-stable) for the commits on `tarzan`; it refuses dirty trees.
- `./tz deps status` prints pin, commit count, clean/dirty and exported/unexported per dep; `./tz check` fails on unexported commits.
- Conflicts during a pin move: resolve, `git am --continue`, then re-export and rerun the full check row.
- Upstream pulls are batched by theme with a verifying test each (FPU result semantics, recompiler correctness); keep generated output diffs reviewable by regenerating into a separate directory first (`tools/generate.py --out DIR`).

## Tried and rejected

- `-O2` on the hot geometry routine: no gain.
- Sleep/pacing tweaks to reach higher FPS: no gain.
- The clock-budget experiment: no gain, risks timing semantics.
- The audio double-count tweak (IOP RPC cycles counted twice): the fix was IOP budget accounting, not a tweak on top.
- The 50 FPS gate byte alone: no swap-rate gain.
- Fast-math or FP contraction: breaks PS2 arithmetic semantics.
- Sprite Round=2/Align=1, Native+TextureOffset=5, half-texel ST inset, and HPO=1 for the world-menu grid: none removed it (the coverage correction did).
- A fixed EE-clock correction alone for audio lead: insufficient without the IOP budget fix.

## Measured wins

- Cached diagnostic environment flags in the VIF hot path (no `getenv` per packet).
- No 64 KiB GS scratch clear per VU reset; reused XGKICK scratch.
- Guarded VU1 generation compiles in about a minute (it once took about half an hour); the guarded unit stays in the `heavy` Ninja pool.
- Individual O2 compile of three integer texture-conversion handlers (original guest checkpoints kept); no load-speedup claim.
