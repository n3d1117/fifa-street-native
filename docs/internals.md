# Internals

Mechanisms, invariants and constants that the code relies on. Commands are in `AGENTS.md`; state and next steps in `docs/STATUS.md`.

## Timing invariants

- PAL: VBlank 50 Hz. Movies are PTS-driven. Nothing may assume 59.94/60 Hz.
- Guest clocks (EE, COP0 Count, IOP, SPU) are never changed and the game is never sped up to raise a frame rate.
- Interactive hardware time follows the monotonic host clock. `PS2X_WALLCLOCK=0` selects deterministic estimated-cycle time instead. Estimated block costs never accelerate time.
- Synchronous guest IOP RPCs must not advance the SPU timeline on top of the EE-issued IOP budget. The scheduler keeps an absolute issued target and credits work already executed; reset clears the target and the fractional 1:8 carry; the SPU epoch stays monotonic.
- Movie presentation epoch is fixed. Late frames present promptly without shifting the PTS epoch or dropping decoded frames. PTS-less intervals use the effective VBlank period.
- `EeScheduler::idlePoll` is the primitive for yielding a guest poll loop to the earliest event (EE timer or IOP/SPU2 idle deadline). It exists; nothing calls it yet. Pending SPU2 IRQs invalidate the IOP bound; wall-clock sampling accounts slept time.
- DISPFB2 changes, guest updates and presents are different metrics. `fps_dispfb2` in the `./rx run` summary is the presented DISPFB2 rate.

## Instruction hooks

PS2Recomp has no mid-function override. Config `[[hooks]]` (address, `before`/`after`, optional `redirect`) make the recompiler emit a call through `g_ps2InstructionHooks[]`. Handlers are registered by the host and installed only if every guest instruction word they depend on matches the retail ELF. `generated/` stays pure recompiler output. No hooks are in use.

Removing a config stub alone cannot restore guest execution: PS2Recomp also auto-binds names in `ps2_call_list.h`, so both lists must change together.

## VIF1 front end and op stream

`PS2Memory::processVIF1Data` (`ps2_vif1_interpreter.cpp`) is split into a front end and a consumer. The front end parses commands and owns everything the EE can observe: VIF1 registers (including ROW, rewritten by STMOD 2/3 UNPACK), DBF/TOPS, stall and IRQ state, partial-command buffering, MSKPATH3 and the DIRECT continuation counters. It emits `Vif1Op`s in order: `Mpg`, `Unpack` (snapshot of CYCLE, MODE, MASK, ROW, COL plus payload), `Mscal`/`Mscnt` (start PC, TOP, ITOP), `Direct` (PATH2 bytes), `Path3Unmask`.

`unpackVectors` is shared: the consumer runs it on VU1 memory, the front end with a null destination to advance ROW. Payloads point into the caller's input, so a deferred consumer must copy at emit (the MFIFO path passes the live ring). MSCAL/MSCNT carry the FBRST D/T halt enables read at issue and leave the VPU_STAT stop bits in `vu1Exec().stopBits` for the EE to publish. MFIFO tag limits follow the configured ring capacity. XGKICK scratch is reused across resets.

## GS bridge interface

`DataStream` (`ps2_data_stream.{h,cpp}`) is the one interface to the GS bridge: `gifSubmit`, `gsReset`, `gsWriteCsr`, `gsVsync`, `gsReadLocal`. GS CSR and SIGLBLID are a pure function of the packets processed, so `gifSubmit` only marks them stale; `observeGs()` reads them back at drain points: any EE access to a GS privileged register, CSR writes, and the VBlank snapshot. `PS2X_GS_VERIFY=1` reads back after every packet and aborts if the lazy value differs. The bridge is owned by the thread that created it (thread-local `s_gs` in `host/metal-host.mm`).

## Data-side worker

`DataStream` has two modes. Inline runs every op on the calling thread. Worker (default) copies each op into a 16 MiB SPSC ring (`StreamRing`, `include/runtime/ps2_stream_ring.h`), executed in order by a worker thread that owns VU1 code and data, the VU1 dispatcher, the `GifArbiter` and the GS bridge. Kill-switch: `PS2X_VU1_WORKER=0` selects inline; `sync` drains after every kick (the no-overlap proof mode).

Ops: `Vif`, `GifPacket` (packets over 1 MiB are read in place while the EE waits), `GifDrain` (an arbiter batch boundary), `GsReset`, `GsWriteCsr`, `GsVsync`, `GsReadback`, `GsReadLocal`. DIRECT parsing and the PATH3 mask stay on the EE and only decide which ops are enqueued, in guest order. Ops publish when the outermost `KickScope` (processVIF1Data, processPendingTransfers, MFIFO kick) ends.

The ring uses monotonic head/tail byte counters (seq-cst). The worker never waits on the EE. Every EE-to-worker blocking edge has a 2 s watchdog that prints the stuck op and aborts. A worker exception is rethrown on the EE at its next enqueue or drain.

The EE waits only at: GS CSR/SIGLBLID access with packets outstanding; any EE load or store to VU1 code or data (`mapVuMemory`); the VBlank vsync with CSR readback; GS local-to-host reads; MSCAL/MSCNT while FBRST halt enables are or were set; and `PS2Memory::initialize` and shutdown. Control-plane state (VIF1/DMA registers, INTC causes, stalls, GIF_STAT) is final on the EE when each kick returns.

Tests: `ps2_data_stream_tests.cpp` covers ring ordering and payload integrity across wraps with random barriers, exception propagation, and identical VU1 memory, MSCAL sequence and GIF packet order versus inline.

## VU

VU1 and VU0 run interpreted. The interpreter is built with `-O2 -fno-fast-math -ffp-contract=off`.

- VU0 macro FMAC computes masked Z/S/U/O and sticky status eagerly (no lazy flag liveness). FDIV updates only I/D and sticky copies, treating exponent-zero operands as zero. VI0 discards integer destinations at translation time.
- VCLIPw compares signed float bit patterns against absolute Ft.w; an exponent-zero bound becomes `0x007fffff` (PCSX2 `_vuCLIP`); clip history keeps its low 24 bits.

## Audio and movies

- The guest audio callback returns zero when its ring cannot take a complete PES payload. The MPEG shim must then leave the PES in the input ring and report only accepted bytes. Erasing it shifts later data and corrupts ADPCM. Demux stops at the first registered audio PES; incomplete and wrapped packets are assembled from both ring sides; CD/EOF accounting follows accepted bytes. Never mask a symptom with mute, gain reduction or a limiter. Code: `deps/ps2recomp/ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp` (`processPssBuffer`, `dispatchGuestStreamCallback`).
- The SPU host issues `Sync(t)` only. An inline/threaded fixture compares events, core state, PCM hash and frame count. `PS2X_AUDIO_STRETCH=0` disables time stretching for backend tests.

## EE FPU, VU0 and IOP semantics

- SQRT.S/RSQRT.S clear current FCR31 I/D causes before setting the operation's exception (sticky flags kept). Negative nonzero Ft sets I/SI; zero or denormal Ft sets D/SD for RSQRT. Classification precedes the destination write, so Fd may alias Ft.
- IOP LWL/LWR merge with the pending load value when its destination matches `rt`, else with `gpr[rt]`. The result keeps its load delay, and a direct register write in the delay slot supersedes the pending load.

## Settings and environment

One `host::Settings` struct (`host/settings.hpp`, defaults there) loads from `<user dir>/settings.plist`. Keys: `renderScale` (1..4, native by default) and `sharperImage`. Settings are applied at start; the window and settings UI are in `host/settings-window.mm`. The runtime receives host config through `PS2HostConfig`, not the environment. Keyboard bindings live in `keyboard.plist`.

`host/metal-host.mm` derives the `RRV_PCSX2_GS_*` bridge variables from `Settings`.

| Variable | Effect |
|---|---|
| `PS2X_WALLCLOCK=0` | deterministic estimated-cycle timing |
| `PS2X_SCALE=1..4`, `PS2X_SHARP=1` | override render scale and sharpening for this process |
| `PS2X_DIAGNOSTIC_INPUT_FIFO=<fifo>` | process-local scripted input |
| `PS2X_TRACE_FAULTS=1`, `PS2X_TRACE_SPU2=1`, `PS2X_TRACE_CD_IO=1` | fault, audio-health and disc-read traces |
| `PS2X_TRACE_SYSCALLS=1`, `PS2X_TRACE_RPC=1` | first calls per EE syscall number; first 2000 SIF RPC events |
| `PS2X_CAPTURE_DIR`, `_FRAMES`, `_START_FIELD`, `_INTERVAL` | bounded Metal frame capture |
| `PS2X_AUDIO_STRETCH=0` | no audio time stretching |
| `PS2X_VU1_WORKER=0\|sync` | inline or drained data side |
| `PS2X_GS_VERIFY=1` | eager GS readback verify |
| `PS2X_VU1_INTERPRETER`, `PS2X_VU0_INTERPRETER` | force the VU interpreter path |
| `PS2X_VU1_CAPTURE[_EVERY\|_SKIP]`, `PS2X_VU1_DIFF[_LOG]`, `PS2X_VU0_DIFF[_COMMIT]` | VU capture and differential tooling; diff modes run each call three times, never time with them |

## Patch stack

`deps.lock` pins each upstream (`ps2recomp`, `pcsx2`, `fukami`) by SHA. `./rx deps sync` creates `deps/<name>` at the pin on branch `rx` and applies `patches/<name>/*.patch` with `git am -3`. `./rx deps export` rewrites `patches/<name>/` with `git format-patch` (byte-stable) and refuses dirty trees. `./rx deps status` shows pin, commits and exported state; `./rx check` fails on unexported commits. Pull upstream in themed batches with a verifying test each; regenerate into a separate directory first (`tools/generate.py --out DIR`) to review output diffs.

## Tried and rejected

- Fast-math or FP contraction: breaks PS2 arithmetic.
- FPCR.FZ around native VU calls: inexact (`FLT_MIN + FLT_MIN * 0.5` gives `0x00800000`, the PS2 result is `0x00C00000`).

## Function discovery and SDK binding (stripped ELF)

No symbol map exists: the recompiler discovers functions itself (entry, JAL targets, prologues) and `generate.py` requires processed == discovered. `./rx where` reads the map from generated file names. SDK stubs in `config/SLES-53064.template.toml` come from `ps2_analyzer`'s embedded SDK signatures plus unique masked byte-pattern matches of the Tarzan port's SDK bodies (jal targets and non-stack immediates masked); libm/libc stay guest code.

## Disc access

The game reads by real disc LBN (it opens `DATA/IOP.BIG` at its ISO extent), so the launcher takes `--disc <image.iso>` and sets it as the runtime CD image for `sceCdRead` and the IOP CDVD; `./rx run` passes the `source_iso` recorded in `reports/disc-inventory.json`. The extracted tree stays the host file view. `sceCdRead` completes through the callback registered with `sceCdCallback`, run from an alarm event after the call returns.
