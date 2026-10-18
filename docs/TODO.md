# Prioritized TODO

## P0 — Whole-game correctness

- Qualify complete progression, checkpoints, saves/reloads, menus, and exit paths;
  record crashes, hangs, and guest-state mismatches.
- Finish audio/video review across every movie and transition. Startup and the
  first story segment have listening confirmation; later movies and EOF behavior
  are still open. Keep movie skipping out of normal qualification.
- Recheck keyboard/gamepad behavior, focus changes, settings-window close/reopen,
  and recovery after backgrounding before each release candidate.

## P1 — Performance without correctness regressions

- Disc-derived guarded VU1 AOT is implemented: 2,048 instruction slots,
  validated light/material variants, exact executed-word guards and transactional
  fallback. Shipping replay: 1,106 captured calls plus 80 edge/fuzz runs pass.
  First mission holds ~25 framebuffer swaps/s with zero fallbacks/failures.
  Qualify later missions; unknown pairs or unsupported entries may still fall back.
- Preserve full cross-program flag/pipeline state. Enable flag elision only with
  an open-program liveness proof, never a captured-program corpus alone.
- Profile frame pacing, CPU/GPU cost, loading stalls, and thermal behavior by
  scene. Treat 60/120 FPS as targets, not guarantees; retain a native-scale
  baseline and compare visual/audio checkpoints after every optimization.
- Measure render-scale and sharpening GPU/memory cost on representative Macs;
  keep defaults conservative and document any scene-specific limits.

### Fukami adoption queue

Reference: [Fukami](https://github.com/danisandoval/fukami), reviewed at
`e8818121566cfaf3d361e38745860b0a9438e27b`. Reuse methods; validate Tarzan behavior.

- After VU coverage, profile generated EE render/culling hot paths; investigate
  batching scheduler-quiet accounting with exact commits at runtime boundaries.
- Profile Metal queue/submission ownership; adopt bounded backpressure,
  deterministic draining and failure wakeups where the current path needs them.
  Retain resource lifetime/synchronization guarantees. Pipeline front caching is
  conditional on a measured Metal lookup hotspot; the upstream Vulkan patch is
  not directly applicable.
- Extend existing timestamped SPU inline/worker equivalence to long transitions,
  underruns and pause/resume; keep host audio pacing separate from guest clocks.
- Audit atomic settings/save publication and verified BYOD import, especially
  for mobile containers. Test interrupted writes and rejected imports. Add
  loading caches only after showing actual read/decompression cost.
- Adopt fixed-input control/candidate runs with first-divergence state, GIF,
  audio and timing evidence. Fukami's RR5-specific camera/LOD patches, reported
  frame rates and platform support are not Tarzan acceptance evidence.

## P2 — Tutorial text clarity

- Investigate blurred tutorial/instruction text separately from 3D render scale.
  Compare source texture resolution, sampling, and UI compositing; preserve the
  original look as the default and verify text legibility without changing
  gameplay timing or layout.

## P3 — Remember the language and optionally bypass its selector

- Implemented in Build 17, off by default. Validated IDs are persisted only
  after the guest accepts them; restoration uses the original menu actions and
  language callbacks. Missing/invalid choices retain the selector.
- Startup restoration is checked for English, French, Spanish, Italian and
  German. Full localized subtitles, movie audio and save progression remain
  part of whole-game qualification.

## P4 — Optional logo skip

- Implemented in Build 18, off by default: “Skip Disney / Ubisoft logos”.
  Only the verified startup `Logo` call is bypassed, before movie setup.
  Default boot retains `LOGO.PSS`; enabled boot reaches the selector or the
  remembered-language title screen without opening that asset. Story and
  attract-mode call sites remain unchanged; unknown names use normal playback.
- Keep the generic `TARZAN_DEBUG_SKIP_MOVIES` bypass limited to diagnostics.

## P5 — Apple mobile platforms, later

- After macOS whole-game qualification, build real iOS/tvOS targets: replace
  AppKit with UIKit/Metal surfaces, consolidate the launcher and runner into one
  process, cross-build the renderer/media/input dependencies, and implement
  user-owned game-data import and container-based saves. Qualify memory,
  suspend/resume, controller input, audio interruptions, and Apple TV import UX
  on physical devices. See [apple-platforms.md](apple-platforms.md).

## Built-in cheats — 99 lives and unlimited spears

- Implemented in Build 19 as separate off-by-default options. Submit the
  original codes to the guest parser at its own verified mission-selection
  menu; retain its SetLife callback, spear flag and confirmation sound.
- First-mission HUD shows 99 lives; ten spear throws retain five spears.
  The built-in lives code grants a finite stock, not invulnerability or
  infinite lives. Wider death/checkpoint/save coverage remains unqualified.
- Codes: [original PS2 cheats](https://gamefaqs.gamespot.com/ps2/530724-disneys-tarzan-untamed/cheats).

## Experimental backlog — Unlock all missions

- Add an off-by-default unlock-all-missions experiment. Prefer the original
  unlock/menu path; preserve normal progression and existing save data, and
  verify mission selection and transitions before shipping.

## Experimental backlog — No damage / invincibility

- Add an off-by-default cheat that prevents damage and resulting life loss.
  Prefer the original cheat path; verify normal enemies, hazards, instant-death
  surfaces, and checkpoint/retry behavior without changing the default game.

## Experimental backlog — Built-in FPS display and unlocks

- Expose the game's built-in framerate display as an off-by-default option;
  verify what it counts against measured frame presentation without altering
  game timing.
- Add optional automatic character unlocks for Jane, Porter and Terk in the
  skiing/surfing challenges, using the original unlock paths.
- Add optional trick unlocks: Ground Tumble (world 1 Terk challenges),
  Scarecrow (world 2), and Corkscrew (world 3). Character rewards correspond
  to world 1 (Jane), world 2 (Porter), and all Terk challenges (Terk).
- Verify these built-in paths and rewards in the PAL PS2 version, including
  character selection and trick execution; the supplied reference also lists
  GameCube rewards. Keep options off by default and preserve earned progress
  and existing saves.
