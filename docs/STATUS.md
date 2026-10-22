# Status

## Current

| Area | State |
|---|---|
| Frame rate | Measured 50 game frames/s in default PAL and 60 in opt-in NTSC during confirmed play. `fps_game` is the median of fully post-confirmation 5 s windows (`./rx run --match --profile`). `--match` fails below 49.5. |
| Video mode | Settings PAL 50 Hz (original, default) / NTSC 60 Hz; uses the game's selector with the matching render height. The selector-only black pitch is fixed. Confirmed Marseille play (`./rx run --match --seconds 60`): visible pitch, PAL 50.0 / NTSC 60.0 fps, HUD-clock slopes inferred from OCR of PAL 0.997 / NTSC 0.997–0.999 seconds/wall second, and zero audio underruns. Set `PS2X_VIDEO_MODE=ntsc` for the NTSC probe. Roma intro coverage is listed below. |
| CPU | Measured with `./rx run --match --profile`: GameThread about 0.43 cores plus worker about 0.49 cores. |
| Audio | The all-EE-threads-blocked wait honors IOP service deadlines (`PS2X_EE_IOP_IDLE=0` restores the old path). Warm button-spam A/B: legacy wait 0 and 171 underruns; current path 0 and 0. Cold Metal cache: two pre-warmed runs had 0 underruns and 0 runtime pipeline compiles; the cold kill-switch run had 6,304 underruns and 98 runtime compiles. All measured 50.0 FPS. |
| Graphics | PCSX2 upstream master; Metal renderer. Boot pre-warms 250 measured FIFA HW pipeline keys on the GS worker (Apple7 feature tag, PCSX2 pin checked by `./rx check`). Cold pre-warm was 1.51 s; warm was 10–11 ms. A three-pair unprofiled warm A/B measured 50.0 FPS median in both arms and median time-to-play of 25.780 s pre-warm vs 25.837 s control. Median CPU was +0.9%; estimated energy was +17% in this probe and varied in the two-pair profile probe. One candidate run had 217 underruns; the other two had zero. Disable with `RRV_PCSX2_GS_PIPELINE_PREWARM=0`. The dashed-texel grid is fixed upstream; user confirmation is pending. |
| Roma intro crash | Fixed: ordinary VIF1 source chains reach END beyond the old 4,096-tag cutoff instead of calling the NULL timeout callback. Confirmed Roma intro/play at PAL 50.0 and opt-in NTSC 60.0 FPS, zero underruns. `PS2X_BOOT_MATCH=venue=5 ./rx run --match --seconds 120` (PAL); add `PS2X_VIDEO_MODE=ntsc` for NTSC. |
| Skill-move crash | Fixed: the recompiler had split the return delay slot at `0x1E8480` into another function, leaving `sp` unrestored. 9 minutes of manual skill moves after the fix ran without a fault. Optional `PS2X_WATCH_RA_STOMP=1` checks RA/SP. |
| Controller | DualSense via SDL3 HIDAPI (type `ps5`); DualShock 2 pressure order L1, R1, L2, R2. Rumble is opt-in and off by default; libpad actuator alignment/direct bytes map the small motor bit to SDL high frequency and the large byte to low frequency, scaled by the 25–100% intensity setting. A traced Marseille match showed guest values forwarded to SDL. With rumble enabled and tracing off, `./rx run --match --seconds 60` measured 50.0 gameplay FPS and zero audio underruns. Physical vibration was not confirmed by touch. |
| Display | Aspect Auto/4:3/16:9; Auto does not follow the game WIDESCREEN option, so select 16:9 manually. Runtime menu-toggle snapshots did not establish a stable guest flag (`docs/internals.md`). Scale 1–6×; 5×/6× captures are correct. At 4×/5×/6×: 50.0 FPS, zero underruns; median GPU 2.75/4.00/5.54 ms. FXAA at 4×: 50.0 FPS, 4.44 ms; one run had 1,086 audio underruns (diagnostic: 3,356). On this 8 GB M1, 5× added 17,831 swapout pages (~279 MiB); 6× added none. FXAA is off. FPS: `./rx run --match --seconds 60`; GPU: clean-quit `./rx run --route match --seconds 60 --app 'build/dist/FIFA Street PS2.app' --quit-cycles 1` with `RRV_GATE4_GS_COUNTERS`; memory: sampled `memory_pressure`, `vm_stat`, and `ps -o rss`. |
| UI texture upscaling | Settings checkbox “Smooth UI textures (xBRZ)”, off by default; at 2×–6× scales small paletted sprites plus the measured PSMT4 256×32 menu glyph atlas. At 4×, same-field language-screen crops show sharper glyphs; the heading and flag pixels are unchanged. Match A/B (two pairs): 50.0 FPS and 0 underruns in both arms; enabled arm measured +4.7% CPU and +25% energy. One uninstrumented enabled match run measured 50.0 FPS and zero black frames. CPU and energy cost are the tradeoff for enabling the option. Other menus and venues remain unverified. |
| Release polish | Package-time generated original icon, sectioned Settings window, launch alerts for missing or mismatched game data, and user setup/troubleshooting docs. `./rx package` audit clean; `./rx run --match --seconds 60` measured 50.0 gameplay FPS and 0 audio underruns. |
| Replay | Post-goal REWIND sequences animate and return to live play (fixed in `af239cd`). |
| Unverified | Directional input response, memory-card saves, full Roma matches and additional teams, other venues/modes, NTSC startup/pacing under background load, HBlank-gated EE timers (`docs/internals.md` faithfulness notes) |
| Platforms | macOS arm64; iOS and tvOS arm64 app builds; iOS and tvOS Simulator launches reach the game’s language selection screen. |

`./rx run` writes the current measurements to its summary; its fields and meanings are documented in `AGENTS.md` and `docs/internals.md`.

## Backlog

Architecture and CPU-efficiency reviews.

### iOS/tvOS next

Measured: iOS and tvOS Simulators boot to the language screen at 50 presents/s (`./rx ipa --platform ios-sim|tvos-sim`). Unverified: real devices, controller input, gameplay, audio, saves, frame rate and thermals on device.

1. First device boot (A12 or newer): title screen, then the first match. Check memory headroom (jetsam) against the 1.5 GB bundle.
2. Lifecycle: pause emulation and audio in background; flush memory cards on resign-active; `AVAudioSession` interruptions.
3. Frame pacing from `CADisplayLink` (`preferredFramesPerSecond` on SDK 14.5); tvOS 50 Hz output via `AVDisplayManager.preferredDisplayCriteria`; game NTSC/60 mode as the opt-in for 60 Hz panels.
4. Precompile PCSX2 Metal shaders to a `.metallib` and prewarm `pipelines-fifa.txt` (optionally `MTLBinaryArchive`) so play has no compile stalls.
5. Apple GPU paths from ARMSX2 (each needs a switch and A/B): skip PS2 Z floor on Apple GPUs, framebuffer-fetch ordering instead of barriers, iOS barrier rules, late-upload encoder fix.
6. Small settings overlay (video mode, scale, rumble) usable with a controller; TV-safe layout on tvOS.
7. Watch `ProcessInfo.thermalState`; degrade optional extras only, never guest timing.
8. Real app icon for macOS, iOS and tvOS (today macOS uses the package-time generated placeholder from `tools/package/make-icon.m`; iOS/tvOS bundles have none; tvOS needs layered top-shelf/app images).
9. Build scripts reconfigure stale CMake caches automatically (hash stamp per build dir); today a cached `CMAKE_SYSTEM_NAME` needs the build dir removed.

### Embedded build efficiency

- ccache for the embedded libc++, deps, ffmpeg and GS bridge builds; only the game uses it today (`cmake/ccache.cmake`). Bridge alone is about 330 s CPU per platform uncached (`.ninja_log` under `build/embedded/<platform>/metal-bridge`).
- Build ffmpeg with `--disable-everything` plus only the MPEG-2 video decoder, `mpegvideo` parser and swscale: the MPEG HLE (`ps2xRuntime/.../Stubs/MPEG.cpp`) uses nothing else and `FEMOVIES/*.MPC` are raw MPEG-2 elementary streams. Verify movie playback after.
- Build only what the GS bridge reaches from libPCSX2: vixl, DEV9, USB, ccc and DebugTools are compiled today but unused (about 40% of bridge CPU, inferred). If FullscreenUI/fonts and GameDatabase go too, freetype, harfbuzz, plutovg, plutosvg and rapidyaml can be dropped.
- XcodeGen: commit only a `project.yml` with macOS, iOS and tvOS app targets that wrap the CMake-built runner, bridge and packaging; `./rx xcode` generates the gitignored `.xcodeproj`. Compilation stays in CMake (LLVM 19 is required; Xcode 12.5.1 has no LLVM 19 toolchain). Check an XcodeGen release runs on macOS 11 and emits projects Xcode 12.5.1 opens.
