# Status — 2026-10-17

Goal: faithful whole-game behavior on native ARM64 + Metal, with stable audio,
input, saves and pacing. This is an experimental static recompilation, not a
completed source decompilation or a verified complete game port.

| Area | Evidence / remaining work |
|---|---|
| Main ELF | 10,171 mapped functions: 9,801 translated (96.36%), 370 native runtime bindings; no skips or decode failures. Mapped spans cover 92.23% of executable-section bytes, including padding/data caveats. |
| Gameplay | First mission reached; user confirmed the stretched-head correction. Full progression and later missions remain untested. |
| Arithmetic | Scalar SQRT.S incorrectly read Fs instead of Ft; corrected compiler and active generated sites. DIV/RSQRT zero/denormal handling corrected at tested sites. |
| VU1 | Build 12 generates guarded native code from the owner's ELF and validated light/material patch writers, without captured banks or jump traces as compiler inputs. All 2,048 instruction slots are represented; unsupported pairs/entries still fall back transactionally. The shipping object passes 1,106 captured executions and 80 edge/fuzz runs with zero rejections or state/GIF/timing mismatches. First-mission gameplay sustains ~25 DISPFB2 changes/s with zero fallbacks/failures. Later missions remain unqualified. |
| IOP / VU0 | Native exact-image VU0 skinning now works in first-mission gameplay; 3,928,064 live differential calls and 1,000 IEEE/VI edge cases matched. Unknown VU0 programs still fall back; IOP remains interpreted. |
| Audio | Found movie PES loss: MPEG ignored zero (buffer-full) callback results and discarded whole audio packets. Fixed packet-by-packet acceptance/retry, incomplete packets and ring wrapping. Startup: 19,986 non-silent blocks align with source; story onset: 51,428 blocks over 15 seconds align with the selected source stream, with no decoded clipping. Five focused MPEG tests pass. Build 8 corrects immutable movie-PTS timing and double-counted synchronous IOP RPC cycles. The real story trace returns to ~48k mixed frames/s, versus ~51k before correction. User confirmed the startup/story audio is fixed. Later movies remain unqualified. See `docs/audio.md`. |
| Saves | Native host-directory card save/reload exercised for slot A at 0%; complete card semantics and all checkpoints unverified. Saves live outside the bundle. |
| Input / UI | Mac keyboard remapping exists. Resize with 4:3 content and native fullscreen implemented; installed bundle resize/fullscreen and normal Quit checked. Settings/keyboard windows retain their ARC ownership across close/reopen; 100 zombie-diagnostic cycles and 50 normal gameplay cycles passed. PS5 hardware validation pending. |
| Loading | Measured host reads through first-mission assets: ~9 MB in ~14 ms; disk reads are not the dominant delay in that run. Mission-transition samples show guest texture conversion/initialization. Three integer conversion handlers compile at O2 with original checkpoints retained; speedup not yet established by controlled A/B. |
| Performance | Build 16 vectorizes exact FMAC range correction; 24 focused VU tests, 1,106 captured executions and 80 edge/fuzz runs pass. Alternating replay median: 16.74 ms scalar vs 16.31 ms SIMD (~2.6% less native VU time). First-mission 120-Hz experiment: ~39.22 DISPFB2 changes/s vs ~39.13 control, no meaningful FPS gain established. Normal boot/story-to-gameplay and Quit pass; default PAL holds ~25/s with zero VU failures/fallbacks. Later missions remain unqualified. |
| Build time | Guarded VU1 compiles in 56–59 seconds, versus roughly 31 minutes previously. Integrated rebuild: 67 seconds; unchanged build: 0.1 seconds. Ninja regenerates VU1 incrementally; strict O2 arithmetic remains enabled. |
| Metal | Measured steady section: 50-Hz field service, p95 interval 20.50 ms; aggregate asynchronous GPU work ~3.52 ms/field; no GPU framebuffer readbacks. CPU work/timing is the next target. |
| Image quality | Upscaled world-menu tile grid traced to ST triangle strips. A guarded half-pixel UI coverage correction removed the black grid in 4×/16:9 capture. The 16:9 title backdrop camera is scoped by its dialog ID, removing the angled black edges in the main-menu capture. Optional Sharper image uses upstream Metal CAS 50% and sharp bilinear presentation; original texture detail remains unchanged. |
| Experiments | Off by default: 60/120 FPS targets and 16:9 mode. Independent internal resolution selector defaults to Native, with 2×/3×/4× options. First-mission high-FPS runs measured ~26–29 framebuffer swaps/s; menus approach the requested cadence. 2×/16:9 held ~25/s. First-mission 4×/4:3 and 3×/16:9 runs also reached ~25/s after loading; screenshots showed sharper geometry with no obvious new artifacts. Movie pacing now converts authored PTS and PTS-less frame intervals using the effective VBlank period; regression coverage includes 50/60/120 Hz. See `docs/experiments.md`. |
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

0. Disc-derived guarded VU1 AOT is implemented and first-mission qualified.
   Validate later missions and any remaining unknown pairs/entries; preserve full
   cross-program flag and pipeline state. No whole-game completion claim.

1. User confirmed startup/story audio is fixed. Extend listening and timing
   acceptance to later movies and transitions; compare the earlier ~93 ms
   initial pre-roll discrepancy with original playback separately.
2. Build 9 restores the authored camera only for the title backdrop dialog;
   4×/16:9 captures fill the main-menu edges while first-mission gameplay keeps
   its widescreen camera. Other menus, progression and later scenes remain to
   qualify. Engine/level flags alone could not safely distinguish this backdrop.
3. Optimize measured guest render/culling and character-update CPU hotspots.
   Upstream presentation throttling removed the sampled Metal drawable wait;
   high-FPS first-mission movement works but 60/120 FPS targets remain unmet.
4. Audit title-used runtime bindings; validate each mission, cutscene, menu,
   save/load/checkpoint and controller path against original behavior.
5. Replace remaining interpreter paths where validated native equivalents are
   feasible; assess iOS/tvOS on actual devices afterward.

See `docs/performance.md` for measured Metal attribution and upstream references.
Fukami adoption candidates and the off-by-default infinite-lives experiment
are tracked in `docs/TODO.md`. Incident-only debugger tools remain local.
