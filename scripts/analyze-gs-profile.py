#!/usr/bin/env python3
"""Summarize the text format written by RRV_GATE4_GS_COUNTERS (bridge v1)."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path


PERF_NAMES = (
    "Prim", "Draw", "DrawCalls", "Readbacks", "Swizzle", "Unswizzle",
    "TextureCopies", "TextureUploads", "Barriers", "RenderPasses",
    "TextureCopiesROV", "DrawCallsROV", "BarriersROV",
)
BASE_NAMES = (
    "field", "parity", "entry_ns", "packets", "bytes", "gif_ns",
    "vsync_ns", "readbacks", "result_changes", "local_reads",
    "local_read_bytes", "local_read_ns",
)


def _numbers(value: str, count: int, label: str) -> list[float]:
    parts = value.split(",")
    if len(parts) != count:
        raise ValueError(f"{label}: expected {count} comma-separated values, got {len(parts)}")
    result = [float(part) for part in parts]
    if not all(math.isfinite(number) for number in result):
        raise ValueError(f"{label}: non-finite number")
    return result


def parse(path: Path) -> list[dict]:
    rows = []
    saw_header = False
    for line_number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("# gate4-gs "):
                if not line.startswith("# gate4-gs v1 columns:"):
                    raise ValueError(f"line {line_number}: unsupported gate4-gs schema")
                saw_header = True
            continue
        if not saw_header:
            raise ValueError(f"line {line_number}: data before '# gate4-gs v1 columns:' header")
        parts = line.split()
        if len(parts) != 15:
            raise ValueError(f"line {line_number}: expected 12 scalars plus p/v/g, got {len(parts)} fields")
        base = [int(value, 10) for value in parts[:12]]
        if not parts[12].startswith("p:") or not parts[13].startswith("v:") or not parts[14].startswith("g:"):
            raise ValueError(f"line {line_number}: expected p:, v:, g: columns")
        p_values = _numbers(parts[12][2:], len(PERF_NAMES), f"line {line_number} p")
        v_values = _numbers(parts[13][2:], len(PERF_NAMES), f"line {line_number} v")
        gpu_values = _numbers(parts[14][2:], 2, f"line {line_number} g")
        row = dict(zip(BASE_NAMES, base))
        row.update(p=dict(zip(PERF_NAMES, p_values)), v=dict(zip(PERF_NAMES, v_values)),
                   gpu_ms=gpu_values[0], pcsx2_avg_gpu_ms=gpu_values[1])
        rows.append(row)
    if not saw_header:
        raise ValueError("missing '# gate4-gs v1 columns:' header")
    rows.sort(key=lambda row: row["field"])
    if any(a["field"] == b["field"] for a, b in zip(rows, rows[1:])):
        raise ValueError("duplicate field index")
    return rows


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(rows: list[dict], first: int | None = None, last: int | None = None) -> dict:
    if first is not None and last is not None and first > last:
        raise ValueError("--from-field must be <= --to-field")
    selected = [row for row in rows if (first is None or row["field"] >= first)
                and (last is None or row["field"] <= last)]
    intervals = []
    gaps = 0
    for earlier, later in zip(selected, selected[1:]):
        if later["field"] == earlier["field"] + 1:
            intervals.append((later["entry_ns"] - earlier["entry_ns"]) / 1_000_000)
        else:
            gaps += 1
    valid_gpu = [r["gpu_ms"] for r in selected if r["gpu_ms"] >= 0]
    valid_avg = [r["pcsx2_avg_gpu_ms"] for r in selected if r["pcsx2_avg_gpu_ms"] >= 0]
    perf = {}
    for name in PERF_NAMES:
        p_sum = sum(r["p"][name] for r in selected)
        v_values = [r["v"][name] for r in selected if r["v"][name] >= 0]
        perf[name] = {"p_sum": p_sum, "v_sum": sum(v_values), "v_valid_fields": len(v_values),
                      "p_plus_v_sum": p_sum + sum(v_values)}
    return {
        "schema": "gate4-gs-v1",
        "selected_fields": len(selected),
        "field_range": ([selected[0]["field"], selected[-1]["field"]] if selected else None),
        "filters_inclusive": {"from_field": first, "to_field": last},
        "field_entry_interval_ms": {
            "count": len(intervals), "non_adjacent_pairs_omitted": gaps,
            "percentile_method": "linear interpolation on sorted values",
            "p50": percentile(intervals, .50), "p95": percentile(intervals, .95),
            "p99": percentile(intervals, .99),
        },
        "instrumented_host_time_ms": {
            "gif_sum": sum(r["gif_ns"] for r in selected) / 1_000_000,
            "vsync_sum": sum(r["vsync_ns"] for r in selected) / 1_000_000,
            "local_read_sum": sum(r["local_read_ns"] for r in selected) / 1_000_000,
        },
        "gpu_ms": {"completed_work_sum": sum(valid_gpu), "valid_fields": len(valid_gpu),
                   "invalid_fields": len(selected) - len(valid_gpu),
                   "pcsx2_average_ms_mean": statistics.fmean(valid_avg) if valid_avg else None},
        "readbacks": {"calls": sum(r["readbacks"] for r in selected),
                      "result_changes": sum(r["result_changes"] for r in selected),
                      "local_read_calls": sum(r["local_reads"] for r in selected),
                      "local_read_bytes": sum(r["local_read_bytes"] for r in selected)},
        "gif": {"packets": sum(r["packets"] for r in selected), "bytes": sum(r["bytes"] for r in selected)},
        "perf_counters": perf,
        "interpretation": [
            "A row is one bridge GS field service; field cadence is not unique gameplay FPS or display-swap FPS.",
            "gif_ns/vsync_ns/local_read_ns are instrumentation-attributed host timings, not a clean benchmark.",
            "GPU samples are asynchronous completed-work deltas and may include earlier fields; sum is the useful total.",
            "p counters cover work since prior GSvsync return; v counters are inside GSvsync and -1 means reset/invalid.",
            "ROV perf counters overlap corresponding regular counters; do not add them to those totals.",
        ],
    }


def self_test() -> None:
    import tempfile

    header = "# gate4-gs v1 columns: fixture"
    def row(field: int, entry: int, gpu: str = "1.0,2.0") -> str:
        scalars = f"{field} 0 {entry} 1 10 1000000 2000000 2 1 3 40 500000"
        return f"{scalars} p:{','.join(['1'] * 13)} v:{','.join(['2'] * 13)} g:{gpu}"
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.txt"
        path.write_text("\n".join((header, row(1, 1_000_000), row(2, 11_000_000),
                                    row(3, 31_000_000), row(4, 61_000_000, "-1,-1"))) + "\n")
        result = summarize(parse(path), 2, 4)
    assert result["selected_fields"] == 3
    assert result["field_entry_interval_ms"]["p50"] == 25
    assert result["field_entry_interval_ms"]["p95"] == 29.5
    assert result["gpu_ms"]["completed_work_sum"] == 2
    assert result["readbacks"]["local_read_bytes"] == 120
    assert result["perf_counters"]["TextureUploads"]["p_plus_v_sum"] == 9


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="RRV_GATE4_GS_COUNTERS text file")
    parser.add_argument("--from-field", type=int, help="inclusive first field index")
    parser.add_argument("--to-field", type=int, help="inclusive last field index")
    parser.add_argument("--output", type=Path, help="also write JSON to this path")
    parser.add_argument("--self-test", action="store_true", help="run the fixture-only parser check")
    args = parser.parse_args()
    try:
        if args.self_test:
            if args.input:
                parser.error("input cannot be combined with --self-test")
            self_test()
            print("self-test passed")
            return 0
        if not args.input:
            parser.error("input is required unless --self-test is used")
        document = json.dumps(summarize(parse(args.input), args.from_field, args.to_field), indent=2) + "\n"
        if args.output:
            args.output.write_text(document, encoding="utf-8")
        sys.stdout.write(document)
        return 0
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
