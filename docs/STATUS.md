## Current

| Area | State |
|---|---|
| Engine | Builds on macOS arm64. Quick tests pass without a disc (see `./rx test`) |
| Game | Recompiles and links (see `./rx build` for translation counts). Boots to the language menu; scripted Cross is accepted and reads `DATA/FEMOVIES/EABIGEN.MPC`, and plays about the first half of the EA SPORTS BIG intro movie (video, no sound; frames show horizontal field striping), then freezes on one frame without a crash. Main menu and gameplay not verified |
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

1. **Movie refill waits on audio completion.** Cross reaches `EABIGEN.MPC`; nine 0x40-sector chunks are read before the packet loop `sub_00367678` stalls. The shared stream is full; its oldest packet carries audio channel 5 token `0x105` and awaits completion callback `0x37F2F0`. The CD worker and request queue are idle; TIM1 and guest timer callbacks advance. No accepted EE SendCmd reaches dispatch with `PS2X_TRACE_RPC=1`; inspect the guest sound-engine pump (`0x382128`, enable/count at `0x561740/0x561743`, stream-slot table `0x561484`) before the command sender. Retail MPEG Create/ClearRefBuff initialize the SDK work layout; GetPicture/Reset preserve private work fields.
2. **Movie striping.** Frames show line striping (interlaced fields combed); compare PCSX2 default deinterlace and the bridge's field presentation.
3. **Audio.** Host pulls silent samples (rms/peak zero); initial underrun burst remains. Trace SPU2 master volumes, MMIX, key-ons and sample DMA; the host ring is connected.
4. **Menus and match.** Verify intro movies with sound, reach the main menu, start a match, then add `./rx run --route match` with scene checks.
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
