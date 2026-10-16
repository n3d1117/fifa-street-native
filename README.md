# Tarzan Freeride — native Apple port

Goal: a faithful, complete native ARM64 port of **Disney’s Tarzan: Freeride (PS2 PAL, SLES-50350)**, with Metal graphics, stable audio/saves/input, and smooth performance. Bring your own disc; no game files are distributed.

Current: first-mission gameplay reached on macOS. EE code is statically recompiled; 9,801 mapped functions are translated and 370 use native runtime bindings. Captured VU1 programs have native paths; IOP/VU0 still use interpreters. This is recompilation, not recovered original source. Whole-game correctness and 50 FPS remain under validation; iOS/tvOS are future targets.

```text
integration/       Cocoa window/input/audio + Metal GS adapter + CMake
scripts/           BYOD extraction/generation, builds, packaging, diagnostics
patches/           Pinned upstream source changes and dependency manifest
config/            Portable template; generated/local config stays untracked
tests/             Focused native/runtime regression checks
tools/vu1recomp/   VU1 translation utility (GPL-3.0, upstream credited)
STATUS.md          Verified behavior, open issues, validation scope
THIRD_PARTY.md     Upstream provenance and licenses
```

Upstream trees are prepared locally from pinned revisions and source patches. Disc images, extracted assets, generated game code/symbol maps, dependencies, toolchains, builds, app bundles, captures, and saves stay outside Git. `.gitignore` uses an allowlist; the staged-file check rejects binary/oversized artifacts. Build helpers derive paths locally; `TARZAN_NATIVE_TOOLS_ROOT` overrides the sibling toolchain location. See `STATUS.md` for current reproducibility limits and checks.
