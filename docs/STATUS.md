## Current

| Area | State |
|---|---|
| Engine | Builds on macOS arm64. Quick tests pass without a disc (see `./rx test`) |
| Game | Recompiles and links (13533 functions discovered by the recompiler, no symbols; 144 HLE-bound SDK stubs). Boots: reads `IOP.BIG` by real LBN, loads its 7 IRX modules on the virtual IOP, and shows the FIFA Street loading screen with a progress bar, then stops (see Next 1). Movies and menus not reached |
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

1. **Loadfile RPC.** The loading bar stops at about a third: the game polls `BindRpc` for SID `0x80000006` (IOP loadfile) from `sub_0044A588` (called through `sub_003E0698` / `sub_003DF818`, loading something beyond the 7 `IOP.BIG` modules). No IOP module serves it: `IOPRP280.IMG`'s Sony modules (loadfile, cdvdfsv, ...) are not run. Decision: HLE the loadfile RPC server (functions load module / load buffer / start / stop through the existing IOP loader) or load the IOPRP280 modules as LLE IRX. Check first what the game loads (path) with `PS2X_TRACE_RPC=1`.
2. **Movies and menus.** `DATA/FEMOVIES/*.MPC` (EA `MPCh` wrapper, raw MPEG-2) not reached yet; EA `SCHl`/`.MUS` audio decoders. The run shows 224 audio underruns (measured, cause not looked at).
3. **Play a match.** Add a scripted fast-match gate for `./rx run`.
4. **Whole-game faithful pass** against PCSX2 behavior.
5. **Performance.** Profile first. Native VU is a later step.
6. **iOS/tvOS.** See the plan below.

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
