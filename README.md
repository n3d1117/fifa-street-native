# Tarzan: Freeride, native for Apple Silicon

A static recompilation of *Disney's Tarzan: Freeride* (PS2, PAL, SLES-50350) into a native arm64 macOS app with Metal graphics.
You supply your own disc. This repository contains no game code, data or assets.

## Status

- Boots, plays movies with sound, and plays the first mission and most map missions at the original 25 FPS. Full progression and every save path are untested.
- Options, off by default: internal resolution up to 4x, 16:9, a 60 FPS mode, logo skip, remember language, and the game's own cheats.

Details: [docs/STATUS.md](docs/STATUS.md). Contributors: [AGENTS.md](AGENTS.md).

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
./tz setup                      # once: upstream checkouts, venv, toolchain, dependencies, pre-commit hook
./tz disc ~/path/to/Tarzan.iso  # verify and extract your disc, derive symbols and config
./tz build                      # generate the game code and build build/game-metal
./tz test                       # optional: quick tests, no disc needed
./tz run                        # play from the build tree
./tz package --install          # optional: /Applications/Tarzan Freeride PS2.app
```

`./tz setup` also installs a pinned, checksum-verified ccache under `tools/build`; builds use it automatically.
`CCACHE_DISABLE=1 ./tz build` bypasses it.

Saves and settings live in `~/Library/Application Support/TarzanFreeridePS2`.
Controls: keyboard (remap in Settings, Keyboard) or any SDL-supported gamepad.
`./tz --help` lists every command.

## License

GPL-3.0-or-later. Upstream components: [THIRD_PARTY.md](THIRD_PARTY.md).
