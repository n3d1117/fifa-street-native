#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BINARY="$ROOT/build/tests/ps2recomp/ps2xTest/ps2x_tests"
LOG="$ROOT/logs/native-regressions.log"
test -x "$BINARY"
mkdir -p "$ROOT/logs"
cd "$ROOT/deps/ps2recomp"
PS2X_WALLCLOCK=0 MINITEST_FILTER='GsGParam scratch layout,sceGszbufaddr uses PSM,sceGsSetDefDBuff uses register ABI,VIF1 buffers split,sceGsSetDefLoadImage builds,GsSetCrt updates SMODE2,sceGsResetGraph frees,PAL 512 display,VIF UNPACK preserves hardware,runtime arena,Guest dispatch preserves a checkpoint yield,IPU_TO HLE DMA,MPEG GetPicture,sceMpegGetPicture,MPEG CD stream EOF ignores final sector padding,RotateThreadReadyQueue,MFIFO,SPR_FROM,EE LQ/SQ,VCALLMSR,sceMpegDemuxPssRing dispatches registered video and audio stream callbacks,sceMpegDemuxPssRing completes EE callbacks in FIFO before returning,VIF1 DIRECT,VIF1 NOP-I stalls,VIF1 MARK-I raises,VIF1 ERR.MII masks,VIF1 DMA keeps D1 active,VIF1 MMIO reads live scalar,invalid opcode with I sets ER1,invalid opcode without I sets ER1,ERR.ME1 masks invalid opcode' \
  "$BINARY" 2>&1 | tee "$LOG"
if ! rg -q '^Total Tests: 45$' "$LOG" || ! rg -q '^Passed: 45$' "$LOG"; then
  echo "Expected exactly 45 native regression cases to pass; see $LOG" >&2
  exit 1
fi
rg -q '^Failed: 0$' "$LOG"
for name in \
  "VIF1 NOP-I stalls at the completed command and STC resumes the next code" \
  "VIF1 MARK-I raises INT without VIS stall" \
  "VIF1 ERR.MII masks command I bits without stalling" \
  "VIF1 DIRECT-I defers its IRQ until split payload completes" \
  "VIF1 DMA keeps D1 active until stalled command suffix resumes" \
  "VIF1 MMIO reads live scalar and ROW/COL registers" \
  "VIF1 MFIFO stops after IRQ tag and resumes at the next tag after STC" \
  "invalid opcode with I sets ER1 without IRQ and STC resumes tail" \
  "invalid opcode without I sets ER1 and STC resumes tail" \
  "ERR.ME1 masks invalid opcode and continues without IRQ"; do
  rg -Fq "$name" "$LOG" || { echo "Expected test was not run: $name" >&2; exit 1; }
done
