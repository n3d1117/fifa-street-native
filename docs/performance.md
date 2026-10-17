# Metal performance research

## Current observation

The confirmed first-mission A/B run in `runtime/checks/fps-ab-mission-1/stdout.log:199-211` reports DISPFB2 changes mostly 21.7–22.8/s in both phases; `reports/fps-ab-phases.json` timestamps the baseline and `upstream50_enabled` phases at 2026-10-16 08:21:18 and 08:22:03 UTC. The GS bridge capture at fields 4500–11997 reports 7,498 field services at 19.995 ms median (50.01 fields/s), p95 20.496 ms, p99 20.600 ms. These are different rates: the roughly 22 DISPFB2 changes/s are not field cadence or a proven unique-game-FPS count. Enabling the published 50-FPS marker did not increase the observed swap/change rate in this run. The capture sums 26,376.10 ms of completed GPU work over 7,498 fields, about 3.52 ms per serviced field as an aggregate (not per-frame latency; GPU samples are asynchronous). Instrumented host attribution sums to 7,276.79 ms GIF and 1,468.77 ms VSync, about 0.97 and 0.20 ms per field respectively; these components are not total CPU time and are not clean benchmark timings. GPU `Readbacks` counters were zero. The base `readbacks` field counts 8,416,650 CSR/SIGLBLID metadata observations (1,123 per field), not GPU readback transfers. This evidence does not indicate a GPU-bound renderer; it also does not establish overall CPU saturation.

The current EE implementation sets byte `0x6B00B8` in `GAM_InitEngineState_Playing` after a string comparison (`generated/SLES-50350-symbolmap-entry/GAM_InitEngineState_Playing__Fv_0x35ec20.cpp:98-154`); `osrSyncCallback` reads it as an unsigned byte and compares it against `0x6B0690` before entering its synchronization path (`generated/SLES-50350-symbolmap-entry/osrSyncCallback__Fi_0x3f8cf0.cpp:219-246`). The ELF `PT_LOAD` at vaddr `0x200000` has filesz `0x2c1380` and memsz `0x580000`; `0x6B00B8` is beyond the file-backed range and is loader-zeroed BSS, not an initial file byte. The PCSX2 patch writes zero. The published PAL-M 50-FPS patch is the exact game ID and says “Might need EE Overclock (130%)” before writing `006B00B8=00000000` ([patch file](https://raw.githubusercontent.com/PeterDelta/PCSX2/main/patches/SLES-50350_368E1540.pnach)). The same-process A/B phase marker and unchanged ~22 DISPFB2 changes/s provide no evidence that zeroing this byte alone raises presentation cadence on this build; the field service remains ~50 Hz. This is a sync/control-flow gate in the inspected generated code, not an explicit timestep multiplier, but unchanged observed swaps also do not prove guest-time equivalence. Do not pair it with EE overclock when testing that question.

Follow-up for this A/B: verify the flag value at runtime in both phases and compare guest mission progress or player distance per real second, since the unchanged DISPFB2 count does not establish guest-time equivalence. Keep the Gate-4 recorder separate from clean timing comparisons because its clocks/counter reads add overhead. The PCSX2 patch author reports an EE-overclock caveat, so any overclocked run must remain a separate experiment.

## Capture analyzer

`scripts/analyze-gs-profile.py` reads the actual `RRV_GATE4_GS_COUNTERS` v1 text rows emitted by `source/deps/pcsx2-gs-bridge-metal/bridge.cpp:558-575,700-723`. Example:

```sh
python3 scripts/analyze-gs-profile.py /path/to/gs-counters.txt --from-field 500 --to-field 900
```

The field bounds are inclusive. Interval percentiles use entry-time deltas only for adjacent field indices; they describe GS field-service cadence, not unique game FPS. The JSON separates `p` counters (between VSync returns) from `v` counters (inside `GSvsync`), counts invalid `v=-1` reset rows, and keeps overlapping ROV counters separate. GPU time is accumulated asynchronously from completed work and may be attributed to earlier fields; use its total rather than treating an individual field sample as that field's render time. GIF/VSync/readback host times are instrumented attribution, not clean benchmark timing. The retained mission capture was analyzed with `python3 scripts/analyze-gs-profile.py reports/mission-fps-ab-gs.txt --from-field 4500`.

## Practical optimization order and mobile constraints

1. Capture an uninstrumented host trace and a separate Gate-4 attribution run in the same gameplay scene. Apple Instruments' Game Performance template can correlate CPU threads, GPU, thermal state and display events; Metal Performance HUD provides live FPS/GPU timing on supported Apple device platforms. GPU counters are useful to separate fill/bandwidth from CPU submission, but Apple notes that counter collection can serialize passes and distort the workload. See [Game Performance analysis](https://developer.apple.com/documentation/xcode/analyzing-the-performance-of-your-metal-app), [Metal Performance HUD](https://developer.apple.com/documentation/xcode/monitoring-your-metal-apps-graphics-performance), and [Apple GPU counter statistics](https://developer.apple.com/documentation/xcode/analyzing-apple-gpu-performance-using-counter-statistics).
2. If GPU-bound, test lower internal GS render scale before changing game timing. GalaxyPad's current New engine uses Petari/MacioGalaxy decompiled game source compiled for ARM64; its iOS Metal view uses `CAMetalLayer.framebufferOnly = YES` and one drawable pixel per point, with internal EFB scale kept separate (`/tmp/tarzan-reference/galaxypad/apple/ios/GalaxyPadMetalView.mm:7-25`). This is a portable presentation/resolution pattern, not a drop-in PS2 optimization. Tarzan currently uses a MIPS ELF static recompile plus a PCSX2 GS bridge; its renderer, synchronization and runtime are different. GalaxyPad's README identifies the deprecated Dolphin/ModernGekko AOT path as Old engine, not the current mobile engine (`/tmp/tarzan-reference/galaxypad/README.md:7-25,84-110`).
3. The existing 1 ms `sample` capture (`reports/mission-fps-ab.sample.txt`, collected 2026-10-16 08:19–08:22 local) shows `HIE_fn_vSendBufferedCharacterToViewport__FPv_0x38d020` on 422 of 1,706 main-thread samples (~24.7%), including nested `VU1Interpreter::run` samples. The exact generated EE routine is `generated/SLES-50350-symbolmap-entry/HIE_fn_vSendBufferedCharacterToViewport__FPv_0x38d020.cpp:16`; the same run's periodic counters show VU1 time around 10.3–10.9%, zero fallback, and DISPFB2 changes around 22/s (`runtime/checks/fps-ab-mission-1/stdout.log:199-211`). This makes that generated routine the best evidenced CPU optimization target, but sampling while the main thread often waits in the host event loop is not a CPU-saturation measurement. The conservative next step is a clean targeted profile of this routine and its hottest PCs, then semantics-preserving compiler/codegen optimization with fixed simulation timing; do not claim it will raise FPS before measuring. GalaxyPad Smooth Motion separates presentation interpolation from game update rate (`/tmp/tarzan-reference/galaxypad/apple/ios/GalaxyPadNewEngineHost.mm:32,351-358`); that requires Tarzan-specific state/history and correctness work.

## Native VU0 checkpoint

The native VU0 path now runs through the first mission without runtime faults, after removing a fixed 4096-tag MFIFO limit that faulted while consuming a valid chain. The limit now follows configured ring capacity; the exact live `Store32` fault was confirmed, and six focused MFIFO tests plus 21 combined VU0/MFIFO tests pass. A normal native run sustained about 24.99–25.09 DISPFB2 changes/s after 60 seconds, including movement. Earlier interpreter/differential runs showed roughly 22–23/s, but these are observational captures, not a controlled A/B; neither rate establishes 60-FPS gameplay.

Native execution exceeded 3.1 million calls. Its mismatch counter is meaningful only in differential mode; the prior `vu0-native-live-diff-2` run compared 3,928,064 duplicate calls with zero mismatches, alongside 1,000 offline IEEE/VI cases. In `reports/vu0-native-fixed.sample.txt`, VU0 accounts for 218/4,536 GameThread samples (~4.8%), versus about 19% in a separate baseline capture. This indicates a lower observed VU0 share in that native capture, not a controlled performance comparison or a whole-game result.

The present Metal bridge and packaged app are macOS targets. iOS/tvOS support would require a platform port and device validation of the runtime, GS backend, display lifecycle, audio/input, memory and thermal behavior; Mac results do not establish mobile performance. Use Apple's [CAMetalLayer API](https://developer.apple.com/documentation/quartzcore/cametallayer) as the platform presentation reference. The 50-FPS toggle did not raise ~22 DISPFB2 changes/s in this observed mission segment, while GS field-service cadence stayed near 50 Hz. The counters show roughly 3.52 ms aggregate GPU work per field, so they do not point to GPU saturation, but they cannot identify a hard guest cap or establish total host CPU headroom. These data do not support a claim that 50/60 unique gameplay FPS is feasible without guest timing changes.

## Build 9 follow-up — 2026-10-17

A fresh first-mission 120-Hz/4×/16:9 sample has 4,428 GameThread samples.
`RenderOpaqueStatic` has 979 inclusive samples, chiefly nested VIF/MFIFO/native
VU1 work; these counts overlap and cannot be added. Native VU1 leaf TUs and
dispatcher already compile at O2 with strict FP settings. Metal nextDrawable
appears in only two sampled stacks, so drawable stalls are not the leading
target in this run. This is CPU attribution, not a GPU-utilization measurement.

The SPU host issued Advance(t), then immediately Sync(t). Both commands process
through the same timestamp; Sync also returns events. Removing the duplicate
Advance reduces command dispatch work without changing emulated time. The new
inline/threaded equivalence fixture checks returned events, core state, output
PCM/hash and mixed-frame count; all match. The existing determinism fixture
also passes. No overall FPS speedup is claimed from this change alone.

Keep Metal resource synchronization intact until a measured stall justifies a
change. Upstream already pipelines ordinary rendering; removing waits needed
for resource reuse or readback would trade correctness for an unproven gain.
See [Apple command-buffer guidance](https://developer.apple.com/library/archive/documentation/3DDrawing/Conceptual/MTLBestPracticesGuide/CommandBuffers.html).
