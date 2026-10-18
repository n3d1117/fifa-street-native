# Tarzan Freeride — native Apple port

Goal: a faithful, complete native ARM64 port of **Disney’s Tarzan: Freeride (PS2 PAL, SLES-50350)**, with Metal graphics, stable audio/saves/input, and smooth performance. Bring your own disc; no game files are distributed.

Current: first-mission gameplay reached on macOS. EE code is statically recompiled; 9,801 mapped functions are translated and 370 use native runtime bindings. Captured VU1 programs have native paths; captured VU0 skinning also runs natively, with interpreter fallbacks; IOP remains interpreted. This is recompilation, not recovered original source. Whole-game correctness and 50 FPS remain under validation; iOS/tvOS are future targets.

```text
host/              Cocoa window/input/audio + Metal GS adapter + CMake
scripts/           packaging, probes, diagnostics (tz is the entry point; see `tz --help`)
patches/           Pinned upstream source changes and dependency manifest
config/            Portable template; generated/local config stays untracked
tests/             Focused native/runtime regression checks
docs/              Performance evidence and research
tools/vu/   VU1 translation utility (GPL-3.0, upstream credited)
STATUS.md          Verified behavior, open issues, validation scope
THIRD_PARTY.md     Upstream provenance and licenses
```

Upstream trees are prepared locally from pinned revisions and source patches. Disc images, extracted assets, generated game code/symbol maps, dependencies, toolchains, builds, app bundles, captures, and saves stay outside Git. `.gitignore` uses an allowlist; the staged-file check rejects binary/oversized artifacts. `toolchain/bootstrap.sh` and `tools/build-deps.sh` populate `tools/` and `build/` from pinned, hash-checked sources; `TARZAN_LLVM` points at an existing LLVM 19. The compiler and build tools remain BYOD and are not checked into Git. See `STATUS.md` for current reproducibility limits and checks, and [TODO](docs/TODO.md) for priorities.
