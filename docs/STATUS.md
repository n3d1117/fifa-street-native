# Status

## Current

| Area | State |
|---|---|
| Frame rate | Measured 50 game frames/s in default PAL and 60 in opt-in NTSC during confirmed play. `fps_game` is the median of fully post-confirmation 5 s windows (`./rx run --match --profile`). `--match` fails below 49.5. |
| Video mode | Settings PAL 50 Hz (original, default) / NTSC 60 Hz; uses the game's selector with the matching render height. The selector-only black pitch is fixed. Confirmed Marseille play (`./rx run --match --seconds 60`): visible pitch, PAL 50.0 / NTSC 60.0 fps, HUD-clock slopes inferred from OCR of PAL 0.997 / NTSC 0.997–0.999 seconds/wall second, and zero audio underruns. Set `PS2X_VIDEO_MODE=ntsc` for the NTSC probe. Other venues/modes remain unverified. |
| CPU | Measured with `./rx run --match --profile`: GameThread about 0.43 cores plus worker about 0.49 cores. |
| Audio | The all-EE-threads-blocked wait honors IOP service deadlines (`PS2X_EE_IOP_IDLE=0` restores the old path). Warm button-spam A/B: legacy wait 0 and 171 underruns; current path 0 and 0. Cold Metal cache: two pre-warmed runs had 0 underruns and 0 runtime pipeline compiles; the cold kill-switch run had 6,304 underruns and 98 runtime compiles. All measured 50.0 FPS. |
| Graphics | PCSX2 upstream master; Metal renderer. Boot pre-warms 250 measured FIFA HW pipeline keys on the GS worker (Apple7 feature tag, PCSX2 pin checked by `./rx check`). Cold pre-warm was 1.51 s; warm was 10–11 ms. A three-pair unprofiled warm A/B measured 50.0 FPS median in both arms and median time-to-play of 25.780 s pre-warm vs 25.837 s control. Median CPU was +0.9%; estimated energy was +17% in this probe and varied in the two-pair profile probe. One candidate run had 217 underruns; the other two had zero. Disable with `RRV_PCSX2_GS_PIPELINE_PREWARM=0`. The dashed-texel grid is fixed upstream; user confirmation is pending. |
| Skill-move crash | Fixed: the recompiler had split the return delay slot at `0x1E8480` into another function, leaving `sp` unrestored. 9 minutes of manual skill moves after the fix ran without a fault. Optional `PS2X_WATCH_RA_STOMP=1` checks RA/SP. |
| Controller | DualSense via SDL3 HIDAPI (type `ps5`); DualShock 2 pressure order L1, R1, L2, R2. Rumble is opt-in and off by default; libpad actuator alignment/direct bytes map the small motor bit to SDL high frequency and the large byte to low frequency, scaled by the 25–100% intensity setting. A traced Marseille match showed guest values forwarded to SDL. With rumble enabled and tracing off, `./rx run --match --seconds 60` measured 50.0 gameplay FPS and zero audio underruns. Physical vibration was not confirmed by touch. |
| Display | Aspect Auto/4:3/16:9; Auto does not follow the game WIDESCREEN option, so select 16:9 manually. Runtime menu-toggle snapshots did not establish a stable guest flag (`docs/internals.md`). Scale 1–6×; 5×/6× captures are correct. At 4×/5×/6×: 50.0 FPS, zero underruns; median GPU 2.75/4.00/5.54 ms. FXAA at 4×: 50.0 FPS, 4.44 ms; one run had 1,086 audio underruns (diagnostic: 3,356). On this 8 GB M1, 5× added 17,831 swapout pages (~279 MiB); 6× added none. FXAA is off. FPS: `./rx run --match --seconds 60`; GPU: clean-quit `./rx run --route match --seconds 60 --app 'build/dist/FIFA Street PS2.app' --quit-cycles 1` with `RRV_GATE4_GS_COUNTERS`; memory: sampled `memory_pressure`, `vm_stat`, and `ps -o rss`. |
| UI texture upscaling | Settings checkbox “Smooth UI textures (xBRZ)”, off by default; only small paletted sprite/quad textures (up to 64×64) at 2×–6×. 4× language-screen capture comparison showed sharper menu text; flags remained intact. Match probe: 50.0 FPS and 0 audio underruns with filter off and on. Enabled run processed 205 textures in 124 ms cumulative elapsed processing time; peak reported cache allocation was 7.62 MiB. `./rx run --match --seconds 60`; cache statistics are in the run log. Other venues remain unverified. |
| Release polish | Package-time generated original icon, sectioned Settings window, launch alerts for missing or mismatched game data, and user setup/troubleshooting docs. `./rx package` audit clean; `./rx run --match --seconds 60` measured 50.0 gameplay FPS and 0 audio underruns. |
| Replay | Post-goal REWIND sequences animate and return to live play (fixed in `af239cd`). |
| Unverified | Directional input response, memory-card saves, other stadiums and modes, HBlank-gated EE timers (`docs/internals.md` faithfulness notes) |
| Platforms | macOS arm64 |

`./rx run` writes the current measurements to its summary; its fields and meanings are documented in `AGENTS.md` and `docs/internals.md`.

## Backlog

Architecture and CPU-efficiency reviews, iOS/tvOS.
