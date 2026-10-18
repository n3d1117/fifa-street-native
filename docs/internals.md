# Internals (stub)

Phase 6 distils the rest of the notes into this file. The environment table is
kept here from the start so it stays current.

## Settings and environment

One `tarzan::Settings` struct (`host/settings.hpp`) is loaded once at startup from
`<user dir>/experimental-settings.plist`, the file the Settings window writes
(format and keys unchanged: `highFPS`, `targetFPS`, `hdWidescreen`, `renderScale`,
`sharperImage`, `rememberLanguage`, `lastLanguage`, `skipLogos`, `extraLives99`,
`unlimitedSpears`; unknown keys are preserved on save). Debug overrides are applied
on top; the guest runtime gets its host configuration through `PS2HostConfig`
(`ps2SetHostConfig`), not through the environment. Keyboard bindings are a separate
file (`keyboard.plist`).

Environment variables still read, and why:

| Variable | Effect |
| --- | --- |
| `TARZAN_EXPERIMENT_HD=1`, `_FPS=60\|120`, `_SCALE=1..4`, `_SHARP=1` | override that Settings field for this process |
| `PS2X_WALLCLOCK=0` | deterministic estimated-cycle timing instead of host-clock pacing |
| `TARZAN_DIAGNOSTIC_INPUT_FIFO=<fifo>` | scripted input (`tz run --route`, `tools/probe`) |
| `TARZAN_TRACE_FAULTS=1`, `TARZAN_TRACE_SPU2=1` | fault and audio-health log lines (`tz run` sets both) |
| `TARZAN_CAPTURE_DIR`, `_FRAMES`, `_START_FIELD`, `_INTERVAL` | bounded Metal frame capture (`tz run --gpu-capture`) |
| `TARZAN_TRACE_GAME_CLOCK=1`, `TARZAN_TRACE_CD_IO=1` | game-clock vs host-clock, disc read trace |
| `TARZAN_DEBUG_SKIP_MOVIES=1`, `TARZAN_DEBUG_KEEP_STORY=1` | skip movie playback for diagnosis |
| `TARZAN_AUDIO_STRETCH=0` | disable audio time stretching (SPU2 backend tests) |
| `TARZAN_TRACE_VU0_SKINNING=<file>` | VU0 deformation trace for catalog capture |
| `PS2X_VU1_CAPTURE[_EVERY\|_SKIP]`, `PS2X_VU1_CATALOG`, `PS2X_VU1_INTERPRETER`, `PS2X_VU1_DIFF[_LOG]`, `PS2X_VU0_INTERPRETER`, `PS2X_VU0_DIFF[_COMMIT]` | VU catalog capture and differential tooling (`tools/vu`) |

Set by the host for the GS bridge library, not by users: `RRV_PCSX2_GS_*` and
`TARZAN_UPSCALE_UI_COVERAGE`, derived from Settings in `host/metal-host.mm`.
