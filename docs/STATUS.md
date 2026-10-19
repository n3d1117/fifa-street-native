## Resume

- Intent: boot FIFA Street past the first fault. Verified and pushed: tail-call discovery, gp from `.reginfo`, JALR fix, `sceCdCallback` completion, LBN map, TODO-stub unbind and build check, probe child kill.
- Next: the boot stops at fault 0x2a62c0 (`sub_002A62A0`): EA's allocator memsets 0xCC55CC55 over `.data` at 0x480250. Suspect: EeScheduler invocation/callback stacks default to 0x1F00000.. (game stack 0x1FF0000, heap 0x57E900..). Move them below the ELF's lowest PT_LOAD via `ps2SetRuntimeArena` from the launcher, with an overlap test; re-run.
- Then: IOP.BIG modules as LLE IRX on the virtual IOP kernel, MPC movies.
- Key files: `deps/ps2recomp/ps2xRuntime/src/lib/Kernel/EeScheduler.cpp`, `ps2_runtime.cpp` (arena), `host/launcher.cpp`.
- Gotchas: lldb watchpoints on `rdram+addr` work (x0 = rdram, x1 = ctx, r[i] at ctx+16*i); `PS2X_TRACE_SYSCALLS/RPC/CD_IO`.

## Current

| Area | State |
|---|---|
| Engine | Builds on macOS arm64. Quick tests pass without a disc (see `./rx test`) |
| Game | Recompiles and links: 13533 functions discovered by the recompiler (no symbols; `sub_<ADDR>`), 13379 translated, 154 HLE-bound SDK stubs, 0 skipped. First run faults in the first seconds (see Next 1). No prior decompilation or recompilation of FIFA Street or any EA PS2 game is known |
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

1. **First boot fault.** `./rx run` stops at `guest-branch:missing-target`: `J 0x44b7d0` from `0x44b848` (ra `0x44b978`), a function start nobody calls with JAL, so discovery misses it (it sits right after the `InitAlarm` stub at `0x44b6f8`). Fix discovery or add an entry point in the template, then continue to the next fault. 5438 functions with a `JALR` promote every instruction as a resume entry (655k entries, 282 MB generated); excluding `JALR` calls from the indirect-jump fallback should cut build time (not measured).
2. **Boot to menus.** IOP boot needs `IOPRP280.IMG` plus the 7 modules in `IOP.BIG` (refpack `BIG4`; the IOP layer must read them, including EA `snddrv`). EA `MPC` movies and `SCHl`/`.MUS` audio need decoders. Re-derive the runtime arena and IOP heap layout for this ELF: the launcher's previous fixed ranges were removed, and the ELF loads at `0x100000` to about `0x5A0000`, so any fixed arena there would overlap it.
3. **Play a match.** Add a scripted fast-match gate for `./rx run`.
4. **Whole-game faithful pass** against PCSX2 behavior.
5. **Performance.** Profile first. Native VU is a later step.
6. **iOS/tvOS.** See the plan below.

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
