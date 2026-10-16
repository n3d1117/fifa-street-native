#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$ROOT/logs/recomp-symbolmap-entry.log"
OUTPUT="$ROOT/generated/SLES-50350-symbolmap-entry"

if [ ! -f "$LOG" ]; then
  echo "Missing mapped recompiler log: $LOG" >&2
  exit 2
fi
for summary in \
  "Functions processed: 10171, recompiled: 9801, stubs: 370, skipped: 0, decode failures: 0" \
  "Unhandled instructions: 0" \
  "Warnings: 6820, errors: 0" \
  "Loaded 10171 functions from Ghidra map"; do
  if ! grep -Fq "$summary" "$LOG"; then
    echo "Mapped recompiler quality gate failed; missing: $summary" >&2
    exit 1
  fi
done
if grep -Eq 'unhandled-instruction|Unknown MMI function:|\[error\]' "$LOG"; then
  echo "Mapped recompiler quality gate failed: unsupported instruction or error diagnostics found" >&2
  exit 1
fi
for artifact in ps2_recompiled_functions.h ps2_recompiled_stubs.h register_functions.cpp ps2___elf_entry_0x200008.cpp; do
  if [ ! -s "$OUTPUT/$artifact" ]; then
    echo "Mapped recompiler quality gate failed: missing artifact $OUTPUT/$artifact" >&2
    exit 1
  fi
done
if ! grep -Fq 'g_ps2RecompiledFunctionTable[0] = ps2___elf_entry_0x200008; // 0x200008' "$OUTPUT/register_functions.cpp"; then
  echo "Mapped recompiler quality gate failed: ELF entry is not registered" >&2
  exit 1
fi
echo "PASS: 10,171 functions emitted; no skipped functions, unhandled instructions, unknown MMI opcodes, or report errors."
