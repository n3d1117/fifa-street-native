## Resume

- Intent: reach and verify active match gameplay, then guard it in `./rx run --route match`.
- Verified: `./rx run --route match` reaches `scene=match_play` after OCR confirms an advancing HUD clock. During a held left-stick-right input, `scePadRead` returned `0xff,0x80`; later right/left holds returned `0xff,0x80` and `0x00,0x80`. Captures show the red-ringed player moving across the court as the HUD clock advances. Camera tracking and live play prevent isolating the player's response to each direction. A traced same-route A/B found substantially more missing output frames with `PS2X_AUDIO_STRETCH=1` than with the default-off stretcher; all output underruns are counted.
- Blocker: match audio still has severe output underruns. Stretcher-on worsens missing frames in the traced same-route comparison. The low-overhead ring trace shows the queue reaching both empty and full while underflow and overrun accrue; this points to bursty producer/consumer timing but does not identify its cause. Active-match profiles are dominated by guest VIF/INTC work with only brief `DataStream::gsVsync`/`StreamRing::waitFor` samples; the reported long EE cycle-accounting gaps were not reproduced.
- Next: (1) verify the SDL device and callback rates, then explain the ring's empty/full swings; (2) identify a guest gameplay-frame metric (`fps_dispfb2` stays unavailable while the register is unchanged; bridge presents are reported separately); (3) isolate the selected player's response to opposing stick input; (4) compare the pitch, portrait and flag dot pattern against PCSX2.
- Gotchas: `commentary_open` means commentary data opened, not that gameplay began; `scene=match_play` requires two advancing HUD-clock readings; the full movie and match intro keep the route near nine minutes; `PS2X_AUDIO_STRETCH=1` re-enables the stretcher.

## Current

| Area | State |
|---|---|
| Engine | Builds on macOS arm64. `./rx test --full` passes all 54 tests |
| Game | The guarded route reaches the live England–Brazil HUD and renders active play under scripted stick input; the selected red-ringed player moves during those captures, but directional response and gameplay fps remain unverified. Bridge presents are separate from `fps_dispfb2`. Audio underflows remain severe |
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

See the Resume block for the current blocker and ordered work.

Diagnosis tools: `./rx run --seconds N` (N >= 8) puts a runtime state block (EE threads, semaphores, sampled pcs resolved to functions, DMA/VIF registers, IOP, recent RPC) in `summary.json` `state`; `PS2X_DUMP_MEM=<hexaddr>:<words>`, `PS2X_TRACE_SYSCALLS/RPC/CD_IO/FAULTS`, SIGUSR1 for a dump on demand. Code edits to headers the generated code includes (`ps2_runtime.h`, ...) cost a 5 minute rebuild; `.cpp` edits 3-10 s.

Known risk: GIF FIFO and EE cycle-rate accuracy need comparison against PCSX2.

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
