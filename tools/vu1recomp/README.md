# VU1 static recompiler tools

These scripts are derived from the RoadTripRecomp VU1 recompiler tools, distributed under GPL-3.0-or-later. Upstream: https://github.com/catsandsoup/Road-trip-Adventure-PS2-Recompile-MacOS/tree/86fa998f201f9abe634097eff2d625bf99360be8/tools/vu1recomp; see the accompanying `LICENSE` file.

`vu1decode.py` and `vu1recomp.py` decode captured 16 KiB VU1 microcode and generate native C++ implementations. `apply_runtime_patch.py` installs the runtime hooks into a PS2Recomp tree. Generated `vu1rec_*.cpp` files and captured `.vu1` images come from game data; keep them in a local build/catalog directory.
