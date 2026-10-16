#!/usr/bin/env python3
"""Install a verified native bundle in Applications; preserve the previous app in Trash."""
import argparse
from datetime import datetime
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('bundle', type=Path)
parser.add_argument('--applications', type=Path, default=Path('/Applications'))
args = parser.parse_args()
source = args.bundle.resolve(strict=True)
if source.suffix != '.app' or not (source / 'Contents/Info.plist').is_file():
    raise SystemExit('Expected a complete app bundle')
subprocess.run(['codesign', '--verify', '--deep', '--strict', str(source)], check=True)
destination = args.applications.resolve() / 'Tarzan Freeride PS2.app'
if destination == source:
    print('Already installed:', destination)
    raise SystemExit(0)
destination.parent.mkdir(parents=True, exist_ok=True)
backup = None
if destination.exists():
    backup = Path.home() / '.Trash' / ('Tarzan-PS2-before-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.app')
    backup.parent.mkdir(parents=True, exist_ok=True)
    destination.rename(backup)
try:
    shutil.move(str(source), str(destination))
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(destination)], check=True)
except Exception:
    if backup and backup.exists() and not destination.exists():
        backup.rename(destination)
    raise
subprocess.run(['/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister', '-f', str(destination)], check=True)
print('Installed:', destination)
if backup:
    print('Previous app is recoverable in Trash:', backup)
