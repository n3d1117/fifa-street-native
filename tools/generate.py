#!/usr/bin/env python3
"""Regenerate the game C++ from the canonical PS2Recomp config, safely.

Compiles into a temporary directory (pure recompiler output), checks structural quality (0 skipped, 0 decode failures, 0 unhandled
instructions, 0 errors, entry registered, processed == discovered), compares the inventory with the
previous run (build/generate-inventory.json) and only then atomically replaces
the files that changed. Unchanged files keep their mtimes.

  tools/generate.py                 regenerate generated/<id> (config/game.json)
  tools/generate.py --out DIR       same, into DIR (for comparing against generated/)
  tools/generate.py --dry-run       validate inputs only

Exit codes: 0 ok, 1 failed, 2 missing prerequisite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

from game import CONFIG as DEFAULT_CONFIG, ROOT

DEFAULT_COMPILER = ROOT / "build/game-metal/ps2recomp/ps2xRecomp/ps2_recomp"
INVENTORY = ROOT / "build/generate-inventory.json"


class Missing(Exception):
    """Missing prerequisite (exit 2)."""


def inside(path: Path, root: Path) -> bool:
    """Containment check that ignores case (macOS paths differ only by case)."""
    p, r = str(path).casefold(), str(root).casefold()
    return p != r and p.startswith(r.rstrip("/") + "/")


# ---- generation -------------------------------------------------------------
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



def stub_count(stage: Path) -> int:
    path = stage / "ps2_recompiled_stubs.h"
    return len(set(re.findall(r"void \w+_0x([0-9a-f]+)\(", path.read_text(errors="replace")))) if path.is_file() else 0


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--compiler", type=Path, default=DEFAULT_COMPILER)
    parser.add_argument("--out", type=Path, help="output directory (default: [general].output of the config)")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/regenerate-game.json")
    parser.add_argument("--dry-run", action="store_true", help="validate inputs without running the compiler")
    args = parser.parse_args()

    config = args.config.resolve()
    compiler = args.compiler.resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = ROOT / "build/logs" / f"generate-compiler-{timestamp}.log"
    report = {"config": str(config), "compiler": str(compiler), "log": str(log_path),
              "changed_count": 0, "changed_files": [], "status": "validation_failed"}
    try:
        if not config.is_file():
            raise Missing(f"config not found: {config} -> ./rx disc")
        if not compiler.is_file() or not os.access(compiler, os.X_OK):
            raise Missing(f"recompiler missing: {compiler} -> ./rx build")
        original_text = config.read_text(encoding="utf-8")
        input_value, _ = get_general_string(original_text, "input")
        output_value, _ = get_general_string(original_text, "output")
        elf = Path(input_value)
        if not elf.is_absolute():
            elf = config.parent / elf
        elf = elf.resolve()
        if not elf.is_file():
            raise Missing(f"ELF input not found: {elf} -> ./rx disc <iso>")
        elf_head = elf.open("rb").read(28)
        if len(elf_head) < 28 or elf_head[:6] != b"\x7fELF\x01\x01":
            fail("expected a little-endian ELF32 input")
        elf_entry = struct.unpack_from("<I", elf_head, 24)[0]
        canonical_output = args.out if args.out else Path(output_value)
        if not canonical_output.is_absolute():
            canonical_output = (config.parent if not args.out else Path.cwd()) / canonical_output
        canonical_output = canonical_output.resolve()
        if not (inside(canonical_output, ROOT) or args.out):
            fail(f"refusing output directory outside project: {canonical_output}")
        report.update(output=str(canonical_output), elf_sha256=sha256_file(elf),
                      config_sha256=sha256_file(config), compiler_sha256=sha256_file(compiler))
        if args.dry_run:
            report["status"] = "dry_run_validated"
            print("dry run OK: inputs valid")
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
            with tempfile.TemporaryDirectory(prefix="game-regen-") as temp_name:
                temp_root = Path(temp_name)
                stage = temp_root / "generated"
                stage.mkdir()
                staged_config = temp_root / "config.toml"
                staged_config.write_text(replace_output(original_text, stage), encoding="utf-8")
                with log_path.open("w", encoding="utf-8") as log:
                    result = subprocess.run([str(compiler), str(staged_config)], cwd=ROOT,
                                            stdout=log, stderr=subprocess.STDOUT, check=False)
                log_text = log_path.read_text(encoding="utf-8", errors="replace")
                if result.returncode != 0:
                    fail(f"compiler exited {result.returncode}; see {log_path}")
                if "Recompilation completed successfully" not in log_text:
                    fail(f"compiler log lacks completion marker; see {log_path}")
                if re.search(r"\[error\]|UnknownMMI|Unhandled (?:instruction(?!s: \d)|opcode|MMI|SPECIAL|COP2)",
                             log_text, flags=re.IGNORECASE):
                    fail(f"compiler log contains an error or UnknownMMI diagnostic; see {log_path}")
                summary = parse_summary(log_text)
                bad = {k: summary[k] for k in ("skipped", "decode_failures", "unhandled", "errors") if summary[k] != 0}
                if summary["processed"] != summary["recompiled"] + summary["stubs"]:
                    bad["processed_partition"] = summary["processed"]
                if summary["processed"] != summary["discovered"] or summary["discovered"] == 0:
                    bad["discovered_vs_processed"] = summary["processed"]
                if bad:
                    fail(f"compiler quality summary is not clean: {bad}; see {log_path}")
                staged = regular_files(stage)
                if not staged:
                    fail("compiler succeeded but staged no output files")
                register_name, startup_name = startup_artifact_names(stage, elf_entry)
                if register_name not in staged or Path(startup_name).stem not in staged[register_name].read_text(encoding="utf-8", errors="replace"):
                    fail(f"staged output lacks required registration/startup artifacts ({register_name}, {startup_name})")

                inventory = {"elf_sha256": report["elf_sha256"], "discovered": summary["discovered"],
                             "translated": summary["recompiled"], "hle_bound_stubs": summary["stubs"],
                             "stub_declarations": stub_count(stage), "skipped": summary["skipped"],
                             "decode_failures": summary["decode_failures"]}
                report["inventory"] = inventory
                previous = None
                if INVENTORY.is_file():
                    try:
                        previous = json.loads(INVENTORY.read_text())
                    except ValueError:
                        previous = None
                if previous and previous.get("elf_sha256") == inventory["elf_sha256"]:
                    if inventory["translated"] < previous["translated"]:
                        fail(f"translated count dropped {previous['translated']} -> {inventory['translated']} "
                             f"(delete {INVENTORY} only if intentional)")
                    if inventory["skipped"] > previous.get("skipped", 0):
                        fail(f"skipped functions appeared: {inventory['skipped']}")

                existing = regular_files(canonical_output) if canonical_output.exists() else {}
                changed = [n for n, p in staged.items()
                           if n not in existing or p.read_bytes() != existing[n].read_bytes()]
                report["changed_files"] = sorted(changed)
                report["changed_count"] = len(changed)
                # Prepare every replacement beside its target, then rename them all.
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
                stale_removed = sorted(set(existing) - set(staged))
                report["stale_existing_files_removed"] = stale_removed if not args.out else []
                if not args.out:
                    for name in stale_removed:
                        (canonical_output / name).unlink()
                if not args.out:
                    write_json(INVENTORY, inventory)
                report["status"] = "success"
                write_json(args.report.resolve(), report)
                print(f"generated: discovered {summary['discovered']}, translated {summary['recompiled']}, HLE-bound stubs {summary['stubs']}, "
                      f"skipped {summary['skipped']}, errors {summary['errors']}; "
                      f"{len(changed)} files changed")
                return 0
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
    except Missing as exc:
        print(exc, file=sys.stderr)
        return 2
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        report["error"] = str(exc)
        try:
            write_json(args.report.resolve(), report)
        except Exception as report_exc:
            print(f"could not write report: {report_exc}", file=sys.stderr)
        print(f"generate failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
