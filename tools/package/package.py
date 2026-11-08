#!/usr/bin/env python3
"""Create a self-contained macOS app from the native build and extracted disc."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from game import GAME

APP_NAME = GAME["app_name"]
EXECUTABLE = GAME["executable"]
BRIDGE_NAME = "librrv-pcsx2-gs-bridge.dylib"
RUNNER = ROOT / "build/game-metal/ps2recomp/ps2xRuntime/ps2EntryRunner"
BRIDGE = ROOT / "build/metal-bridge/pcsx2/pcsx2-gs-bridge" / BRIDGE_NAME
LLVM_LIB = Path(os.environ.get("RX_LLVM") or ROOT / "tools/build/llvm19") / "lib"
OUTPUT = ROOT / "build/dist" / (APP_NAME + ".app")
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
BAD_PATH = re.compile(r"/Users/|tools/build|[Dd]ownloads")

def run(args, capture=False):
    return subprocess.run([str(x) for x in args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)

def copy_fresh(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)  # Creates a fresh destination inode; never hard-links signed inputs.
    if os.path.samefile(source, destination):
        raise RuntimeError("copy unexpectedly aliases its source inode")

def architectures(path):
    return run(["lipo", "-archs", path], capture=True).stdout.strip().split()

def dependencies(path):
    lines = run(["otool", "-L", path], capture=True).stdout.splitlines()[1:]
    return [line.strip().split(" ", 1)[0] for line in lines if line.strip()]

def rpaths(path):
    lines = run(["otool", "-l", path], capture=True).stdout.splitlines()
    result = []
    for index, line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH":
            for following in lines[index + 1:index + 4]:
                fields = following.strip().split()
                if fields and fields[0] == "path":
                    result.append(fields[1])
                    break
    return result

def ensure_app_dependency_closure(runner, bridge, runtime_dir, bundle_runner):
    image_rpaths = {
        Path(runner): rpaths(runner), Path(bridge): rpaths(bridge),
        runtime_dir / "libc++.1.dylib": rpaths(runtime_dir / "libc++.1.dylib"),
        runtime_dir / "libc++abi.1.dylib": rpaths(runtime_dir / "libc++abi.1.dylib"),
    }
    app_paths = rpaths(bundle_runner)
    images = list(image_rpaths)
    for image in images:
        for dependency in dependencies(image):
            if dependency.startswith(("/usr/lib/", "/System/Library/")):
                continue
            candidates = []
            if dependency.startswith("@loader_path/"):
                candidates.append(image.parent / dependency[len("@loader_path/"):])
            elif dependency.startswith("@executable_path/"):
                candidates.append(bundle_runner.parent / dependency[len("@executable_path/"):])
            elif dependency.startswith("@rpath/"):
                suffix = dependency[len("@rpath/"):]
                for entry in image_rpaths[image] + app_paths:
                    if entry.startswith("@loader_path/"):
                        base = image.parent / entry[len("@loader_path/"):]
                    elif entry.startswith("@executable_path/"):
                        base = bundle_runner.parent / entry[len("@executable_path/"):]
                    else:
                        base = Path(entry)
                    candidates.append(base / suffix)
            else:
                candidates.append(Path(dependency))
            if not any(candidate.exists() for candidate in candidates):
                raise RuntimeError("unbundled non-system dependency: " + str(image) + " -> " + dependency)

def write_info_plist(contents):
    values = {
        "CFBundleDevelopmentRegion": "en", "CFBundleExecutable": EXECUTABLE,
        "CFBundleIdentifier": GAME["bundle_id"],
        "CFBundleInfoDictionaryVersion": "6.0", "CFBundleName": APP_NAME,
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1", "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.games",
        "LSSupportsGameMode": True,
        "NSPrincipalClass": "NSApplication",
    }
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(values, stream)

def remove_tree(path):
    def writable(func, p, _):
        os.chmod(p, 0o700)
        func(p)
    for p in sorted(Path(path).rglob("*"), reverse=True):
        if p.is_dir() and not p.is_symlink():
            p.chmod(0o755)
    shutil.rmtree(path, onerror=writable)

def audit_bundle(app):
    """Return a list of problems; empty means the bundle is clean."""
    app = Path(app)
    game = app / "Contents/Resources/game"
    problems = []
    for path in sorted(app.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(app)
        if path.suffix.lower() == ".iso":
            problems.append(f"disc image in bundle: {rel}")
        if path.name == GAME["elf"] and game not in path.parents:
            problems.append(f"game ELF outside Resources/game: {rel}")
        with open(path, "rb") as stream:
            magic = stream.read(4)
        if magic not in MACHO_MAGIC:
            continue
        archs = architectures(path)
        if archs != ["arm64"]:
            problems.append(f"{rel}: architectures {archs}, expected arm64 only")
        for ref in dependencies(path) + rpaths(path):
            if BAD_PATH.search(ref):
                problems.append(f"{rel}: references {ref}")
    return problems

def install(source, applications):
    source = Path(source).resolve(strict=True)
    run(["codesign", "--verify", "--deep", "--strict", source])
    destination = applications.resolve() / (APP_NAME + ".app")
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if destination.exists():
        backup = Path.home() / ".Trash" / (EXECUTABLE + "-before-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".app")
        backup.parent.mkdir(parents=True, exist_ok=True)
        destination.rename(backup)
    try:
        shutil.move(str(source), str(destination))
        run(["codesign", "--verify", "--deep", "--strict", destination])
    except Exception:
        if backup and backup.exists() and not destination.exists():
            backup.rename(destination)
        raise
    run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister",
         "-f", destination])
    print("Installed:", destination)
    if backup:
        print("Previous app is recoverable in Trash:", backup)

def remove_nonbundle_rpaths(image):
    for entry in rpaths(image):
        absolute = entry.startswith("/") and not entry.startswith(("/System/Library/", "/usr/lib/"))
        if absolute or BAD_PATH.search(entry) or "../../" in entry:  # also build-tree-relative entries such as @loader_path/../../tools/build
            run(["install_name_tool", "-delete_rpath", entry, image])

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    game_dir = ROOT / "extracted/disc"
    if not RUNNER.is_file() or not BRIDGE.is_file() or not (game_dir / GAME["elf"]).is_file():
        raise RuntimeError("run ./rx disc <iso> and ./rx build first")
    if any(path.is_symlink() for path in game_dir.rglob("*")):
        raise RuntimeError("game extraction contains a symlink")
    if OUTPUT.exists():
        remove_tree(OUTPUT)
    contents = OUTPUT / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    frameworks = contents / "Frameworks"
    runtime = frameworks / "Runtime"
    for directory in (macos, resources, runtime, frameworks / "PCSX2GSBridge"):
        directory.mkdir(parents=True, exist_ok=True)
    write_info_plist(contents)
    runner = macos / EXECUTABLE
    bridge = frameworks / "PCSX2GSBridge" / BRIDGE_NAME
    copy_fresh(RUNNER, runner)
    copy_fresh(BRIDGE, bridge)
    for name in ("libc++", "libc++abi"):
        source = LLVM_LIB / (name + ".1.0.dylib")
        if not source.exists():
            source = LLVM_LIB / (name + ".1.dylib")
        copy_fresh(source, runtime / (name + ".1.dylib"))
    shutil.copytree(BRIDGE.parent / "resources", bridge.parent / "resources")
    shutil.copy2(ROOT / "bridge/gs/pipelines-fifa.txt", bridge.parent / "resources/pipelines-fifa.txt")
    shutil.copytree(game_dir, resources / "game")
    for image in (runner, bridge, *runtime.glob("*.dylib")):
        remove_nonbundle_rpaths(image)
    for image in runtime.glob("*.dylib"):
        run(["install_name_tool", "-add_rpath", "@loader_path/", image])
        run(["install_name_tool", "-id", "@rpath/" + image.name, image])
    run(["install_name_tool", "-id", "@rpath/" + BRIDGE_NAME, bridge])
    run(["install_name_tool", "-add_rpath", "@executable_path/../Frameworks/Runtime", runner])
    run(["install_name_tool", "-add_rpath", "@executable_path/../Frameworks/PCSX2GSBridge", runner])
    run(["install_name_tool", "-add_rpath", "@loader_path/../Runtime", bridge])
    ensure_app_dependency_closure(runner, bridge, runtime, runner)
    for image in (*runtime.glob("*.dylib"), bridge):
        run(["codesign", "--force", "--sign", "-", image])
    run(["codesign", "--force", "--deep", "--strict", "--sign", "-", OUTPUT])
    run(["codesign", "--verify", "--deep", "--strict", OUTPUT])
    problems = audit_bundle(OUTPUT)
    if problems:
        raise RuntimeError("bundle: " + "; ".join(problems))
    if args.install:
        install(OUTPUT, Path("/Applications"))
    else:
        print(OUTPUT)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("package: " + str(error), file=sys.stderr)
        sys.exit(1)
