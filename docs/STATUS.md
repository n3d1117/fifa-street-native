# Status

## Current

| Area | State |
|---|---|
| Frame rate | Measured 50 game frames/s during confirmed play. `fps_game` is the median of fully post-confirmation 5 s windows (`./rx run --match --profile`). `--match` fails below 49.5. |
| CPU | Measured with `./rx run --match --profile`: GameThread about 0.43 cores plus worker about 0.49 cores. |
| Audio | A bounded two-vsync GS queue reduces EE blocking, with ordered GS snapshots and EE-owned FIELD. Warm match underrun bursts remain; cold-cache dynamic shader/pipeline specialization also still causes underruns (`./rx run --match`). Fixed pipelines already compile at device setup. |
| Graphics | PCSX2 upstream master; Metal renderer. The dashed-texel grid is fixed upstream; user confirmation is pending. |
| Replay | Post-goal REWIND sequences animate and return to live play (fixed in `af239cd`). |
| Unverified | Directional input response, memory-card saves, other stadiums and modes, HBlank-gated EE timers (`docs/internals.md` faithfulness notes) |
| Platforms | macOS arm64 |

`./rx run` writes the current measurements to its summary; its fields and meanings are documented in `AGENTS.md` and `docs/internals.md`.

## Next

1. Reduce cold-cache dynamic Metal specialization bursts longer than the two-PAL-vsync allowance; fixed-pipeline prewarming is already in device setup, and build-time metallib compilation is unavailable with Command Line Tools.
2. Diagnose residual warm-match underruns, the late frame-rate outlier, and increased energy reported by the profiled queue A/B (`./rx ab --control PS2X_GS_VSYNC_QUEUE=0 --pairs 2 --profile`). A profiled control also hit the stream watchdog; its cause is unproven.
3. Add an offline VU1 benchmark and savestate workflow; SloppyWyn's PS2Recomp fork has snapshot work to evaluate.
4. Evaluate lean code generation and build-time effects against SloppyWyn's fork.

## Backlog

DualSense support, all-stadium and mode coverage, architecture and CPU-efficiency reviews, release polish, iOS/tvOS.
