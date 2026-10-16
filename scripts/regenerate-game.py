#!/usr/bin/env python3
"""Safely regenerate game C++ from the canonical PS2Recomp config.

The default operation compiles into a temporary output directory, checks the
compiler's quality summary, then atomically replaces only changed files. Use
--dry-run to validate paths/config without invoking the compiler.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/SLES-50350.symbolmap-entry.toml"
DEFAULT_COMPILER = ROOT / "build/recompiler/ps2xRecomp/ps2_recomp"


def fail(message: str) -> None:
    raise RuntimeError(message)


def get_general_string(config_text: str, key: str) -> Tuple[str, Tuple[int, int]]:
    """Return a simple quoted [general] string and its source span."""
    section = re.search(r"(?ms)^\[general\]\s*$.*?(?=^\[|\Z)", config_text)
    if not section:
        fail("config has no [general] section")
    matches = list(re.finditer(
        rf"(?m)^{re.escape(key)}\s*=\s*(['\"])(.*?)\1\s*(?:#.*)?$",
        section.group(0),
    ))
    if len(matches) != 1:
        fail(f"expected exactly one [general].{key} string, found {len(matches)}")
    m = matches[0]
    absolute_start = section.start() + m.start(2)
    absolute_end = section.start() + m.end(2)
    return m.group(2), (absolute_start, absolute_end)


def replace_output(config_text: str, output_dir: Path) -> str:
    _old, (start, end) = get_general_string(config_text, "output")
    # JSON string escaping is compatible with TOML basic strings for paths.
    return config_text[:start] + json.dumps(str(output_dir))[1:-1] + config_text[end:]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_summary(log_text: str) -> Dict[str, int]:
    patterns = {
        "discovered": r"Functions discovered:\s*(\d+)",
        "processed": r"Functions processed:\s*(\d+)",
        "recompiled": r"Functions processed:.*?recompiled:\s*(\d+)",
        "stubs": r"Functions processed:.*?stubs:\s*(\d+)",
        "skipped": r"skipped:\s*(\d+)",
        "decode_failures": r"decode failures:\s*(\d+)",
        "unhandled": r"Unhandled instructions:\s*(\d+)",
        "errors": r"Warnings:\s*\d+\s*,\s*errors:\s*(\d+)",
    }
    summary: Dict[str, int] = {}
    for name, pattern in patterns.items():
        matches = re.findall(pattern, log_text, flags=re.IGNORECASE)
        if not matches:
            fail(f"compiler log lacks required `{name}` summary")
        summary[name] = int(matches[-1])
    return summary


def symbol_map_info(path: Path) -> Tuple[int, int]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not {"Name", "Start"}.issubset(rows[0]):
        fail(f"symbol map is empty or lacks Name/Start columns: {path}")
    starts = []
    for row in rows:
        try:
            starts.append(int(row["Start"], 0))
        except (TypeError, ValueError):
            fail(f"invalid Start address in symbol map row: {row}")
    return len(rows), starts[0]  # canonical map's first row is the ELF entry


def startup_artifact_names(stage: Path, entry: int) -> Tuple[str, str]:
    register_name = "register_functions.cpp"
    suffix = f"_0x{entry:x}.cpp"
    matches = [name for name in regular_files(stage) if name.endswith(suffix)]
    if len(matches) != 1:
        fail(f"expected one generated startup entry ending {suffix}, found {len(matches)}")
    return register_name, matches[0]


def validate_required_artifacts(output: Path, entry: int) -> None:
    register_name = output / "register_functions.cpp"
    if not register_name.is_file():
        fail(f"required registration artifact missing: {register_name}")
    suffix = f"_0x{entry:x}.cpp"
    matches = [p for p in output.iterdir() if p.is_file() and p.name.endswith(suffix)]
    if len(matches) != 1:
        fail(f"expected one startup entry artifact ending {suffix} in {output}, found {len(matches)}")
    if matches[0].stem not in register_name.read_text(encoding="utf-8", errors="replace"):
        fail(f"startup entry {matches[0].name} is absent from register_functions.cpp")


def regular_files(root: Path) -> Dict[str, Path]:
    result: Dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            fail(f"staged output contains symlink: {path.relative_to(root)}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path
    return result


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--report", type=Path, default=ROOT / "reports/regenerate-game.json")
    parser.add_argument("--dry-run", action="store_true", help="validate inputs and report plan without running compiler")
    args = parser.parse_args()

    config = args.config.resolve()
    compiler = args.compiler.resolve()
    report_path = args.report.resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = ROOT / "logs" / f"regenerate-game-{timestamp}.log"
    report = {
        "config": str(config),
        "compiler": str(compiler),
        "log": str(log_path),
        "dry_run": args.dry_run,
        "changed_count": 0,
        "changed_files": [],
        "stale_existing_files_retained": [],
        "elf_sha256": None,
        "canonical_config_sha256": None,
        "compiler_sha256": None,
        "symbol_map": None,
        "symbol_map_sha256": None,
        "symbol_map_rows": None,
        "startup_entry": None,
        "summary": None,
        "status": "validation_failed",
    }

    try:
        if not config.is_file():
            fail(f"config not found: {config}")
        if not compiler.is_file() or not os.access(compiler, os.X_OK):
            fail(f"compiler missing or not executable: {compiler}")
        original_text = config.read_text(encoding="utf-8")
        input_value, _ = get_general_string(original_text, "input")
        output_value, _ = get_general_string(original_text, "output")
        map_value, _ = get_general_string(original_text, "ghidra_output")
        elf = Path(input_value)
        if not elf.is_absolute():
            elf = config.parent / elf
        elf = elf.resolve()
        if not elf.is_file():
            fail(f"ELF input not found: {elf}")
        symbol_map = Path(map_value)
        if not symbol_map.is_absolute():
            symbol_map = config.parent / symbol_map
        symbol_map = symbol_map.resolve()
        if not symbol_map.is_file():
            fail(f"symbol map not found: {symbol_map}")
        map_rows, map_entry = symbol_map_info(symbol_map)
        with elf.open("rb") as elf_stream:
            elf_bytes = elf_stream.read(28)
        if len(elf_bytes) < 28 or elf_bytes[:6] != b"\x7fELF\x01\x01":
            fail("expected a little-endian ELF32 input")
        elf_entry = struct.unpack_from("<I", elf_bytes, 24)[0]
        if map_entry != elf_entry:
            fail(f"symbol map first entry 0x{map_entry:x} != ELF e_entry 0x{elf_entry:x}")
        canonical_output = Path(output_value)
        if not canonical_output.is_absolute():
            canonical_output = config.parent / canonical_output
        canonical_output = canonical_output.resolve()
        if canonical_output == ROOT or ROOT not in canonical_output.parents:
            fail(f"refusing output directory outside project: {canonical_output}")
        report["output"] = str(canonical_output)
        report["elf"] = str(elf)
        report["elf_sha256"] = sha256_file(elf)
        report["canonical_config_sha256"] = sha256_file(config)
        report["compiler_sha256"] = sha256_file(compiler)
        report["symbol_map"] = str(symbol_map)
        report["symbol_map_sha256"] = sha256_file(symbol_map)
        report["symbol_map_rows"] = map_rows
        report["startup_entry"] = f"0x{elf_entry:x}"

        if args.dry_run:
            validate_required_artifacts(canonical_output, elf_entry)
            report["status"] = "dry_run_validated"
            write_report(report_path, report)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0

        lock = ROOT / "build/regenerate-game.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            fail(f"another regeneration appears active (lock exists): {lock}")
        os.write(lock_fd, f"pid={os.getpid()}\n".encode())
        os.close(lock_fd)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="tarzan-game-regen-") as temp_name:
                temp_root = Path(temp_name)
                stage = temp_root / "generated"
                stage.mkdir()
                staged_config = temp_root / "config.toml"
                staged_config.write_text(replace_output(original_text, stage), encoding="utf-8")
                with log_path.open("w", encoding="utf-8") as log:
                    result = subprocess.run([str(compiler), str(staged_config)], cwd=ROOT,
                                            stdout=log, stderr=subprocess.STDOUT, check=False)
                report["compiler_exit_code"] = result.returncode
                report["log"] = str(log_path)
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                if result.returncode != 0:
                    fail(f"compiler exited {result.returncode}; see {log_path}")
                if "Recompilation completed successfully" not in log_text:
                    fail(f"compiler log lacks completion marker; see {log_path}")
                if re.search(r"\[error\]|UnknownMMI|Unhandled (?:instruction|opcode|MMI|SPECIAL|COP2)",
                              log_text, flags=re.IGNORECASE):
                    fail(f"compiler log contains an error or UnknownMMI diagnostic; see {log_path}")
                summary = parse_summary(log_text)
                report["summary"] = summary
                bad = {key: summary[key] for key in
                       ("skipped", "decode_failures", "unhandled", "errors")
                       if summary[key] != 0}
                if summary["processed"] != summary["recompiled"] + summary["stubs"]:
                    bad["processed_partition"] = summary["processed"]
                if summary["processed"] != map_rows or summary["discovered"] != map_rows:
                    bad["symbol_map_row_count"] = summary["processed"]
                if bad:
                    fail(f"compiler quality summary is not clean: {bad}; see {log_path}")
                subprocess.run([sys.executable, str(ROOT / "scripts/patch-generated-experiments.py"),
                                "--generated-dir", str(stage), "--elf", str(elf)], check=True)
                staged = regular_files(stage)
                if not staged:
                    fail("compiler succeeded but staged no output files")
                register_name, startup_name = startup_artifact_names(stage, elf_entry)
                if register_name not in staged or Path(startup_name).stem not in staged[register_name].read_text(encoding="utf-8", errors="replace"):
                    fail(f"staged output lacks required registration/startup artifacts ({register_name}, {startup_name})")
                existing = regular_files(canonical_output) if canonical_output.exists() else {}
                changed = [name for name, path in staged.items()
                           if name not in existing or path.read_bytes() != existing[name].read_bytes()]
                stale = sorted(set(existing) - set(staged))
                report["changed_files"] = sorted(changed)
                report["changed_count"] = len(changed)
                report["stale_existing_files_retained"] = stale

                # Prepare all replacements beside their targets first. Unchanged files
                # are never touched, preserving their mtimes exactly.
                prepared = []
                for name in changed:
                    target = canonical_output / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temp_path = tempfile.mkstemp(prefix=target.name + ".regen-", dir=target.parent)
                    os.close(fd)
                    shutil.copyfile(staged[name], temp_path)
                    os.chmod(temp_path, stat.S_IMODE(staged[name].stat().st_mode))
                    prepared.append((Path(temp_path), target))
                for temp_path, target in prepared:
                    os.replace(temp_path, target)
                report["status"] = "success"
                write_report(report_path, report)
                print(json.dumps(report, indent=2, sort_keys=True))
                return 0
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
    except Exception as exc:
        report["error"] = str(exc)
        try:
            write_report(report_path, report)
        except Exception as report_exc:
            print(f"could not write report: {report_exc}", file=sys.stderr)
        print(f"regeneration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
