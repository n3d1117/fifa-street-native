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
