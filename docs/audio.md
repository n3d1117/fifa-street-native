# Movie audio checkpoint — 2026-10-16

The guest audio callback returns zero when its destination ring cannot accept a
complete PES payload. The MPEG shim previously erased the PES and reported its
bytes consumed regardless. Five consecutive rejected startup packets were
observed; playback then jumped from aligned source bytes to data shifted by
three bytes, causing ADPCM corruption and saturation.

Demux now stops at the first registered audio PES. Callback completion reports
only accepted bytes to the calling guest thread. Rejected PES and incomplete
packets remain in the input ring for retry. Both sides of a wrapped packet are
assembled before parsing. CD/EOF accounting follows the accepted byte count.
No mute, gain reduction, limiter or replacement soundtrack was introduced.

Validation: five focused MPEG tests pass (rejection/retry, partial and wrapped
packets, odd-sized consecutive payloads, callback FIFO and video lead handling).
Startup capture: all 19,986 non-silent decoded blocks match aligned LOGO source;
no saturated decoded samples. First 15 seconds of W1_1_GM.PSS selected ADPCM
substream FF A1 00 01: 25,714 blocks per channel match aligned source, with no
invalid block headers or saturated decoded samples. Source comparisons ignore
only each block's loop-flag byte, deliberately rewritten by the game driver.
Filter by the selected private substream before concatenating PSS audio.

Signed build 3 is installed and launch-checked under Applications. The bundled
C++ runtime libraries now use bundle-relative search paths; leftover Downloads
toolchain paths had blocked launch before main. Listening confirmation remains
pending. A separate startup pre-roll discrepancy of roughly 93 ms remains to
compare with original PS2 playback. This is not whole-game audio qualification.
Three ring overruns in the debug-skip navigation run occurred during silence;
there were no clipping windows across the 314-second run. Temporary per-block
mixer instrumentation was removed before packaging; local captures are ignored.

## Movie synchronization correction

The later audio lead had two independent clock causes. Late video frames shifted
the presentation epoch, accumulating video delay; the epoch now remains fixed.
Synchronous guest IOP RPC instructions also advanced the SPU timeline in addition
to the full EE-issued IOP budget. The IOP scheduler now accumulates an absolute
issued target, crediting work already executed before advancing further. Reset
clears the target and fractional 1:8 carry, while the SPU epoch stays monotonic.
Interactive hardware time follows the monotonic host clock rather than allowing
estimated EE block costs to accelerate it. Explicit diagnostic overrides remain.

The physical-guest RPC regression checks budget debt, repayment, reset and
fractional carry; the IOP emulator smoke suite passes. The strict-clock regression
and 33 focused MPEG/VBlank/VU/MFIFO tests pass. The first real story run with only
the EE clock correction still produced about 51k mixed frames/s: that change alone
was insufficient. With IOP budget accounting corrected, the real W1_1_GM movie
trace returns to approximately 48k frames/s with a fixed video PTS epoch. This
removes measured cumulative clock drift; it does not certify lip-sync or all
movies. Listening confirmation and the earlier initial pre-roll comparison remain
separate acceptance gates.

Build 8 story-to-gameplay qualification: 42 health intervals averaged
48.00024k mixed frames/s (47.98–48.02k range), with no accumulating video
PTS offset. The movie EOF transition logged a silent ring flush/underrun burst
(19,520 underrun and 16,896 overrun frames); it is not counted as clean audio
throughout the transition. First-mission movement was captured and the process
quit normally. Further listening and later-movie acceptance remain required.
