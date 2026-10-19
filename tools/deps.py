#!/usr/bin/env python3
"""Manage upstream checkouts in deps/ as a patch stack kept in patches/.

deps.lock lists `name url sha`. deps/<name> is a local git checkout whose
branch `rx` holds our commits on top of the pinned sha; patches/<name>/
holds the same commits as `git format-patch` files.

  sync [name...]    create/refresh deps/<name> from the pin + patches
  export [name...]  rewrite patches/<name>/ from the commits on `rx`
  status            one line per dep

Exit codes: 0 ok, 1 refused or failed, 2 missing prerequisite.
"""
import filecmp
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMAT_PATCH = ['format-patch', '--zero-commit', '--no-signature', '--no-stat', '--full-index']


class Refused(Exception):
    pass


def read_lock():
    deps = {}
    for line in (ROOT / 'deps.lock').read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            name, url, sha = line.split()
            deps[name] = (url, sha)
    return deps


def git(name, *args, check=True):
    cmd = ['git', '-C', str(ROOT / 'deps' / name), *args]
    return subprocess.run(cmd, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def has_branch(name):
    return git(name, 'rev-parse', '--verify', '-q', 'refs/heads/rx', check=False).returncode == 0


def is_dirty(name):
    return bool(git(name, 'status', '--porcelain').stdout.strip())


def commit_count(name, sha):
    return int(git(name, 'rev-list', '--count', f'{sha}..rx').stdout)


def export_to(name, sha, out):
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob('*.patch'):
        old.unlink()
    git(name, *FORMAT_PATCH, '-o', str(out), f'{sha}..rx')


def differs(name, sha):
    """True if the patches in patches/<name>/ differ from what `export` would write."""
    with tempfile.TemporaryDirectory() as tmp:
        export_to(name, sha, Path(tmp))
        fresh = sorted(p.name for p in Path(tmp).glob('*.patch'))
        have_dir = ROOT / 'patches' / name
        have = sorted(p.name for p in have_dir.glob('*.patch')) if have_dir.is_dir() else []
        return fresh != have or not all(
            filecmp.cmp(Path(tmp) / f, have_dir / f, shallow=False) for f in fresh)


def sync(name, url, sha):
    path = ROOT / 'deps' / name
    if not (path / '.git').exists():
        path.mkdir(parents=True, exist_ok=True)
        git(name, 'init', '-q')
        git(name, 'remote', 'add', 'origin', url)
        git(name, 'remote', 'add', 'upstream', url)
    if has_branch(name):
        if is_dirty(name):
            raise Refused(f'{name}: uncommitted edits in deps/{name}; commit them, then `deps.py export` (or discard)')
        if differs(name, sha):
            raise Refused(f'{name}: rx commits differ from patches/{name}; run `deps.py export` to keep them or reset the branch to discard')
        return f'{name}: up to date'
    if git(name, 'cat-file', '-e', f'{sha}^{{commit}}', check=False).returncode != 0:
        git(name, 'fetch', '-q', '--depth', '1', 'origin', sha)
    git(name, 'checkout', '-q', '--detach', sha)
    git(name, 'switch', '-q', '-C', 'rx')
    patches = sorted((ROOT / 'patches' / name).glob('*.patch'))
    if patches:
        git(name, 'am', '-q', '-3', '--keep-cr', *map(str, patches))
    return f'{name}: synced {sha[:9]} + {len(patches)} patches'


def export(name, url, sha):
    if not has_branch(name):
        raise Refused(f'{name}: no rx branch; run sync first')
    if is_dirty(name):
        raise Refused(f'{name}: uncommitted edits in deps/{name}; commit them first')
    export_to(name, sha, ROOT / 'patches' / name)
    return f'{name}: exported {commit_count(name, sha)} patches'


def status(name, url, sha):
    if not (ROOT / 'deps' / name / '.git').exists():
        return f'{name}: pin {sha[:9]}, not synced'
    if not has_branch(name):
        return f'{name}: pin {sha[:9]}, no rx branch'
    return (f'{name}: pin {sha[:9]}, {commit_count(name, sha)} commits, '
            f'{"dirty" if is_dirty(name) else "clean"}, '
            f'{"UNEXPORTED" if differs(name, sha) else "exported"}')


COMMANDS = {'sync': sync, 'export': export, 'status': status}


def main(argv):
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__)
        return 1
    # `git am` needs a committer identity; fall back so it works without global config.
    os.environ.setdefault('GIT_COMMITTER_NAME', 'rx-deps')
    os.environ.setdefault('GIT_COMMITTER_EMAIL', 'deps@localhost')
    # Run from a hook (`git commit -a` -> pre-commit -> rx check), these point at the main
    # repo's temporary index and would make every deps/<x> checkout read the wrong index.
    for var in ('GIT_INDEX_FILE', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_PREFIX'):
        os.environ.pop(var, None)
    try:
        lock = read_lock()
    except (OSError, ValueError) as e:
        print(f'deps.lock unreadable: {e}', file=sys.stderr)
        return 2
    names = argv[2:] or list(lock)
    unknown = [n for n in names if n not in lock]
    if unknown:
        print(f'unknown dep: {" ".join(unknown)}', file=sys.stderr)
        return 1
    code = 0
    for name in names:
        try:
            print(COMMANDS[argv[1]](name, *lock[name]))
        except Refused as e:
            print(e, file=sys.stderr)
            code = 1
        except FileNotFoundError as e:
            print(f'{name}: missing prerequisite: {e.filename}', file=sys.stderr)
            return 2
        except subprocess.CalledProcessError as e:
            print(f'{name}: git {" ".join(e.cmd[3:])} failed: {e.stderr.strip()}', file=sys.stderr)
            code = 1
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
