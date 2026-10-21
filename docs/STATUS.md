# Status

## Current

| Area | State |
|---|---|
| Frame rate | Measured 50 game frames/s during confirmed play. `fps_game` is the median of fully post-confirmation 5 s windows (`./rx run --match --profile`). `--match` fails below 49.5. |
| Video mode | PAL remains the default. Startup hardcodes the PAL selector before GS reset; no console-query-controlled NTSC boot path found. Generic NTSC code exists, but correct-speed NTSC gameplay is unverified; see `docs/internals.md`. |
| CPU | Measured with `./rx run --match --profile`: GameThread about 0.43 cores plus worker about 0.49 cores. |
| Audio | The warm-run underrun mechanism was the all-EE-threads-blocked wait omitting IOP service deadlines; rapid-button spam runs now pass with zero underruns, while the legacy wait produced underruns in A/B. Cold Metal shader-cache stalls remain. |
| Graphics | PCSX2 upstream master; Metal renderer. The dashed-texel grid is fixed upstream; user confirmation is pending. |
| Skill-move crash | Corrected the omitted return delay slot at `0x1E8480` that explains the repeated epilogue and bad RA; manual Amsterdam skill-move verification is pending. Optional `PS2X_WATCH_RA_STOMP=1` checks RA/SP and reports the first typed store changing the saved RA. |
| Replay | Post-goal REWIND sequences animate and return to live play (fixed in `af239cd`). |
| Unverified | Directional input response, memory-card saves, other stadiums and modes, HBlank-gated EE timers (`docs/internals.md` faithfulness notes) |
| Platforms | macOS arm64 |

`./rx run` writes the current measurements to its summary; its fields and meanings are documented in `AGENTS.md` and `docs/internals.md`.

## Next

1. Manually verify Amsterdam skill moves at 4x scale with the installed app; if a fault remains, include `[ra-stomp]` and `[ee-context-history]` records.
2. Shader pre-warm at boot: ship FIFA's measured pipeline-key list (tagged by GPU feature set, checked against the PCSX2 pin) plus a per-device learned list in Application Support; precompile at GS device creation; kill-switch `RRV_PCSX2_GS_PIPELINE_PREWARM=0`. Measured with a prototype: about 250 keys, 1.5 s cold / 12 ms warm, 0 runtime compiles and 0 underruns on a cold run. Ubershaders were measured and rejected (Tried and rejected).
3. Add an offline VU1 benchmark and savestate workflow; SloppyWyn's PS2Recomp fork has snapshot work to evaluate.
4. Evaluate lean code generation and build-time effects against SloppyWyn's fork.

## Backlog

DualSense support, all-stadium and mode coverage, architecture and CPU-efficiency reviews, release polish, iOS/tvOS.
