#!/usr/bin/env python3
"""Build a self-contained, ad-hoc-signed macOS app bundle whose main executable is the Metal runner itself.

Generates the icon, builds dist/Tarzan Freeride PS2.app, audits it (no disc image or game ELF outside
Resources/game, no Mach-O with /Users, Downloads or tools/build paths, arm64 only) and with --install
replaces /Applications/Tarzan Freeride PS2.app (previous copy goes to the Trash).
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(os.environ.get("TARZAN_NATIVE_TOOLS_ROOT") or ROOT)
RUNNER_DEFAULT = ROOT / "build/game-metal/ps2recomp/ps2xRuntime/ps2EntryRunner"
BRIDGE_DEFAULT = ROOT / "build/metal-bridge/pcsx2/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib"
BRIDGE_RESOURCES_DEFAULT = BRIDGE_DEFAULT.parent / "resources"
LLVM_LIB = NATIVE / "tools/build/llvm19/lib"
ICON_DEFAULT = ROOT / "build/AppIcon.icns"
GAME_DEFAULT = ROOT / "extracted/disc"
OUTPUT_DEFAULT = ROOT / "dist/Tarzan Freeride PS2.app"
BRIDGE_NAME = "librrv-pcsx2-gs-bridge.dylib"
EXECUTABLE = "TarzanFreeridePS2"  # the game itself: one process, no launcher


def run(args, capture=False):
    return subprocess.run([str(x) for x in args], check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        "CFBundleIconFile": "AppIcon.icns", "CFBundleIdentifier": "org.rrv.tarzan-freeride-ps2",
        "CFBundleInfoDictionaryVersion": "6.0", "CFBundleName": "Tarzan Freeride PS2",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "19", "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.games",
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


def make_icon():
    """Draw the project icon with native commands (no game artwork) into build/AppIcon.icns."""
    build = ROOT / "build"
    iconset = build / "AppIcon.iconset"
    tool = build / "package-tools/make-icon"
    png = build / "AppIcon-1024.png"
    source = ROOT / "tools/package/make-icon.m"
    if ICON_DEFAULT.is_file() and ICON_DEFAULT.stat().st_mtime >= source.stat().st_mtime:
        return
    tool.parent.mkdir(parents=True, exist_ok=True)
    iconset.mkdir(parents=True, exist_ok=True)
    run(["xcrun", "clang", "-fobjc-arc", "-framework", "AppKit", source, "-o", tool])
    run([tool, png])
    for size in (16, 32, 128, 256, 512):
        run(["sips", "-z", size, size, png, "--out", iconset / f"icon_{size}x{size}.png"], capture=True)
        run(["sips", "-z", size * 2, size * 2, png, "--out", iconset / f"icon_{size}x{size}@2x.png"], capture=True)
    run(["iconutil", "-c", "icns", iconset, "-o", ICON_DEFAULT])


MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
BAD_PATH = re.compile(r"/Users/|tools/build|[Dd]ownloads")


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
        if path.name.startswith("SLES_") and game not in path.parents:
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
    destination = applications.resolve() / "Tarzan Freeride PS2.app"
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if destination.exists():
        backup = Path.home() / ".Trash" / ("Tarzan-PS2-before-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".app")
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
    parser.add_argument("--runner", type=Path, default=RUNNER_DEFAULT,
                        help="Metal ps2EntryRunner; never falls back to build/game")
    parser.add_argument("--bridge", type=Path, default=BRIDGE_DEFAULT)
    parser.add_argument("--bridge-resources", type=Path, default=BRIDGE_RESOURCES_DEFAULT)
    parser.add_argument("--game-dir", type=Path, default=GAME_DEFAULT)
    parser.add_argument("--icon", type=Path, default=ICON_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--install", action="store_true",
                        help="move the audited bundle to /Applications (previous copy to the Trash)")
    parser.add_argument("--applications", type=Path, default=Path("/Applications"))
    parser.add_argument("--prepare-only", action="store_true",
                        help="Create a visibly incomplete app scaffold without runner or 1 GB game assets")
    args = parser.parse_args()

    if sys.platform != "darwin":
        raise RuntimeError("App bundle packaging requires macOS")
    output = args.output.resolve()
    if output.exists():
        if output.parent != OUTPUT_DEFAULT.parent:
            raise RuntimeError("refusing to overwrite existing bundle: " + str(output))
        remove_tree(output)  # dist/ holds only our own build product
    if args.icon == ICON_DEFAULT:
        make_icon()
    if not args.icon.is_file():
        raise RuntimeError("custom icon is missing; run without --icon to generate it: " + str(args.icon))
    runner = args.runner.resolve()
    if not args.prepare_only and not runner.is_file():
        raise RuntimeError("Metal runner is not ready at the expected path; build it or pass --runner. Legacy build/game is never used.")
    if args.prepare_only and args.runner and runner.exists():
        print("Scaffold mode requested; runner and game assets will not be staged.")

    app = output
    contents = app / "Contents"
    macos = contents / "MacOS"
    frameworks = contents / "Frameworks"
    resources = contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    (frameworks / "PCSX2GSBridge").mkdir(parents=True)
    (frameworks / "Runtime").mkdir(parents=True)
    write_info_plist(contents)
    copy_fresh(args.icon, resources / "AppIcon.icns")

    manifest = {"bundle": str(app), "app_icon_sha256": sha256(resources / "AppIcon.icns"),
                "release_qualification": "not established by packaging; requires actual Metal capability and gameplay reports"}
    if args.prepare_only:
        (resources / "PACKAGE_INCOMPLETE.txt").write_text(
            "Scaffold only. No game ELF or runner is bundled. This is not a runnable or release-approved app.\n",
            encoding="utf-8")
        manifest.update({"runner_bundled": False, "game_assets_bundled": False, "release_ready": False})
    else:
        bridge = args.bridge.resolve()
        bridge_resources = args.bridge_resources.resolve()
        game_dir = args.game_dir.resolve()
        game_elf = game_dir / "SLES_503.50"
        if not bridge.is_file() or not game_elf.is_file() or not bridge_resources.is_dir():
            raise RuntimeError("runner, verified Metal bridge/resources, and extracted SLES_503.50 are required")
        if "arm64" not in architectures(runner) or "arm64" not in architectures(bridge):
            raise RuntimeError("runner and bridge must contain arm64 Mach-O slices")
        runner_deps = dependencies(runner)
        if not any(BRIDGE_NAME in dep for dep in runner_deps):
            raise RuntimeError("runner does not link the PCSX2 GS Metal bridge; refusing the legacy runner")
        imports = run(["nm", "-u", runner], capture=True).stdout
        if "rrv_pcsx2_gs_bridge_create" not in imports:
            raise RuntimeError("runner does not import the PCSX2 GS bridge create API")
        if not (bridge_resources / "default.metallib").is_file():
            raise RuntimeError("bridge resources are incomplete (missing default.metallib)")
        for name in ("libc++.1.0.dylib", "libc++abi.1.0.dylib"):
            if not (LLVM_LIB / name).is_file():
                raise RuntimeError("matching LLVM 19 C++ runtime is missing: " + str(LLVM_LIB / name))

        bundled_runner = macos / EXECUTABLE
        bundled_bridge = frameworks / "PCSX2GSBridge" / BRIDGE_NAME
        runtime = frameworks / "Runtime"
        copy_fresh(runner, bundled_runner)
        copy_fresh(bridge, bundled_bridge)
        copy_fresh(LLVM_LIB / "libc++.1.0.dylib", runtime / "libc++.1.dylib")
        copy_fresh(LLVM_LIB / "libc++abi.1.0.dylib", runtime / "libc++abi.1.dylib")
        shutil.copytree(bridge_resources, frameworks / "PCSX2GSBridge/resources")
        # Metal libraries are signed code objects on macOS even though they
        # are data files rather than Mach-O images. Sign them before signing
        # the containing bridge/app so deep strict verification can seal them.
        for metallib in sorted((frameworks / "PCSX2GSBridge/resources").rglob("*.metallib")):
            run(["codesign", "--force", "--sign", "-", metallib])
        if any(path.is_symlink() for path in game_dir.rglob("*")):
            raise RuntimeError("game extraction contains a symlink; refusing unsafe bundle copy")
        shutil.copytree(game_dir, resources / "game", copy_function=shutil.copy2)
        for path in sorted((resources / "game").rglob("*"), reverse=True):
            if path.is_symlink():
                raise RuntimeError("game extraction contains a symlink; refusing unsafe bundle copy: " + str(path))
            path.chmod(0o555 if path.is_dir() else 0o444)
        (resources / "game").chmod(0o555)

        remove_nonbundle_rpaths(bundled_runner)
        remove_nonbundle_rpaths(bundled_bridge)
        for library in (runtime / "libc++.1.dylib", runtime / "libc++abi.1.dylib"):
            remove_nonbundle_rpaths(library)
            run(["install_name_tool", "-add_rpath", "@loader_path/", library])
        run(["install_name_tool", "-id", "@rpath/libc++.1.dylib", runtime / "libc++.1.dylib"])
        run(["install_name_tool", "-id", "@rpath/libc++abi.1.dylib", runtime / "libc++abi.1.dylib"])
        run(["install_name_tool", "-id", "@rpath/" + BRIDGE_NAME, bundled_bridge])
        run(["install_name_tool", "-add_rpath", "@executable_path/../Frameworks/Runtime", bundled_runner])
        run(["install_name_tool", "-add_rpath", "@executable_path/../Frameworks/PCSX2GSBridge", bundled_runner])
        run(["install_name_tool", "-add_rpath", "@loader_path/../Runtime", bundled_bridge])
        ensure_app_dependency_closure(bundled_runner, bundled_bridge, runtime, bundled_runner)
        manifest.update({
            "runner_bundled": True, "runner_sha256": sha256(bundled_runner),
            "runner_metal_bridge_link_verified": True, "bridge_sha256": sha256(bundled_bridge),
            "bridge_resources_directory": "Contents/Frameworks/PCSX2GSBridge/resources",
            "game_elf_sha256": sha256(resources / "game/SLES_503.50"),
            "game_asset_files": sum(1 for path in (resources / "game").rglob("*") if path.is_file()),
            "runtime_capabilities_verified": False, "gameplay_verified": False,
        })
    # Sign inside out only after all Mach-O edits and assets are final.
    for image in (frameworks / "Runtime/libc++abi.1.dylib", frameworks / "Runtime/libc++.1.dylib",
                  frameworks / "PCSX2GSBridge" / BRIDGE_NAME):
        if image.exists():
            run(["codesign", "--force", "--sign", "-", image])
    # The main executable is signed by the deep app signature below (signing it alone fails
    # while Frameworks resources are still unsealed).
    # GS resources live under Frameworks and need the recursive signing pass.
    # Seal the final manifest afterward without re-signing nested binaries.
    run(["codesign", "--force", "--deep", "--strict", "--sign", "-", app])
    # Record hashes of the signed binaries; signing changes their bytes.
    if not args.prepare_only:
        manifest["runner_sha256"] = sha256(bundled_runner)
        manifest["bridge_sha256"] = sha256(bundled_bridge)
    with (resources / "package-manifest.json").open("w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    run(["codesign", "--force", "--strict", "--sign", "-", app])
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", app])
    print("Prepared scaffold (incomplete)" if args.prepare_only else "Built Metal-linked app bundle; runtime qualification remains pending")
    print(output)
    problems = [] if args.prepare_only else audit_bundle(output)
    for problem in problems:
        print("audit: " + problem, file=sys.stderr)
    if problems:
        raise RuntimeError(f"bundle audit failed ({len(problems)} problems); not installing")
    if not args.prepare_only:
        print("audit: clean")
    if args.install:
        install(output, args.applications)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("package: " + str(error), file=sys.stderr)
        raise SystemExit(1)
