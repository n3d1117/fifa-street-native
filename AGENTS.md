# AGENTS.md

Native arm64 + Metal static recompilation of FIFA Street (PS2, PAL, SLES-53064).
README.md: setup. `docs/internals.md`: mechanisms and constants. `docs/STATUS.md`: state and next steps.

## Hard rules

1. **Bring your own disc.** Never commit disc images, extracted files, generated code, symbol maps, binaries, build products, captures, saves or logs.
2. **Preserve PS2 semantics.**
   - No fast-math, no FP contraction, no reordering of guest float math. The per-file flags in `CMakeLists.txt` are load-bearing.
   - PAL timing: VBlank 50 Hz, movies 25 fps. Nothing may assume 59.94/60 Hz. Never change guest clocks or speed the game up to raise FPS.
   - FIFA Street's gameplay frame rate is not known. Measure it; do not assume it.
   - Faithful port only: no game mods (widescreen, cheats, logo skip, FPS changes).
3. **Never hand-edit `generated/`.** It is rebuilt from the ELF. Fix at the right layer (below).
4. **Claim only what you ran.** Label claims measured, inferred or guessed. Translation coverage is not gameplay evidence; a boot or first-level result is not whole-game evidence.
5. **One item at a time: verify, commit, push, confirm the push, then the next.** Never leave a commit unpushed.
6. **One build or GUI run at a time.** Builds share `build/game-metal`, and concurrent runs distort timing. `./rx` holds `build/.rx.lock`; a second command waits.
7. **User-visible changes end with `./rx package --install`.** The user judges by playing `/Applications/FIFA Street PS2.app`. Say what they should look or listen for; counters do not replace their eyes and ears.
8. **Keep chat updates brief**, about once a minute during long work.
9. **Before inventing a fix,** search upstream deps' current versions and other PS2Recomp-based ports; port the mechanism, then validate it here.
10. **Delegate when the task is not quick.** The lead agent plans and hands precise work to cheaper subagents.

## Commands

Driver: `./rx`. Every command is quiet: PASS/FAIL, time, a log path (`build/logs/`), the first error. Exit 0 pass, 1 failed, 2 missing prerequisite (the message names the fix). Never pipe through `head`/`tail`; use `-v` only if the log is not enough. `./rx --help` is the reference.

| Command | Use |
|---|---|
| `./rx setup` | one-time: deps sync, venv, toolchain, build-deps, pre-commit hook |
| `./rx deps [sync\|export\|status]` | upstream checkouts and the patch stack |
| `./rx disc <iso> [--dest DIR]` | verify ISO and boot ELF SHA-1 (`config/game.json`), extract to `extracted/disc` |
| `./rx build [--reconfigure]` | rebuild stale deps and recompiler; then generate and build the game once its config exists. `--reconfigure` after exit 2 about path or case |
| `./rx test [--full]` | quick ctest tier, no disc; `--full` adds slow-labelled tests |
| `./rx check [--tracked]` | hygiene checks |
| `./rx run [--seconds N] [--save DIR] [--user-dir D] [--profile] [--app PATH [--quit-cycles N]]` | bounded boot run with isolated saves and a summary |
| `./rx where <0xADDR\|name>`, `./rx where --unimplemented` | generated file, config entry or HLE handler; unimplemented stubs |
| `./rx package [--install]` | app bundle `FIFA Street PS2.app` |

Judge a run by its summary: `reached`, `seconds`, `fps_game`, `cpu_user_s`, `cpu_sys_s`, `audio_underruns`, `crash`, `stubs`. CPU seconds are the game child process only. Open the log only when the summary is wrong.

**Verification scope.**

| You changed | Run before committing |
|---|---|
| docs, `tools/` Python | `./rx check` |
| `host/`, other compiled code | `./rx build && ./rx test`; plus `./rx run --seconds 60` once the game boots |
| `deps/ps2recomp` runtime/HLE, VU, GS, SPU2, timing | the above, plus `./rx test --full` |
| recompiler (`deps/ps2recomp/ps2xRecomp`) or `config/` | `./rx build` (regenerates), `./rx test --full`, `./rx run --seconds 60` once booting |

## Where a fix goes (first layer that works)

1. `config/SLES-53064.template.toml`: stubs, entry points, jump tables, function boundaries. `config/SLES-53064.toml` is derived by `./rx disc`; edit the template.
2. `deps/ps2recomp`: translation (`ps2xRecomp/`), runtime/HLE (`ps2xRuntime/`), IOP (`ps2xIOP/`).
3. `deps/pcsx2` (GS, SPU2), `bridge/` (GS bridge `bridge/gs`, SPU2 backend `bridge/spu2`).
4. `host/`: window, input, audio output, settings.

Config `[[hooks]]` (instruction hooks) exist but none are used. Treat them as a last resort; each must be verified against the retail ELF words it depends on and must stay faithful.

## Changing a dependency

`deps/<x>` is a local checkout on branch `rx` (upstream pin plus our commits); `patches/<x>/` is the tracked copy. Flow: `git -C deps/<x> commit -am "<what and why>"` (one commit per logical change), `./rx deps export`, then `git add patches && git commit -m "<x>: <what>" && git push`.

- Fix a patch: `git -C deps/<x> commit --fixup <sha> && git -C deps/<x> rebase --autosquash <pin>`, then export.
- New upstream revision: edit the SHA in `deps.lock`, `./rx deps sync` (resolve with `git am --continue`), export, run the full check row. `./rx check` fails while dep work is unexported.

## Find things fast

| Symptom / topic | Look in |
|---|---|
| wrong behavior at a guest PC | `./rx where <addr>`, then that generated file (read-only) |
| translation (MIPS/FPU/MMI/VU macro) | `deps/ps2recomp/ps2xRecomp/src/lib/*_translator.cpp`, `code_generator.cpp` |
| HLE (CD, MPEG, GS, pad, memcard, SIF/RPC) | `deps/ps2recomp/ps2xRuntime/src/lib/Kernel/Stubs/`, `Kernel/Syscalls/` |
| threads, clocks; DMA, VIF, GIF, MFIFO | `deps/ps2recomp/ps2xRuntime/src/lib/` (`Kernel/EeScheduler.cpp`, `ps2_runtime.cpp`, `ps2_memory.cpp`, `ps2_vif1_interpreter.cpp`, `gs/`) |
| VU interpreter | `deps/ps2recomp/ps2xRuntime/src/lib/vu/` |
| IOP modules, SPU2, audio output | `deps/ps2recomp/ps2xIOP/src/`, `host/spu2-host.cpp` |
| rendering, upscaling, presentation | `host/metal-gs-adapter.cpp`, `deps/pcsx2/pcsx2/GS/`, `bridge/gs/` |
| window, settings, input | `host/metal-host.mm`, `host/settings.hpp` (defaults), `host/settings.mm`, `host/settings-window.mm`, `host/keyboard-controls.mm`, `host/gamepad-input.mm` |
| app bundle, probes | `tools/package/`, `tools/probe/` |
| disc identity, game paths | `config/game.json`, `tools/game.py`, `tools/disc.py` |
| why something is the way it is | `docs/internals.md`, then `git log -S '<identifier>'` |

Search with `git grep -n <pattern> -- <path>`; for deps use `git -C deps/<x> grep`. Do not read `generated/` wholesale (use `./rx where`), `deps/pcsx2` broadly, or `build/` and logs beyond the file a command named.

## Gotchas

- **Rebuild cost.** Generated code is split into unity units. Recompiler and config changes regenerate and recompile everything: batch them. Never change flags that regroup generated code; scope new defines to the host.
- **Path casing.** The checkout is reachable as `~/Downloads/...` and `~/downloads/...`; CMake treats them as different trees. Always go through `./rx`; never run `cmake` or `ninja` by hand.
- **Toolchain.** `RX_LLVM` selects an existing LLVM 19; otherwise `./rx setup` builds one into `tools/build`. ccache is enabled by `cmake/ccache.cmake` when present (`CCACHE_DISABLE=1` bypasses it). Parallelism comes from `tools/jobs.py` (`RX_JOBS` overrides).
- **Installed app is self-contained.** No `tools/` or checkout paths in bundled binaries (`./rx package` audits).
- **Automation.** Use the process-local diagnostic input (`PS2X_DIAGNOSTIC_INPUT_FIFO`), not synthetic key events. Screen recording is blocked; use window or GPU capture.
- **Measurement.** Never measure FPS with tracing, VU compare mode or GPU capture on. A/B control and candidate back to back, same route.
- **Saves** live in `~/Library/Application Support/FIFAStreetPS2`. Tests and runs use isolated dirs, never the user's.

## Working method

- **Bug:** reproduce on the real build, find the mechanism with runtime evidence, make the smallest fix at the right layer, show the reproduction now passes. Ask first: is the translation wrong or is HLE/runtime incomplete? After 3 failed attempts, re-diagnose from evidence. Record refuted ideas as one line in `docs/internals.md` "Tried and rejected".
- **Optimizations** ship with a kill-switch and, where results must be identical, a verify mode that runs both paths and reports mismatches. Profile first, change one thing, compare against the same route without it, report the measured delta or "no change".
- **HLE bindings need identity evidence** (RPC SID and function number, strings, arity) and a test asserting the handler.
- **Delete before adding.** Remove dead paths, one-off scripts and obsolete env knobs in the change that obsoletes them.
- **One source of truth.** Flags in `cmake/` and `CMakePresets.json`, pins in `deps.lock`, disc identity in `config/game.json`.
- **One-off investigations** use a scratch file outside the repo.
- **Pausing:** commit `wip: <what>` and put a 5-line "Resume" block at the top of `docs/STATUS.md` (intent, verified, next step, key files, gotchas). On pickup, re-verify its claims on the real artifact and delete the block when done.
- **A rule that keeps breaking becomes a check** (test, CMake error, hook), not more text.

## Commits and comments

- One verified unit per commit. Removal before reshaping; failing test before fix. Bug fixes carry the reproduction and evidence; perf commits carry baseline, after, delta and route.
- Comments only for what code cannot say and nobody can change (a PS2 hardware quirk, an upstream constraint).

## Docs

- Maintain only `README.md`, this file, `docs/STATUS.md` and `docs/internals.md`. Keep them lean.
- Write mechanisms and invariants, not session logs: no build numbers, run IDs, dates, or sample counts. Name the command that regenerates any quoted number.
- Done work lives in git history; STATUS keeps current truth and the ordered next steps. Mention an outside project only as a one-line source for a constant the code depends on.
