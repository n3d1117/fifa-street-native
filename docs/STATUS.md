## Resume

- Intent: gameplay. `./rx run --route match` reaches the Marseille match intro (scene: match = commentary stream opened); not yet verified that play runs (kickoff, moving players).
- Verified: 0x2e0000 root cause fixed (false function splits from data-table guesses); route reaches match; menus show regular-dot texture artifacts (portraits, flags, pitch) to compare with PCSX2.
- Blocker: audio underruns in the match (about 10k frames/s, route FAILs). Measured: the EE thread does not account cycles for 200-350 ms about once a second during the match (`advanceIopEeCycles` gaps), so IOP/SPU2 mixes in bursts (ring overrun plus underrun). Hypothesis: a blocking wait on the VU1/GS worker (VU1 interpreter is 70% of worker time, all `noimage` fallbacks) or CD/stream work on the EE thread; find it with `./rx run --profile` timed into the match (probe `--profile-after 190`).
- Next: (1) find the periodic EE stall, advance IOP during it or remove it; (2) route: kickoff and held direction plus pass, detect play, capture a frame; (3) measure gameplay fps (`fps_dispfb2`, 20 s steady, no tracing) and record it; (4) dot pattern vs PCSX2; (5) startup underruns now count only after the first audible sample.
- Gotchas: input hold 0.12 s (menu auto-repeat doubles longer presses); route timings are fixed delays (about 215 s total); `PS2X_AUDIO_STRETCH=1` re-enables the stretcher.

## Current

| Area | State |
|---|---|
| Engine | Builds on macOS arm64. Quick tests pass without a disc (see `./rx test`) |
| Game | Recompiles and links (see `./rx build` for translation counts). Boots to the language menu; scripted Cross is accepted; EA SPORTS BIG and FIFA Street intro movies play with sound (SPU2 rms non-zero), then the legal screen, front-end menus and a loading screen. A crash follows (below). Match gameplay not verified |
| Entry | `0x100008`; `.text` at `0x100000`, about 3.6 MB |
| Platforms | macOS arm64 only |

## Disc

`./rx disc <iso>` writes `reports/disc-inventory.json` (untracked): 169 files, 1.73 GB, all under `DATA/` except `SLES_530.64`, `IOPRP280.IMG`, `SYSTEM.CNF` (PAL). Measured from the extracted files:

- **Containers** (magic): `BIGF` (16 `.BIG`, 2 `.VIV`, with `.BH` index `VivF`/`Viv3`), `BIG4` (`MODELS`, `ANIMS`, `DATAFILE`), `BIG3` (`PRELOADZ`), `c0fb`/`10fb` refpack-compressed wrappers (`FEMAN`, `INGAME`, `IOP.BIG`). Stadiums are one `BIGF` each.
- **Audio**: `SCHl` EA streams (`.AST`, and `PBPDAT_{E,F,D}.BIG` commentary data, 330-450 MB each), `ABKC` banks (`.ABK`), `.MUS` with no magic (`PRIME`, `SECOND` are 173/19 MB), `MOIR` `.CSI`, `xDFP` `.MPF`, `FX` `.FX`. Format internals not yet read.
- **Movies**: `DATA/FEMOVIES/*.MPC` (5), magic `MPCh`, then a raw MPEG-2 elementary stream (`00 00 01 B3`, 640x512, "Encoded with MPEG Gimex module"). Not PSS. The ELF has `PsIIlibmpeg`/`PsIIlibipu`, so decoding is via IPU (inferred).
- **IOP**: `IOPRP280.IMG` is a ROMDIR image of 16 Sony modules: sysmem, loadcore, sifcmd, sifman, threadman, ioman, modload, fileio, cdvdman (2.23), cdvdfsv (2.23), loadfile, timemani, romdrv, eesync, sysclib, stdio (extinfo versions; dated 2003). Name `280` and the ELF's `PsIIlib* 2800` markers point to SDK 2.8.0 (inferred).
- **IOP.BIG** (refpack of `BIG4`) holds 7 `.irx` the ELF loads from the disc ("Loading IOP module %s from IOP bigfile"): Sony `sio2man` 3.00, `mtapman` 3.02, `padman` 4.05, `libsd` 3.03, `mcman` 2.34, `mcserv` 2.16, and EA `snddrv` 9.02 (`SND_Library_EE_Driver`, on top of libsd). No `.IRX` files sit loose on the disc.
- The ELF also names `usbd`, `inet`, `dev9`, `smap` and other net/USB IRX paths (strings only; no evidence they are loaded in normal play). The ELF opens files as `DATA/...` and references `C:/fifast/source/...` paths.

## Next

1. **Crash after front-end music starts.** After `PRIME.MUS`/`RAM6.MUS` reads the main thread returns to guest pc `0x2e0000` (mid-function in `sub_002DE7F8`, not a function entry; `sub_00275EC8` loop calls a vtable entry through `jalr $a1`, then `jr $ra` lands there). Find the stack or vtable value that is wrong; suspects: sound-bank routines run by the EE interpreter, HLE return values.
2. **Audio start-up underruns.** The first second reports a burst of underruns (host ring primed late); steady state is clean.
3. **Movie striping.** Earlier frames showed line combing; the last runs did not. Re-check against PCSX2 deinterlace.
4. **Menus and match.** Reach the main menu, start a match, then add `./rx run --route match` with scene checks.
5. **Whole-game faithful pass** against PCSX2 behavior.
6. **Performance.** Profile first. Native VU is a later step.
7. **iOS/tvOS.** See the plan below.

Diagnosis tools: `./rx run --seconds N` (N >= 8) puts a runtime state block (EE threads, semaphores, sampled pcs resolved to functions, DMA/VIF registers, IOP, recent RPC) in `summary.json` `state`; `PS2X_DUMP_MEM=<hexaddr>:<words>`, `PS2X_TRACE_SYSCALLS/RPC/CD_IO/FAULTS`, SIGUSR1 for a dump on demand. Code edits to headers the generated code includes (`ps2_runtime.h`, ...) cost a 5 minute rebuild; `.cpp` edits 3-10 s.

Known risk: PCSX2 needs GIF FIFO and EE cycle-rate accuracy for FIFA Street 2; watch both.

## Open questions

- Gameplay frame rate on this title (not known; do not assume 25 or 50).
- Whether MPC movies decode through the existing MPEG/IPU HLE as raw MPEG-2 elementary streams, and the `.MUS` format.

## iOS / tvOS plan

Feasible because the design is ahead-of-time: no JIT, and the VU and IOP fallbacks are interpreters. Landscape only, sideloaded. Device validation, not a recompile.

1. Host-agnostic core: the host owns the main thread; the core runs on its own thread behind a small C API and a `HostSurface` interface.
2. Cross-build the static libraries (GS bridge, SoundTouch, sse2neon, movie-only FFmpeg) with LLVM 19 against the iOS or tvOS 14 SDK. Xcode supplies `metal` and `actool`.
3. PCSX2 Metal GS on iOS: caller-owned `CAMetalLayer`, no IOKit, check the GPU's texture support. Decide the shader fast-math flag deliberately; it conflicts with the arithmetic rule.
4. UIKit host, audio-session handling, GameController input, disc import with hash checks, saves in Application Support. Never bundle game data.
5. Device validation: start at 1x; check memory (jetsam), thermals and movies. No speed-ups to inflate FPS.
