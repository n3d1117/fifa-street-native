# VU1 static recompiler tools

These scripts are derived from the RoadTripRecomp VU1 recompiler tools, distributed under GPL-3.0-or-later. Upstream: https://github.com/catsandsoup/Road-trip-Adventure-PS2-Recompile-MacOS/tree/86fa998f201f9abe634097eff2d625bf99360be8/tools/vu1recomp; see the accompanying `LICENSE` file.

`vu1decode.py` and `vu1recomp.py` decode captured VU microcode and generate native C++ implementations. The default `--unit vu1` mode handles 16 KiB VU1 images. `--unit vu0` handles 4 KiB VU0 images with a guarded subset: reachable lower instructions must be LQI or NOP, and upper instructions must be supported FMAC, ACC, or NOP operations. Other reachable instructions fail generation so the runtime can use the interpreter.

VU0 catalogs use `<hash>.vu0` images and an `index.txt` with one row per entry:
`<16-digit-hash> <entry-PC-in-hex> <capture-count>`. The generator reads the hash
and entry PC; the capture count is informational. To make a local BYOD catalog,
launch the game with `TARZAN_TRACE_VU0_SKINNING` set to a writable filename
prefix, then reach gameplay. The bounded trace hook writes `<prefix>.code.bin`
(the complete 4096-byte VU0 micro memory), `.data.before.bin`, and
`.state.before.txt` on the first matching deformation call; it also appends
call metadata to `.log`. Keep these captures local and do not commit them.

Create the catalog entry by hashing all 4096 bytes of `.code.bin` with the
generator's FNV-1a 64-bit offset basis `1469598103934665603` and multiplier
`1099511628211`, copying the image to `<hash>.vu0`, and recording the observed
entry PC from the trace log (typically `0000`) and count in `index.txt`. For
example, run this from the repository root after capture:

```sh
python3 - <<'PY'
import re
from collections import Counter
from pathlib import Path

capture = Path('/tmp/tarzan-vu0')
catalog = Path('runtime/vu0-catalog')
catalog.mkdir(parents=True, exist_ok=True)
code = capture.with_name(capture.name + '.code.bin').read_bytes()
if len(code) != 4096:
    raise SystemExit(f'expected 4096-byte VU0 image, got {len(code)}')
log = capture.with_name(capture.name + '.log').read_text()
entries = Counter(int(pc, 16) for pc in re.findall(r'call=\d+ ee_pc=0x[0-9a-f]+ start_pc=0x([0-9a-fA-F]+)', log))
if not entries:
    raise SystemExit('no entry PCs found in trace log')
h = 1469598103934665603
for byte in code:
    h = ((h ^ byte) * 1099511628211) & 0xffffffffffffffff
name = f'{h:016x}'
(catalog / f'{name}.vu0').write_bytes(code)
rows = ''.join(f'{name} {pc:04x} {count}\n' for pc, count in sorted(entries.items()))
(catalog / 'index.txt').write_text(rows)
print(f'cataloged {name} with {len(entries)} observed entry PC(s)')
PY
```

Replace `/tmp/tarzan-vu0` with the prefix used for capture. The script catalogs
each distinct `start_pc` and its observed call count from the trace log. Generate
`vu0rec_<hash>.cpp` files with:

```sh
python3 tools/vu1recomp/vu1recomp.py --unit vu0 \
  --catalog runtime/vu0-catalog --out generated/vu0-native
```

The VU0 generator uses 4 KiB micro/data-memory masks and a separate VU0 registrar interface. Generated `vu0rec_*.cpp` files and captured `.vu0` images come from game data; keep them in local runtime and generated directories.
