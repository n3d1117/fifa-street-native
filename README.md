# Tarzan: Freeride, native for Apple Silicon

A static recompilation of *Disney's Tarzan: Freeride* (PS2, PAL, SLES-50350) into a native arm64 macOS app with Metal graphics.
You supply your own disc. This repository contains no game code, data or assets.

## Status

- Boots, plays movies with sound, and reaches first-mission gameplay at the original 25 FPS.
- Later missions, full progression and every save path are not yet tested.
- EE code is recompiled ahead of time. VU1 and known VU0 programs run natively with an interpreter fallback. The IOP is interpreted.
- Options, off by default: internal resolution up to 4x, 16:9, 60/120 FPS targets, logo skip, remember language, and the game's own cheats.

Details: [docs/STATUS.md](docs/STATUS.md).

## Requirements

- Apple Silicon Mac, macOS 11 or later, and Xcode Command Line Tools (`xcode-select --install`).
- Python 3.9 or later.
- LLVM 19 with libc++:
  - macOS 13 and later: `brew install llvm@19`, then `export TARZAN_LLVM=$(brew --prefix llvm@19)`.
  - Older macOS: `./tz setup` builds it into `tools/build` (about an hour, once).
- Your own PAL disc as an ISO:
  - ISO SHA-1 `6efdf922e38e757ce0494d27ead2bf7df491d51f`
  - `SLES_503.50` SHA-1 `fcb6d2ef522512ee4f08d0a415b2013248a37c44`

## Build and run

```sh
git clone https://github.com/n3d1117/tarzan-freeride-native.git
cd tarzan-freeride-native
./tz setup                      # upstream checkouts (deps.lock + patches/), Python venv, toolchain, FFmpeg/SDL3/PCSX2 deps, pre-commit hook (once)
./tz disc ~/path/to/Tarzan.iso  # verify and extract your disc, derive symbols and config
./tz build                      # generate the game code and build build/game-metal
./tz test                       # optional: quick test tier (no disc needed); --full adds slow tests and the VU regression
                                # corpus (skipped if you do not have it locally)
./tz run                        # play from the build tree; --route first-mission scripts a bounded run to gameplay
./tz run --save runtime/regression/saves/<name> --seconds 60  # boot with that fixture's memcards/ in an isolated user dir
./tz package --install          # optional: /Applications/Tarzan Freeride PS2.app
```

Saves and settings live in `~/Library/Application Support/TarzanFreeridePS2`.
Controls: keyboard (remap in Settings, Keyboard) or any SDL-supported gamepad.
`./tz --help` lists every command.

## Layout

| Path | What |
|---|---|
| `host/` | macOS app: window, input, audio, Metal GS adapter, settings, game hooks |
| `deps.lock`, `patches/` | pinned PS2Recomp, PCSX2 and fukami revisions and our changes to them; `./tz setup` checks them out into `deps/` |
| `tools/` | disc extraction, code generation, VU compiler, packaging, probes |
| `config/` | recompiler configuration for SLES-50350 |
| `tests/`, `cmake/` | host tests, CMake modules |
| `docs/` | `STATUS.md` (state and roadmap), `internals.md` (mechanisms) |

Local only, never committed: `deps/`, `disc/`, `extracted/`, `generated/`, `build/`, `runtime/`, `dist/`.

## License

GPL-3.0-or-later. Upstream components: [THIRD_PARTY.md](THIRD_PARTY.md).
