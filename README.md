# FIFA Street native

A static recompilation of *FIFA Street* (PS2, PAL, SLES-53064) into a native arm64 macOS app with Metal graphics.
You supply your own disc. This repository contains no game code, data or assets.

See [docs/STATUS.md](docs/STATUS.md) for verified game state and next steps.

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
./rx xcode                    # generate the Xcode project (CMake remains the compiler build)
```

## Play

Open **FIFA Street PS2.app** from Applications. A supported PAL disc must be verified with `./rx disc` before packaging; the app bundle contains the extracted game data. If building from this checkout, `./rx disc <path-to-iso>`, `./rx build`, then `./rx package --install`. New settings files default to NTSC; an existing saved `videoMode=pal` setting remains PAL.

Use a gamepad or the default keyboard bindings: arrows move; Cross X/Space, Circle C/Escape, Square Z/keypad 0, Triangle V/keypad 1, L1 Q, R1 W, L2 E, R2 R, Start Return, Select Tab. Open **Controls** from the app menu to view or rebind keys.

Open **Settings** from the app menu, then Save and restart the game to apply changes:

- Video mode: the game's NTSC 60 Hz mode (default) or PAL 50 Hz (original). Aspect: Auto (default), 4:3 or 16:9.
- Enhancements: internal scale 1× (default) to 6×; anisotropic filtering off by default; sharper image, FXAA and Smooth UI textures (xBRZ) off by default. These affect presentation; larger scales use more memory.
- Controller: rumble off by default; intensity defaults to 100% and matters when rumble is enabled.
- Cheats: unlock all venues off by default; enabling it sets the game's own venue cheat flag.

## iPhone, iPad and Apple TV (experimental)

Needs Xcode 12.5.1 (iOS/tvOS 14.5 SDKs) at `~/Developer/Xcode-12.5.1/Xcode.app`, plus `./rx setup` and `./rx disc` done.

```sh
export DEVELOPER_DIR=~/Developer/Xcode-12.5.1/Xcode.app/Contents/Developer
./rx ipa --platform ios        # or tvos, ios-sim, tvos-sim
```

Output: `build/embedded/<iphoneos|appletvos|iphonesimulator|appletvsimulator>/FIFA-Street-PS2-<ios|tvos>.ipa` (about 1.5 GB, game data included). Device builds are unsigned: install with a sideloading tool that signs (AltStore, Sideloadly) or sign with your own certificate. `-sim` platforms also boot the app in the Simulator and save a screenshot to `build/embedded/logs/`. The first build of a platform takes about an hour. Requires a game controller; there are no touch controls.

## Troubleshooting

- **Game data missing or unsupported:** verify your own PAL disc with `./rx disc <path-to-iso>`, rebuild, and package again. The installed app looks for `SLES_530.64` in its bundled `Contents/Resources/game` folder.
- **App does not open:** build with `./rx build`, then create or install the bundle with `./rx package` or `./rx package --install`. Check `~/Library/Application Support/FIFAStreetPS2/Logs/native.log` for a detached launch error.
- **Settings do not change the current session:** save in Settings and restart the game; settings are read at launch.

Saves and settings: `~/Library/Application Support/FIFAStreetPS2`.

License: GPL-3.0-or-later. See `LICENSE` and `THIRD_PARTY.md`.
