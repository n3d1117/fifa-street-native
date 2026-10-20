# Status

## Current

| Area | State |
|---|---|
| Boot | Logos, language menu, EA intro movies with audio, front end, team and stadium selection |
| Match | Live play at full speed: HUD clock 1.0 s per wall s, native VU1 from the disc, zero interpreter fallbacks (`./rx run --match`) |
| Audio | Zero underruns in movies and match (`./rx run --match`); some runs still show underrun bursts (measured 0 to about 8000 across six runs; cause not yet known) |
| Graphics | PCSX2 HW renderer at PCSX2 defaults (no per-game hacks); Metal shaders compiled at start from `deps/pcsx2`. Pitch, flags, headshots and venue preview render correctly on the match route |
| Unverified | Directional input response, memory-card saves, other stadiums and modes, gameplay fps |
| Platforms | macOS arm64 |

Loops: `./rx run --match` (live match, about 28 s), `./rx run --route match` (menus, about 2 min). Runs of 8 s or more put a runtime state dump in `summary.json` `state`.

## Next

1. Build times and test-loop tooling.
2. Performance: VU1 flag liveness, profile-driven CPU and Metal work.
3. Drop dead patches after an audit.
4. Faithfulness checks against PCSX2: GIF FIFO, EE cycle rate, VIF1 CYCLE CL=0.
5. Docs pass.

## Backlog

DualSense support; built-in cheats as settings (unlock all stadiums), then an all-stadium audit; architecture re-audit; CPU-efficiency pass; visual enhancements as settings; release polish; iOS/tvOS.

## Disc

`./rx disc <iso>` writes `reports/disc-inventory.json`. Data under `DATA/` in EA archives (`BIGF`, `BIG4`, `BIG3`, refpack `c0fb`/`10fb`). Movies: `FEMOVIES/*.MPC` (`MPCh` + raw MPEG-2). Audio: `SCHl` streams, `ABKC` banks, `.MUS`. IOP: `IOPRP280.IMG` (16 Sony modules) plus `DATA/IOP.BIG` (sio2man, mtapman, padman, libsd, mcman, mcserv, EA snddrv). SDK 2.8.0 (inferred). The ELF is stripped.
