# Internals

Mechanisms, invariants and constants. Commands are in `AGENTS.md`; state and roadmap in `docs/STATUS.md`.

## Timing invariants

- PAL: gameplay 25 FPS, VBlank 50 Hz, movies 25 fps (PTS-driven). Nothing may assume 59.94/60 Hz unless an opt-in experiment set it.
- Interactive hardware time follows the monotonic host clock (`PS2X_WALLCLOCK=0` selects deterministic estimated-cycle time). Estimated block costs never accelerate it. EE, COP0 and IOP/SPU clocks are never changed to raise FPS; no overclock. The 60/120 experiment changes only the guest VBlank period and the FPS gate.
- Synchronous guest IOP RPCs must not advance the SPU timeline on top of the EE-issued IOP budget: the scheduler accumulates an absolute issued target and credits work already executed; reset clears the target and the fractional 1:8 carry; the SPU epoch stays monotonic.
- Movie presentation epoch is fixed. Late frames present promptly without shifting the PTS epoch or dropping decoded frames. PTS and PTS-less intervals use the effective VBlank period.
- DISPFB2 changes, guest updates and presents are different metrics. `fps_dispfb2` in the `tz run` summary is the first; gameplay is 25/s, movies 50/s. Gameplay is CPU-bound (GPU about 3.5 ms per 20 ms field, no readbacks). Guest render/culling (`HIE_fn_vSendBufferedCharacterToViewport` and its VIF/MFIFO/VU1 children) dominates. Metal drawable waits matter only at 120 Hz without the upstream display-rate throttle, which is enabled.

## Game clock and FPS mechanism

- The FPS gate byte `0x6B00B8` is the min-sync byte stored by `GAM_InitEngineState_Playing` (`0x35ec20`) and read by `osrSyncCallback` (`0x3F8CF0`) against the VBlank counter `0x6B0690`. It lies in loader-zeroed BSS. Provenance: the published PCSX2 PAL patch writes zero there (`006B00B8=00000000`); its EE-overclock note is not followed. Zeroing it alone gives no gain; the opt-in VBlank period change is what lets high targets move. High FPS also relies on the upstream display-rate presentation throttle.
- Engine time comes from COP0 Count: `Timer_InitModule` (`0x496680`) installs `Timer_ReadRDTSC` (`0x496620`, `mfc0` at `0x496644`) at `0x6EC1C0` with divisor 294,912,000 at `0x6EC1C8`; unsigned-wraparound deltas accumulate at `0x6F06B8` (previous Count `0x6F06C0`). `fn_vActualizeEngineClock` (`0x360140`) runs before each world update; elapsed ms are integer `(timer - start) * 1000 / divisor` and VBlanks are not counted. Accumulated game ms are at `0x4D1A10`, the current delta at `0x4D1A18`/`0x4D1A20`; the guest clamps delta to 5-133 ms (bounds `0x4A34D0`/`0x4A34C8`). AI `GetDT`, animation, `CMusicLS::Evolve` and callbacks consume this clock. No fixed 40 ms step exists.
- Parity check: `TARZAN_TRACE_GAME_CLOCK=1 ./tz run --route first-mission --seconds 240`, repeated with `--fps-target 60`; sum `[game-clock] game_ticks` over `host_ms` for the confirmed playable scene. First-mission ratios were 1.0000 at default, 60 and 120, with smaller mean deltas at higher targets. This is clock parity, not identical physics or whole-game correctness; long stalls still hit the delta clamp.

## Game hooks

PS2Recomp has no mid-function override, so our dep patch adds config `[[hooks]]` (address, `before`/`after`, optional `redirect`). The recompiler emits a call through `g_ps2InstructionHooks[]`; `host/game-hooks.cpp` registers handlers (`PS2_REGISTER_GAME_OVERRIDE`) and installs them only if every guest instruction word they depend on matches the retail ELF. `generated/` stays pure recompiler output. Each hook is inert when its setting is off.

| Hook (address) | Purpose | Setting |
|---|---|---|
| camera zoom `0x369a3c`, Y-FOV `0x369a7c` | PAL-M 16:9 camera constants (`0x3f210000`, `0x3f400000`) | `hdWidescreen` |
| FPS gate init `0x35ec74`, sync `0x3f8df4` | zero the min-sync byte | `highFPS` |
| clock trace `0x360388` | game vs host milliseconds | `TARZAN_TRACE_GAME_CLOCK` |
| language `0x34fce0`, `0x350350`, `0x2cd48c`, `0x2c0e40` | persist accepted language IDs, restore via the original menu actions, hide the selector | `rememberLanguage`, `lastLanguage` |
| cheats `0x2dac2c` (redirect `0x2dada4`) | inject the original cheat strings; HUD shows 99 lives; spears stay at five | `extraLives99`, `unlimitedSpears` |
| `IsItemActive` `0x2db810` (function replacement, only while on) | reports true to the six map-lock call sites (`0x2c41e4 0x2c43ac 0x2c4424 0x2c4494 0x2c463c 0x2c46d4`, from `ProcessMenuMessage_Locking` `0x2c3e90`); other callers and stored progress untouched, so saves are byte-identical. Terk's locks (`0x2c4cb0`) unchanged | `unlockAllMissions` |
| boot mission: tick in `CMenu::Process` `0x350350`, logs at `CLevelManager::SetCurrentLevel` `0x2dbfb0` and `DLG_vEndOfLevelLoading` `0x345590` | once the saved language is accepted and 25 menu ticks passed, call `CLevelManager::GetInstance` `0x281e00` then `SetCurrentLevel(this, level, mode, player)` as the map's `ProcessMenuMessage_ChangeMap` does after its lock check and story movie; mode 1 is Story Mode, player 0 Tarzan, level ids follow the `w1_1_gm w1_2_raft ...` name order at `0x4b3328` (1..14 for the 12 map missions, 5 and 10 are bungee). Never writes saves | env `TARZAN_BOOT_MISSION=level,mode,player` (probe `--mission`; isolated user dir only) |
| logo skip `0x35e2c4` (redirect `0x35e2cc`) | skip only the startup `Logo` call | `skipLogos` |

- **Widescreen camera.** The title backdrop is a C3DWorldDlg with dialog ID `0x107003b9`; first-mission gameplay uses `0x3e9`. The backdrop keeps its authored camera; all others widen. The hook checks the CAM frame's saved caller and dialog pointer (prologue, caller and SetId words verified), never heap addresses. Logic: `host/experimental-clock.cpp`.
- **UI half-pixel coverage** (render scale above 1, `TARZAN_UPSCALE_UI_COVERAGE` set by `host/metal-host.mm`). Menu backgrounds are ST triangle-strip quads, not sprites, and showed a black grid when upscaled. The correction extends right/bottom coverage by half a guest pixel, guarded to clamped, 256-wide, axis-aligned four-vertex ST quads with constant Q=1, depth, color and full 0..1 mapping. Texture bytes, native scale and perspective geometry are untouched. **Sharper image:** upstream Metal contrast-adaptive sharpening at 50% plus sharp bilinear; FXAA off. **Resolution:** Native (default), 2x, 3x, 4x, independent of widescreen and FPS; `host/settings.mm` migrates old preferences without `renderScale` (widescreen on becomes 2x).

## VU1 and VU0 native paths

- **VU1.** `tools/vu/vu1recomp.py` generates guarded native code from every disc VU1 upload chain (`tools/vu/extract-vu-microcode.py`) plus validated light/material patch writers; no captured banks or jump traces are compiler inputs. A native run executes only when the exact executed microcode words match; unsupported pairs and rejected runs fall back to the interpreter transactionally (state untouched). Guarded pools are partitioned by the exact first instruction pair, with a side-effect-free selector before native execution (merging unrelated uploads exceeds the generator's node limit). An unknown first pair rejects, including calls entering elsewhere in the bank. The guarded unit is the longest compile (Ninja pool `heavy`).
- **Guard coverage lessons.** The ELF's 904-slot upload must be in the generator verbatim (later-mission banks differ only in residual microcode above it). The material recipe must cover the BAL patch slots through `0x2b8` and the `0x2548` target, whose LUI/ADDIU writer is checked against the ELF. Entry zero alone does not identify the failing guard.
- **VU0.** Native paths for captured skinning programs, keyed by an FNV-1a 64-bit hash (offset `1469598103934665603`, multiplier `1099511628211`) of the 4096-byte micro memory. `vu1recomp.py --unit vu0` accepts only reachable lower LQI/NOP and supported FMAC/ACC/NOP upper instructions; anything else fails generation so the runtime interprets.
- **MFIFO** tag limit follows configured ring capacity. XGKICK scratch is reused across resets.  **Flags:** interpreter/oracle `-O2 -fno-fast-math -ffp-contract=off`; native VU code `-O2 -ffp-contract=off -frounding-math`.
- **Corpus and replay** (`runtime/regression/vu1/`, local, never committed): `tools/vu/test/` (built by `build.sh`) replays MSCAL snapshots through the interpreter and both native variants and compares GIF packets, VF/VI/ACC/Q/P/I/R/PC, selected flags, VU1 data memory and cycles. `./tz test --full` requires every local snapshot to complete with 0 mismatches, 0 rejected, 0 skipped, plus edge/fuzz runs. Diff modes `PS2X_VU1_DIFF[_LOG]`, `PS2X_VU0_DIFF[_COMMIT]` run each call three times; never time with them. Harness build overrides: `PS2X_VU1_RUNTIME`, `PS2X_LLVM19_ROOT`, `PS2X_LIBCXX_INCLUDE`, `PS2X_SSE2NEON_DIR`, `PS2X_VU1GEN_DIR`, `PS2X_VU1TEST_OUT`.
- **Capture.** VU1: run with `PS2X_VU1_CAPTURE=runtime/regression/vu1/snapshots` and `PS2X_VU1_CATALOG=runtime/regression/vu1/catalog` (`./tz run --route missions` with the catalog pairs sweep snapshots with full banks; it hashes every call, so repeat without it for timing; copy chosen `snap_<hash>_*.bin` into `live-later-missions/snapshots/`). VU0: run with `TARZAN_TRACE_VU0_SKINNING=<prefix>`; the first deformation call writes `<prefix>.code.bin` and state files; copy `.code.bin` to `<hash>.vu0` and add `<hash> <entry-pc-hex> <count>` lines to `index.txt`, then `python3 tools/vu/vu1recomp.py --unit vu0 --catalog <dir> --out <out>`.
- **Regeneration** runs inside `./tz build`. Manual: `python3 tools/vu/vu1recomp.py --catalog runtime/regression/vu1/catalog --out generated/vu1-native [--jr-targets <file>]`; if a generated JR/JALR reaches an uninferred target, dump targets with `vu1_replay ... --dump-jr-targets <file>` and regenerate.  `tools/vu` is adapted from the Road-trip-Adventure VU1 tools (GPL-3.0-or-later; see `tools/vu/LICENSE`).

## VIF1 front end and op stream

`PS2Memory::processVIF1Data` (`ps2_vif1_interpreter.cpp`) is split into a front end and a consumer. The front end parses commands and owns everything the EE can observe: VIF1 registers (including ROW, rewritten by STMOD 2/3 UNPACK), DBF/TOPS, stall and IRQ state, partial-command buffering, MSKPATH3 and the DIRECT continuation counters. It emits `Vif1Op`s in order: `Mpg`, `Unpack` (snapshot of CYCLE, MODE, MASK, ROW, COL plus payload), `Mscal`/`Mscnt` (start PC, TOP, ITOP), `Direct` (PATH2 bytes), `Path3Unmask`. `unpackVectors` is shared: the consumer runs it on VU1 memory, the front end with a null destination to advance ROW. Payloads point into the caller's input, so a deferred consumer must copy at emit (the MFIFO path passes the live ring). MSCAL/MSCNT carry the FBRST D/T halt enables read at issue and leave the VPU_STAT stop bits in `vu1Exec().stopBits` for the EE to publish.

`DataStream` (`ps2_data_stream.{h,cpp}`) is the one interface to the GS bridge: `gifSubmit`, `gsReset`, `gsWriteCsr`, `gsVsync`, `gsReadLocal`. GS CSR and SIGLBLID are a pure function of the packets processed, so `gifSubmit` only marks them stale and `observeGs()` reads them back at drain points: any EE access to a GS privileged register, CSR writes, the VBlank snapshot. `PS2X_GS_VERIFY=1` also reads back after every packet and aborts if the lazy value differs. The bridge owner is the thread that called `tarzan_metal_gs_create` (`s_gs` in `host/metal-host.mm` is thread-local).

## Data-side worker

`DataStream` has two modes. Inline runs every op on the calling thread. Worker (default) copies each op into a 16 MiB SPSC ring (`StreamRing`, `include/runtime/ps2_stream_ring.h`) executed in order by a worker thread that owns VU1 code/data, the VU1 interpreter/native dispatcher, the `GifArbiter` and the GS bridge. Kill-switch: `PS2X_VU1_WORKER=0` selects inline; `sync` drains after every kick (the no-overlap proof mode). Ops: `Vif`, `GifPacket` (packets over 1 MiB are read in place while the EE waits), `GifDrain` (an arbiter batch boundary, semantic because the arbiter sorts per batch), `GsReset`, `GsWriteCsr`, `GsVsync`, `GsReadback`, `GsReadLocal`. DIRECT parsing and the PATH3 mask stay on the EE and only decide which ops are enqueued, in guest order. Ops publish when the outermost `KickScope` (processVIF1Data, processPendingTransfers, MFIFO kick) ends.

The ring uses monotonic head/tail byte counters (seq-cst). The worker never waits on the EE; every EE-to-worker blocking edge has a 2 s watchdog that prints the stuck op and aborts. A worker exception is rethrown on the EE at its next enqueue or drain.

The EE waits only at: GS CSR/SIGLBLID access with packets outstanding, any EE load/store to VU1 code or data (`mapVuMemory`), the VBlank vsync with CSR readback, GS local-to-host reads, MSCAL/MSCNT while FBRST D/T halt enables are or were set, and `PS2Memory::initialize`/shutdown. Control-plane state (VIF1/DMA registers, INTC causes, stalls, GIF_STAT) is final on the EE when each kick returns, because none of it is charged to guest time. Steady gameplay waits once per vsync. Barrier rates are regenerated by counting at the accessors in `ps2_memory.cpp` and `EeScheduler.cpp`.

Tests: `ps2_data_stream_tests.cpp` covers ring ordering and payload integrity across wraps with random barriers, exception propagation, and identical VU1 memory, MSCAL sequence and GIF packet order versus inline (including an EE load from VU1 data as a barrier).

## Profile attribution (decision basis)

Game-thread leaf samples at target 60 in the confirmed playable first-mission scene, measured before the worker; regenerate by sampling per `docs/STATUS.md`. Profiled FPS is not throughput evidence.

| Work | % of game thread |
|---|---:|
| VU1 native bodies and helpers (FMAC flag helpers 13.7, range correction 3.0, rest 11.8) | 28.5 |
| Guest, VU0 and remaining helpers (guest function self-time 19.3) | 28.6 |
| GS bridge submission and presentation | 8.8 |
| IOP execution plus scheduling/HLE | 9.6 |
| VIF1 interpreter 5.8, GIF 2.0, DMA/MFIFO 1.2, native staging 4.2, XGKICK 3.5, lookup 0.1 | 16.8 |
| SPU2 mixing and stretcher 3.7, EE scheduler/checkpoints 4.4 | 8.1 |

Transactional bank copies cost 3.1%; lookup is generation-cached and not a target. Per-frame work: about 725 MSCAL and 242 MSCNT, 4,470 guest cycles per MSCAL; MSCNT at `0x05e8` takes two cycles but still stages both banks.

## Audio and movies

- The guest audio callback returns zero when its ring cannot take a complete PES payload. The MPEG shim must then leave the PES in the input ring and report only accepted bytes; erasing it shifts later data and corrupts ADPCM. Demux stops at the first registered audio PES; incomplete and wrapped packets are assembled from both ring sides; CD/EOF accounting follows accepted bytes. Never mask a symptom with mute, gain reduction or a limiter. Code: `deps/ps2recomp/ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp` (`processPssBuffer`, `dispatchGuestStreamCallback`).
- Filter PSS audio by the private substream (story movie `FF A1 00 01`) before comparing to source; comparisons ignore each block's loop-flag byte, which the game driver rewrites.
- Healthy mixing is about 48k frames/s with no accumulating PTS offset. At movie EOF the ring flush bursts underrun/overrun frames; `tz run` classifies these by signature as movie-boundary, separate from steady-state underruns.
- The SPU host issues `Sync(t)` only; an inline/threaded fixture compares events, core state, PCM hash and frame count. `TARZAN_AUDIO_STRETCH=0` disables stretching for backend tests.

## Reachable library helpers

- FileIO `_sceFsSemInit` (`0x2250b8`) and `_sceFsSigSema` (`0x225138`) run original guest code: a no-argument initializer on the semaphore slot `0x49f94c` (sentinel `-1` creates via `CreateSema` `0x2213a0`, counts one/one; later calls reuse it) and a signal helper tail-calling `SignalSema` (`0x2213c0`). No RPC is involved. RPC `_sceRpcFreePacket` (`0x223eb8`), `_sceRpcGetFPacket` (`0x223ed8`) and `_sceRpcGetFPacket2` (`0x223f08`) are packet-pool pointer arithmetic with no SID/function dispatch; identities come from ELF symbols, offsets and call targets. Tests: `fileio_guest`, `rpc_packet_guest`.
- Removing a config stub alone cannot restore guest execution: PS2Recomp also auto-binds names in `ps2_call_list.h`; these helpers are removed from both lists.

## EE FPU, VU0 macro and IOP semantics

- SQRT.S/RSQRT.S clear current FCR31 I/D causes before setting the operation's exception (sticky flags kept); negative nonzero Ft sets I/SI, zero or denormal Ft sets D/SD for RSQRT. Classification precedes the destination write so Fd may alias Ft.
- VU0 macro FMAC computes masked Z/S/U/O and sticky status eagerly (no lazy flag liveness). FDIV updates only I/D and sticky copies, treating exponent-zero operands as zero. VI0 discards integer destinations at translation time. VCLIPw compares signed float bit patterns against absolute Ft.w; an exponent-zero bound becomes `0x007fffff` (PCSX2 `_vuCLIP`); clip history keeps its low 24 bits.
- IOP LWL/LWR merge with the pending load value when its destination matches `rt`, else with `gpr[rt]`; the result keeps its load delay, and a direct register write in the delay slot supersedes the pending load.  `./tz test` covers these edges; `./tz test --full` adds native VU replay; neither establishes later-mission behavior.

## Settings and environment

One `tarzan::Settings` (`host/settings.hpp`, defaults there) loads once from `<user dir>/experimental-settings.plist` (keys: `highFPS`, `targetFPS` (only 60 loads; a stored 120 migrates to 60), `hdWidescreen`, `renderScale`, `sharperImage`, `rememberLanguage`, `lastLanguage`, `skipLogos`, `extraLives99`, `unlimitedSpears`, `unlockAllMissions`; unknown keys preserved). All experiments default off and apply after restart; the normal path keeps original timing, 4:3 framing and native scale. Debug overrides apply on top; the runtime gets host config through `PS2HostConfig`, not the environment. Keyboard bindings live in `keyboard.plist`.

| Variable | Effect |
|---|---|
| `TARZAN_EXPERIMENT_HD=1`, `_FPS=60\|120` (120 has no UI; `tz run --fps-target` sets it), `_SCALE=1..4`, `_SHARP=1` | override that setting for this process |
| `PS2X_WALLCLOCK=0` | deterministic estimated-cycle timing |
| `TARZAN_DIAGNOSTIC_INPUT_FIFO=<fifo>` | scripted input (routes, probe) |
| `TARZAN_TRACE_FAULTS=1`, `TARZAN_TRACE_SPU2=1` | fault and audio-health lines (`tz run` sets both) |
| `TARZAN_CAPTURE_DIR`, `_FRAMES`, `_START_FIELD`, `_INTERVAL` | bounded Metal frame capture (probe `--gpu-capture`) |
| `TARZAN_TRACE_GAME_CLOCK=1`, `TARZAN_TRACE_CD_IO=1` | clock and disc-read traces (loading is dominated by guest texture conversion, not reads) |
| `TARZAN_DEBUG_SKIP_MOVIES=1`, `TARZAN_DEBUG_KEEP_STORY=1` | skip movie playback for diagnosis |
| `TARZAN_AUDIO_STRETCH=0` | no audio time stretching |
| `TARZAN_TRACE_VU0_SKINNING=<file>` | VU0 deformation trace |
| `PS2X_VU1_CAPTURE[_EVERY\|_SKIP]`, `PS2X_VU1_CATALOG`, `PS2X_VU1_INTERPRETER`, `PS2X_VU0_INTERPRETER`, `PS2X_VU1_DIFF[_LOG]`, `PS2X_VU0_DIFF[_COMMIT]` | VU capture and differential tooling |
| `PS2X_VU1_WORKER=0`, `PS2X_GS_VERIFY=1` | inline data side; eager GS readback verify |

`RRV_PCSX2_GS_*` and `TARZAN_UPSCALE_UI_COVERAGE` are set by the host for the GS bridge from Settings (`host/metal-host.mm`).

## Probe and routes

`tools/probe/probe.py` drives `tz run`. `--route first-mission --user-dir <empty-dir>` creates a separate slot, enters a new game through process-local FIFO input and refuses a nonempty save directory. `fps_gameplay` starts after all route inputs and the first stable movie-free pair of samples (intervals over 10 s excluded) and runs 20 s (`--measure`: 60 s); the probe OCR-confirms and dismisses the Continue card and reports `scene`. `--mission N|NAME` runs one map mission without the map: isolated user dir with skipLogos and the first language remembered, `TARZAN_BOOT_MISSION`, waits for the `[tarzan-mission]` start and level-loaded lines, then two steady periodic samples (the hook's loaded line precedes the real load), plays 10 s (`--measure`: 60 s) and judges `fps_gameplay` and audio underruns from the first steady sample (the load itself bursts underruns). Pad buttons: Cross continues cards and jumps [inferred]; Square and Circle as attack and spear are a guess. `--route missions` writes `unlockAllMissions` into isolated user dirs, guards every step with window OCR (map, pause, quit confirm, tutorial card, character card, Terk's map), reads the mission name from the map, plays 45 s (hold Up, pulsed Cross/Square/Circle, side steps), quits through the pause menu and restarts in a fresh user dir after a crash or hang. A card that bounces back to the map is reported locked. Per-mission fallbacks are deltas of the cumulative `[vu1rec] periodic` counters. Probe `ready_elapsed_seconds` is not a load time.

## Patch stack

`deps.lock` pins each upstream (`ps2recomp`, `pcsx2`, `fukami`) by SHA. `./tz deps sync` creates `deps/<name>` at the pin on branch `tarzan` and applies `patches/<name>/*.patch` with `git am -3`. `./tz deps export` rewrites `patches/<name>/` with `git format-patch` (byte-stable) and refuses dirty trees. `./tz deps status` shows pin, commits, clean/dirty, exported/unexported; `./tz check` fails on unexported commits. Pull upstream in themed batches with a verifying test each; regenerate into a separate directory first (`tools/generate.py --out DIR`) to review output diffs.

## Tried and rejected

- `-O2` on the hot geometry routine; sleep/pacing tweaks; the clock-budget experiment; the 50 FPS gate byte alone: no gain.
- Fast-math or FP contraction: breaks PS2 arithmetic.
- Counting IOP RPC cycles twice, or a fixed EE-clock correction, for audio lead: the fix was IOP budget accounting.
- Inline checkpoint cycle budget (+0.77%) and VU1 operand-normalization elision (+0.25%): exact but no measurable FPS gain.
- FPCR.FZ around native VU calls: inexact (`FLT_MIN + FLT_MIN * 0.5` gives `0x00800000`, required `0x00C00000`).
- Sprite Round/Align, TextureOffset, half-texel ST inset and PCSX2's HPO=1 hint for the menu grid: none removed it (the coverage correction did).
