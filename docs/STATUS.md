# Status

## Current

| Area | State |
|---|---|
| Frame rate | Measured 50 game frames/s in default PAL and 60 in opt-in NTSC during confirmed play. `fps_game` is the median of fully post-confirmation 5 s windows (`./rx run --match --profile`). `--match` fails below 49.5. |
| Video mode | Settings PAL 50 Hz (original, default) / NTSC 60 Hz; uses the game's selector with the matching render height. The selector-only black pitch is fixed. Confirmed Marseille play (`./rx run --match --seconds 60`): visible pitch, PAL 50.0 / NTSC 60.0 fps, HUD-clock slopes inferred from OCR of PAL 0.997 / NTSC 0.997–0.999 seconds/wall second, and zero audio underruns. Set `PS2X_VIDEO_MODE=ntsc` for the NTSC probe. Other venues/modes remain unverified. |
| CPU | Measured with `./rx run --match --profile`: GameThread about 0.43 cores plus worker about 0.49 cores. |
| Audio | The warm-run underrun mechanism was the all-EE-threads-blocked wait omitting IOP service deadlines; re-verified with `./rx run --match --seconds 120 --button-spam` ×8: 50 fps in all, 7 with zero underruns, one with a single 49-frame burst during a 43 fps window (inferred: a first-use pipeline compile, see shader pre-warm). `./rx ab --control PS2X_EE_IOP_IDLE=0 --pairs 2 --match --button-spam --seconds 120`: legacy wait 0 and 171 underruns, fix 0 and 0. Cold Metal shader-cache stalls remain. |
| Graphics | PCSX2 upstream master; Metal renderer. The dashed-texel grid is fixed upstream; user confirmation is pending. |
| Skill-move crash | Fixed: the recompiler had split the return delay slot at `0x1E8480` into another function, leaving `sp` unrestored. 9 minutes of manual skill moves after the fix ran without a fault. Optional `PS2X_WATCH_RA_STOMP=1` checks RA/SP. |
| Controller | DualSense via SDL3 HIDAPI (type `ps5`); DualShock 2 pressure order L1, R1, L2, R2. Rumble is opt-in and off by default; libpad actuator alignment/direct bytes map the small motor bit to SDL high frequency and the large byte to low frequency, scaled by the 25–100% intensity setting. A traced Marseille match showed guest values forwarded to SDL. With rumble enabled and tracing off, `./rx run --match --seconds 60` measured 50.0 gameplay FPS and zero audio underruns. Physical vibration was not confirmed by touch. |
| Display | Aspect Auto/4:3/16:9; Auto does not follow the game WIDESCREEN option, so select 16:9 manually. Runtime menu-toggle snapshots did not establish a stable guest flag (`docs/internals.md`). Scale 1–6×; 5×/6× captures are correct. At 4×/5×/6×: 50.0 FPS, zero underruns; median GPU 2.75/4.00/5.54 ms. FXAA at 4×: 50.0 FPS, 4.44 ms; one run had 1,086 audio underruns (diagnostic: 3,356). On this 8 GB M1, 5× added 17,831 swapout pages (~279 MiB); 6× added none. FXAA is off. FPS: `./rx run --match --seconds 60`; GPU: clean-quit `./rx run --route match --seconds 60 --app 'build/dist/FIFA Street PS2.app' --quit-cycles 1` with `RRV_GATE4_GS_COUNTERS`; memory: sampled `memory_pressure`, `vm_stat`, and `ps -o rss`. |
| Replay | Post-goal REWIND sequences animate and return to live play (fixed in `af239cd`). |
| Unverified | Directional input response, memory-card saves, other stadiums and modes, HBlank-gated EE timers (`docs/internals.md` faithfulness notes) |
| Platforms | macOS arm64 |

`./rx run` writes the current measurements to its summary; its fields and meanings are documented in `AGENTS.md` and `docs/internals.md`.

## Next

Work these in order. **Every task updates this file in the same commit that completes it:** move it to Current or remove it, and record what was measured.

**Fixes and tooling**
1. Shader pre-warm at boot: ship FIFA's measured pipeline-key list (tagged by GPU feature set, checked against the PCSX2 pin) plus a per-device learned list in Application Support; precompile at GS device creation; kill-switch `RRV_PCSX2_GS_PIPELINE_PREWARM=0`. A prototype measured about 250 keys, 1.5 s cold / 12 ms warm, and 0 runtime compiles and 0 underruns on a cold run. Ubershaders were measured and rejected.
2. Lean code generation evaluation (SloppyWyn's fork `docs/LEAN-CODEGEN.md`): build time and binary size behind a generator kill-switch.

**Modern feel (all opt-in, off by default)**

4. De-interlacing: measure SMODE2/field parity, then offer a "Deinterlacing" menu (Automatic default, Off, Bob, Weave, Blend, Adaptive), chosen by matched motion captures.
5. Texture upscaling filter for UI and text: opt-in smart upscaling (xBRZ or ScaleFX, as PPSSPP and Dolphin offer) of small 2D textures (fonts, UI) when they are loaded into the PCSX2 texture cache. It processes only the game's own textures; no packs. Measure texture-cache cost and compare captures.

## Backlog

Architecture and CPU-efficiency reviews, release polish, iOS/tvOS.
