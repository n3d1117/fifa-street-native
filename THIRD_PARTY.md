# Third-party components

The project is GPL-3.0-or-later (`LICENSE`). Upstream notices are retained in each dependency checkout and in `patches/`.

| Component | Source | License |
|---|---|---|
| PS2Recomp | github.com/ran-j/PS2Recomp | GPL-3.0 |
| PCSX2 (GS, SPU2, SoundTouch) | github.com/PCSX2/pcsx2 v2.8.2 | GPL-3.0; SoundTouch LGPL-2.1 |
| fukami (GS/SPU2 bridges) | github.com/danisandoval/fukami | GPL-3.0 |
| VU recompiler (`tools/vu`) | github.com/catsandsoup/Road-trip-Adventure-PS2-Recompile-MacOS | GPL-3.0 |
| FFmpeg, SDL3, sse2neon | built locally by `./rx setup` | LGPL-2.1+, zlib, MIT |

No disc, game executable, assets, game-derived generated code or symbol maps are distributed.
The app icon is drawn by `tools/package/make-icon.m` without game artwork.
