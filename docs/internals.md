# Internals

Mechanisms, invariants and constants that the code relies on. Commands are in `AGENTS.md`; state and next steps in `docs/STATUS.md`.

## Timing invariants

- PAL: VBlank 50 Hz. Movies are PTS-driven. Nothing may assume 59.94/60 Hz.
- Guest clocks (EE, COP0 Count, IOP, SPU) are never changed and the game is never sped up to raise a frame rate.
- Interactive hardware time follows the monotonic host clock. `PS2X_WALLCLOCK=0` selects deterministic estimated-cycle time instead. Estimated block costs never accelerate time.
- Synchronous guest IOP RPCs must not advance the SPU timeline on top of the EE-issued IOP budget. The scheduler keeps an absolute issued target and credits work already executed; reset clears the target and the fractional 1:8 carry; the SPU epoch stays monotonic. With live host audio, outside-thread delay, semaphore and event-flag waits pace IOP progress against elapsed host time so a wait cannot overflow the output ring before EE accounting catches up. Without a playback device, pacing is disabled.
- Movie presentation epoch is fixed. Late frames present promptly without shifting the PTS epoch or dropping decoded frames. PTS-less intervals use the effective VBlank period.
- `EeScheduler::idlePoll` yields a guest poll loop to the earliest event (EE timer or IOP/SPU2 idle deadline); pending SPU2 IRQs invalidate the IOP bound and wall-clock sampling accounts slept time. Its one caller is the vblank poll below.
- When every EE thread is blocked, `EeScheduler::run` also waits to the next IOP service deadline (using the same 1 ms wall-clock sample when the IOP cannot safely skip); omitting this deadline caused warm audio underruns during sound-heavy input. `PS2X_EE_IOP_IDLE=0` restores the old wait path.
- Game frames, host presents and vsyncs are different metrics. FIFA Street never changes DISPFB, so DISPFB change rate is useless. `fps_game` in `./rx run` is the median of fully post-confirmation 5 s gameplay windows; `./rx run --match` fails below 49.5. It counts GS FRAME_1/2 writes that retarget a context onto the displayed (DISPFB) buffer (`m_game_frames` in `deps/pcsx2` GSState, logged as `[gs-game]` every 5 s). `gpu_presents_per_sec` is the vsync rate. The probe reports bridge presents, HUD-clock observations and approximate state-dump VBlank/EE progress separately. Route profiling starts after gameplay confirmation; a successful route finishes after its gameplay observation tail. Menu inputs wait for OCR scene labels; movie skip waits for `INTRO.MPC` to open, and match-intro Cross requires the Marseille title plus commentary data.

The PAL renderer constructor (`0x294178`) initializes selector `1` at owner + `0x44C` and width/height `512` at +`0x450`/+`0x454`. `host/video-mode.cpp` wraps that constructor only for Settings NTSC, preserves incoming `a0` across the original call, then sets selector `0` **and height `448`**. Every dependent constructor store and setup load/call is ELF-word verified before installing. PAL installs no wrapper. `0x295820/24/28` pass width, height and selector to `0x295218`; selector `0` reaches `0x3C4FD8` with OMode `2`, then `sceGsResetGraph` through `0x3C28BC`. Selector `1` selects OMode `3`. The game owns allocation, projection, movie timing and VBlank selection; guest clocks and instructions are unchanged.

A selector-only NTSC override leaves the PAL 512-line height. The NTSC branch also calls `0x3C51A0`, setting GS owner + `0x8A8`; `0x3C2E58..78` then reserves at least ten 64-pixel blocks per scanline, versus eight for PAL. Per-draw GS dumps (`RRV_PCSX2_GS_DRAWDUMP`, `_FRAMES=start,1`, `./rx run --match`) measured PAL color buffers at block addresses `0x0000/0x1000`, depth `0x2000`, reflection target `0x3000`, pitch texture `0x3300` (PSMT8). Selector-only NTSC moves the second color buffer to `0x1400`, depth to `0x2400`, reflection to `0x3400`; the pitch's first/third triangle-fan passes carry invalid `TEX0=0xFFFFFFFFFFFFFFFF` (TBP0 `0x3FFF`, PSM `0x3F`), while the reflection pass uses valid TBP0 `0x3400`. Geometry reaches the GS. Increased VRAM reservation exhausting pitch residency is inferred from these addresses and the invalid descriptor; this is upstream of the GS texture cache, not a display-composition or VU clipping failure. Pairing selector `0` with 448 lines moves the second color buffer to `0x1180`, depth to `0x1F80`, reflection to `0x2D80`, and restores valid pitch TBP0 `0x2F80` (PSMT8) plus the visible surface in window captures. `video_mode` tests the paired state update and refusal of missing/mismatched ELF words; `settings` tests persistence, defaults and environment precedence.

## Generated guest ABI

Generated translation units include only `ps2_guest_abi.h`, `ps2_guest_macros.h`, and the generated function/stub declarations (plus debug logging when enabled). Runtime-only headers remain behind the out-of-line ABI entry points; edits to them do not rebuild generated unity units. Fast RAM access, address classification and guest arithmetic remain inline with the same semantics.

The EE scheduler keeps an always-on, allocation-free ring of its last 32 context transitions (thread switch, block, suspend, invocation save/entry/return/restore and poll yield). Missing-target reports, guest exceptions and uncaught executor exceptions dump `[ee-context-history]` records plus every live base/invocation context: thread, depth/kind, PC, RA, SP, EPC and delay-slot state. There is no logging on the normal path. `PS2RuntimeKernel` tests wrapping, nested-context identity and reset. For a manual crash report, include the entire `begin`/`end` block and adjacent missing-target line.

The skill-move `pc=ra=0x3` fault is explained by an omitted return delay slot: the discovered boundary at `0x1E8480` split `addiu sp,sp,0x30` from the `jr ra` at `0x1E847C` in `0x1E8410`. Decoding and entry reslicing now include the real delay instruction across a boundary. Without that restoration, `0x1E83A8` reads its callee's saved `0x1E83E4` as RA, runs its epilogue twice and leaves the original saved RA (`0x2AE7C8`) in v0, matching the manual fault's dispatch trace and registers. `PS2Recompiler` tests the split-symbol emission; gameplay confirmation remains a manual playtest. The scheduler diagnostic uses the same current PC for source and target, so those fields alone cannot identify the bad transfer.

## Instruction hooks

PS2Recomp has no mid-function override. Config `[[hooks]]` (address, `before`/`after`, optional `redirect`) make the recompiler emit a call through `g_ps2InstructionHooks[]`. Handlers are registered by the host and installed only if every guest instruction word they depend on matches the retail ELF. `generated/` stays pure recompiler output. The vblank poll below and the opt-in saved-RA diagnostic use instruction hooks.

**Vblank poll** (`host/poll-idle.cpp`, hook `0x3C1994`): `sub_003C18D0` spins on the word at `s1+0xBC` (callback slot `s1+0xCC`) until the VBlank-start interrupt handler `sub_003C4ED0` (INTC cause 2) clears it. While the word is non-zero, no callback is registered and timing is wall-clock, the hook sets `$v0`/`pc` as the taken back-edge leaves them and calls `idlePoll`, which wakes at the VBlank deadline. `PS2X_POLL_IDLE=0` spins, `verify` checks the loop registers and that the wait only advances Count; deterministic timing never sleeps. Test: `poll_idle`.

**Saved-RA watch** (`host/ra-watch.cpp`, `PS2X_WATCH_RA_STOMP=1`, off by default): retail-word-verified hooks at `0x1E83C4/CC/E4/FC` remember the `0x1E83A8` frame's saved RA, check RA and SP after each callee, and check the epilogue before returning. A RAM-store observer reports the first overlapping typed guest write that changes the watched eight bytes, with the guest store PC, address and proposed value; bulk writes bypassing typed stores are detected at the next hook. `[ra-stomp] reason=return-186238/return-1e8410` identifies a callee whose return left RA or SP wrong; `slot` is the saved-RA address, `entry_sp` the caller SP before this function's allocation, and `expected_sp` the frame SP at callee returns. Send any `[ra-stomp]` anomaly plus the adjacent context-history and missing-target records. Normal play with the watch disabled retains null-observer/hook checks only; do not measure performance with the watch enabled.

**Cheats** (`host/cheats.cpp`, Settings "Cheat: unlock all venues", `PS2X_CHEAT_UNLOCK_VENUES=1`, off by default): FIFA Street's cCheatCodeManager venue handler (`0x1E8CC8`) only stores 1 to `0x480684` and shows "All venues unlocked!"; venue select reads it (`0x2CD248`, `0x2D320C`). The front-end update (`0x2ECA38`) is wrapped and raises that word after the original runs; ELF words are verified first. The kits/tournaments/players handlers store `0x480688`/`0x48068C`/`0x480690` the same way.

Removing a config stub alone cannot restore guest execution: PS2Recomp also auto-binds names in `ps2_call_list.h`, so both lists must change together.

## Boot to match (diagnostic)

`./rx run --match` sets `PS2X_BOOT_MATCH` in a scratch user dir. `host/boot-match.cpp` replaces the front end's per-frame update (`0x2ECA38`) in the function table with a wrapper that runs the original, then once the front-end resource slot (`0x47EFEC`) is filled: stores venue 3 directly at `GameSetup+0xA0`, calls `GameSetup::SetControl` (`0x12AE00`) for side 1 to pad 0, and calls `Playgame` (`0x2E5220`). Inert without the variable, in the real save directory, or if a dependent ELF word differs. Test: `boot_match`.

The `match-boot` route repeats Cross while the language screen remains visible because a single early press can be missed before pad polling begins.

## VIF1 front end and op stream

`PS2Memory::processVIF1Data` (`ps2_vif1_interpreter.cpp`) is split into a front end and a consumer. The front end parses commands and owns everything the EE can observe: VIF1 registers (including ROW, rewritten by STMOD 2/3 UNPACK), DBF/TOPS, stall and IRQ state, partial-command buffering, MSKPATH3 and the DIRECT continuation counters. It emits `Vif1Op`s in order: `Mpg`, `Unpack` (snapshot of CYCLE, MODE, MASK, ROW, COL plus payload), `Mscal`/`Mscnt` (start PC, TOP, ITOP), `Direct` (PATH2 bytes), `Path3Unmask`.

`unpackVectors` is shared: the consumer runs it on VU1 memory, the front end with a null destination to advance ROW. Payloads point into the caller's input, so a deferred consumer must copy at emit (the MFIFO path passes the live ring). MSCAL/MSCNT carry the FBRST D/T halt enables read at issue and leave the VPU_STAT stop bits in `vu1Exec().stopBits` for the EE to publish. MFIFO tag limits follow the configured ring capacity. XGKICK scratch is reused across resets.

## GS bridge interface

`DataStream` (`ps2_data_stream.{h,cpp}`) is the one interface to the GS bridge: `gifSubmit`, `gsReset`, `gsWriteCsr`, `gsVsync`, `gsReadLocal`. GS CSR and SIGLBLID are a pure function of the packets processed, so `gifSubmit` only marks them stale; `observeGs()` reads them back at drain points: any EE access to a GS privileged register, CSR writes, and the VBlank snapshot. `PS2X_GS_VERIFY=1` reads back after every packet and aborts if the lazy value differs. The bridge is owned by the thread that created it (thread-local `s_gs` in `host/metal-host.mm`).

PCSX2's Metal shaders are compiled at bridge start from `deps/pcsx2` (`GSMTLDeviceInfo.mm`, `newLibraryWithSource`, Metal 2.3, fast math as PCSX2's own metallib build): the bridge build inlines every `.metal` file into `resources/pcsx2-metal-shaders.metal`. No Xcode `metal` compiler is needed, and shader patches in the stack take effect.

FIFA Street decompresses many 8-bit textures (pitch, flags, headshots, venue previews) on the GS: a low-resolution PSMCT32 base is stretched 2x or 4x with nearest sprites, a PSMT8/PSMT4 detail layer is added through a CLUT with `Cd + Cs` under FBMSK `0xfc000000`/`0xfe000000`, and the 32-bit target is then sampled as PSMT8. Every byte of the target is a texel index, so any per-channel error in the HW renderer, alpha included, shows as a regular grid of 8-texel dashes. `RRV_PCSX2_GS_DRAWDUMP`, `_TEX=1`, `_FRAMES=start,count` dump PCSX2's per-draw context, source textures and targets.

## Data-side worker

`DataStream` has two modes. Inline runs every op on the calling thread. Worker (default) copies each op into a 16 MiB SPSC ring (`StreamRing`, `include/runtime/ps2_stream_ring.h`), executed in order by a worker thread that owns VU1 code and data, the VU1 dispatcher, the `GifArbiter` and the GS bridge. Kill-switch: `PS2X_VU1_WORKER=0` selects inline; `sync` drains after every kick (the no-overlap proof mode).

Ops: `Vif`, `GifPacket` (packets over 1 MiB are read in place while the EE waits), `GifDrain` (an arbiter batch boundary), `GsReset`, `GsWriteCsr`, `GsVsync`, `GsReadback`, `GsReadLocal`. DIRECT parsing and the PATH3 mask stay on the EE and only decide which ops are enqueued, in guest order. Ops publish when the outermost `KickScope` (processVIF1Data, processPendingTransfers, MFIFO kick) ends.

The ring uses monotonic head/tail byte counters (seq-cst). The worker never waits on the EE. Every EE-to-worker blocking edge has a 2 s watchdog that prints the stuck op and aborts. A worker exception is rethrown on the EE at its next enqueue or drain.

The EE waits only at: GS CSR/SIGLBLID access with packets outstanding; any EE load or store to VU1 code or data (`mapVuMemory`); the VBlank vsync with CSR readback; GS local-to-host reads; MSCAL/MSCNT while FBRST halt enables are or were set; and `PS2Memory::initialize` and shutdown. Control-plane state (VIF1/DMA registers, INTC causes, stalls, GIF_STAT) is final on the EE when each kick returns.

VIF1 FLUSH/FLUSHE/FLUSHA do not wait for queued VU1 work or GIF paths, unlike PCSX2 `Vif_Codes.cpp`. FIFA Street issues them about a million times per match (measured); making them wait blocks the EE on the worker thread (see Tried and rejected).

Tests: `ps2_data_stream_tests.cpp` covers ring ordering and payload integrity across wraps with random barriers, exception propagation, and identical VU1 memory, MSCAL sequence and GIF packet order versus inline.

Replay frame capture queues two 0x34-byte records (`sub_0029D2F8`); `sub_0029DD38` waits on record+8, written to 1 at `0x3BF3E0` by the VIF1 DMA handler after a GS download. Reverse VIF1 DMA (`DIR=0`, retail `CHCR=0x184`) ignores MOD, preserves TADR, consumes the native GS FIFO on its owner thread, advances MADR/QWC and raises DMAC1. The REWIND freeze was traced to the completion word not being written; the game tick and VBlank continued advancing. Replay animation was fixed in `af239cd`.

## VU

VU0 runs interpreted. VU1 native code comes from disc-derived programs; the interpreter handles anything else, and `PS2X_VU1_INTERPRETER=1` forces it. `PS2X_VU1_DIFF=1` compares native state, data, timing and packets with interpretation; `./rx run` reports the counts and fails on a mismatch, budget stop or missing differential coverage. Differential agreement establishes interpreter agreement only. Strict floating-point flags apply to both paths. Native state, data and GIF packets commit only after successful completion.

- **Programs (disc-derived).** The game links EAGL microprograms (`...__EAGLMicroCode` descriptors: `{slots, n, 0xffffffff, chain, chain_end}`) into the boot ELF and into `mat.o` of `DATA/INITSTAZ.BIG` (BIGF holding one refpack BIGF; `mat.o` is a MIPS relocatable). Each descriptor points at a DMA chain of VIF MPG uploads that the game copies and places (inferred: only the MPG destination field is patched) at varying micro-memory bases; banks are back-to-back placements of such programs plus stale residue. `tools/vu/extract-programs.py` walks the chains as VIF1 sees them (tag payloads only, TTE off) and writes each distinct program; an odd final MPG takes its last word from the next packet's first 8 payload bytes (zero), so that word belongs to the program. Every MPG payload seen on the match route is a slice of an extracted program. `vu1recomp.py --programs` computes live exit flags across all programs and MSCNT continuations, then emits position-independent `live` and `full` functions (`vu1prog_<name>.cpp`): node PCs are program-relative, BAL/JALR links add `ctx.relocBase >> 3`, JR targets and the end PC add or subtract the base, and execution leaving the program fails closed. The dispatcher scans micro memory per code generation for whole-program word matches and needs no per-pair guard; MSCAL and MSCNT pick the placement containing the PC. `PS2X_VU1_FLAGS=full` restores all flag computations; default is `live`. Kill-switch: `PS2X_VU1_PROGRAMS=0`.

- VU0 macro FMAC computes masked Z/S/U/O and sticky status eagerly (no lazy flag liveness). FDIV updates only I/D and sticky copies, treating exponent-zero operands as zero. VI0 discards integer destinations at translation time.
- VCLIPw compares signed float bit patterns against absolute Ft.w; an exponent-zero bound becomes `0x007fffff` (PCSX2 `_vuCLIP`); clip history keeps its low 24 bits.

## Audio and movies

- The guest audio callback returns zero when its ring cannot take a complete PES payload. The MPEG shim must then leave the PES in the input ring and report only accepted bytes. Erasing it shifts later data and corrupts ADPCM. Demux stops at the first registered audio PES; incomplete and wrapped packets are assembled from both ring sides; CD/EOF accounting follows accepted bytes. Never mask a symptom with mute, gain reduction or a limiter. Code: `deps/ps2recomp/ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp` (`processPssBuffer`, `dispatchGuestStreamCallback`).
- The SPU host issues `Sync(t)` only. An inline/threaded fixture compares events, core state, PCM hash and frame count. Time stretching is off unless `PS2X_AUDIO_STRETCH=1`: with the host-clock-locked guest the stretcher reacted to producer bursts by dropping about a fifth of the output frames in a match. Underrun statistics count every frame the host requests but the SPU ring cannot provide, including startup; do not gate the counter on sample amplitude or playback state.

## EE FPU, VU0 and IOP semantics

- SQRT.S/RSQRT.S clear current FCR31 I/D causes before setting the operation's exception (sticky flags kept). Negative nonzero Ft sets I/SI; zero or denormal Ft sets D/SD for RSQRT. Classification precedes the destination write, so Fd may alias Ft.
- IOP LWL/LWR merge with the pending load value when its destination matches `rt`, else with `gpr[rt]`. The result keeps its load delay, and a direct register write in the delay slot supersedes the pending load.

## Settings and environment

`tools/probe/probe.py` reads macOS `proc_pid_rusage` RUSAGE_INFO_V4 around the bounded run when available. `energy_per_s` is billed energy in nJ/s; `wakeups_per_s` is package-idle plus interrupt wakeups per second. Both are null when the API or either sample is unavailable. They are process counters, not whole-system power readings.

One `host::Settings` struct (`host/settings.hpp`, defaults there) loads from `<user dir>/settings.plist`. Keys: `videoMode` (`pal` default or `ntsc`), `renderScale` (1..4, native by default), `anisotropy` (0, 2, 4, 8 or 16; off by default), `sharperImage`, and `displayAspect` (`auto`, `4:3`, or `16:9`). Settings are applied at start; the window and settings UI are in `host/settings-window.mm`. The runtime receives host config through `PS2HostConfig`, not the environment. Keyboard bindings live in `keyboard.plist`.

`host/metal-host.mm` derives bridge variables from `Settings`.

Display aspect also controls the window's content ratio and the letterboxed full-screen viewport; the GS bridge uses PCSX2's aspect draw rectangle. `auto` selects PCSX2's automatic GS mode detection. The FIFA widescreen option is stored in guest state that the host does not currently read, so use `16:9` after enabling the game's own 16:9 option; `4:3` preserves the original host presentation.

| Variable | Effect |
|---|---|
| `PS2X_WALLCLOCK=0` | deterministic estimated-cycle timing |
| `PS2X_POLL_IDLE=0` / `verify` | spin in the vblank poll / check its loop registers and wait context (never measure verify mode) |
| `PS2X_EE_IOP_IDLE=0` | disable the IOP service deadline in the all-EE-threads-blocked wait path |
| `PS2X_CHEAT_UNLOCK_VENUES=1` | the game's own venue cheat flag (Settings checkbox; off by default) |
| `PS2X_VIDEO_MODE=pal\|ntsc` | original PAL 50 Hz / opt-in game NTSC 60 Hz with 448-line render height (Settings; restart to apply) |
| `PS2X_ASPECT=auto|4:3|16:9` | display aspect override (Settings menu; defaults to `auto`) |
| `PS2X_SCALE=1..4`, `PS2X_ANISO=0|2|4|8|16`, `PS2X_SHARP=1` | override render scale, anisotropic filtering and sharpening for this process |
| `PS2X_DIAGNOSTIC_INPUT_FIFO=<fifo>` | process-local scripted input |
| `PS2X_TRACE_FAULTS=1`, `PS2X_TRACE_SPU2=1`, `PS2X_TRACE_CD_IO=1` | fault, audio-health and disc-read traces |
| `PS2X_TRACE_AUDIO_RING=1` | once-per-second SPU2 ring depth, mix rate, pulls and under/overruns without per-event SPU2 tracing |
| `PS2X_SPU2_RING_FRAMES=<4096..65536>` | set output-ring capacity (power of two); default 16384, set 8192 to restore the previous capacity |
| `PS2X_TRACE_SYSCALLS=1`, `PS2X_TRACE_RPC=1` | first calls per EE syscall number; first 2000 SIF RPC events |
| `PS2X_CAPTURE_DIR`, `_FRAMES`, `_START_FIELD`, `_INTERVAL` | bounded Metal frame capture |
| `PS2X_AUDIO_STRETCH=1` | enable adaptive audio time stretching (off by default) |
| `RRV_PCSX2_GS_PRESENT_MIN_US=<us>` | minimum interval between host presents (default 8000, 0 disables); frames closer than this are skipped so a burst of catch-up VBlanks cannot block the EE on `nextDrawable` |
| `RRV_PCSX2_GS_TEST_PRESENT_STALL_MS=<ms>` | inject one Metal present stall; the StreamRing watchdog excludes only this host drawable-acquisition interval |
| `PS2X_VU1_WORKER=0\|sync` | inline or drained data side |
| `PS2X_GS_VERIFY=1` | eager GS readback verify |
| `PS2X_VU1_INTERPRETER`, `PS2X_VU0_INTERPRETER` | force the VU interpreter path |
| `PS2X_VU1_FLAGS=full|live` | retain all VU1 flag work or use generated liveness (default `live`) |
| `PS2X_VU1_DIFF[_LOG]`, `PS2X_VU0_DIFF[_COMMIT]` | VU differential tooling; diff modes run each call twice, never time with them |

MPEG work layouts vary by SDK revision. Run retail Create and ClearRefBuff; the host decoder must not write private work fields or executable-specific globals. Reset clears the common work+0/+4/+8 state. Retail `sceMpegIsEnd` at `0x40A3E8` is `8C830040 03E00008 8C620000` (load handle+0x40, return work+0); its HLE binding reads host decoder completion. GetPicture publishes dimensions in the public handle and writes the decoded image.

## Controller packets

Pressure mode reports packet ID `0x79`; analog mode without pressure reports `0x73`, digital `0x41` (PCSX2 `SIO/Pad/PadTypes.h`). The game validates `data[0] == 0` and `data[1] == 0x79` at `0x103750..0x10376C` before consuming buttons.

SIF command handler tables use 8-byte user entries and 12-byte system entries (ps2sdk `sifcmd-common.h`). Packets use separate temporary IOP storage. The EE public SendCmd ABI passes destination/size in t0/t1; the internal entry adds mode in a1 and shifts packet/size/source/destination/extra-size through a2/a3/t0/t1/t2. Retail wrappers at `0x448290` and `0x448158` establish these layouts. SIF replies may interrupt HLE guest callbacks. `PS2X_TRACE_RPC=1` includes accepted SendCmd dispatch results.

EA snddrv registers RPC SID `0x534E44`, then command ID 0 through IOP sifcmd import 10 (handler `0x268FC`, argument `0x331C0` after relocation); import 8 supplies the 16-entry handler table at `0x331C8`. Subcommand 4 replies to EE command ID 1/subcommand 5; EE handler `0x37AC58` forwards it to `0x385CE0`.

EA sound banks (`.ABK`, magic `ABKC`) embed relocated EE code; the game links its `jal` targets to ELF symbols at load and calls it through function pointers. Code above the recompiled ELF runs in `ps2EeInterpret` (`ps2xRuntime/src/lib/ps2_ee_interpreter.cpp`, integer subset, reports anything else). The public `sceSifSendCmd` (`0x448290`, a0=cid) and internal `sceSifSendCmdInternal` (`0x448158`, mode in a1) are separate handlers. EA's snddrv init busy-waits on SPU2 DMA completion, so SPU2 events are advanced while IOP guest code runs. IOP AutoDMA (ADMA) goes through the PCSX2 SPU2 core. Movies are raw MPEG-2 streams fed by the game's callbacks; `sequence_end_code` ends them. Diagnostics: `PS2X_TRACE_IOP_IMPORTS`, `PS2X_TRACE_RPC` (SIF commands both ways), `PS2X_TRACE_SPU2`, `PS2X_DUMP_RDRAM=<path>` (with SIGUSR1).

## Patch stack

`deps.lock` pins each upstream (`ps2recomp`, `pcsx2`) by SHA. `./rx deps sync` creates `deps/<name>` at the pin on branch `rx` and applies `patches/<name>/*.patch` with `git am -3`. `./rx deps export` rewrites `patches/<name>/` with `git format-patch` (byte-stable) and refuses dirty trees. `./rx deps status` shows pin, commits and exported state; `./rx check` fails on unexported commits. Pull upstream in themed batches with a verifying test each; regenerate into a separate directory first (`tools/generate.py --out DIR`) to review output diffs.

## Tried and rejected

- PAL Metal FIFO pacing on this 60 Hz panel: clean-quit gameplay traces measured presented intervals of 20.114±7.048 ms off vs 20.033±6.786 ms on; deviations from the best phase of the expected 1-1-1-1-2 refresh cadence were 25.32% vs 22.44%, and mean submit-to-present latency was 11.852 vs 25.071 ms. Trace command: `RRV_PCSX2_GS_PRESENT_TRACE=<path> PS2X_PRESENT_SYNC=<0|1> python3 tools/probe/probe.py --route match --quit-at-end --app 'build/dist/FIFA Street PS2.app'`. The three-pair `./rx ab --control PS2X_PRESENT_SYNC=0 --candidate PS2X_PRESENT_SYNC=1 --pairs 3 --match` had 50.0 FPS median in non-goal `fps_game_series` windows in both arms, but one candidate run had 46,254 audio-underrun frames; the clean-quit sync trace had 372 vs 0 control. Keep rejected; true input-to-photon latency and 120/144 Hz behavior remain unmeasured.
- Boot hook for any venue: direct `PS2X_BOOT_MATCH=venue=5` loads `ROMA.BH` and `ROMA.BIG` but faults at `0x3D23C0` through a null `0x48EBC8` callback; a normal menu route reaches `match_play` without a crash (`./rx run --route match --seconds 60`). The front-end handler at `0x2CCEB8` calls `setvenuebyid` (`0x2E64C0`), whose raw id 5 selected Amsterdam in the earlier trace, while the hook's raw GameSetup id 5 selects Roma. Inference: the normal menu/gameflow establishes callback state; its writer was not identified by the temporary `0x48EBC8` write/value trace.
- Selector-only NTSC startup: keeps the PAL constructor’s 512-line height, expands GS reservations and sends an invalid pitch TEX0 to the GS. Mode selection must pair selector `0` with the 448-line NTSC height. PAL is unchanged.
- Goal/replay peaks above 1.0 come from the upstream DC blocker overshooting a clamped near-full-scale SPU2 mix (measured raw peak 32,766); the host adds no gain and matches PCSX2's output stage.
- FPU direction-index overflow and unaligned SQ are not needed to explain this skill-move fault: the missed `0x1E8480` stack restoration predicts both epilogue dispatches and v0; quadword macros already mask the low four address bits, as PCSX2 does.
- Treating scheduler `source=target=0x3` as evidence of a missing translated function: both fields come from the same saved PC; the preceding guest epilogue can return an unchecked stack-loaded RA.

- Fast-math, FP contraction and FPCR flush-to-zero change PS2 arithmetic.
- The VBlank poll is not a lost DMA IRQ: the VBlank handler clears the poll word after the VIF1 END tag and configured field count. A rejected language-menu input was a pad packet-ID mismatch.
- EE-side texture upload corruption was ruled out by matching uploads and GS local memory; the dashed-texel issue was the upstream Metal FBMask alpha truncation, now fixed.
- `SDL_GetAudioStreamQueued` does not measure device queue depth because callback playback drains it immediately.
- EE alarm host-deadline waits did not explain startup audio gaps; tracing found no long waits in `processDueDeadlines`.
- Raising thread QoS and blocking immediately in stream-ring waits did not improve the measured route and increased wakeups; see `./rx ab` for repeatable comparisons.
- A last-placement VU1 program cache was not validated because a run lost gameplay progress. `PacketCapture` must stage VU packets until native execution succeeds; the stream ring copies only its op header.
- Metal pipeline and function creation were too short to explain audio bursts. The observed stall was `CAMetalLayer nextDrawable` on the GS worker during catch-up presents, while the EE waited at the GS vsync drain.
- PCSX2's present throttle is inactive without FIFO vsync. CD reads and ordinary VU1/GS traffic did not explain the stalls.
- Waiting for queued VU1 work on each VIF1 FLUSH blocks the EE on the worker and violates the hardware's short drain semantics.
- `MTLBinaryArchive` prewarming did not resolve cold-launch shader-cache underrun bursts; cold-cache stalls remain open.
- Bounded GS vsync backlog (EE up to 2 vsyncs ahead of the worker, as PCSX2 MTGS VsyncQueueSize): cold-cache underruns only 5.6k -> 4.9k, warm still 1 in 6 runs, energy +177% and one 49.2 fps run in the A/B. Cold-cache stalls are bursts of about 15 dynamic ps_main/HW pipeline compiles of 5-7 ms each (about 170 ms), longer than the queue.
- Hybrid ubershaders for first-use pipeline stalls: a 25-field runtime ps_main compiled 7x slower (30.7 vs 4.5 ms), raised GPU time 155% when forced, still needed 64 fixed pipelines (blend/format), and upstream PCSX2 has no Metal version. Boot pre-warm of the measured key list is the chosen path.
- MTLBinaryArchive pre-warm of recorded keys: cold runs still underran (about 7.5k).
- 4× field-mode menu captures: HPO 0–5, Round Sprite 1/2, and Align Sprite alone and combined showed no visible text/UI improvement; forced bilinear including sprites corrupted team flags, while forced bilinear excluding sprites showed no visible change. Full-mode Merge Sprite and Native Scaling do not apply to the observed field mode.
- Same-size Lanczos/full-chroma MPEG conversion at the decoded 640×512 frame size produced no visible improvement in matched intro captures; the visible stretch remains in GS presentation, so the conversion experiment was reverted.

## Function discovery and SDK binding (stripped ELF)

No symbol map exists: the recompiler discovers functions itself (entry, JAL targets, prologues) and `generate.py` requires processed == discovered. `./rx where` reads the map from generated file names. SDK stubs in `config/SLES-53064.template.toml` come from `ps2_analyzer`'s embedded SDK signatures plus unique masked byte-pattern matches of known Sony SDK bodies (jal targets and non-stack immediates masked); libm/libc stay guest code.

## Disc access

The game reads by real disc LBN (it opens `DATA/IOP.BIG` at its ISO extent). `./rx disc` writes `extracted/disc/.lbn-map` (volume size, each file's extent, and the non-file sectors) and `.lbn-gaps.bin` (those sectors); both are local and bundled with the extraction. `ps2x/iop/lbn_map.h` serves `sceCdRead`, `sceCdSearchFile` and the IOP CDVD from them: one numbering scheme, no ISO at runtime; a missing map reports `-> ./rx disc <iso>`. `sceCdRead` completes through the callback registered with `sceCdCallback`, run from an alarm event after the call returns. `./rx build` fails if a configured stub resolves to a TODO handler.
