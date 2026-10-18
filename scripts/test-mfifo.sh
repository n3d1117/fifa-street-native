#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build/tests"
BINARY="$BUILD/ps2recomp/ps2xTest/ps2x_tests"
LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/mfifo-targeted-tests.log"

if [ ! -x "$BINARY" ]; then
  echo "Targeted test binary is missing: $BINARY" >&2
  exit 2
fi

mkdir -p "$LOG_DIR"
cd "$ROOT/deps/ps2recomp"
MINITEST_FILTER=MFIFO,SPR_FROM "$BINARY" 2>&1 | tee "$LOG"

if ! rg -q '^Total Tests: 6$' "$LOG" || ! rg -q '^Passed: 6$' "$LOG" || ! rg -q '^Failed: 0$' "$LOG"; then
  echo "Expected exactly six filtered MFIFO/SPR_FROM tests to pass; see $LOG" >&2
  exit 1
fi
for name in \
  "DMAC SPR_FROM chain writes scratchpad data across MFIFO ring wrap" \
  "DMAC SPR_FROM copies scratchpad to RDRAM and completes channel 8" \
  "VIF1 MFIFO resumes when producer data is available at nonzero RBOR" \
  "VIF1 MFIFO accepts chains longer than 4096 zero-length CNT tags" \
  "VIF1 MFIFO stops after IRQ tag and resumes at the next tag after STC" \
  "VIF1 MFIFO REF REFS and REFE read external payload across a wrapped nonzero ring"; do
  if ! rg -Fq "$name" "$LOG"; then
    echo "Expected test was not run: $name" >&2
    exit 1
  fi
done

echo "Filtered MFIFO and SPR_FROM tests passed; log: $LOG"
