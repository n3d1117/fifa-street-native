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

- Eliminate scene-dependent VU1 interpreter fallback: cover disc microprograms
  and legal indirect branch targets, validate against captured execution, and
  qualify each mission rather than relying on first-level coverage.
- Follow Fukami's static block-AOT architecture: extract shipped MPG uploads,
  compile every instruction-slot entry into relocatable, deduplicated blocks,
  and select by exact executable bytes instead of whole-bank identity. Preserve
  Tarzan's scheduler/pipeline state at block boundaries and arbitrary indirect
  jumps. Include runtime-patched variants; no JIT or mission-by-mission manual
  discovery as the primary coverage strategy. Static ELF extraction is ready;
  block translation and runtime integration remain open.
- Audit cross-program flag liveness before enabling block-level elision:
  unknown later programs must receive complete observable flag/pipeline state;
  a captured-program corpus alone cannot establish whole-game dead flags.
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

- Add an opt-in “Remember last language” preference, off by default. Store only
  a validated game language ID after the guest accepts it; on the next cold
  start, feed it through the guest language-selection path and verify menus,
  subtitles, movie audio, and saves in every supported language.
- When enabled, reuse that saved choice and bypass the selector on the next
  boot. With no valid saved choice, show the selector. Never infer a language
  from the host locale silently.
- Implementation leads: guest `SetActiveLanguageID` at 0x47D040 receives the
  language ID in `$a1`, stores it at manager offset `+0x20`, and notifies
  registered callbacks. `ProcessMenuMessage_SetRequestedLanguage` at 0x2CD410
  checks menu-manager field `+0x130` against sentinel `-1` and enters a guest
  callback path; trace where a concrete selected ID reaches the setter before
  adding a hook. Confirm boot-time object lifetime and valid ID mapping too.

## P4 — Optional logo skip

- Investigate an off-by-default “Skip Disney / Ubisoft logos” option that lands
  at the guest Press Start screen or language selector. Identify and validate
  the exact logo state/asset and transition first; skip only those logos, never
  the language flow or story movies. Fall back to the normal boot path on any
  mismatch.
- Do not reuse the generic `readMpeg` debug bypass at 0x444080: it skips MPEG
  playback without distinguishing branding from story content. It is currently
  gated by `TARZAN_DEBUG_SKIP_MOVIES` for probes only.

## P5 — Apple mobile platforms, later

- After macOS whole-game qualification, build real iOS/tvOS targets: replace
  AppKit with UIKit/Metal surfaces, consolidate the launcher and runner into one
  process, cross-build the renderer/media/input dependencies, and implement
  user-owned game-data import and container-based saves. Qualify memory,
  suspend/resume, controller input, audio interruptions, and Apple TV import UX
  on physical devices. See [apple-platforms.md](apple-platforms.md).

## Experimental backlog — Infinite lives

- Add an opt-in infinite-lives option, off by default. Identify and verify the
  guest life decrement/reset paths; preserve health, mission rules, normal saves,
  and default behavior. Test death, retry, checkpoints, and mission transitions.
