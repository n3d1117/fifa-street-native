# Status

Experimental static recompilation, not a decompilation and not a verified complete port. Coverage figures and first-mission evidence are never whole-game claims. Numbers below are measured unless marked inferred; regenerate them with the named command.

| Area | Works | Open |
|---|---|---|
| EE recompile | Every mapped function translated or HLE-bound, none skipped (`./tz build`) | Configured HLE names with unimplemented handlers (`./tz where --unimplemented`); indirect reachability unverified |
| Gameplay | Boot, story movie, first mission. `./tz run --route missions` (unlock-all, about 45 s scripted play each) ran 11 of 12 map missions at 25.0 FPS with no crash, hang or unimplemented stub | Play beyond 45 s, mission completion, Terk's challenges, progression, ending. `Into the Caverns` is gated by a film-reel requirement card the unlock override does not cover. The game requests intro movies absent from the disc for 9 mission entries and skips them |
| Audio / movies | Startup and first story movie in sync, no clipping; movie PES accepted packet by packet | Later movies; startup pre-roll vs. hardware; intermittent underrun bursts at mission transitions; a 64-multiple underrun count on some title boots (reproduce 5 times on a quiet machine before touching the classifier) |
| VU1 | Native, guarded, disc-derived. First-mission and all 11 reachable missions: 0 fallbacks. `./tz test --full`: 0 mismatches, 0 rejected, 0 skipped | Longer play; dead-flag elision needs an open-program liveness proof |
| VU0 | Captured skinning programs native, others interpreted | Capture later-level programs |
| IOP | Interpreted; LWL/LWR pairs forward pending load values | |
| Saves | Card save/reload for one slot | All checkpoints, multiple slots |
| Input | Keyboard remapping, SDL gamepads | Real PS5/Xbox pad testing |
| Video | Native to 4x internal resolution, 16:9, optional sharpening, UI half-pixel coverage fix | Blurry tutorial text at higher scale |
| Performance | Default 25 FPS PAL. The 60 target reaches 60.0 `fps_gameplay` in first-mission gameplay with the data-side worker (36.2 inline; the target caps the result, so headroom is unmeasured). Gameplay is CPU-bound | later missions at 60 not measured; guest render/culling and VU1 flag helpers dominate the profile |
| Process | One app process, one build tree `build/game-metal`, settings in one plist | Settings window and fullscreen are not scriptable |
| Platforms | macOS arm64 | iOS/tvOS: plan only (below) |

Measuring: `./tz run --route first-mission --fps-target 60 --measure` (60 s steady window, `scene` must be `playable`). Profile by sampling the game thread with `sample <child-pid> 20` after confirming gameplay; the built-in `--profile` defaults to boot.

## Next

macOS first, in this order.

1. **Offline GS replay benchmark** from local captures, with no game launch; first prove the bridge's headless lifecycle on macOS 11.
2. **Profile-first CPU work.** VU1 FMAC flag cost, guest code speed (memory fast path, register stores, direct calls), native IOP. Only what the profile prices above about 3% of the game thread. Every change gets a kill-switch and, where results must match, a verify mode.
3. **Smaller app and faster builds, by build profile.** Minimal FFmpeg (MPEG-2 decoder and parser only; PSS frames must stay bit-identical), split the largest translation units and the PCH, trim the GS bridge.
4. **Later, nice-to-haves.** Apple GameController backend in place of SDL. Cheat-section features: FPS overlay, invincibility, character and trick unlocks, button-swap remap. Visual toggles: frame pacing, sharper tutorial text. 120 FPS: test on a 120 Hz display (60 Hz display measured 55.7, presentation-paced).
5. **iOS/tvOS, last.** See the plan below.

## Open questions / known issues

- Whether the 60 target holds in later missions (unmeasured).
- Mission completion, boss outcomes and every save path are untested.
- Intermittent audio underrun bursts at mission transitions are undiagnosed.

## iOS / tvOS plan

Feasible because the design is ahead-of-time: no JIT anywhere, and the VU and IOP fallbacks are interpreters. Landscape only, sideloaded. It is a device-validation project, not a recompile. Blockers: memory (jetsam limits with upscaled GS textures) and the port work below.

**Toolchain route.** Xcode 12.5.1 runs on macOS 11 and carries the iPhoneOS/AppleTVOS 14.5 SDKs and the Metal 2.3 compiler. Expand the `.xip` with `xip -x` anywhere, accept its license once with `DEVELOPER_DIR` pointing into it, and point `DEVELOPER_DIR` at it. Native code is built by our LLVM 19 (`-target arm64-apple-ios14.0` or `arm64-apple-tvos14.0`, `-isysroot` the SDK, `ld64.lld`, which the bootstrap does not build yet); Xcode supplies `metal`/`metallib` and `actool`.

**Packaging.** `tz package --platform ios|tvos --iso <disc> [--developer-dir PATH]` (planned): preflight with exit 2 naming the missing piece, cross-build, compile shaders, import the user's disc into the bundle with the `tz disc` checks, audit, zip an unsigned `.ipa`. The result contains game data: never committed or shared. Signing is the user's.

**Steps.**
1. Host-agnostic core. One process and a settings struct already exist. The real change is thread ownership: `PS2Runtime::run()` makes its caller the host poll loop, GS teardown relies on that loop pumping the main thread, and the runtime calls the Metal host and gamepad entry points directly. Invert it: the host owns the main thread and the core runs on its own thread behind a small C API and a `HostSurface` interface. One commit per step, macOS app working after each: paths/HOME, settings as input, host-pushed input, GS glue out of the runtime, `HostSurface`, core thread (gated by `./tz run --app <bundle> --quit-cycles N`).
2. Cross-build static libs (GS bridge, SoundTouch, sse2neon, movies-only FFmpeg); measure generated-code compile time and size early.
3. PCSX2 Metal GS on iOS: retain the caller's `CAMetalLayer`, `MTLCreateSystemDefaultDevice`, no IOKit/CocoaTools, `texture_barrier = false` (the one fix needing gameplay validation), BC texture support query, `.metal` sources compiled with `-std=ios-metal2.3`. Upstream passes `-ffast-math` to the shader compiler; decide deliberately, it conflicts with the arithmetic rule on the GPU side.
4. UIKit host: `CAMetalLayer` view, lifecycle, audio-session interruptions, GameController input.
5. Disc import: document picker, verify hashes, staged copy; saves in Application Support; never bundle game data.
6. Touch HUD: `UIView` overlay with GameController as the primary path; edit mode and per-device layout JSON in Application Support.
7. Device validation: start at 1x, check memory vs. jetsam, thermals, pacing, movies; no speed-up to inflate FPS.
8. tvOS: controller required, import over a LAN page, focus-engine menus, purgeable `Caches`.
