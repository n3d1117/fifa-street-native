# Upstream provenance

Maintained integration and source modifications are provided under GPL-3.0-or-later;
the included GPL text is in `LICENSE`. Retain upstream notices in reused files.

| Component | Source / role |
|---|---|
| PS2Recomp | [ran-j/PS2Recomp](https://github.com/ran-j/PS2Recomp), compiler/runtime; exact revision in `deps.lock`, local patches in `patches/ps2recomp/`. |
| Fukami | [danisandoval/fukami](https://github.com/danisandoval/fukami), standalone Metal GS and SPU2 bridges; GPL-3.0 notices retained. |
| PCSX2 | [PCSX2/pcsx2](https://github.com/PCSX2/pcsx2), GS/Metal/SPU2 cores; pinned v2.8.2 source and resource provenance are checked locally. |
| VU1 tooling | [catsandsoup/Road-trip-Adventure-PS2-Recompile-MacOS](https://github.com/catsandsoup/Road-trip-Adventure-PS2-Recompile-MacOS), adapted native VU compiler; copied license/provenance in `tools/vu`. |
| SoundTouch | Upstream dependency used by the producer audio stretcher; its LGPL notices remain in the locally fetched source. |
| FFmpeg / SDL3 / sse2neon | Locally built media, input/audio and SIMD dependencies; not vendored or uploaded. Their own licenses and notices apply. |

Reference research: [KillzoneRecomp](https://github.com/drakolordx7/KillzoneRecomp),
[GalaxyPad](https://github.com/chrissotraidis/galaxypad), and the
[PCSX2 PAL 50 FPS patch](https://github.com/PCSX2/pcsx2_patches/blob/master/patches/SLES-50350_368E1540.pnach).
The published frame-rate patch is an opt-in experiment, not a correctness claim.

No disc, game executable, assets, game-derived generated code, symbol maps or
prebuilt game app are distributed by this repository. The icon is generated
from project drawing commands in `scripts/make-icon.m`, without game artwork.
