# AGENTS.md

Native arm64 + Metal port of Tarzan: Freeride (PS2 PAL SLES-50350) by static recompilation.
README.md covers setup. This file covers working here. Mechanisms and constants: `docs/internals.md`. State and roadmap: `docs/STATUS.md`.

## Hard rules

1. **Bring your own disc.** Never commit disc images, extracted files, generated code, symbol maps, binaries, build products, captures, saves or logs.
2. **Preserve PS2 semantics.**
   - No fast-math, no FP contraction, no reordering of guest float math. The per-file flags in `CMakeLists.txt` are load-bearing.
   - Never change guest clocks or speed the game up to raise FPS.
3. **Never hand-edit `generated/`.** It is rebuilt from the ELF. Fix at the right layer (below).
4. **Claim only what you ran.** Label claims measured, inferred or guessed. Translation coverage is not gameplay evidence; first-mission evidence is not whole-game evidence.
5. **One item at a time: verify, commit, push, confirm the push, then the next.** Never leave a commit unpushed.
6. **One build or GUI run at a time.** Builds share `build/game-metal`, and concurrent runs distort timing.
7. **User-visible changes end with `./tz package --install`.** The user judges by playing `/Applications/Tarzan Freeride PS2.app`. Say what they should look or listen for; counters do not replace their eyes and ears.
8. **Keep chat updates brief**, about once a minute during long work.
9. **Before inventing a fix,** search the upstream deps' current versions and other PS2Recomp-based ports. Port the mechanism, then validate it on this game.

## Commands

Every `tz` command is quiet: PASS/FAIL, time, a log path (`build/logs/`), the first error. Exit 0 pass, 1 failed, 2 missing prerequisite (the message names the fix). Never pipe it through `head`/`tail`; use `-v` only if the log is not enough. `./tz --help` is the reference.

| Command | Use |
|---|---|
| `./tz build` | generate if inputs changed, build `build/game-metal` (0.2 s no-op; long after a recompiler/config change). `--reconfigure` after exit 2 about path/case |
| `./tz test` / `--full` | quick tier (ctest, no disc, seconds) / adds slow tests and the 1,106-run VU1 replay |
| `./tz run --seconds 60` | bounded boot check with a summary |
| `./tz run --route first-mission` | scripted new game to first gameplay, isolated saves (about 3 min) |
| `./tz run --save <fixture-dir> --seconds 60` | bounded boot with `<fixture-dir>/memcards/` copied into fresh isolated saves |
| `./tz run --app <bundle> --quit-cycles N` | boot and quit the packaged app N times |
| `./tz where <0xADDR\|name>` | generated file, config entry, HLE handler |
| `./tz check [--tracked]` | hygiene, header-rebuild warning, unexported dep work |
| `./tz deps [sync\|export\|status]` | upstream checkouts and our patch stack |
| `./tz disc <iso>`, `./tz setup`, `./tz package` | disc import, one-time setup, app bundle |

Judge a run by its summary (`reached`, `fps_dispfb2`, `cpu_user_s`, `cpu_sys_s`, `vu_fallbacks`, `audio_underruns`, `crash`). CPU seconds are for the game child process only. Open the log only when the summary is wrong. Run `./tz run` on a quiet machine; it waits up to 60 s for one.

| You changed | Run before committing |
|---|---|
| docs, `tools/` Python | `./tz check` |
| `host/`, other compiled code | `./tz build && ./tz test && ./tz run --seconds 60` |
| `deps/ps2recomp` runtime/HLE, VU, GS, SPU2, timing | the above, then `./tz test --full` and `./tz run --route first-mission` |
| recompiler (`deps/ps2recomp/ps2xRecomp`) or `config/` | `./tz build` (regenerates), `./tz test --full`, `./tz run --route first-mission` |

## Where a fix goes (first layer that works)

1. `config/SLES-50350.template.toml`: stubs, entry points, jump tables, function boundaries. (`config/SLES-50350.toml` is derived from it by `tz disc`; edit the template.)
2. `[[hooks]]` in that config plus `host/game-hooks.cpp`: narrow game hooks, verified against the original ELF words first.
3. `deps/ps2recomp`: translation (`ps2xRecomp/`), runtime/HLE (`ps2xRuntime/`), IOP (`ps2xIOP/`).
4. `deps/pcsx2`, `deps/fukami`: GS, SPU2, bridge.
5. `host/`: window, input, audio output, settings.

## Changing a dependency

`deps/<x>` is a local git checkout on branch `tarzan`: upstream pin plus our commits. `patches/<x>/` is the tracked copy. `deps/` is never committed. Upstream code changes are commits there, then export.

```sh
git -C deps/ps2recomp commit -am "<what and why>"   # one commit per logical change
./tz deps export                                    # rewrites patches/ps2recomp/
git add patches && git commit -m "ps2recomp: <what>" && git push
```

- Fix an existing patch: `git -C deps/<x> commit --fixup <sha> && git -C deps/<x> rebase --autosquash <pin>`, then `./tz deps export`.
- New upstream revision: edit the SHA in `deps.lock`, `./tz deps sync` (`git am -3`; resolve with `git am --continue`), `./tz deps export`, run the full check row.
- `./tz check` fails while dep work is unexported.

## Find things fast

| Symptom / topic | Look in |
|---|---|
| wrong behavior at a guest PC | `./tz where <addr>`, then that generated file (read-only) |
| MIPS/FPU/MMI/VU-macro translation | `deps/ps2recomp/ps2xRecomp/src/lib/*_translator.cpp`, `code_generator.cpp` |
| function boundaries, stubs, jump tables | `config/SLES-50350.template.toml` |
| HLE (CD, MPEG, GS, pad, memcard, SIF/RPC) | `deps/ps2recomp/ps2xRuntime/src/lib/Kernel/Stubs/`, `Kernel/Syscalls/` |
| movies, movie audio/PES handling | `deps/ps2recomp/ps2xRuntime/src/lib/Kernel/Stubs/MPEG.cpp`, `host/spu2-host.cpp` |
| threads, scheduling, clocks | `deps/ps2recomp/ps2xRuntime/src/lib/Kernel/EeScheduler.cpp`, `ps2_runtime.cpp` |
| DMA, VIF, GIF, MFIFO | `deps/ps2recomp/ps2xRuntime/src/lib/ps2_memory.cpp`, `ps2_vif1_interpreter.cpp`, `gs/` |
| VU0/VU1 native code and guards | `tools/vu/` (generator), `deps/ps2recomp/ps2xRuntime/src/lib/vu/` (runtime) |
| IOP modules, SPU2, audio output | `deps/ps2recomp/ps2xIOP/src/`, `host/spu2-host.cpp` |
| rendering, upscaling, widescreen, presentation | `host/metal-gs-adapter.cpp`, `deps/pcsx2/pcsx2/GS/`, `deps/fukami/tools/pcsx2-gs-bridge/` |
| window, settings and their defaults, keyboard, gamepad | `host/metal-host.mm`, `settings.hpp` (defaults), `settings.mm` (load/migrate), `experimental-settings.mm` (UI), `keyboard-controls.mm`, `gamepad-input.mm` |
| FPS unlock, widescreen camera, cheats, logo skip, language | `host/game-hooks.cpp` + `[[hooks]]` in the config |
| app bundle, signing, icon | `tools/package/` |
| probes, window capture, GS profile | `tools/probe/` |
| why something is the way it is | `docs/internals.md`, then `git log -S '<identifier>'` |

Read this file before searching. Search with `git grep -n <pattern> -- <path>` (fast, skips ignored trees; for deps use `git -C deps/<x> grep`). Do not read `generated/` wholesale (10k files; use `tz where`), `deps/pcsx2` broadly (grep with a path filter), or `build/` and logs except the file a `tz` command pointed to.

## Gotchas (each cost hours)

- **Rebuild cost.** Generated code is about 350 unity units; never change flags that regroup it (scope new defines to the host). A recompiler/config change means full regeneration and recompile: batch such changes.
- **Path casing.** The checkout is reachable as `~/Downloads/...` and `~/downloads/...`; CMake treats them as different trees. Always go through `./tz`; never run `cmake`/`ninja` by hand.
- **Toolchain.** `TARZAN_LLVM` selects an existing LLVM 19; otherwise `toolchain/bootstrap.sh` builds one into `tools/build`. Build parallelism comes from `tools/jobs.py` (`TZ_JOBS` overrides).
- **Installed app must be self-contained:** no `tools/` or Downloads paths in bundled binaries. `./tz package` audits this.
- **Automation.** Use the probe's process-local diagnostic input, not synthetic key events. Screen recording is blocked; use window or GPU capture. Guard routes with screen-text checks; timed inputs land in the wrong menu.
- **Measurement.** Never measure FPS with tracing, VU compare mode (runs each call 3x) or GPU capture on. A/B control and candidate back to back, same route, never concurrently. Gameplay is CPU-bound.
- **PAL timing.** Gameplay 25 FPS, VBlank 50 Hz, movies 25 fps. Anything assuming 59.94/60 Hz is a bug unless an opt-in experiment set it.
- **VU changes.** `./tz test --full` must show zero mismatches and zero rejected; a rejected native run must leave state untouched.
- **Saves** live in `~/Library/Application Support/TarzanFreeridePS2`. Tests and routes use isolated or copied save dirs, never the user's.

## Working method

- **Bug:** reproduce on the real build, find the mechanism with runtime evidence, make the smallest fix at the right layer, show the reproduction now passes. Ask first: is the translation wrong or is HLE/runtime incomplete? After 3 failed attempts, stop and re-diagnose from evidence. Record refuted ideas in `docs/internals.md` "Tried and rejected".
- **Optimizations** ship with a kill-switch and, where results must be identical, a verify mode that runs both paths and reports mismatches.
- **HLE bindings need identity evidence** (RPC SID and function number, strings, arity) and a test asserting the handler.
- **Performance:** profile first, change one thing, compare against the same route without it, report the measured delta or "no change".
- **Delete before adding.** Remove dead paths, one-off scripts and obsolete env knobs in the change that obsoletes them.
- **One source of truth.** Flags in `cmake/` and `CMakePresets.json`, pins in `deps.lock`, disc identity in `tools/disc.py`. Point to them.
- **No new script for a one-off investigation:** use a scratch file outside the repo. Add to `tz` only what joins the loop.
- **A rule that keeps breaking becomes a check** (test, CMake error, hook), not more text here.

## Commits and comments

- One verified unit per commit; removal before reshaping, failing test before fix.
- Bug-fix commits carry the reproduction and the evidence it is fixed. Perf commits carry baseline, after, delta and route.
- Comments only for what code cannot say and nobody can change (a PS2 hardware quirk, an upstream constraint). No comments restating code or narrating history.

## Pausing and resuming

To stop mid-task: commit `wip: <what>` and put a 5-line "Resume" block at the top of `docs/STATUS.md` (intent, verified so far, next step, key files, gotchas). On pickup, re-verify its claims on the real artifact, and delete the block when done.

## Docs

- Maintain only `README.md`, this file, `docs/STATUS.md` and `docs/internals.md`.
- Write mechanisms and invariants, not session logs: no build numbers, run IDs, dated prose, sample counts, or `file:line` pointers into untracked files. Name the command that regenerates any number you quote.
- Mention an outside project only as a one-line source for a constant the code depends on.
