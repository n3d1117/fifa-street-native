#!/usr/bin/env python3
"""Assemble a runnable iOS/tvOS app from an embedded build and extracted disc."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import game

GAME = game.GAME
BRIDGE_NAME = "librrv-pcsx2-gs-bridge.dylib"
PLATFORMS = {"iphonesimulator", "iphoneos", "appletvos", "appletvsimulator"}


def run(args, capture=False):
    return subprocess.run([str(v) for v in args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def remove(path):
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def architectures(path):
    return run(["lipo", "-archs", path], True).stdout.strip().split()


def is_macho(path):
    with path.open("rb") as stream:
        return stream.read(4) in {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("platform", choices=sorted(PLATFORMS))
    args = parser.parse_args()
    platform = args.platform
    base = ROOT / "build/embedded" / platform
    build = base / "game"
    output = base / (GAME["app_name"] + ".app")
    bridge = base / "metal-bridge/pcsx2-gs-bridge" / BRIDGE_NAME
    bridge_resources = bridge.parent / "resources"
    source_game = ROOT / "extracted/disc"
    source_bundle = build / "ps2recomp/ps2xRuntime" / (GAME["executable"] + ".app")
    source_runner = source_bundle / GAME["executable"]
    if not source_runner.is_file():
        candidates = list(build.rglob(GAME["executable"]))
        candidates = [p for p in candidates if p.is_file() and p.parent.name.endswith(".app")]
        if len(candidates) == 1:
            source_runner = candidates[0]
            source_bundle = source_runner.parent
    if not source_runner.is_file() or not bridge.is_file() or not bridge_resources.is_dir():
        raise RuntimeError("embedded runner, GS bridge, and bridge resources are required")
    if not (source_game / GAME["elf"]).is_file():
        raise RuntimeError("extracted FIFA Street files are missing; run ./rx disc <iso>")
    if any(p.is_symlink() for p in source_game.rglob("*")):
        raise RuntimeError("game extraction contains a symlink; refusing unsafe app copy")

    remove(output)
    (output / "Frameworks").mkdir(parents=True)
    (output / "game").mkdir()
    shutil.copy2(source_runner, output / GAME["executable"])
    shutil.copy2(source_bundle / "Info.plist", output / "Info.plist")
    info_plist_path = output / "Info.plist"
    with info_plist_path.open("rb") as stream:
        info_plist = plistlib.load(stream)
    info_plist["CFBundleIdentifier"] = GAME["bundle_id"]
    info_plist["CFBundleName"] = GAME["app_name"]
    info_plist["CFBundleExecutable"] = GAME["executable"]
    info_plist["CFBundlePackageType"] = "APPL"
    info_plist["LSApplicationCategoryType"] = "public.app-category.sports-games"
    info_plist["LSSupportsGameMode"] = True
    with info_plist_path.open("wb") as stream:
        plistlib.dump(info_plist, stream)
    shutil.copy2(bridge, output / "Frameworks" / BRIDGE_NAME)
    runtime = ROOT / "tools/embedded" / platform / "libcxx/lib"
    for library in ("libc++.1.dylib", "libc++abi.1.dylib"):
        source = runtime / library
        if not source.is_file():
            raise RuntimeError(f"embedded C++ runtime is missing: {source}")
        shutil.copy2(source, output / "Frameworks" / library)
    shutil.copytree(bridge_resources, output / "Frameworks/resources")
    manifest = ROOT / "bridge/gs/pipelines-fifa.txt"
    if manifest.is_file():
        shutil.copy2(manifest, output / "Frameworks/resources/pipelines-fifa.txt")
    shutil.copytree(source_game, output / "game", dirs_exist_ok=True, copy_function=shutil.copy2)

    runner = output / GAME["executable"]
    bundled_bridge = output / "Frameworks" / BRIDGE_NAME
    ids = run(["otool", "-D", bundled_bridge], True).stdout.splitlines()
    if len(ids) > 1 and ids[1] != "@rpath/" + BRIDGE_NAME:
        run(["install_name_tool", "-id", "@rpath/" + BRIDGE_NAME, bundled_bridge])
    dependencies = run(["otool", "-L", runner], True).stdout.splitlines()[1:]
    for line in dependencies:
        dependency = line.strip().split(" ", 1)[0]
        if BRIDGE_NAME in dependency and dependency != "@rpath/" + BRIDGE_NAME:
            run(["install_name_tool", "-change", dependency, "@rpath/" + BRIDGE_NAME, runner])
    for path in (runner, bundled_bridge):
        refs = "\n".join(run(["otool", "-L", path], True).stdout.splitlines()[1:])
        if "/Users/" in refs or str(ROOT) in refs:
            raise RuntimeError(f"build-tree dependency in load commands for {path.name}")
    runner_load_commands = run(["otool", "-l", runner], True).stdout
    if "@executable_path/Frameworks" not in runner_load_commands:
        run(["install_name_tool", "-add_rpath", "@executable_path/Frameworks", runner])
    run(["codesign", "--force", "--deep", "--sign", "-", output])
    run(["codesign", "--verify", "--deep", "--strict", output])
    print(output)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("ios app: " + str(error), file=sys.stderr)
        raise SystemExit(1)
