# FIFA Street native

A static recompilation of *FIFA Street* (PS2, PAL, SLES-53064) into a native arm64 macOS app with Metal graphics.
You supply your own disc. This repository contains no game code, data or assets.

**Status:** engine only. The game is not recompiled yet. See [docs/STATUS.md](docs/STATUS.md).

## Requirements

- Apple Silicon Mac, macOS 11 or later, and Xcode Command Line Tools (`xcode-select --install`).
- Python 3.9 or later.
- LLVM 19 with libc++:
  - macOS 13 and later: `brew install llvm@19`, then `export RX_LLVM=$(brew --prefix llvm@19)`.
  - Otherwise `./rx setup` builds it.
- Your own PAL disc as an ISO:
  - ISO SHA-1 `23f98299466edc206bdf11407f4cb653bfc0e79c`
  - `SLES_530.64` SHA-1 `fd167777fb6b28a118192b772436c8560461843e`

## Build and run

```sh
git clone https://github.com/n3d1117/fifa-street-native.git
cd fifa-street-native
./rx setup                    # once: deps, venv, toolchain, build-deps, pre-commit hook
./rx disc ~/path/to/FIFA.iso  # verify and extract your disc
./rx build                    # build the engine (and the game once it is configured)
./rx test                     # quick tests, no disc needed
./rx package --install        # optional: /Applications/FIFA Street PS2.app
```

Saves and settings: `~/Library/Application Support/FIFAStreetPS2`.

License: GPL-3.0-or-later. See `LICENSE` and `THIRD_PARTY.md`.
