#!/usr/bin/env python3
"""Create local canonical configuration and symbol maps from the owner's extracted ELF."""
from pathlib import Path
import json
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
vu = ROOT / 'runtime/vu1-disc'
subprocess.run([sys.executable, str(ROOT / 'scripts/extract-vu-microcode.py'),
                str(elf), '--output', str(vu), '--tarzan-patches'], check=True)
catalog = vu / 'catalog'
catalog.mkdir(exist_ok=True)
(catalog / 'index.txt').write_text('')
(catalog / 'jr_targets.txt').write_text('')
manifest = json.loads((vu / 'coverage.json').read_text())['tarzan_patch_candidates']
args = [sys.executable, str(ROOT / 'tools/vu1recomp/vu1recomp.py'),
        '--catalog', str(catalog), '--out', str(ROOT / 'generated/vu1-disc-native'),
        '--guarded', '--entry', '0']
for candidate in manifest['candidates']:
    args.extend(['--pool-code', str(vu / 'tarzan-patch-candidates' / candidate['file'])])
for pc in sorted({pc for group in manifest['groups'].values() for pc in group['entry_vu_pcs']}):
    args.extend(['--indirect-entry', pc])
subprocess.run(args, check=True)
print('Created local config, symbol maps and disc-derived native VU1 code (untracked).')
