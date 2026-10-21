# Status

## Current

| Area | State |
|---|---|
| Frame rate | Measured 50 game frames/s during confirmed play. `fps_game` is the median of fully post-confirmation 5 s windows (`./rx run --match --profile`). `--match` fails below 49.5. |
| CPU | Measured with `./rx run --match --profile`: GameThread about 0.43 cores plus worker about 0.49 cores. |
| Audio | The warm-run underrun mechanism was the all-EE-threads-blocked wait omitting IOP service deadlines; rapid-button spam runs now pass with zero underruns, while the legacy wait produced underruns in A/B. Cold Metal shader-cache stalls remain. |
| Graphics | PCSX2 upstream master; Metal renderer. The dashed-texel grid is fixed upstream; user confirmation is pending. |
| Replay | Post-goal REWIND sequences animate and return to live play (fixed in `af239cd`). |
| Unverified | Directional input response, memory-card saves, other stadiums and modes, HBlank-gated EE timers (`docs/internals.md` faithfulness notes) |
| Platforms | macOS arm64 |

`./rx run` writes the current measurements to its summary; its fields and meanings are documented in `AGENTS.md` and `docs/internals.md`.

## Next

1. Reduce cold-shader-cache stalls: assess build-time metallib compilation if supported by the toolchain, then asynchronous compilation or boot prewarming.
2. Add an offline VU1 benchmark and savestate workflow; SloppyWyn's PS2Recomp fork has snapshot work to evaluate.
3. Evaluate lean code generation and build-time effects against SloppyWyn's fork.

## Backlog

DualSense support, all-stadium and mode coverage, architecture and CPU-efficiency reviews, release polish, iOS/tvOS.
