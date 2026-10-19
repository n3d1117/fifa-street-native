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

## Reachable library helpers

- FileIO `_sceFsSemInit` (`0x2250b8`) and `_sceFsSigSema` (`0x225138`) run the original guest code rather than throwing HLE aliases. The disc ELF symbols and instructions identify a no-argument initializer using the semaphore slot at `0x49f94c`: sentinel `-1` creates a semaphore through `CreateSema` (`0x2213a0`), with initial/max count one; subsequent calls return the existing ID. The signal helper loads that slot into `a0` and tail-calls `SignalSema` (`0x2213c0`). No RPC service is involved. `fileio_guest` exercises initialization, reuse, signaling and overflow through those generated functions and the runtime syscalls.
- RPC `_sceRpcFreePacket` (`0x223eb8`, one packet pointer) clears bit zero of packet word `+0x10` and clears word `+0x18`, preserving other bytes. `_sceRpcGetFPacket` (`0x223ed8`, one descriptor pointer) computes the signed remainder of cursor `+0x24` by count `+0x18`, stores remainder plus one and returns pool pointer `+0x14` plus remainder times 64. `_sceRpcGetFPacket2` (`0x223f08`, descriptor and signed index) selects pool `+0x1c` when the index is nonnegative and below the count at descriptor `+0x20`, otherwise calls the rotating helper. These identities come from the disc ELF symbols, argument registers, memory offsets and call targets; they have no RPC SID/function dispatch. `rpc_packet_guest` covers memory effects, alias preservation, wrapping, signed remainder, indexed bounds and fallback through the generated functions.
- Removing a config stub alone cannot restore guest execution: PS2Recomp also auto-binds names in `ps2_call_list.h`. The FileIO pair and RPC packet trio are removed from both lists, and their throwing handlers are deleted.
- `./tz where --unimplemented` regenerates the configured direct `TODO_NAMED` handler count, resolving optional leading underscores as the runtime does. This is a binding audit, not evidence of dynamic reachability.

## IOP load delay

- LWL/LWR merge with the pending load value when its destination matches `rt`; otherwise they merge with `gpr[rt]`. The merged result still has a load delay. Address calculation, ALU operands and stores read the committed registers; a direct register write in the delay slot still supersedes the pending load. `./tz test` covers both pair orders, unaligned offsets and ordinary load-delay behavior.

## Audio and movies

- The guest audio callback returns zero when its destination ring cannot take a complete PES payload. The MPEG shim must then leave the PES in the input ring and report only accepted bytes to the calling guest thread; erasing it shifts later data and corrupts ADPCM. Demux stops at the first registered audio PES; incomplete and wrapped packets are assembled from both ring sides before parsing; CD/EOF accounting follows accepted bytes. Never mask a symptom with mute, gain reduction or a limiter. Code: `deps/ps2recomp/ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp`, `processPssBuffer` (leaves a rejected PES in the ring) and the `onComplete` callback in `dispatchGuestStreamCallback` (a zero result re-presents it on the next `sceMpegDemuxPssRing`).
- Filter PSS audio by the selected private substream (for the story movie `FF A1 00 01`) before comparing against source. Source comparisons ignore each block's loop-flag byte, which the game driver rewrites.
- Healthy mixing is about 48k mixed frames/s with no accumulating PTS offset. At movie EOF the ring flush produces a burst of underrun/overrun frames; `tz run` classifies these by signature as movie-boundary, separate from steady-state underruns. A 64-multiple steady count on some title boots is open (STATUS B15).
- SPU host issues `Sync(t)` only (it processes through `t` and returns events); a preceding `Advance(t)` was redundant. An inline/threaded equivalence fixture compares events, core state, output PCM hash and frame count.
- `TARZAN_AUDIO_STRETCH=0` disables time stretching for SPU2 backend tests.

## EE FPU and VU0 macro flags

- SQRT.S and RSQRT.S clear current FCR31 I/D causes before setting the operation's exception, retaining sticky flags and unrelated fields. Negative nonzero Ft sets I/SI; zero or denormal Ft sets D/SD for RSQRT. Flag classification precedes the destination write so Fd may alias Ft. The existing arithmetic helpers retain their operand and signed-zero behavior.
- VU0 macro FMAC computes masked Z/S/U/O and sticky status eagerly. A local underflow mask survives result flushing and a tiny multiply feeding a zero-ACC MADD/MSUB; integer SIMD classifies flags without extra context state. No flag liveness or lazy-read optimization is assumed. FDIV operations update only I/D and their sticky copies, treating exponent-zero operands as zero.
- VI0 discards integer arithmetic, transfer and load destinations at translation time. VCLIPw compares signed float bit patterns against absolute Ft.w; an exponent-zero bound becomes the largest denormal bit pattern, excluding all denormal Fs lanes. Clip history retains its low 24 bits.
- The CLIP exponent-zero bound `0x007fffff` follows PCSX2 VUops.cpp `_vuCLIP`.
- `./tz test` covers the translator and runtime edge regressions; `./tz test --full` adds native VU replay. These checks do not establish later-mission behavior.

## VU0 and VU1 native paths

- **VU1.** `tools/vu/vu1recomp.py` generates guarded native code from every disc VU1 upload chain (extracted by `tools/vu/extract-vu-microcode.py`) plus validated light/material patch writers; no captured banks or jump traces are compiler inputs. Each upload is placed at its VU destination in a full bank with NOP padding; every bank enters the guarded instruction pool through `tools/disc.py`. The runtime can combine these exact pairs with residual microcode from earlier uploads. All 2,048 instruction slots are represented. A native run executes only when the exact executed microcode words match (guards); unsupported pairs/entries and rejected runs fall back to the interpreter transactionally (state untouched). The guarded translation unit is the longest compile (own Ninja pool `heavy`); Ninja regenerates it incrementally when inputs change.
- **Later-mission guard coverage.** Runtime banks `0785d8c727a7a1fb` and `476c479186e0c7a0` contain the ELF's 904-slot upload verbatim and differ only in residual microcode above it. Omitting that upload from the generator caused exact-pair rejection at PC zero. Bank `4a4b16036a677c9a` contains the main program with material BAL patches through `0x2b8`; replay exposed rejection at `0x278`. The material recipe must cover those slots and the `0x2548` target, whose guest LUI/ADDIU writer is checked against the original ELF before generation. Entry zero alone does not identify the failing guard.
- **Upload separation.** Guarded pools are partitioned by the exact first instruction pair, with a side-effect-free selector before native execution. Each partition retains guards on every executed pair and full flag state. Combining unrelated overlapping uploads into one pipeline graph creates cross-program paths and exceeds the generator's node limit; separation keeps the existing limit. An unknown first pair rejects transactionally, including calls whose entry PC is elsewhere in the bank.
- **VU0.** Native paths exist for captured skinning programs, keyed by an FNV-1a 64-bit hash (offset `1469598103934665603`, multiplier `1099511628211`) of the full 4096-byte micro memory. `vu1recomp.py --unit vu0` accepts only reachable lower instructions LQI/NOP and supported FMAC/ACC/NOP upper instructions; anything else fails generation so the runtime interprets. Unknown programs always fall back.
- **MFIFO.** The tag limit follows configured ring capacity (a fixed 4096-tag limit once faulted on a valid chain).
- **XGKICK** scratch storage is reused across VU resets; a 64 KiB GS scratch clear per reset was removed (measured win).
- **FMAC range correction** is vectorized exactly (small measured gain).
- Compiler flags: interpreter/oracle `-O2 -fno-fast-math -ffp-contract=off`; native VU code `-O2 -ffp-contract=off -frounding-math`.
- **Capture and catalog (local, never committed).** VU1: run with `PS2X_VU1_CAPTURE=runtime/regression/vu1/snapshots` and `PS2X_VU1_CATALOG=runtime/regression/vu1/catalog`. VU0: run with `TARZAN_TRACE_VU0_SKINNING=<prefix>` and reach gameplay; the first matching deformation call writes `<prefix>.code.bin` (4096 bytes), `.data.before.bin`, `.state.before.txt` and appends to `.log`. Hash `.code.bin`, copy it to `<hash>.vu0`, and add `<hash> <entry-pc-hex> <count>` per observed `start_pc` (from `call=N ee_pc=0x.. start_pc=0x..` log lines) to `index.txt`. Then `python3 tools/vu/vu1recomp.py --unit vu0 --catalog <dir> --out <out>`.
- **Later-mission replay capture.** `PS2X_VU1_CATALOG=runtime/regression/vu1/catalog ./tz run --route missions` pairs the sweep's `vu1-snapshots` with full code banks in the local replay catalog. Copy selected `snap_<hash>_*.bin` files into `runtime/regression/vu1/live-later-missions/snapshots/`, then run `./tz test --full`. Catalog capture hashes every call: use this run for diagnosis, then repeat without the variable for timing. Captured banks are replay inputs only; native generation still derives entirely from the disc.
- **Regeneration** happens inside `./tz build` (Ninja). Manual: `python3 tools/vu/vu1recomp.py --catalog runtime/regression/vu1/catalog --out generated/vu1-native [--jr-targets <file>]`. If a generated JR/JALR reaches a destination the static decoder could not infer, dump observed targets with `vu1_replay ... --dump-jr-targets <file>` and regenerate with `--jr-targets`.
- **Replay harness** (`tools/vu/test/`, built by `tools/vu/test/build.sh`, run by `./tz test --full`): replays MSCAL snapshots through the interpreter and both native variants and compares GIF packets, VF/VI/ACC/Q/P/I/R/PC, selected flags, all VU1 data memory and cycle counts. The corpus is `runtime/regression/vu1/` (`catalog/`, `snapshots/`, `live-*/snapshots/`); `./tz test --full` requires one completed run per local snapshot (at least the original 1,106), 0 mismatches, 0 rejected, 0 skipped, plus edge/fuzz runs (`--fuzz N`, `--no-ftz`). Interpreter diff modes: `PS2X_VU1_DIFF[_LOG]`, `PS2X_VU0_DIFF[_COMMIT]` run each call three times; never use them for timing. Generator unit tests run by `./tz test`: `test_extract_vu_microcode.py`, `test_guarded_pool.py`, `test_static_targets.py`, `test_vu0_generation.py` (and `build-vu0.sh`/`vu0_replay.cpp` for VU0).
- Overrides for the harness build: `PS2X_VU1_RUNTIME`, `PS2X_LLVM19_ROOT`, `PS2X_LIBCXX_INCLUDE`, `PS2X_SSE2NEON_DIR`, `PS2X_VU1GEN_DIR`, `PS2X_VU1TEST_OUT`.
- `tools/vu` is adapted from the Road-trip-Adventure VU1 tools (GPL-3.0-or-later; upstream and license in `tools/vu/LICENSE`).

## Performance attribution mechanisms

Use the card-free first-mission scene and sampling procedure in `docs/STATUS.md`. These are diagnostic measurements at target 60, not optimization A/B results; no diagnostic instrumentation remains in the product.

Temporary dispatcher timers keyed by full-bank hash, entry and resume bit bracket lookup, input staging, native call and publication. Their wall-time shares are 0.15%, 1.24%, 31.64% and 8.78%; publication includes nested GIF/GS and must not be added to the sample table. All steady images select guarded program 2 (first-pair signature `080002ff800506bc`):

| Bank hash | Entry | Calls/frame | Guest cycles/call | Native call % of wall, including XGKICK |
|---|---:|---:|---:|---:|
| `4cf75c5af9706b9e` | `0` | 180.2 | 6484 | 10.37 |
| `4ad89786d3fe5680` | `0` | 281.3 | 2564 | 7.56 |
| `a56054169c2d2401` | `0` | 74.1 | 7064 | 5.62 |
| `c1ae250a3450d5a0` | `0` | 103.1 | 4353 | 4.55 |
| `e5d087db502bc345` | `0` | 54.1 | 3563 | 1.69 |
| `6350f9a03526f258` | `0` | 16.0 | 5625 | 0.93 |
| `72c322aaeabb5b5e` | `0` | 10.0 | 8428 | 0.71 |
| `a486b26c52116853` | `0` | 6.0 | 2626 | 0.15 |
| All continuations | `0x05e8` | 242.3 | 2 | 0.07 |

Top ten guest functions by **self-time as a percentage of the entire game thread**, mapped with `./tz where <address>`; these exclude runtime callees:

| Guest PC | Function | Self |
|---|---|---:|
| `0x3df8b0` | `osrPSX2SendStripToVU` | 4.26% |
| `0x3ec3d0` | `CRenderBatchUnit::RenderOpaqueStatic` | 0.80% |
| `0x3f43e0` | `osrSetObjectMatrix` | 0.70% |
| `0x3c8470` | `osr_compute_fog` | 0.60% |
| `0x3d8a60` | `memcpyAlignedQW` | 0.48% |
| `0x3ec910` | `CRenderBatchUnit::RenderTransparent` | 0.41% |
| `0x3e9960` | `osrIsSphereCulled` | 0.36% |
| `0x3f8b20` | `osrSwapBuffer` | 0.34% |
| `0x319920` | `CInterpolation::InterpolateBetweenKeys` | 0.32% |
| `0x3dd720` | `osrUpdateJumpTable` | 0.29% |

## VIF1 front end and op stream

`PS2Memory::processVIF1Data` (`ps2_vif1_interpreter.cpp`) is split for the R11 worker. The front end parses commands and owns everything the EE can observe: VIF1 registers (including ROW, which STMOD 2/3 UNPACK rewrites), DBF/TOPS, stall and IRQ state, partial-command buffering, MSKPATH3 state and the DIRECT continuation counters. It emits `Vif1Op`s in command order: `Mpg`, `Unpack` (carrying a snapshot of CYCLE, MODE, MASK, ROW, COL and the payload), `Mscal`/`Mscnt` (start PC, TOP, ITOP), `Direct` (PATH2 bytes) and `Path3Unmask`. The `emit` lambda is the only consumer today and applies each op immediately in the same thread, so behavior is identical to the former single function. The UNPACK payload is bounded to the encoded bytes plus one vector, the furthest the decoder can read. `unpackVectors` is shared: the consumer runs it on VU1 data memory, the front end runs it with a null destination to advance ROW only (modes 2 and 3). Op payloads point into the caller's input; a deferred consumer must copy them at emit (the MFIFO path passes the live ring). MSCAL/MSCNT carry the FBRST D/T halt enables read on the EE at issue (`setVu1ControlHooks`), run through `applyVif1Op`, and leave the VPU_STAT stop bits in `vu1Exec().stopBits` for the EE to publish. `m_path3Masked` stays EE state read by `submitGifPacket`.

`DataStream` (`ps2_data_stream.{h,cpp}`) is the one interface to the GS bridge: `gifSubmit` (the arbiter's process function), `gsReset`, `gsWriteCsr`, `gsVsync`, `gsReadLocal`. GS CSR and SIGLBLID are a pure function of the packets the bridge has processed, so `gifSubmit` only marks them stale and `observeGs()` reads them back at the explicit drain points: any EE read or write of a GS privileged register, CSR writes, and the VBlank snapshot. `PS2X_GS_VERIFY=1` also reads back eagerly after every packet and aborts if the lazy value differs at an observation. The bridge owner is whichever thread called `tarzan_metal_gs_create` (`s_gs` in `host/metal-host.mm` is thread-local; `owner_thread_init` runs in create).

Barrier rates in card-free gameplay at the 60 target (diagnostic counters in the runtime, not kept; about 150 game frames per sample, per game frame): EE reads or writes of VU1 code/data 0, GS CSR reads/writes 0, other GS privileged register reads 0, VIF1/GIF CHCR polls 0, VIF1 FIFO stores 0, local-memory readbacks 0, GIF_STAT reads 1.0, VIF1 FBRST writes 1.0, VIF1 register reads 59, VBlank (GS vsync plus CSR readback) 1.67. Work to move: 776 MFIFO kicks, 724 MSCAL, 242 MSCNT, 11,500 `processVIF1Data` calls and 4.3 MB of VIF1 input, 1,976 PATH1 and 615 PATH2 packets (no PATH3), 4.9 MB of GIF input and 2,591 bridge submissions. The EE therefore does not observe data-side state after kicks in steady gameplay, and the only forced synchronization is the VBlank. Regenerate by counting at the accessors in `ps2_memory.cpp` and `EeScheduler.cpp`.

## Data-side worker

`DataStream` has two modes. Inline executes every op on the calling thread. Worker (default) copies each op into a 16 MiB SPSC ring (`StreamRing`, `include/runtime/ps2_stream_ring.h`) and a worker thread executes them in order; the worker owns VU1 code/data and the VU1 interpreter/native dispatcher, the `GifArbiter` and the GS bridge (it calls `tarzan_metal_gs_create` and `destroy`, so the thread-local `s_gs` lives there). `PS2X_VU1_WORKER=0` selects inline and `sync` drains at the end of every kick (the no-overlap proof mode). Ops: `Vif` (MPG, UNPACK, MSCAL, MSCNT with the payload copied at enqueue), `GifPacket` (arbiter submit plus optional drain; packets over 1 MiB are read in place while the EE waits), `GifDrain` (an arbiter batch boundary, which is semantic because the arbiter sorts per batch), `GsReset`, `GsWriteCsr`, `GsVsync`, `GsReadback`, `GsReadLocal`. DIRECT parsing, the PATH3 mask and the masked-PATH3 FIFO stay on the EE and only decide which `GifPacket`/`GifDrain` ops are enqueued, in guest order. Ops are published to the worker when the outermost `KickScope` (processVIF1Data, processPendingTransfers, MFIFO kick) ends, not per op.

The ring publishes head and retires tail as monotonic byte counters (sequentially consistent, which is store-release/load-acquire on arm64). The worker never waits on the EE, so the only blocking edges are EE to worker, and each is guarded by a 2 s watchdog that prints the op the worker is stuck on and aborts. A worker exception is stored and rethrown on the EE at its next enqueue or drain.

The EE waits (barrier) only at: GS CSR or SIGLBLID access while packets are outstanding (`observeGs`), any EE guest load/store to VU1 code or data (`mapVuMemory`), the VBlank vsync with its CSR readback, GS local-to-host reads, an MSCAL/MSCNT while FBRST D/T halt enables are set or were set at the previous job (the VPU_STAT stop bits then depend on the run; otherwise they are 0 and published at issue), `PS2Memory::initialize` and shutdown. Control-plane state (VIF1 registers, DMA registers and completion, INTC causes, stalls, GIF_STAT) is final on the EE when each kick returns, because none of it is charged to guest time. At the 60 target, steady gameplay waits only at the VBlank, once per vsync.

Verification: `ps2_data_stream_tests.cpp` checks ring ordering and payload integrity across wraps with random barriers, exception propagation, and that a worker-mode `PS2Memory` ends with the same VU1 memory, MSCAL sequence and GIF packet order as an inline one (including an EE load from VU1 data as a barrier). First-mission at the 60 target, same route and scene, worker on versus `PS2X_VU1_WORKER=0`: see `docs/STATUS.md` row 11.

## Game hooks and constants

PS2Recomp has no mid-function override, so our dep patch adds config `[[hooks]]` (address, `before`/`after`, optional `redirect`). The recompiler emits a call through `g_ps2InstructionHooks[]` at that instruction; `host/game-hooks.cpp` registers the handlers (`PS2_REGISTER_GAME_OVERRIDE`) and installs them only if every guest instruction word they depend on matches the retail ELF. `generated/` is pure recompiler output. Each hook does nothing when its setting is off.

| Hook (address) | Purpose | Setting |
|---|---|---|
| camera zoom `0x369a3c`, Y-FOV `0x369a7c` | PAL-M 16:9 camera constants (`0x3f210000`, `0x3f400000`) | `hdWidescreen` |
| FPS gate init `0x35ec74`, sync `0x3f8df4` | zero the min-sync byte `0x6B00B8` | `highFPS` |
| clock trace `0x360388` | game vs host milliseconds | `TARZAN_TRACE_GAME_CLOCK` |
| language `0x34fce0`, `0x350350`, `0x2cd48c`, `0x2c0e40` | persist accepted language IDs; restore through the original menu actions; hide the selector | `rememberLanguage`, `lastLanguage` |
| cheats `0x2dac2c` (redirect `0x2dada4`) | inject the original code strings into the cheat parser; HUD shows 99 lives; spears stay at five | `extraLives99`, `unlimitedSpears` |
| `IsItemActive` `0x2db810` (function replacement, installed only while the setting is on) | the map lock handler `ProcessMenuMessage_Locking` (`0x2c3e90`) asks it from six call sites (return addresses `0x2c41e4 0x2c43ac 0x2c4424 0x2c4494 0x2c463c 0x2c46d4`) which map entries are shown and selectable; for those callers only it reports true. Other callers (saving, completion, final cinematic) and all stored progress are untouched, so saves are byte-identical to a run without it. Terk's challenge locks (`0x2c4cb0`) are not changed | `unlockAllMissions` |
| logo skip `0x35e2c4` (redirect `0x35e2cc`) | skip only the startup `Logo` call (verified by the `Logo` string); other movie call sites unchanged | `skipLogos` |

- **FPS gate `0x6B00B8`.** `GAM_InitEngineState_Playing` (0x35ec20) stores a min-sync byte there; `osrSyncCallback` reads it as an unsigned byte and compares it with `0x6B0690` before entering its sync path. The address lies in loader-zeroed BSS beyond the ELF's file-backed range. The published PCSX2 PAL patch for SLES-50350 writes zero there (`006B00B8=00000000`), which is the provenance of the constant; its note about EE overclock is not followed, no overclock is used. Zeroing the byte alone gave no swap-rate gain; the opt-in VBlank period change is what lets high-FPS targets move.
- **High FPS** also relies on the display-rate presentation throttle (upstream PCSX2) so a 60 Hz display does not block every 120 Hz field. Targets are not achieved-rate claims: `./tz run --route first-mission --fps-target 60` measured about 36 `fps_gameplay` on the confirmed playable scene (an earlier 39.7 included the Continue card), versus 25.0 at default; menus approach the target. Clock-traced runs below are timing diagnostics, not throughput comparisons.
- **Game clock mechanism (verified against the original ELF words).** `GAM_InitEngineState_Playing` resets and starts the clock; `fn_vEngine` calls `fn_vActualizeEngineClock` (`0x360140`) before updating the world. `Timer_InitModule` (`0x496680`) installs `Timer_ReadRDTSC` (`0x496620`) at `0x6EC1C0` and the 294,912,000 ticks/s divisor at `0x6EC1C8`. The reader extends COP0 Count (`mfc0` at `0x496644`) using unsigned wraparound differences, accumulated at `0x6F06B8`, with the previous Count at `0x6F06C0`. Engine elapsed time is `(timer - start) * 1000 / divisor`, in integer milliseconds; it does not count VBlanks. The interactive scheduler supplies Count from monotonic host time at the unchanged EE frequency.
- **Game time and consumers.** `0x4D1A10` holds accumulated game milliseconds; `0x4D1A18` and `0x4D1A20` hold the current update delta, with a rolling delta sum at `0x4D1A30`. The guest clamps delta to 5–133 ms (bounds at `0x4A34D0`/`0x4A34C8`), waiting for the minimum and discarding excess elapsed time above the maximum. AI `GetDT`, elapsed-time queries and callback deadlines use this clock. `fn_vMakeCharacterReact` passes delta / 1000, with the character's slowdown factor, to the mechanics routine; animation timing uses the engine timer structure and slowdown factors; `CMusicLS::Evolve` scales transitions by the same delta. `osrSyncCallback` (`0x3F8CF0`) counts VBlanks at `0x6B0690` for buffer readiness/min-sync and resets that counter after consuming a buffer; this is separate from the engine clock. There is no fixed 40 ms step in the examined update path.
- **Game-speed measurement.** Run `TARZAN_TRACE_GAME_CLOCK=1 ./tz run --route first-mission --seconds 240`, then repeat with `--fps-target 60` or `120`. Dismiss the OCR-confirmed Continue card through `TARZAN_DIAGNOSTIC_INPUT_FIFO` and visually confirm the playable scene: the route can otherwise report first-mission while the card remains visible. Over the final approximately 25 seconds of confirmed first-mission gameplay, sum `[game-clock] game_ticks` and divide by summed `host_ms`; both are milliseconds. Measured ratios (game seconds per wall second) are **1.000012 default**, **1.000014 at target 60** and **1.000004 at target 120**; mean update deltas are **40.00 ms**, **27.78 ms** and **26.61 ms**, respectively. The corresponding guest/wall durations are 25.160/25.159687 s, 25.034/25.033648 s and 25.036/25.035890 s. More updates therefore use smaller deltas, preserving elapsed game time. Window captures/OCR found no readable HUD timer, so there is no independent HUD-clock ratio. This establishes first-mission clock parity, not identical physics trajectories, audiovisual synchronization or whole-game correctness; long stalls can still trigger the original delta clamp. No timing correction is warranted by this measurement.
- **Widescreen camera scoping.** The title backdrop is a C3DWorldDlg with serialized dialog ID `0x107003b9`; first-mission gameplay uses `0x3e9`. The widescreen adjustment preserves the backdrop's authored camera and widens all other cameras. The hook checks the CAM frame's saved caller and dialog pointer (so guest yields do not lose context), with prologue, caller and SetId words verified against the ELF. Never select cameras by heap addresses. Logic: `host/experimental-clock.cpp`.
- **UI half-pixel coverage correction** (render scale above 1 only, `TARZAN_UPSCALE_UI_COVERAGE` set by `host/metal-host.mm`). World-selection and menu backgrounds are ST triangle-strip quads, not GS sprites, and showed a black grid when upscaled. The correction extends right/bottom coverage by half a guest pixel. Guard: clamped, 256-wide, axis-aligned four-vertex ST quads with constant Q=1, depth and color and full 0..1 texture mapping. It does not touch texture bytes, the native-scale path or perspective geometry. The Tarzan Normal half-pixel-offset (HPO=1) hint from PCSX2's game database alone did not remove the grid.
- **Sharper image:** upstream Metal contrast-adaptive sharpening at 50% plus sharp bilinear presentation; FXAA stays off; texture detail unchanged.
- **Resolution:** Native (default), 2x, 3x, 4x GS render scale, independent of widescreen and FPS. Defaults live in `host/settings.hpp`; `host/settings.mm` migrates old preferences without a `renderScale` key (widescreen on → 2x). Original 4:3 menus and movies are not re-authored.
- **Diagnostics** `TARZAN_TRACE_CD_IO=1` reports host disc reads (loading is dominated by guest texture conversion, not reads). Probe `ready_elapsed_seconds` is not a load time.

## Settings and environment

One `tarzan::Settings` struct (`host/settings.hpp`) is loaded once at startup from
`<user dir>/experimental-settings.plist`, the file the Settings window writes
(format and keys unchanged: `highFPS`, `targetFPS`, `hdWidescreen`, `renderScale`,
`sharperImage`, `rememberLanguage`, `lastLanguage`, `skipLogos`, `extraLives99`,
`unlimitedSpears`, `unlockAllMissions`; unknown keys are preserved on save). Debug overrides are applied
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

All experiments default off and apply after restart: `highFPS` off (`targetFPS` 60 or 120 when on), `hdWidescreen` off, `renderScale` Native, `sharperImage` off, `rememberLanguage` off, `skipLogos` off, `extraLives99` off, `unlimitedSpears` off, `unlockAllMissions` off. The normal path keeps original guest timing, 4:3 framing and native render scale. Window lifecycle: the Settings and Keyboard windows keep ARC ownership across close/reopen; keyboard capture monitors clean up on close. Routes: `./tz run --route first-mission --user-dir <empty-dir>` creates a separate slot, enters a new game via process-local FIFO input and refuses a nonempty save directory. `--route missions` writes `unlockAllMissions` into its own isolated user dirs (never the real one), guards every step with window OCR (screen kinds: map, pause, quit confirm, tutorial card, character card, Terk's map), reads the mission name from the map, plays 45 s (hold Up, pulsed Cross/Square/Circle, side steps; no OCR during the play window), quits through the pause menu, and restarts the game in a fresh user dir if a mission crashes or hangs. A card that bounces back to the map is reported as locked. It sets `PS2X_VU1_CAPTURE` (first 8 MSCALs per program and entry) into the run directory; per-mission fallbacks are deltas of the cumulative `[vu1rec] periodic` counters.

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
- R18 inline checkpoint cycle budget: exact accounting, reported 60-target steady-window A/B +0.77%; no measurable FPS gain.
- R27 VU1 operand-normalization elision using `compute_norm`: exact, 38% fewer normalization calls, reported 60-target steady-window A/B +0.25%; no measurable FPS gain.
- R27 FPCR.FZ around native VU calls: inexact intermediate-product flushing; `FLT_MIN + FLT_MIN * 0.5` gives `0x00800000` with FZ versus required `0x00C00000`; reject independently of FPS.
- Sprite Round=2/Align=1, Native+TextureOffset=5, half-texel ST inset, and HPO=1 for the world-menu grid: none removed it (the coverage correction did).
- A fixed EE-clock correction alone for audio lead: insufficient without the IOP budget fix.

## Measured wins

- Cached diagnostic environment flags in the VIF hot path (no `getenv` per packet).
- No 64 KiB GS scratch clear per VU reset; reused XGKICK scratch.
- Guarded VU1 generation compiles in about a minute (it once took about half an hour); the guarded unit stays in the `heavy` Ninja pool.
- Individual O2 compile of three integer texture-conversion handlers (original guest checkpoints kept); no load-speedup claim.
