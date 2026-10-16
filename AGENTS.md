Keep this repository bring-your-own-disc: never track discs, extracted game data,
generated game code, symbols, binaries, build products, saves, or captures.
Use the allowlist and run `python3 scripts/check-repo-hygiene.py --staged` before
each commit. Export changed upstream patches with `scripts/snapshot-upstreams.py`.

Commit only reviewable checkpoints after checks appropriate to the change.
Immediately push every commit to the private origin and verify the push succeeded.
Do not leave a successful commit unpushed or claim whole-game correctness from
first-mission evidence. All mapped functions includes native runtime bindings;
translation coverage is separate from gameplay acceptance.

Use `build/game-metal`, not the retired non-Metal build. Root owns shared Ninja
builds and GUI test runs; delegate narrowly with explicit file ownership. Preserve
PS2 arithmetic/timing semantics; do not enable fast-math or speed up the game to
inflate FPS. User-facing updates should be brief and roughly once per minute.
