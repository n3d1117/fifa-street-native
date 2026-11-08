#!/usr/bin/env python3
"""Generate native C++ from the verified game ELF."""
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from typing import Dict, Tuple

from game import CONFIG, ROOT

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

def regular_files(root: Path) -> Dict[str, Path]:
    result: Dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            fail(f"staged output contains symlink: {path.relative_to(root)}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path
    return result

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

def main():
    compiler = ROOT / "build/game-metal/ps2recomp/ps2xRecomp/ps2_recomp"
    text = CONFIG.read_text()
    input_name, _ = get_general_string(text, "input")
    output_name, _ = get_general_string(text, "output")
    elf = Path(input_name)
    output = Path(output_name)
    if not elf.is_absolute():
        elf = CONFIG.parent / elf
    if not output.is_absolute():
        output = CONFIG.parent / output
    output = output.resolve()
    if ROOT not in output.parents:
        fail("generated output must be inside the checkout")
    with elf.open("rb") as stream:
        header = stream.read(28)
    if len(header) != 28 or header[:6] != b"\x7fELF\x01\x01":
        fail("expected a little-endian ELF32 input")
    entry = struct.unpack_from("<I", header, 24)[0]
    with tempfile.TemporaryDirectory(prefix="fifa-generate-") as temporary:
        temporary = Path(temporary)
        stage = temporary / "output"
        stage.mkdir()
        config = temporary / "config.toml"
        config.write_text(replace_output(text, stage))
        result = subprocess.run([str(compiler), str(config)], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if result.returncode:
            fail(result.stdout)
        summary = parse_summary(result.stdout)
        if any(summary[k] for k in ("skipped", "decode_failures", "unhandled", "errors")):
            fail(result.stdout)
        if not summary["discovered"] or summary["processed"] != summary["discovered"]:
            fail("recompiler did not process every discovered function")
        validate_required_artifacts(stage, entry)
        staged = regular_files(stage)
        existing = regular_files(output) if output.exists() else {}
        for name, source in staged.items():
            destination = output / name
            if name in existing and existing[name].read_bytes() == source.read_bytes():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            replacement = destination.with_name(destination.name + ".tmp")
            shutil.copyfile(source, replacement)
            os.replace(replacement, destination)
        for name in set(existing) - set(staged):
            existing[name].unlink()
    print("Generated native sources:", output)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("generate: " + str(error), file=sys.stderr)
        sys.exit(1)
