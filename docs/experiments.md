# Experiments

Settings → Experimental stores per-user preferences outside the app. Both
experiments default off and apply after restarting. The normal path retains
original guest timing, 4:3 camera framing and native GS render scale.

- **Higher frame rate:** select a 60 or 120 FPS target. Applies the published PAL
  sync unlock and an opt-in guest VBlank period. EE clock, COP0 elapsed time and
  IOP/SPU clocks are unchanged. These are targets, not achieved-rate claims;
  first-mission gameplay currently measures about 26–29 framebuffer swaps/s,
  while menus approach the selected cadence. Later gameplay requires validation.
  Movie PTS and PTS-less intervals use the effective VBlank period to preserve
  authored duration; authored movie frames are not interpolated. Presentation uses PCSX2's existing display-rate
  throttle so a 60 Hz display does not block every 120 Hz guest field.
- **Higher resolution and widescreen:** 2× internal GS scale in the existing
  field renderer, 16:9 window/fullscreen layout and the PAL camera/FOV patch.
  It does not switch to the Ridge Racer-specific full-frame path. Original
  4:3 menus and movies are not re-authored.

The BYOD generated-code patcher checks expected ELF instructions and reapplies
four title-scoped hooks after regeneration. It never patches the disc. Use
`TARZAN_TRACE_GAME_CLOCK=1` to compare game milliseconds and host milliseconds;
framebuffer changes, guest updates and displayed presents are distinct metrics.
Diagnostic `TARZAN_EXPERIMENT_FPS=60|120` and `TARZAN_EXPERIMENT_HD=1` override only
the current process. `probe-first-level.py --user-dir <copied-saves> --play`
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
