#!/usr/bin/env python3
"""Reject game data, binaries, oversized files and misplaced files in Git's index."""
import argparse
from pathlib import PurePosixPath
import subprocess


def git(*args):
    return subprocess.check_output(['git', *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--staged', action='store_true')
    group.add_argument('--tracked', action='store_true')
    args = parser.parse_args()
    command = ('diff', '--cached', '--name-only', '-z', '--diff-filter=ACMR') if args.staged else ('ls-files', '-z')
    names = [n.decode() for n in git(*command).split(b'\0') if n]
    allowed_roots = {'bridge', 'host', 'cmake', 'tests', 'patches', 'tools', 'toolchain', 'config', 'docs', '.githooks'}
    allowed_root_files = {'.gitignore', 'AGENTS.md', 'README.md', 'LICENSE', 'THIRD_PARTY.md', '.gitattributes', '.editorconfig', 'deps.lock', 'CMakeLists.txt', 'CMakePresets.json', 'requirements.txt', 'rx'}
    allowed_extensions = {'.py', '.sh', '.m', '.mm', '.c', '.cc', '.cpp', '.cxx', '.h', '.hh', '.hpp', '.cmake', '.md', '.txt', '.patch', '.diff', '.json', '.toml', '.yml'}
    problems = []
    total = 0
    for name in names:
        path = PurePosixPath(name)
        size = int(git('cat-file', '-s', ':' + name))
        total += size
        reason = None
        if len(path.parts) == 1 and name not in allowed_root_files:
            reason = 'unexpected root file'
        elif len(path.parts) > 1 and path.parts[0] not in allowed_roots:
            reason = 'data/build/vendor directory'
        elif path.suffix not in allowed_extensions and path.name not in {'deps.lock', 'LICENSE', 'CMakeLists.txt', 'pre-commit', '.gitignore', '.gitattributes', '.editorconfig', 'rx'}:
            reason = 'unexpected extension or binary/media artifact'
        elif path.parts[0] == 'config' and name != 'config/game.json' and not name.endswith('.template.toml'):
            reason = 'local/game-derived configuration'
        elif path.parts[0] == 'tools' and not name.startswith(('tools/vu/', 'tools/metal-deps/compat/', 'tools/probe/', 'tools/package/')) and name not in {'tools/deps.py', 'tools/build-deps.sh', 'tools/build-embedded-runtimes.sh', 'tools/disc.py', 'tools/game.py', 'tools/generate.py', 'tools/where.py', 'tools/jobs.py', 'tools/check-repo-hygiene.py', 'tools/check-generated-includes.py'}:
            reason = 'dependency/toolchain output'
        elif size > 1024 * 1024:
            reason = 'file exceeds 1 MiB'
        else:
            content = git('show', ':' + name)
            if b'\0' in content or any(line.startswith(b'GIT binary patch') for line in content.splitlines()):
                reason = 'binary content'
        if reason:
            problems.append(f'{name}: {reason}')
    if total > 10 * 1024 * 1024:
        problems.append('selected index content exceeds 10 MiB; review before raising the cap')
    if problems:
        raise SystemExit('Repository hygiene FAILED:\n' + '\n'.join(problems))
    print(f'PASS: {len(names)} source/document files, {total:,} bytes; no binary/game artifacts.')


if __name__ == '__main__':
    main()
