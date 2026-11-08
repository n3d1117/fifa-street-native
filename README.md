# FIFA Street native

Native Apple Silicon port of **FIFA Street** for PlayStation 2 (PAL, **SLES-53064**), built with PS2Recomp and the PCSX2 Metal renderer.

You must supply your own disc image. Game files and generated game code are not included.

## Requirements

- Apple Silicon Mac running macOS 11 or later.
- Xcode Command Line Tools: `xcode-select --install`.
- Python 3.8 or later, Git and an internet connection.
- A PAL SLES-53064 ISO with SHA-1 `23f98299466edc206bdf11407f4cb653bfc0e79c`.

## Build and run

```sh
git clone https://github.com/n3d1117/fifa-street-native.git
cd fifa-street-native
./rx setup
./rx disc "/path/to/FIFA Street.iso"
./rx build
./rx run
```

Setup downloads and builds the pinned toolchain and dependencies. To use an existing LLVM 19 installation with libc++, set `RX_LLVM` to its installation directory before setup.

To install a self-contained macOS app:

```sh
./rx package --install
open "/Applications/FIFA Street PS2.app"
```

Use a gamepad or the keyboard: arrows move; X/Space = Cross, C = Circle, Z = Square, V = Triangle, Q/E = L1/R1, Left/Right Shift = L2/R2, Return = Start, Tab = Select. Keyboard bindings and graphics options are available from the app menu. Restart after changing graphics settings.

Saves and settings are stored in `~/Library/Application Support/FIFAStreetPS2`.

## iOS and tvOS

These builds additionally require full Xcode and a game controller. Select your Xcode installation, then build either platform:

```sh
export DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer"
./rx ipa --platform ios
./rx ipa --platform tvos
```

IPAs are written to `build/embedded/iphoneos/` or `build/embedded/appletvos/`. They include your extracted game files and must be signed with your own Apple provisioning profile before installation. Do not redistribute them.

For an arm64 Simulator app, use `--platform ios-sim` or `--platform tvos-sim`. The `.app` is written under `build/embedded/iphonesimulator/` or `build/embedded/appletvsimulator/`; install it with `xcrun simctl install booted "/path/to/FIFA Street PS2.app"`.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE) and [THIRD_PARTY.md](THIRD_PARTY.md).
