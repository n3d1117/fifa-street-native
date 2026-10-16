#!/usr/bin/env python3
"""Prepare pinned PCSX2 source and provenance-checked v2.8.2 Metal shaders."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIN = "fd9d310ccbb6b8b62c976da8886a3c8fd3a10ff3"
TAG = "v2.8.2"
ASSET = "pcsx2-v2.8.2-macos-Qt.tar.xz"
ASSET_SHA256 = "3ed9eb40a33eae67134142c24255a079be444f0616ca29575a04a37981f7d426"
ASSET_SIZE = 24_377_200
FILES = ("default.metallib", "Metal22.metallib", "Metal23.metallib")
API = f"https://api.github.com/repos/PCSX2/pcsx2/releases/tags/{TAG}"
URL = f"https://github.com/PCSX2/pcsx2/releases/download/{TAG}/{ASSET}"


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> int:
    deps = ROOT / "source/deps"
    archive_dir = ROOT / "build/metal-bridge"
    resource_dir = archive_dir / "pcsx2-2.8.2-resources"
    source_dir = deps / "pcsx2-2.8.2"
    archive_dir.mkdir(parents=True, exist_ok=True)
    deps.mkdir(parents=True, exist_ok=True)

    # Establish asset identity from GitHub's immutable-release API before using bytes.
    req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "tarzan-metal-bridge-preparer"})
    with urllib.request.urlopen(req, timeout=30) as response:
        release = json.load(response)
    if release.get("tag_name") != TAG or release.get("immutable") is not True:
        raise SystemExit(f"Refusing non-immutable or unexpected release: {release.get('tag_name')}")
    assets = [a for a in release.get("assets", []) if a.get("name") == ASSET]
    if len(assets) != 1:
        raise SystemExit(f"Expected exactly one {ASSET} in official v2.8.2 release")
    asset = assets[0]
    if asset.get("size") != ASSET_SIZE or asset.get("digest") != f"sha256:{ASSET_SHA256}" or asset.get("browser_download_url") != URL:
        raise SystemExit("Official GitHub release asset metadata differs from the reviewed pin; refusing download")

    archive = archive_dir / ASSET
    if not archive.exists() or archive.stat().st_size != ASSET_SIZE or digest(archive) != ASSET_SHA256:
        partial = archive.with_suffix(archive.suffix + ".part")
        partial.unlink(missing_ok=True)
        req = urllib.request.Request(URL, headers={"User-Agent": "tarzan-metal-bridge-preparer"})
        with urllib.request.urlopen(req, timeout=60) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
        if partial.stat().st_size != ASSET_SIZE or digest(partial) != ASSET_SHA256:
            partial.unlink(missing_ok=True)
            raise SystemExit("Downloaded PCSX2 archive failed official size/SHA-256 verification")
        partial.replace(archive)

    resource_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:xz") as tf:
        members = {}
        for member in tf.getmembers():
            if pathlib.PurePosixPath(member.name).name in FILES and ".app/Contents/Resources/" in member.name:
                name = pathlib.PurePosixPath(member.name).name
                if name in members or not member.isfile():
                    raise SystemExit(f"Ambiguous or invalid Metal resource in release: {member.name}")
                members[name] = member
        if set(members) != set(FILES):
            raise SystemExit(f"Pinned release lacks required Metal resources: {sorted(set(FILES) - set(members))}")
        for name, member in members.items():
            src = tf.extractfile(member)
            if src is None:
                raise SystemExit(f"Cannot read release member {member.name}")
            out = resource_dir / name
            with out.open("wb") as f:
                shutil.copyfileobj(src, f)

    manifest = {
        "source": "https://github.com/PCSX2/pcsx2/releases/tag/v2.8.2",
        "release_tag": TAG,
        "release_id": release["id"],
        "release_immutable": release["immutable"],
        "release_asset_id": asset["id"],
        "release_asset_name": ASSET,
        "release_asset_size": ASSET_SIZE,
        "release_asset_api_digest": asset["digest"],
        "download_url": URL,
        "archive_sha256": digest(archive),
        "pcsx2_source_revision": PIN,
        "resources": {n: {"sha256": digest(resource_dir / n), "size": (resource_dir / n).stat().st_size} for n in FILES},
    }
    (archive_dir / "metal-resources-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    if source_dir.exists():
        got = run("git", "-C", str(source_dir), "rev-parse", "HEAD") if (source_dir / ".git").exists() else ""
        if got != PIN or run("git", "-C", str(source_dir), "status", "--porcelain"):
            raise SystemExit(f"Existing source checkout is not clean at {PIN}: {source_dir}")
    else:
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", "https://github.com/PCSX2/pcsx2.git", str(source_dir)], check=True)
        subprocess.run(["git", "-C", str(source_dir), "checkout", "--detach", PIN], check=True)
    if run("git", "-C", str(source_dir), "rev-parse", "HEAD") != PIN:
        raise SystemExit(f"PCSX2 checkout does not match locked commit {PIN}")
    print(f"verified resources: {resource_dir}")
    print(f"release provenance: {archive_dir / 'metal-resources-manifest.json'}")
    print(f"clean pinned PCSX2 source: {source_dir} ({PIN})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print(f"prepare-metal-bridge: {exc}", file=sys.stderr)
        sys.exit(1)
