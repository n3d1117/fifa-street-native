# Experiments

Settings → Experimental stores per-user preferences outside the app. Optional
experiments default off and apply after restarting. The normal path retains
original guest timing, 4:3 camera framing and native GS render scale.
Internal resolution is independently selectable as Native, 2×, 3× or 4×.

- **Higher frame rate:** select a 60 or 120 FPS target. Applies the published PAL
  sync unlock and an opt-in guest VBlank period. EE clock, COP0 elapsed time and
  IOP/SPU clocks are unchanged. These are targets, not achieved-rate claims;
  first-mission gameplay currently measures about 26–29 framebuffer swaps/s,
  while menus approach the selected cadence. Later gameplay requires validation.
  Movie PTS and PTS-less intervals use the effective VBlank period to preserve
  authored duration; authored movie frames are not interpolated. Presentation uses PCSX2's existing display-rate
  throttle so a 60 Hz display does not block every 120 Hz guest field.
- **Widescreen:** 16:9 window/fullscreen layout and the PAL camera/FOV patch.
- **Sharper image:** opt-in upstream Metal contrast-adaptive sharpening at 50%
  plus sharp bilinear presentation. FXAA stays off; source texture detail is
  unchanged. Native-resolution artwork can still look soft at large sizes.
- **Internal resolution:** Native (default), 2×, 3× or 4× GS rendering in the
  existing field renderer. Independent of widescreen and FPS. Higher scales
  sharpen geometry edges and increase GPU/memory work; original texture and
  movie detail is unchanged. Existing build-5 widescreen preferences retain
  their 2× scale on migration. No Ridge Racer-specific full-frame path is used.
  Original 4:3 menus and movies are not re-authored.

The BYOD generated-code patcher checks expected ELF instructions and reapplies
four title-scoped hooks after regeneration. It never patches the disc. Use
`TARZAN_TRACE_GAME_CLOCK=1` to compare game milliseconds and host milliseconds;
framebuffer changes, guest updates and displayed presents are distinct metrics.
Diagnostic `TARZAN_EXPERIMENT_FPS=60|120` and `TARZAN_EXPERIMENT_HD=1` override only
the current process. `TARZAN_EXPERIMENT_SCALE=1|2|3|4` selects an
independent scale without saving preferences. `probe-first-level.py --user-dir <copied-saves> --play`
provides a bounded first-mission movement/capture route, not whole-game proof.

References: [PCSX2 PAL patches](https://github.com/PCSX2/pcsx2_patches/blob/main/patches/SLES-50350_368E1540.pnach),
[KillzoneRecomp timing work](https://github.com/drakolordx7/KillzoneRecomp/blob/master/docs/findings.md),
[PCSX2 presentation throttle](https://github.com/PCSX2/pcsx2/blob/master/pcsx2/GS/Renderers/Common/GSDevice.cpp).

First-mission 2×/16:9 testing held approximately 25 swaps/s with the FPS
experiment off. A 120-Hz profile initially spent ~73% of sampled game-thread
time waiting for Metal drawables; enabling the upstream presentation throttle
removed that wait from the follow-up sample. Guest render/culling and character
updates now dominate. These runs had another game instance running and are
not isolated maximum-performance benchmarks.

The 120-Hz route with the real first-mission story movie reached gameplay;
observed audio health reported no underruns, clipping or nonfinite samples.
This does not qualify all movies, synchronization or whole-game behavior.

Resolution approach: [PCSX2 rendering and texture detail](https://pcsx2.net/blog/2024/pcsx2-2-release/).

Additional first-mission checks: 4×/4:3 and 3×/16:9 both reached about
25 swaps/s after loading, with movement and sharper geometry visible in
captures. These are short compatibility checks, not all-scene GPU budgets.

Clarity investigation (2026-10-16): the world-selection background uses
ST triangle-strip quads, not GS sprites. Sprite Round=2/Align=1, half-pixel
Native+TextureOffset=5, and half-texel ST inset did not remove the grid in
4×/16:9 captures. The official Tarzan game database specifies Normal HPO=1
for blur/shadow alignment; this alone did not remove the grid either.
References: [upscaling coverage and sampling](https://pcsx2.net/blog/2015/explanation-hacks-needed-for-upscaling-glitches/),
[upstream Tarzan renderer fix](https://github.com/PCSX2/pcsx2/blob/master/bin/resources/GameIndex.yaml),
[alignment improvements](https://pcsx2.net/blog/2025/pcsx2-2.4_2.2/).

Loading instrumentation: `TARZAN_TRACE_CD_IO=1` reports actual host reads and
cumulative bytes/time (first 32 calls, every 1024 calls and each MiB boundary).
Short sector-padding reads are distinct from failed reads. Guest texture
conversion appeared prominently in the measured mission transition; three
integer conversion handlers now compile individually at O2, with all original
guest checkpoints and strict arithmetic retained. No measured load-speedup
claim follows merely from this compiler change. Probe scheduled input waits
are not game loading time; `ready_elapsed_seconds` is only a VU0 activity hint.

The working correction extends the right/bottom coverage of the traced
full-texture UI strips by half a guest pixel, only above native scale. It is
guarded to clamped 256-wide, axis-aligned four-vertex ST quads with constant
Q=1, depth and color and full 0..1 texture mapping. It does not modify the
texture bytes, original native-scale path or perspective geometry. The
4×/16:9 world-selection capture no longer shows the black grid.
`TARZAN_UPSCALE_UI_COVERAGE=0` disables this correction for diagnosis.

Window lifecycle: Experimental Settings and Keyboard Controls now retain
their ARC-owned windows across close/reopen; keyboard capture monitors still
clean up on close. A combined 100-cycle close/reopen run with Objective-C
zombie diagnostics reported no lifetime errors. Movie timing no longer
accumulates late-frame shifts into its PTS epoch; late frames present promptly
without discarding decoded frames or changing audio clocks.

Repeatable routing: `probe-first-level.py --diagnostic-input --fast --keep-story`
skips only the logo and exercises the real first-mission story using a bounded,
process-local FIFO input path. It uses copied saves when supplied `--user-dir`,
does not depend on desktop keyboard focus, and is disabled during normal play.
Movie audio and video now share real-time hardware pacing; synchronous IOP RPC
work consumes the accumulated IOP budget instead of double-counting cycles.

Menu camera scoping: the title backdrop is a C3DWorldDlg with serialized ID
`0x107003b9`; first-mission gameplay uses a different dialog ID (`0x3e9`).
The widescreen camera adjustment preserves the backdrop's authored camera while
other dialog/camera paths retain widescreen. The generated-code hook checks the
CAM frame's saved caller and dialog pointer, so guest yields do not lose its
context. Prologue, caller and SetId instructions are verified against the ELF.
`TARZAN_TRACE_CAMERA_CONTEXT=1` logs up to 64 distinct camera/dialog tuples for
future scene diagnosis; it never selects cameras by transient heap addresses.

Fresh-start diagnostics: `probe-first-level.py --new-game --user-dir <empty-dir>`
creates a separate slot, fills the name through normal input, and enters the
first Story mission. It rejects personal/nonempty save directories and the
installed-launcher mode. `--fast --keep-story` retains the actual story movie;
omitting `--play` avoids automated tutorial confirmations. This route verifies
new-game creation; it does not yet reliably reproduce the reported tip card.
