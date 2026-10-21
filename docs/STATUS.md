# Status

## Current

| Area | State |
|---|---|
| Frame rate | Measured 50 game frames/s during confirmed play. `fps_game` is the median of fully post-confirmation 5 s windows (`./rx run --match --profile`). `--match` fails below 49.5. |
| Video mode | PAL remains the default. Startup hardcodes the PAL selector before GS reset; no console-query-controlled NTSC boot path found. Generic NTSC code exists, but correct-speed NTSC gameplay is unverified; see `docs/internals.md`. |
| CPU | Measured with `./rx run --match --profile`: GameThread about 0.43 cores plus worker about 0.49 cores. |
| Audio | The warm-run underrun mechanism was the all-EE-threads-blocked wait omitting IOP service deadlines; rapid-button spam runs now pass with zero underruns, while the legacy wait produced underruns in A/B. Cold Metal shader-cache stalls remain. |
| Graphics | PCSX2 upstream master; Metal renderer. The dashed-texel grid is fixed upstream; user confirmation is pending. |
| Skill-move crash | Fixed: the recompiler had split the return delay slot at `0x1E8480` into another function, leaving `sp` unrestored. 9 minutes of manual skill moves after the fix ran without a fault. Optional `PS2X_WATCH_RA_STOMP=1` checks RA/SP. |
| Controller | DualSense via SDL3 HIDAPI (type `ps5`); DualShock 2 pressure order L1, R1, L2, R2. Rumble not forwarded yet. |
| Display | Display aspect setting Auto / 4:3 / 16:9; the game's own Options 16:9 is anamorphic and needs Display aspect 16:9 until Auto follows the game flag. Render scale 1-4x. |
| Replay | Post-goal REWIND sequences animate and return to live play (fixed in `af239cd`). |
| Unverified | Directional input response, memory-card saves, other stadiums and modes, HBlank-gated EE timers (`docs/internals.md` faithfulness notes) |
| Platforms | macOS arm64 |

`./rx run` writes the current measurements to its summary; its fields and meanings are documented in `AGENTS.md` and `docs/internals.md`.

## Next

Work these in order. **Every task updates this file in the same commit that completes it:** move it to Current or remove it, and record what was measured.

**Fixes and tooling**
1. NTSC 60 Hz mode (in progress): the PAL build hardcodes selector 1 at constructor `0x29426C`; the setter `0x3C4FD8` has an NTSC branch (selector 0 writes OMode 2). Make the flip take effect, then ship Settings "Video mode: PAL 50 Hz / NTSC 60 Hz" only if the match clock stays about 1.0 s per wall second at about 60 fps.
2. Shader pre-warm at boot: ship FIFA's measured pipeline-key list (tagged by GPU feature set, checked against the PCSX2 pin) plus a per-device learned list in Application Support; precompile at GS device creation; kill-switch `RRV_PCSX2_GS_PIPELINE_PREWARM=0`. A prototype measured about 250 keys, 1.5 s cold / 12 ms warm, and 0 runtime compiles and 0 underruns on a cold run. Ubershaders were measured and rejected.
3. Audio clipping on goals and replays: peaks reach 1.1-1.6x full scale with up to 446 clipped samples/s. Match PCSX2's SPU2 output stage (gain, core mix, final clamp) in `bridge/spu2` and `host/spu2-host.cpp`; no limiter.
4. Boot hook for any venue: `PS2X_BOOT_MATCH=venue=5` (Roma) faults on a NULL `0x48EBC8` callback because the hook skips the front-end venue path. `setvenuebyid` (`0x2E64C0`) maps ids differently (id 5 loaded Amsterdam). Follow the menu path for every venue, and have `--match` honour `PS2X_BOOT_MATCH`.
5. Re-verify the warm-run audio fix: 8 consecutive `./rx run --match --seconds 120 --button-spam` runs on a clean build with 0 underruns, and an A/B against `PS2X_EE_IOP_IDLE=0`.
6. Faster test loop: offline VU1 benchmark (Tarzan `61b10ca`) and save states/determinism hashes (evaluate SloppyWyn's PS2Recomp fork); trim `--match`.
7. Lean code generation evaluation (SloppyWyn's fork `docs/LEAN-CODEGEN.md`): build time and binary size behind a generator kill-switch.

**Modern feel (all opt-in, off by default)**

8. Anisotropic filtering: Settings Off/2x/4x/8x/16x mapped to PCSX2 `MaxAnisotropy` (already env-wired in `bridge/gs/bridge.cpp`). Verify with matched pitch captures and GPU ms.
9. True 4K and anti-aliasing: allow 5x/6x render scale (6x = 3840 px wide; clamp to PCSX2's device max), add an FXAA checkbox beside the existing CAS sharpening. Measure per-vsync GPU ms (`RRV_PCSX2_GS_DIAG`) and memory pressure on the 8 GB M1 at 4x/5x/6x.
10. Frame pacing and input latency: an opt-in "Synchronize presentation" setting that routes presents through PCSX2's existing Metal present-at-time pacer (FIFO). Measure frame-interval variance and input-to-photon latency on 60/120/144 Hz displays. Guest timing is untouched.
11. Rumble: decode `scePadSetActAlign`/`scePadSetActDirect` in the pad HLE (currently dropped), forward the motor values to `SDL_RumbleGamepad` (DualSense), with a rumble on/off setting and intensity.
12. De-interlacing: measure SMODE2/field parity, then offer a "Deinterlacing" menu (Automatic default, Off, Bob, Weave, Blend, Adaptive), chosen by matched motion captures.
13. Auto widescreen: locate the game's widescreen flag (`gWidescreenMode` / the Options 16:9 handler) and read it each frame to drive Display aspect Auto; explicit 4:3 and 16:9 override it.

## Backlog

HD texture-pack support (deferred), all-stadium and mode coverage, architecture and CPU-efficiency reviews, release polish, iOS/tvOS.
