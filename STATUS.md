# Status — 2026-10-16

Goal: faithful whole-game behavior on native ARM64 + Metal, with stable audio,
input, saves and pacing. This is an experimental static recompilation, not a
completed source decompilation or a verified complete game port.

| Area | Evidence / remaining work |
|---|---|
| Main ELF | 10,171 mapped functions: 9,801 translated (96.36%), 370 native runtime bindings; no skips or decode failures. Mapped spans cover 92.23% of executable-section bytes, including padding/data caveats. |
| Gameplay | First mission reached; user confirmed the stretched-head correction. Full progression and later missions remain untested. |
| Arithmetic | Scalar SQRT.S incorrectly read Fs instead of Ft; corrected compiler and active generated sites. DIV/RSQRT zero/denormal handling corrected at tested sites. |
| VU1 | 13 captured microprogram images have native paths. 914 captured replay states matched; 213,759 live differential calls recorded no mismatch. Unknown later images can fall back. |
| IOP / VU0 | Interpreter paths remain; these are explicit gaps toward fully native subsystem execution. |
| Audio | Producer-side SoundTouch removed steady-state ring drops in measured story capture. Empty outside-thread event waits were fixed and regression-tested. Startup overrun dropped from 124,512 to 4,096 frames in the 8-second probe; remaining drops and perceptual quality still need work. |
| Saves | Native host-directory card save/reload exercised for slot A at 0%; complete card semantics and all checkpoints unverified. Saves live outside the bundle. |
| Input / UI | Mac keyboard remapping exists. Resize with 4:3 content and native fullscreen implemented; final bundled fullscreen regression pending. PS5 hardware validation pending. |
| Performance | Prior native run ~24.2 DISPFB2 changes/s. Instrumented same-process 50-FPS-patch A/B remained ~22 changes/s. These are framebuffer swaps, not proof of simulation speed or unique frames. |
| Metal | Measured steady section: 50-Hz field service, p95 interval 20.50 ms; aggregate asynchronous GPU work ~3.52 ms/field; no GPU framebuffer readbacks. CPU work/timing is the next target. |
| Runtime bindings | Audit found 33 active configured names mapped to unimplemented handlers; execution on later title paths is unknown. Other shims also need behavioral validation. |
| Mobile / tvOS | Feasibility only. AppKit host, deployment/toolchain support, audio/session lifecycle and device performance need separate work. |

## Repository and reproduction

This source-only repository excludes original disc images, extracted files,
generated game code/maps, dependencies, tools, binaries, captures and user data.
Pinned upstream revisions and working-tree source patches are snapshotted by
`scripts/snapshot-upstreams.py`; `scripts/restore-upstreams.py` restores missing
checkouts without overwriting existing work. `patches/upstreams.json` is the
lock/patch manifest. Legacy standalone patches are historical, not cumulative
instructions to apply over the snapshot.

Local sequence: restore upstreams; supply a supported PAL disc and run
`extract-disc.py`; run `configure-local.py` to derive the symbol map/config;
prepare the native toolchain/dependencies; build the Metal bridge and game;
generate the icon with `make-icon.sh`; package with `package-ps2-app.py`.
Build tooling is preserved, but a fresh-machine end-to-end rebuild is not yet
qualified. Current native toolchain defaults to a sibling `tarzan-native` folder
and can be overridden with `TARZAN_NATIVE_TOOLS_ROOT`. No toolchain or game is
uploaded. Build the canonical target in `build/game-metal`.

Use focused tests and actual window captures. Shader/readback captures change
timing; do not use them as clean performance benchmarks. Coverage figures and
first-mission evidence must never be presented as whole-game completion.

## Next acceptance gates

1. Fix and verify startup audio without hiding it with a mute/gain workaround.
2. Validate resize/fullscreen and install the signed bundle in Applications.
3. Profile CPU hotspots, preserve original timing, then re-evaluate the published
   PAL 50 FPS patch with simulation/movement and audio synchronization checks.
4. Audit title-used runtime bindings; validate each mission, cutscene, menu,
   save/load/checkpoint and controller path against original behavior.
5. Replace remaining interpreter paths where validated native equivalents are
   feasible; assess iOS/tvOS on actual devices afterward.

See `docs/performance.md` for measured Metal attribution and upstream references.
