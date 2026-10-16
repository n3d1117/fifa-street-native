#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INPUT="$ROOT/extracted/disc/SLES_503.50"
ANALYZER="$ROOT/build/recompiler/ps2xAnalyzer/ps2_analyzer"
RECOMP="$ROOT/build/recompiler/ps2xRecomp/ps2_recomp"
ANALYZER_CONFIG="$ROOT/config/SLES-50350.analyzer.toml"
CONFIG="$ROOT/config/SLES-50350.toml"

for file in "$INPUT" "$ANALYZER" "$RECOMP"; do
  if [ ! -f "$file" ]; then
    echo "Required input or tool is missing: $file" >&2
    exit 2
  fi
done
mkdir -p "$ROOT/config" "$ROOT/generated" "$ROOT/logs" "$ROOT/reports"

"$ANALYZER" "$INPUT" "$ANALYZER_CONFIG" > "$ROOT/logs/analyzer-run.log" 2>&1

ANALYZER_CONFIG="$ANALYZER_CONFIG" CONFIG="$CONFIG" ROOT="$ROOT" python3 - <<'PY'
import os
import re
from pathlib import Path

source = Path(os.environ["ANALYZER_CONFIG"])
target = Path(os.environ["CONFIG"])
root = Path(os.environ["ROOT"])
text = source.read_text(encoding="utf-8")

def replace_value(name: str, value: str) -> None:
    global text
    pattern = rf"(?m)^{re.escape(name)}\s*=.*$"
    text, count = re.subn(pattern, f"{name} = {value}", text, count=1)
    if count != 1:
        raise RuntimeError(f"expected exactly one {name} setting in analyzer TOML")

replace_value("output", '"' + str(root / "generated" / "SLES-50350") + '"')
replace_value("single_file_output", "false")
replace_value("patch_syscalls", "false")
replace_value("patch_cop0", "false")
replace_value("patch_cache", "false")

# Bound output generation and avoid unreviewed automatic instruction rewrites.
single_line = re.search(r"(?m)^single_file_output\s*=\s*false\s*$", text)
if not single_line:
    raise RuntimeError("single_file_output setting missing after rewrite")
insert_at = single_line.end()
text = text[:insert_at] + "\nlow_memory_mode = true\noutput_worker_threads = 2" + text[insert_at:]

patches = re.search(r"(?ms)^\[patches\]\s*\n.*?(?=^\[|\Z)", text)
if not patches:
    raise RuntimeError("patches table missing from analyzer TOML")
block = patches.group(0)
instructions = re.search(r"(?ms)^instructions\s*=\s*\[.*?^\]\s*", block)
if instructions:
    block = block[:instructions.start()] + "instructions = []\n\n" + block[instructions.end():]
else:
    block += "instructions = []\n"
text = text[:patches.start()] + block + text[patches.end():]

target.write_text(text, encoding="utf-8")
PY

"$RECOMP" "$CONFIG" > "$ROOT/logs/recomp-run.log" 2>&1

ROOT="$ROOT" python3 - <<'PY'
import os
import re
from collections import Counter
from pathlib import Path

root = Path(os.environ["ROOT"])
log = (root / "logs/recomp-run.log").read_text(encoding="utf-8", errors="replace")
start = log.find("========== PS2Recomp report ==========")
if start < 0:
    raise RuntimeError("recompiler log did not contain its final report")
end_marker = "======================================"
end = log.find(end_marker, start)
if end < 0:
    raise RuntimeError("recompiler report was incomplete")
block = log[start:end + len(end_marker)]
summary = block.split("\nEvents:", 1)[0]
errors = [line for line in log.splitlines() if "[error] unhandled-instruction" in line]
categories = Counter()
for line in errors:
    match = re.search(r"Unhandled ([A-Za-z0-9]+)(?: instruction| format| opcode)", line)
    categories[match.group(1) if match else "other"] += 1
unknown_mmi = sum(1 for line in log.splitlines() if line.startswith("Unknown MMI function:"))
generated = root / "generated/SLES-50350"
file_count = sum(1 for path in generated.rglob("*") if path.is_file())
byte_count = sum(path.stat().st_size for path in generated.rglob("*") if path.is_file())
report = summary + "\n"
report += f"\nUnhandled instruction error events: {len(errors)}\n"
report += f"Unknown MMI decoder diagnostics: {unknown_mmi}\n"
report += "Unhandled categories: " + ", ".join(f"{key}={value}" for key, value in categories.most_common()) + "\n"
report += f"Generated artifacts: {file_count} files, {byte_count} bytes at {generated}\n"
report += f"Full analyzer log: {root / 'logs/analyzer-run.log'}\n"
report += f"Full recompiler log: {root / 'logs/recomp-run.log'}\n"
report += "\nSample unhandled events (first 12):\n"
report += "\n".join(errors[:12]) + "\n"
(root / "reports/recomp-report.txt").write_text(report, encoding="utf-8")
PY
