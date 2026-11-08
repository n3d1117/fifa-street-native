#!/usr/bin/env python3
"""Fetch pinned dependencies and apply the native platform source patches."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def git(path, *args, capture=False):
    result = subprocess.run(["git", "-C", str(path), *args], check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout


def sync(name, url, pin):
    path = ROOT / "deps" / name
    patch = ROOT / "patches" / (name + ".patch")
    if (path / ".git").exists():
        if git(path, "status", "--porcelain", capture=True).strip():
            raise RuntimeError(f"deps/{name} has local edits")
        actual = git(path, "diff", "--binary", "--full-index", pin, "HEAD", capture=True)
        if actual != patch.read_bytes():
            raise RuntimeError(f"deps/{name} differs from the pinned patch; move that checkout aside and rerun setup")
        print(name + ": ready")
        return
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "remote", "add", "origin", url)
    git(path, "fetch", "-q", "--depth", "1", "origin", pin)
    git(path, "checkout", "-q", "-b", "rx", pin)
    git(path, "apply", "--index", str(patch))
    git(path, "-c", "user.name=Native port", "-c", "user.email=build@localhost",
        "commit", "-q", "-m", "Native Apple platform integration")
    print(name + ": ready")


def main():
    for key in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_PREFIX"):
        os.environ.pop(key, None)
    for line in (ROOT / "deps.lock").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            sync(*line.split())


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("deps: " + str(error), file=sys.stderr)
        sys.exit(1)
