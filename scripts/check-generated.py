#!/usr/bin/env python3
"""Quality gate and hash manifest for the active PS2 generated source tree."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fail(message: str) -> None:
    raise RuntimeError(message)


def config_string(text: str, key: str) -> str:
    match = re.search(r"(?m)^\s*" + re.escape(key) + r"\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not match:
        fail(f"active config has no string setting {key!r}")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/SLES-50350.symbolmap-entry.toml")
    parser.add_argument("--log", type=Path, default=ROOT / "logs/recomp-symbolmap-entry-vcallmsr-check.log")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/generated-quality.json")
    args = parser.parse_args()

    config_path = args.config.resolve()
    log_path = args.log.resolve()
    if not config_path.is_file() or not log_path.is_file():
        fail(f"missing config or recompiler log: {config_path}, {log_path}")
    config = config_path.read_text(encoding="utf-8")
    input_elf = Path(config_string(config, "input")).resolve()
    map_csv = Path(config_string(config, "ghidra_output")).resolve()
    output = Path(config_string(config, "output")).resolve()
    for path in (input_elf, map_csv, output):
        if not path.exists():
            fail(f"configured input/map/output does not exist: {path}")

    text = log_path.read_text(encoding="utf-8", errors="replace")
    if "Recompilation completed successfully" not in text:
        fail("recompiler log has no successful completion marker")
    if re.search(r"\[error\]|unhandled-instruction|Unknown MMI function:", text):
        fail("recompiler log contains an error, unhandled instruction, or unknown MMI diagnostic")

    summary = re.search(
        r"Functions processed:\s*(\d+),\s*recompiled:\s*(\d+),\s*stubs:\s*(\d+),\s*skipped:\s*(\d+),\s*decode failures:\s*(\d+)",
        text,
    )
    loaded = re.search(r"Loaded\s+(\d+)\s+functions from Ghidra map", text)
    if not summary or not loaded:
        fail("recompiler log is missing function summary or loaded-map count")
    processed, recompiled, stubs, skipped, decode_failures = map(int, summary.groups())
    loaded_count = int(loaded.group(1))
    with map_csv.open(newline="", encoding="utf-8-sig") as stream:
        map_rows = list(csv.DictReader(stream))
    if processed != loaded_count or loaded_count != len(map_rows):
        fail(f"function/map count mismatch: processed={processed}, loaded={loaded_count}, CSV={len(map_rows)}")
    if skipped or decode_failures:
        fail(f"recompiler skipped {skipped} functions and had {decode_failures} decode failures")

    required = (
        "ps2_recompiled_functions.h",
        "ps2_recompiled_stubs.h",
        "register_functions.cpp",
        "ps2___elf_entry_0x200008.cpp",
        "sceDevVu0Exec_0x21ad00.cpp",
    )
    for name in required:
        if not (output / name).is_file():
            fail(f"required generated artifact is missing: {output / name}")
    registration = (output / "register_functions.cpp").read_text(encoding="utf-8", errors="replace")
    if "g_ps2RecompiledFunctionTable[0] = ps2___elf_entry_0x200008; // 0x200008" not in registration:
        fail("ELF entry is not registered at the generated function table entry 0")

    bad_reference = []
    file_count = 0
    for path in output.rglob("*"):
        if path.is_file():
            file_count += 1
            if path.suffix in {".cpp", ".h", ".hpp"}:
                content = path.read_text(encoding="utf-8", errors="replace")
                if "ctx->vi[27]" in content:
                    bad_reference.append(str(path.relative_to(output)))
    if bad_reference:
        fail("generated source indexes VI[27] for CMSAR0: " + ", ".join(bad_reference[:8]))
    target = (output / "sceDevVu0Exec_0x21ad00.cpp").read_text(encoding="utf-8", errors="replace")
    if "ctx->vu0_cmsar0 & 0x1FF" not in target or "ctx->vu0_tpc = instr_index" not in target:
        fail("VCALLMSR generated function does not use CMSAR0 and update architectural TPC")

    result = {
        "status": "PASS",
        "config": str(config_path),
        "log": str(log_path),
        "input_elf": str(input_elf),
        "input_elf_sha256": sha256(input_elf),
        "symbol_map": str(map_csv),
        "symbol_map_sha256": sha256(map_csv),
        "output": str(output),
        "function_count": processed,
        "recompiled": recompiled,
        "stubs": stubs,
        "skipped": skipped,
        "decode_failures": decode_failures,
        "generated_file_count": file_count,
        "forbidden_vi27_references": 0,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"PASS: {processed} mapped functions; zero skipped/decode failures/errors; CMSAR0 VCALLMSR verified.")
    print(f"ELF SHA-256: {result['input_elf_sha256']}")
    print(f"Map SHA-256: {result['symbol_map_sha256']}")
    print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
