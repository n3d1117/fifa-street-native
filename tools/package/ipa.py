#!/usr/bin/env python3
"""Create and audit an unsigned, freshly zipped iOS or tvOS Payload IPA."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import game

PLATFORM_INFO = {
    "iphoneos": ("ios", 2), "iphonesimulator": ("ios", 7),
    "appletvos": ("tvos", 3), "appletvsimulator": ("tvos", 8),
}
MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def run(args):
    return subprocess.run([str(x) for x in args], check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def macho(path):
    with path.open("rb") as stream:
        return stream.read(4) in MAGIC


def load_commands(path):
    return run(["otool", "-l", path])


def audit(app, expected_platform):
    errors = []
    for path in sorted(app.rglob("*")):
        if path.is_symlink() or not path.is_file() or not macho(path):
            continue
        archs = run(["lipo", "-archs", path]).strip().split()
        if archs != ["arm64"]:
            errors.append(f"{path.relative_to(app)} has architectures {archs}")
        commands = load_commands(path)
        if re.search(r"(?:^|\s)/Users/", commands, re.M):
            errors.append(f"{path.relative_to(app)} contains an absolute /Users load path")
        if "/Users/" in run(["otool", "-L", path]):
            errors.append(f"{path.relative_to(app)} has an absolute /Users dependency")
        for match in re.finditer(r"cmd LC_BUILD_VERSION\s+platform (\d+)", commands):
            if int(match.group(1)) != expected_platform:
                errors.append(f"{path.relative_to(app)} LC_BUILD_VERSION platform {match.group(1)}, expected {expected_platform}")
        if "LC_BUILD_VERSION" not in commands:
            errors.append(f"{path.relative_to(app)} has no LC_BUILD_VERSION platform")
        for match in re.finditer(r"cmd LC_RPATH\s+path (\S+)", commands):
            if match.group(1).startswith("/Users/"):
                errors.append(f"{path.relative_to(app)} has absolute rpath {match.group(1)}")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", choices=sorted(PLATFORM_INFO))
    args = parser.parse_args()
    flavor, expected = PLATFORM_INFO[args.platform]
    base = ROOT / "build/embedded" / args.platform
    app = base / (game.GAME["app_name"] + ".app")
    output = base / f"FIFA-Street-PS2-{flavor}.ipa"
    if not app.is_dir():
        raise RuntimeError("assembled app missing; run tools/package/ios_app.py first")

    remove = output
    if remove.exists():
        remove.unlink()
    with tempfile.TemporaryDirectory(prefix="rx-ipa-") as temporary:
        payload = Path(temporary) / "Payload"
        staged = payload / app.name
        payload.mkdir()
        shutil.copytree(app, staged, symlinks=False)
        for signature in list(staged.rglob("_CodeSignature")):
            shutil.rmtree(signature)
        for path in sorted(p for p in staged.rglob("*") if p.is_file() and not p.is_symlink()):
            if macho(path):
                subprocess.run(["codesign", "--remove-signature", path], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if any(p.name == "_CodeSignature" for p in staged.rglob("*")):
            raise RuntimeError("signature directory remained in unsigned IPA staging")
        errors = audit(staged, expected)
        if errors:
            raise RuntimeError("IPA audit failed: " + "; ".join(errors))
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(payload.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(Path(temporary)))
    print(f"{output}\t{output.stat().st_size}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("ipa: " + str(error), file=sys.stderr)
        raise SystemExit(1)
