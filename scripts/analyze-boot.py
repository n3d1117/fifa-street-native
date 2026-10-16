#!/usr/bin/env python3
"""Summarize a native boot log and map any logged guest PC to ELF symbols."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = ROOT / "config/SLES-50350.symbols-entry.csv"
PC_RE = re.compile(r"(?i)\b(?:pc|epc|address|addr)\s*[:= ]\s*(?:0x)?([0-9a-f]{6,8})\b")
FATAL_RE = re.compile(
    r"(?i)(unhandled exception|\[terminate\]|\[launcher\]|fatal|failed to|assertion|\bexception\b|\bSIG(?:SEGV|ABRT|BUS)\b)"
)


def read_symbols(path: Path) -> list[tuple[int, int, str]]:
    rows = []
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            try:
                start = int(row["Start"], 0)
                end = int(row["End"], 0)
            except (KeyError, ValueError):
                continue
            if end > start:
                rows.append((start, end, row.get("Name", "?")))
    return sorted(rows)


def lookup(pc: int, symbols: list[tuple[int, int, str]]) -> str:
    # Maps are small enough for a direct scan and this keeps the script dependency-free.
    for start, end, name in symbols:
        if start <= pc < end:
            return f"{name} + 0x{pc - start:X} (0x{start:08X}-0x{end:08X})"
    before = [item for item in symbols if item[0] <= pc]
    if before:
        start, end, name = before[-1]
        return f"unmapped; nearest prior symbol {name} at 0x{start:08X} (end 0x{end:08X})"
    return "unmapped (before first symbol)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="captured runner stdout/stderr log")
    parser.add_argument("--symbols", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--output", type=Path, help="write Markdown report (default: stdout)")
    parser.add_argument("--tail", type=int, default=12, help="number of trailing log lines to include")
    args = parser.parse_args()

    lines = args.log.read_text(encoding="utf-8", errors="replace").splitlines()
    symbols = read_symbols(args.symbols)
    result_path = args.log.with_name("result.json")
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    fatal = [(i + 1, line) for i, line in enumerate(lines) if FATAL_RE.search(line)]
    pcs = []
    for i, line in enumerate(lines, 1):
        for match in PC_RE.finditer(line):
            value = int(match.group(1), 16)
            pcs.append((i, value, line))

    repeated = Counter(line.strip() for line in lines[-100:] if line.strip())
    repeated = [(count, line) for line, count in repeated.most_common(8) if count > 1]
    out = [f"# Boot log analysis: {args.log}", "", f"- Lines captured: {len(lines)}"]
    if result:
        out.extend([f"- Probe result: {'timed out' if result.get('timed_out') else 'exited'} after {result.get('elapsed_seconds', '?')}s (exit {result.get('exit_code', '?')})",
                    f"- Gameplay verified: {result.get('gameplay_verified', False)}"])
    out.extend([f"- Fatal-looking lines: {len(fatal)}", f"- Guest-PC matches: {len(pcs)}", ""])
    if fatal:
        out += ["## Fatal-looking lines", ""]
        out += [f"- Line {n}: `{line}`" for n, line in fatal]
        out.append("")
    if pcs:
        out += ["## Guest PCs", ""]
        out += [f"- Line {n}: `0x{pc:08X}` → {lookup(pc, symbols)}; log: `{line.strip()}`" for n, pc, line in pcs]
        out.append("")
    if repeated:
        out += ["## Repeated trailing lines (last 100 lines)", ""]
        out += [f"- {count}× `{line}`" for count, line in repeated]
        out.append("")
    out += ["## Final log lines", "", "```text", *lines[-max(1, args.tail):], "```", ""]
    report = "\n".join(out)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
