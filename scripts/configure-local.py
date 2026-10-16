#!/usr/bin/env python3
"""Create local canonical configuration and symbol maps from the owner's extracted ELF."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
elf = ROOT / 'extracted/disc/SLES_503.50'
if not elf.is_file():
    raise SystemExit('Extract your own PAL disc first: scripts/extract-disc.py /path/to/disc.iso')
(ROOT / 'reports').mkdir(exist_ok=True)
subprocess.run([sys.executable, str(ROOT / 'scripts/generate-symbol-map.py')], check=True)
config = (ROOT / 'config/SLES-50350.template.toml').read_text().replace('@ROOT@', str(ROOT))
(ROOT / 'config/SLES-50350.symbolmap-entry.toml').write_text(config)
print('Created local canonical config and game-derived symbol maps (untracked).')
