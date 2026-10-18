#!/usr/bin/env python3
"""Restore pinned upstream source trees and this repo's local patches."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "patches/upstreams.json"
REPOSITORIES = {
    "ps2recomp-metal": ("source/PS2Recomp-metal", "https://github.com/ran-j/PS2Recomp.git"),
    "fukami": ("source/fukami", "https://github.com/danisandoval/fukami.git"),
    "pcsx2-metal": ("source/deps/pcsx2-2.8.2-bridge", "https://github.com/PCSX2/pcsx2.git"),
}


def run(args: list[str], *, cwd: Path | None = None, check: bool = True) -> bytes:
    result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"{' '.join(args)} failed: {detail}")
    return result.stdout


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeError(f"invalid relative {label}: {value!r}")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in ("", ".", "..") for part in parsed.parts):
        raise RuntimeError(f"unsafe relative {label}: {value!r}")
    return Path(*parsed.parts)


def read_manifest() -> list[dict[str, object]]:
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {MANIFEST.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("repositories"), list):
        raise RuntimeError("unsupported or malformed upstream manifest")
    entries = data["repositories"]
    if (len(entries) != len(REPOSITORIES) or
            {item.get("id") for item in entries if isinstance(item, dict)} != set(REPOSITORIES)):
        raise RuntimeError("manifest must contain exactly the three registered upstreams")
    return entries


def validate_entry(entry: dict[str, object]) -> tuple[str, Path, str, Path, str, list[dict[str, object]]]:
    repo_id = str(entry.get("id"))
    rel_path, url = REPOSITORIES[repo_id]
    if entry.get("path") != rel_path or entry.get("url") != url:
        raise RuntimeError(f"manifest path or upstream URL differs for {repo_id}")
    revision = entry.get("base_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise RuntimeError(f"invalid pinned revision for {repo_id}")
    patch_rel = safe_relative(entry.get("patch"), f"patch path for {repo_id}")
    expected_patch = Path("patches") / f"{repo_id}.patch"
    if patch_rel != expected_patch:
        raise RuntimeError(f"unexpected patch path for {repo_id}")
    patch_hash = entry.get("patch_sha256")
    if not isinstance(patch_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", patch_hash):
        raise RuntimeError(f"invalid patch SHA-256 for {repo_id}")
    untracked = entry.get("untracked")
    if not isinstance(untracked, list):
        raise RuntimeError(f"invalid untracked-file list for {repo_id}")
    for item in untracked:
        if not isinstance(item, dict):
            raise RuntimeError(f"invalid untracked entry for {repo_id}")
        source_rel = safe_relative(item.get("path"), f"source path for {repo_id}")
        snapshot_rel = safe_relative(item.get("snapshot"), f"snapshot path for {repo_id}")
        if snapshot_rel != Path("patches/upstreams") / repo_id / source_rel:
            raise RuntimeError(f"unexpected snapshot path for {repo_id}: {snapshot_rel}")
        if source_rel.suffix not in {
            ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
            ".m", ".mm", ".inc", ".in", ".cmake", ".py", ".sh", ".txt",
            ".md", ".json", ".toml", ".yaml", ".yml", ".xml", ".ini", ".cfg",
            ".S", ".s", ".asm", ".metal", ".vert", ".frag", ".glsl", ".plist",
        } and source_rel.name not in {"CMakeLists.txt", "Makefile", "GNUmakefile"}:
            raise RuntimeError(f"unapproved untracked source suffix: {source_rel}")
        if any(part.lower() in {
                ".git", "build", "builds", "cmake-build-debug", "cmake-build-release",
                "generated", "out", "dist", "obj", "bin", "logs", "coverage",
                "deriveddata", "__pycache__",
        } for part in source_rel.parts):
            raise RuntimeError(f"unapproved generated source path: {source_rel}")
        if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise RuntimeError(f"invalid untracked-file SHA-256 for {repo_id}")
    return repo_id, Path(rel_path), revision, patch_rel, patch_hash, untracked


def load_patch(path: Path, expected_hash: str) -> bytes:
    try:
        patch = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"missing patch {path.relative_to(ROOT)}: {exc}") from exc
    if sha256(patch) != expected_hash:
        raise RuntimeError(f"patch SHA-256 mismatch: {path.relative_to(ROOT)}")
    return patch


def snapshot_data(entry: dict[str, object]) -> tuple[Path, bytes, str]:
    source_rel = safe_relative(entry["path"], "upstream source path")
    snapshot_rel = safe_relative(entry["snapshot"], "snapshot path")
    source_name = source_rel.as_posix()
    snapshot_prefix = Path("patches/upstreams")
    if snapshot_rel.parts[:2] != snapshot_prefix.parts:
        raise RuntimeError(f"snapshot is outside patches/upstreams: {snapshot_rel}")
    try:
        data = (ROOT / snapshot_rel).read_bytes()
    except OSError as exc:
        raise RuntimeError(f"missing untracked snapshot {snapshot_rel}: {exc}") from exc
    if sha256(data) != entry["sha256"]:
        raise RuntimeError(f"untracked snapshot SHA-256 mismatch: {snapshot_rel}")
    if len(data) > 1024 * 1024 or b"\0" in data:
        raise RuntimeError(f"untracked source is oversized or binary: {snapshot_rel}")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"untracked source is not UTF-8 text: {snapshot_rel}") from exc
    mode = entry.get("mode", "0644")
    if not isinstance(mode, str) or not re.fullmatch(r"0[0-7]{3}", mode):
        raise RuntimeError(f"invalid mode for untracked source {source_name}")
    return source_rel, data, mode


def reject_symlink_parents(repo: Path, target: Path) -> None:
    current = target.parent
    while current != repo and repo in current.parents:
        if current.is_symlink():
            raise RuntimeError(f"refusing to follow symlink directory: {current.relative_to(ROOT)}")
        current = current.parent


def verify_restored(repo: Path, revision: str, patch_path: Path, patch: bytes,
                    untracked: list[dict[str, object]]) -> None:
    if not (repo / ".git").exists():
        raise RuntimeError(f"Git checkout is missing: {repo.relative_to(ROOT)}")
    actual_revision = run(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    if actual_revision != revision:
        raise RuntimeError(f"{repo.relative_to(ROOT)} is at {actual_revision}, expected {revision}")
    if patch:
        reverse = subprocess.run(["git", "-C", str(repo), "apply", "--reverse", "--check", str(patch_path)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if reverse.returncode:
            detail = reverse.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"patch is not applied cleanly in {repo.relative_to(ROOT)}: {detail}")
    for item in untracked:
        source_rel, data, _mode = snapshot_data(item)
        target = repo / source_rel
        reject_symlink_parents(repo, target)
        if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
            raise RuntimeError(f"restored untracked source differs: {repo.relative_to(ROOT) / source_rel}")


def restore_one(entry: dict[str, object], check_only: bool) -> None:
    repo_id, repo_rel, revision, patch_rel, patch_hash, untracked = validate_entry(entry)
    repo = ROOT / repo_rel
    patch_path = ROOT / patch_rel
    patch = load_patch(patch_path, patch_hash)
    # Validate every bundle file before cloning or applying anything.
    bundle = [(snapshot_data(item), item) for item in untracked]
    if check_only:
        verify_restored(repo, revision, patch_path, patch, untracked)
        print(f"OK {repo_id}: revision, patch, and {len(untracked)} untracked files match")
        return

    if repo.exists():
        if not (repo / ".git").exists():
            raise RuntimeError(f"refusing to replace existing non-Git path: {repo.relative_to(ROOT)}")
        actual_revision = run(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
        if actual_revision != revision:
            raise RuntimeError(f"refusing existing checkout at {actual_revision}: {repo.relative_to(ROOT)}")
        try:
            verify_restored(repo, revision, patch_path, patch, untracked)
            print(f"unchanged {repo_id}: already restored")
            return
        except RuntimeError:
            pass
        status = run(["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all"])
        if status:
            raise RuntimeError(f"refusing to modify dirty checkout: {repo.relative_to(ROOT)}")
        for source_rel, data, _mode in (item[0] for item in bundle):
            target = repo / source_rel
            reject_symlink_parents(repo, target)
            if target.is_symlink() or (target.exists() and
                    (not target.is_file() or target.read_bytes() != data)):
                raise RuntimeError(f"refusing to overwrite existing file: {repo_rel / source_rel}")
    else:
        repo.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", str(entry["url"]), str(repo)])
        run(["git", "-C", str(repo), "checkout", "--detach", revision])
        actual_revision = run(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
        if actual_revision != revision:
            raise RuntimeError(f"clone produced {actual_revision}, expected {revision}")

    if patch:
        checked = subprocess.run(["git", "-C", str(repo), "apply", "--check", str(patch_path)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if checked.returncode:
            detail = checked.stderr.decode("utf-8", "replace").strip()
            raise RuntimeError(f"patch does not apply to {repo_id}: {detail}")
        run(["git", "-C", str(repo), "apply", str(patch_path)])

    for (source_rel, data, mode), _item in bundle:
        target = repo / source_rel
        reject_symlink_parents(repo, target)
        if target.is_symlink():
            raise RuntimeError(f"refusing to overwrite symlink: {repo_rel / source_rel}")
        if target.exists():
            if not target.is_file() or target.read_bytes() != data:
                raise RuntimeError(f"refusing to overwrite existing file: {repo_rel / source_rel}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        os.chmod(target, int(mode, 8))
    verify_restored(repo, revision, patch_path, patch, untracked)
    print(f"restored {repo_id}: {revision[:12]}, {len(untracked)} untracked files")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="verify existing checkouts, applied patches, and bundled files without changing anything")
    args = parser.parse_args()
    try:
        entries = read_manifest()
        for entry in entries:
            if not isinstance(entry, dict):
                raise RuntimeError("malformed repository entry")
            restore_one(entry, args.check)
        return 0
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
