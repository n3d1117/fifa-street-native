# Status

## Current

| Area | State |
|---|---|
| Frame rate | Measured 60.0 game frames/s and 60.0 presents/s in the default NTSC mode and 50.0/50.0 in opt-in PAL during confirmed play, zero audio underruns in two quiet NTSC runs (runs during a concurrent build underran; measure on an idle machine). `fps_game` is the median of fully post-confirmation 5 s windows (`./rx run --match --seconds 60`); `--match` checks 58.5 NTSC / 49.5 PAL. |
| Video mode | NTSC 60 Hz (default) / PAL 50 Hz (original, opt-in); the host selects the game's own mode and supplies its 59.94/50 Hz rate to the Metal present pacer. NTSC uses selector 0 with the matching 448-line render height; the selector-only black pitch is fixed. Explicit `PS2X_VIDEO_MODE=pal` remains supported. iOS and tvOS Simulator builds reach the language screen; gameplay cadence is not measured by those boot runs. Roma intro coverage is listed below. |
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
| Platforms | macOS arm64; iOS and tvOS arm64 app builds; iOS and tvOS Simulator launches reach the language screen and survive a background/resume cycle. |
| Game Mode | iOS/tvOS bundles set `LSApplicationCategoryType=public.app-category.sports-games` and `LSSupportsGameMode`; macOS has a games category and the same support key. Needs device check: confirm the Game Mode banner or Control Center indicator on an iPhone running iOS 18+. |

`./rx run` writes the current measurements to its summary; its fields and meanings are documented in `AGENTS.md` and `docs/internals.md`.

## Backlog

Architecture and CPU-efficiency reviews.

### iOS/tvOS next

Measured: iOS and tvOS Simulator builds render the language screen. Unverified: real devices, controller input, gameplay, audio, saves, frame rate and thermals on device.

1. First device boot (A12 or newer): title screen, then the first match. Check memory headroom (jetsam) against the 1.5 GB bundle.
2. Verify NTSC game and present cadence during a real device match; confirm the PAL opt-in still paces at 50 Hz.
3. Precompile PCSX2 Metal shaders to a `.metallib` and prewarm `pipelines-fifa.txt` (optionally `MTLBinaryArchive`) so play has no compile stalls.
4. Apple GPU paths from ARMSX2 (each needs a switch and A/B): skip PS2 Z floor on Apple GPUs, framebuffer-fetch ordering instead of barriers, iOS barrier rules, late-upload encoder fix.
5. Small settings overlay (video mode, scale, rumble) usable with a controller; TV-safe layout on tvOS.
6. Watch `ProcessInfo.thermalState`; degrade optional extras only, never guest timing.
7. Real app icon for macOS, iOS and tvOS (today macOS uses the package-time generated placeholder from `tools/package/make-icon.m`; iOS/tvOS bundles have none; tvOS needs layered top-shelf/app images).

### Embedded build efficiency

- Deferred: trimming libPCSX2 for the bridge. vixl, ccc, DEV9, DebugTools and USB are 43% of a clean bridge's summed `.ninja_log` action time, but they are not dead: the arm64 `GSDrawScanlineCodeGenerator` includes VIXL, core sources (`Elfheader.cpp`, `Interpreter.cpp`) include ccc/SymbolGuardian headers, and GS startup calls ImGuiManager/FullscreenUI. With per-platform ccache a warm bridge rebuild takes about 9 s, so only cold builds would gain. DEV9 and USB alone remain a possible cut.
