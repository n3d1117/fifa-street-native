"""The game this checkout ports: config/game.json plus the paths derived from it.

Every tool takes identity and per-game paths from here; CMake reads the same file.
"""
import json
import os
from pathlib import Path

ROOT = Path(os.environ.get("RX_ROOT") or Path(__file__).resolve().parents[1])
GAME = json.loads((ROOT / "config/game.json").read_text())
ID = GAME["id"]
DISC = ROOT / "extracted/disc"
ELF = DISC / GAME["elf"]
TEMPLATE = ROOT / f"config/{ID}.template.toml"  # tracked: hand-maintained recompiler config
CONFIG = ROOT / f"config/{ID}.toml"             # derived by `rx build` (@ROOT@ substituted)
GENERATED = ROOT / f"generated/{ID}"
