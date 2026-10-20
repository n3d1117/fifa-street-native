# Texture corruption handoff (branch tex)

Symptom: team-select flags/headshots, stadium preview and live-match pitch show a regular grid of horizontal dark dashes
of wrong texels. PCSX2 renders the game correctly with no fixes.

## Established
- The affected textures are already wrong in GS local memory (bad data is produced or uploaded before rendering).
- Not VU1 worker ordering (PS2X_VU1_WORKER=0 same), not a GIF path switch mid-upload, not GPU palette conversion,
  shaders match pinned PCSX2, PCSX2 32/8/4-bit upload/readback/palette paths pass native tests.
- Fixed here (real bugs, did NOT cure the grid; screens re-captured with `./rx run --route match`, still bad):
  PSLLVW/PSRLVW/PSRAVW (operands were swapped, all 4 lanes shifted), PADDUH/PSUBUH (wrapped instead of saturating).
  Regenerated code was byte-identical for the PADDUH/PSUBUH change ("0 files changed"), so the game never executes those.
- Reviewed against PCSX2 MMI.cpp and found correct: PEXT5, PPAC5, PPACW/H/B, PEXTL/U*, PINTH, PINTEH, PEXEH/EW/CH/CW,
  PREVH, PROT3W, PCPYLD/UD, PADSBH, LWL/LWR/SWL/SWR/LDL/LDR/SDL/SDR translations. Not reviewed: PMULT/PMADD/PDIV
  families (ps2_mmi_* helpers), PCPYH, QFSRV, PABS (PCSX2 saturates 0x80000000; ours does not), PMAXH/PMINH.

## Last attempt
Texture audit (diag patch kept untracked at build/texture-audit-diag.patch, adds PS2X_TEXTURE_AUDIT_DIR,
PS2X_TEXTURE_AUDIT_MIN_QWC, PS2X_TEXTURE_SNAPSHOT_MARKER to host/metal-gs-adapter.cpp) dumped path-3 image uploads.
Uploads repeat every few fields: PSMT8 (0x13) 512x104, 640x480, 304x228, 264x76, 120x108, 456x20; PSMT4 376x88.
Raw 8-bit data is not viewable without its CLUT. Temporary log in ps2_memory.cpp GIF chain walk (dropped) printed
tag/data EE addresses for GIF chain payloads >= 500 qwc, e.g. data@01161cc0, 01146480, 01336040 (1024 qwc),
012005c0 (2048 qwc), 0112f4c0 (4096 qwc). Not yet correlated to a specific texture or written-by analysis.

## Next hypotheses (ranked)
1. EE-side conversion/swizzle code writing the buffer: find the writer of one affected buffer (e.g. EE 0x01161cc0)
   with a write watch (mprotect + SIGSEGV handler printing a backtrace of generated functions, then `./rx where`),
   then diff its MMI/FP ops against PCSX2. Review the unreviewed MMI ops above first (PABS, PMULT/PMADD families, QFSRV).
2. Row pitch / width: compare the bar period (dash spacing in px) with BITBLTBUF.DBW and TRXREG width of one affected
   upload (IMAGE log lines carry bitblt/trxreg). A period of 16/32/64 texels would point at GS-side block layout.
3. HLE memcpy/memmove (Kernel/Stubs/LibC.cpp, template bindings) or the refpack decompressor corrupting data
   before upload: compare the decompressed buffer against a reference refpack decode of the disc file.

## Reproduce
- `./rx run --route match` (window frames in build/runs/<id>/frames/sample-NNN/window-000.png; sample-036 is select team,
  sample-095 live play). `./rx run --match` boots straight into a live match.
- Env: PS2X_VU1_WORKER=0, PS2X_DUMP_MEM=<hexaddr>:<words>, PS2X_TRACE_SYSCALLS/RPC/CD_IO.
- Audio underruns fail the route run (headless/niced); ignore for texture work.

## Worktree
/Users/ned/Downloads/fifa-street-native-tex, own deps clones on branch rx and own build tree. Run with `nice -n 10`.
Header edits to runtime macros cost a ~5 min rebuild.
