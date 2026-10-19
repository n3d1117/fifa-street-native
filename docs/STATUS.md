# Status

Experimental static recompilation. Not a decompilation, and not a verified port of any part of the game yet.

## Current

| Area | State |
|---|---|
| Engine | Builds on macOS arm64. Quick tests pass without a disc (see `./rx test`) |
| Game | Not recompiled yet. No symbols exist: the ELF is stripped and no prior decompilation or recompilation of FIFA Street or any EA PS2 game is known |
| Entry | `0x100008`; `.text` at `0x100000`, about 3.6 MB |
| Platforms | macOS arm64 only |

## Next

1. **Disc import.** `./rx disc <iso>`, then inventory the disc: IOP modules, `IOPRP280.IMG`, FMV and audio formats.
2. **First recompile.** Function discovery with ps2recomp's analyzer (`ps2_analyzer`) from entry `0x100008`; a first `config/SLES-53064.template.toml`.
3. **Boot to menus.** EA FMV, audio and IOP formats are unknown. Re-derive the runtime arena and IOP heap layout for this ELF: the launcher's previous fixed ranges were removed, and the ELF loads at `0x100000` to about `0x5A0000`, so any fixed arena there would overlap it.
4. **Play a match.** Add a scripted fast-match gate for `./rx run`.
5. **Whole-game faithful pass** against PCSX2 behavior.
6. **Performance.** Profile first. Native VU is a later step.
7. **iOS/tvOS.** See the plan below.

Known risk: PCSX2 needs GIF FIFO and EE cycle-rate accuracy for FIFA Street 2; watch both.

## Open questions

- Gameplay frame rate on this title (not known; do not assume 25 or 50).
- Movie and audio paths, including any PSS or MPEG streams.

## iOS / tvOS plan

Feasible because the design is ahead-of-time: no JIT, and the VU and IOP fallbacks are interpreters. Landscape only, sideloaded. Device validation, not a recompile.

1. Host-agnostic core: the host owns the main thread; the core runs on its own thread behind a small C API and a `HostSurface` interface.
2. Cross-build the static libraries (GS bridge, SoundTouch, sse2neon, movie-only FFmpeg) with LLVM 19 against the iOS or tvOS 14 SDK. Xcode supplies `metal` and `actool`.
3. PCSX2 Metal GS on iOS: caller-owned `CAMetalLayer`, no IOKit, check the GPU's texture support. Decide the shader fast-math flag deliberately; it conflicts with the arithmetic rule.
4. UIKit host, audio-session handling, GameController input, disc import with hash checks, saves in Application Support. Never bundle game data.
5. Device validation: start at 1x; check memory (jetsam), thermals and movies. No speed-ups to inflate FPS.
