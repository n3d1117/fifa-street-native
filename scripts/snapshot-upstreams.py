#!/usr/bin/env python3
"""Snapshot local upstream working-tree changes without staging or committing.

Tracked text changes are exported as ordinary git patches. Small, untracked
source files are copied under patches/upstreams/<id>/ and listed in the
manifest. The source checkouts and their indexes are read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "patches"
MAX_UNTRACKED_BYTES = 1024 * 1024
TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
    ".m", ".mm", ".inc", ".in", ".cmake", ".py", ".sh", ".txt",
    ".md", ".json", ".toml", ".yaml", ".yml", ".xml", ".ini", ".cfg",
    ".S", ".s", ".asm", ".metal", ".vert", ".frag", ".glsl", ".plist",
}
TEXT_NAMES = {"CMakeLists.txt", "Makefile", "GNUmakefile"}
GENERATED_DIRS = {
    ".git", "build", "builds", "cmake-build-debug", "cmake-build-release",
    "generated", "out", "dist", "obj", "bin", "logs", "coverage",
    "deriveddata", "__pycache__",
}
REPOSITORIES = (
    {
        "id": "ps2recomp-metal",
        "path": "source/PS2Recomp-metal",
        "url": "https://github.com/ran-j/PS2Recomp.git",
        "patch": "patches/ps2recomp-metal.patch",
        # Local commits sit on branch "tarzan"; the patch is taken against the
        # real upstream base, not against the local HEAD.
        "base": "2c5fbb9389e11dd95693385969490c9e8e6f57b4",
    },
    {
        "id": "fukami",
        "path": "source/fukami",
        "url": "https://github.com/danisandoval/fukami.git",
        "patch": "patches/fukami.patch",
    },
    {
        "id": "pcsx2-metal",
        "path": "source/deps/pcsx2-2.8.2-bridge",
        "url": "https://github.com/PCSX2/pcsx2.git",
        "patch": "patches/pcsx2-metal.patch",
    },
)


def git(repo: Path, *args: str, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed in {repo}: {message}")
    return result.stdout


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_allowed_text_path(path: str) -> bool:
    posix = PurePosixPath(path)
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        return False
    if any(part.lower() in GENERATED_DIRS for part in posix.parts):
        return False
    return posix.name in TEXT_NAMES or posix.suffix in TEXT_SUFFIXES


def tracked_patch(repo: Path, base: str = "HEAD") -> tuple[bytes, list[str]]:
    raw_paths = git(repo, "diff", "--no-renames", "--name-only", "-z", base, "--")
    changed = [os.fsdecode(item) for item in raw_paths.split(b"\0") if item]
    allowed: list[str] = []
    skipped: list[str] = []
    for path in changed:
        if not is_allowed_text_path(path):
            skipped.append(path)
            continue
        # A source-named path can still contain binary data. Git reports binary
        # numstat as "-\t-"; such content is never written to the patch.
        numstat = git(repo, "diff", "--no-renames", "--numstat", base, "--", f":(literal){path}")
        if numstat.startswith(b"-\t-\t"):
            skipped.append(path)
            continue
        allowed.append(path)
    if not allowed:
        return b"", skipped
    patch = git(repo, "diff", "--no-ext-diff", "--no-renames", "--no-color",
                "--full-index", base, "--", *(f":(literal){path}" for path in allowed))
    return patch, skipped


def untracked_sources(repo: Path) -> list[dict[str, object]]:
    raw_paths = git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    entries: list[dict[str, object]] = []
    for raw_path in raw_paths.split(b"\0"):
        if not raw_path:
            continue
        rel = os.fsdecode(raw_path)
        source = repo / rel
        if not is_allowed_text_path(rel) or source.is_symlink() or not source.is_file():
            continue
        size = source.stat().st_size
        if size > MAX_UNTRACKED_BYTES:
            print(f"skip oversized untracked file ({size} bytes): {rel}", file=sys.stderr)
            continue
        data = source.read_bytes()
        if b"\0" in data:
            print(f"skip binary untracked file: {rel}", file=sys.stderr)
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            print(f"skip non-UTF-8 untracked file: {rel}", file=sys.stderr)
            continue
        snapshot = f"patches/upstreams/{repo_id_for_path(repo)}/{rel}"
        entries.append({
            "path": rel,
            "snapshot": snapshot,
            "sha256": sha256(data),
            "mode": format(stat.S_IMODE(source.stat().st_mode), "04o"),
        })
    return entries


def repo_id_for_path(repo: Path) -> str:
    relative = repo.resolve().relative_to(ROOT.resolve()).as_posix()
    for spec in REPOSITORIES:
        if spec["path"] == relative:
            return str(spec["id"])
    raise RuntimeError(f"unregistered upstream path: {relative}")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def collect_snapshot() -> tuple[list[dict[str, object]], list[tuple[Path, bytes]]]:
    manifest_repos: list[dict[str, object]] = []
    writes: list[tuple[Path, bytes]] = []
    for spec in REPOSITORIES:
        repo = ROOT / str(spec["path"])
        if not (repo / ".git").exists():
            raise RuntimeError(f"upstream checkout is missing or not a Git repo: {spec['path']}")
        revision = str(spec.get("base") or git(repo, "rev-parse", "HEAD").decode().strip())
        patch, skipped = tracked_patch(repo, revision)
        if skipped:
            for path in skipped:
                print(f"skip non-source or binary tracked change: {spec['path']}/{path}", file=sys.stderr)
        patch_rel = str(spec["patch"])
        writes.append((ROOT / patch_rel, patch))
        untracked = untracked_sources(repo)
        for entry in untracked:
            source = repo / str(entry["path"])
            writes.append((ROOT / str(entry["snapshot"]), source.read_bytes()))
        manifest_repos.append({
            "id": spec["id"],
            "path": spec["path"],
            "url": spec["url"],
            "base_revision": revision,
            "patch": patch_rel,
            "patch_sha256": sha256(patch),
            "untracked": untracked,
        })
        print(f"{spec['id']}: {revision[:12]}, {len(patch)} patch bytes, "
              f"{len(untracked)} untracked source files")
    manifest = {
        "schema_version": 1,
        "repositories": manifest_repos,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return manifest_repos, writes + [(PATCHES / "upstreams.json", manifest_bytes)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="inspect revisions and planned snapshot sizes without writing files")
    args = parser.parse_args()
    try:
        _manifest, writes = collect_snapshot()
        if args.dry_run:
            print(f"dry run: would write {len(writes)} files under patches/")
            return 0
        for path, data in writes:
            atomic_write(path, data)
        print("Upstream snapshots written; source checkouts and Git indexes were not changed.")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"snapshot failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
