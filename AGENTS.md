Keep this repository bring-your-own-disc: never track discs, extracted game data,
generated game code, symbols, binaries, build products, saves, or captures.
Use the allowlist and run `python3 tools/check-repo-hygiene.py --staged` before
each commit. Change upstream code in deps/<name> as commits, then `python3 tools/deps.py export`.

Commit only reviewable checkpoints after checks appropriate to the change.
Immediately push every commit to the private origin and verify the push succeeded.
Do not leave a successful commit unpushed or claim whole-game correctness from
first-mission evidence. All mapped functions includes native runtime bindings;
translation coverage is separate from gameplay acceptance.

Use `build/game-metal`, not the retired non-Metal build. Root owns shared Ninja
builds and GUI test runs; delegate narrowly with explicit file ownership. Preserve
PS2 arithmetic/timing semantics; do not enable fast-math or speed up the game to
inflate FPS. User-facing updates should be brief and roughly once per minute.

## Checks before committing

| Change | Run |
|---|---|
| docs, tooling, hygiene | `./tz check` |
| host/UI, anything else compiled | `./tz build` + `./tz test` (quick tier: ctest, no GUI) |
| runtime, VU, recompiler or `config/` | `./tz test --full` (adds slow tests and the 1,106-run VU1 replay) |

`./tz build` is the single entry for building: one tree, `build/game-metal`, holds the
game, the tests and the recompiler. Build parallelism is derived from the host
(`tools/jobs.py`; `TZ_JOBS` overrides). `./tz run` waits for a quiet machine before measuring.
