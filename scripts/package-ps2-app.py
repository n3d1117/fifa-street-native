#!/usr/bin/env python3
"""Build a self-contained, ad-hoc-signed macOS app bundle for the Metal runner."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
NATIVE = Path(os.environ.get("TARZAN_NATIVE_TOOLS_ROOT", ROOT.parent / "tarzan-native"))
RUNNER_DEFAULT = ROOT / "build/game-metal/ps2recomp/ps2xRuntime/ps2EntryRunner"
BRIDGE_DEFAULT = ROOT / "build/metal-bridge/pcsx2/pcsx2-gs-bridge/librrv-pcsx2-gs-bridge.dylib"
BRIDGE_RESOURCES_DEFAULT = BRIDGE_DEFAULT.parent / "resources"
LLVM_LIB = NATIVE / "tools/build/llvm19/lib"
ICON_DEFAULT = ROOT / "build/AppIcon.icns"
GAME_DEFAULT = ROOT / "extracted/disc"
OUTPUT_DEFAULT = ROOT / "dist/Tarzan Freeride PS2.app"
BRIDGE_NAME = "librrv-pcsx2-gs-bridge.dylib"


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
        "CFBundleDevelopmentRegion": "en", "CFBundleExecutable": "TarzanFreeridePS2Launcher",
        "CFBundleIconFile": "AppIcon.icns", "CFBundleIdentifier": "org.rrv.tarzan-freeride-ps2",
        "CFBundleInfoDictionaryVersion": "6.0", "CFBundleName": "Tarzan Freeride PS2",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "16", "LSMinimumSystemVersion": "11.0",
        "LSApplicationCategoryType": "public.app-category.games",
        "NSPrincipalClass": "NSApplication",
    }
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(values, stream)


def compile_launcher(destination):
    sdk = run(["xcrun", "--show-sdk-path"], capture=True).stdout.strip()
    run(["xcrun", "--find", "clang"], capture=True)
    run(["xcrun", "clang", "-fobjc-arc", "-O2", "-mmacosx-version-min=11.0",
         "-isysroot", sdk, str(ROOT / "scripts/ps2-app-launcher.m"),
         "-framework", "Foundation", "-o", str(destination)])
    destination.chmod(0o755)


def remove_nonbundle_rpaths(image):
    for entry in rpaths(image):
        if entry.startswith("/") and not entry.startswith(("/System/Library/", "/usr/lib/")):
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
    parser.add_argument("--prepare-only", action="store_true",
                        help="Create a visibly incomplete app scaffold without runner or 1 GB game assets")
    args = parser.parse_args()

    if sys.platform != "darwin":
        raise RuntimeError("App bundle packaging requires macOS")
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("refusing to overwrite existing bundle: " + str(output))
    if not args.icon.is_file():
        raise RuntimeError("custom icon is missing; run scripts/make-icon.sh: " + str(args.icon))
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
    (frameworks / "PS2Recomp").mkdir(parents=True)
    (frameworks / "PCSX2GSBridge").mkdir(parents=True)
    (frameworks / "Runtime").mkdir(parents=True)
    write_info_plist(contents)
    copy_fresh(args.icon, resources / "AppIcon.icns")
    compile_launcher(macos / "TarzanFreeridePS2Launcher")

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

        bundled_runner = frameworks / "PS2Recomp/ps2EntryRunner"
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
        run(["install_name_tool", "-add_rpath", "@executable_path/../Runtime", bundled_runner])
        run(["install_name_tool", "-add_rpath", "@executable_path/../PCSX2GSBridge", bundled_runner])
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
                  frameworks / "PCSX2GSBridge" / BRIDGE_NAME,
                  frameworks / "PS2Recomp/ps2EntryRunner"):
        if image.exists():
            run(["codesign", "--force", "--sign", "-", image])
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
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("package-ps2-app: " + str(error), file=sys.stderr)
        raise SystemExit(1)
