# Apple platform roadmap

The current app targets ARM64 macOS + Metal. iOS/tvOS are not built or tested.
The selected toolchain is Command Line Tools, not full Xcode; a supported Mac
with Xcode and Apple device SDKs is needed for actual device qualification.

1. Separate the native runtime from AppKit behind `tarzan_run` (host/tarzan-app.hpp);
   on macOS the app binary already is the game (AppKit on the main thread, guest on its own).
2. Add UIKit lifecycle and UIView/CAMetalLayer presentation, controller-first
   input, audio-session interruption handling and suspend/resume behavior.
3. Cross-build the existing Metal bridge, SDL3, FFmpeg and other dependencies
   for devices and simulators. Keep strict guest arithmetic and movie support.
4. Add BYOD import, content validation and container-based saves; design a
   remote-friendly ingest flow for Apple TV. Do not ship game assets in Git.
5. Qualify memory, thermal behavior, pacing, movies and progression on devices,
   starting at native resolution. Higher internal scale raises GPU memory cost.

An Xcode project should expose these real targets once the platform boundary
and dependency builds exist. A project scaffold alone would not be a port.

References: [Apple Metal layer](https://developer.apple.com/documentation/quartzcore/cametallayer),
[SDL iOS/tvOS](https://github.com/libsdl-org/SDL/blob/main/docs/README-ios.md),
[Apple directory import](https://developer.apple.com/documentation/uikit/providing-access-to-directories).
