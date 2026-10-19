#!/usr/bin/env python3
"""Verify and extract your own disc (identity: config/game.json).

  tools/disc.py <iso>               verify SHA-1s, extract to extracted/disc, write reports/disc-inventory.json
  tools/disc.py <iso> --dest DIR    same, into DIR

Exit codes: 0 ok, 1 verification failed, 2 missing prerequisite.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import struct
import sys
from pathlib import Path, PurePosixPath

from game import DISC, ELF, GAME, ROOT

DEFAULT_REPORT = ROOT / "reports" / "disc-inventory.json"
STAMP = ROOT / "build/disc.stamp"


class Fail(Exception):
    """Verification failure (exit 1)."""


class Missing(Exception):
    """Missing prerequisite (exit 2)."""


def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(iso_path: str) -> Path:
    """Normalize ISO path and strip ISO9660 ;version suffixes safely."""
    parts = []
    for component in PurePosixPath(iso_path).parts:
        if component in ("/", "", "."):
            continue
        if component == ".." or "/" in component or "\\" in component:
            raise ValueError(f"unsafe ISO path component: {component!r}")
        component = re.sub(r";[0-9]+$", "", component)
        if component in ("", ".", ".."):
            raise ValueError(f"unsafe normalized ISO path: {iso_path!r}")
        parts.append(component)
    if not parts:
        raise ValueError(f"not a file path: {iso_path!r}")
    return Path(*parts)


def elf_report(path: Path) -> dict:
    data = path.read_bytes()
    info = {"size_bytes": len(data), "sha1": hashlib.sha1(data).hexdigest()}
    if len(data) < 52 or data[:6] != b"\x7fELF\x01\x01":
        info["error"] = "boot file is not a little-endian ELF32"
        return info
    header = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    entry, phoff, shoff = header[3], header[4], header[5]
    phentsize, phnum, shentsize, shnum, shstrndx = header[8], header[9], header[10], header[11], header[12]
    info.update({"machine_id": header[1], "entry_point": f"0x{entry:08x}"})
    info["segments"] = [
        dict(zip(("type", "offset", "vaddr", "paddr", "file_size", "memory_size", "flags", "alignment"),
                 struct.unpack_from("<IIIIIIII", data, phoff + i * phentsize)))
        for i in range(phnum) if phentsize >= 32 and phoff + (i + 1) * phentsize <= len(data)]
    sections = []
    if shentsize >= 40 and shoff + shnum * shentsize <= len(data):
        raw = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]
        names = b""
        if shstrndx < len(raw) and raw[shstrndx][4] + raw[shstrndx][5] <= len(data):
            names = data[raw[shstrndx][4]:raw[shstrndx][4] + raw[shstrndx][5]]
        for name_off, kind, flags, addr, offset, size, *_ in raw:
            end = names.find(b"\0", name_off)
            name = names[name_off:end].decode("ascii", "replace") if end >= 0 else ""
            sections.append({"name": name, "type": kind, "flags": f"0x{flags:x}", "address": f"0x{addr:08x}", "size": size})
    info["sections"] = sections
    info["has_symbol_table"] = any(sec["type"] == 2 for sec in sections)
    return info


def read_boot_elf(iso) -> bytes:
    buf = io.BytesIO()
    iso.get_file_from_iso_fp(buf, iso_path=f"/{GAME['elf']};1")
    return buf.getvalue()


def extract(iso_path: Path, dest: Path, report_path: Path) -> None:
    import pycdlib
    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    try:
        got = hashlib.sha1(read_boot_elf(iso)).hexdigest()
        if got != GAME["elf_sha1"]:
            raise Fail(f"{GAME['elf']} SHA-1 mismatch: got {got}, expected {GAME['elf_sha1']}")
        records, seen = [], set()
        for dirpath, _dirs, files in iso.walk(iso_path="/"):
            for filename in files:
                source = (dirpath.rstrip("/") + "/" + filename) if dirpath != "/" else "/" + filename
                relative = safe_relative(source)
                key = relative.as_posix().casefold()
                if key in seen:
                    raise Fail(f"duplicate path after stripping ISO version suffix: {relative}")
                seen.add(key)
                records.append({"iso_path": source, "relative_path": relative.as_posix(),
                                "size_bytes": iso.get_record(iso_path=source).data_length})
        records.sort(key=lambda r: r["relative_path"].casefold())
        dest.mkdir(parents=True, exist_ok=True)
        for record in records:
            target = (dest / record["relative_path"]).resolve()
            if dest != target and dest not in target.parents:
                raise Fail(f"path escaped extraction root: {record['relative_path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not target.is_file() or target.stat().st_size != record["size_bytes"]:
                    raise Fail(f"existing extraction path conflicts or has wrong size: {target}")
                continue
            iso.get_file_from_iso(local_path=str(target), iso_path=record["iso_path"])
        extensions = {}
        for record in records:
            suffix = Path(record["relative_path"]).suffix.lower() or "[no extension]"
            extensions[suffix] = extensions.get(suffix, 0) + 1
        report = {
            "game": GAME["id"], "source_iso": str(iso_path), "iso_size_bytes": iso_path.stat().st_size,
            "volume_space_size_sectors": iso.pvd.space_size, "extraction_root": str(dest),
            "file_count": len(records), "total_file_bytes": sum(r["size_bytes"] for r in records),
            "extension_counts": dict(sorted(extensions.items())),
            "system_cnf": (dest / "SYSTEM.CNF").read_text("ascii", errors="replace") if (dest / "SYSTEM.CNF").is_file() else None,
            "boot_elf": elf_report(dest / GAME["elf"]),
            "files": records,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"extracted {report['file_count']} files, {report['total_file_bytes']:,} bytes to {dest}")
    finally:
        iso.close()


def stamp_key(iso_path: Path) -> str:
    st = iso_path.stat()
    return hashlib.sha256(f"{iso_path}:{st.st_size}:{st.st_mtime_ns}:{GAME['elf_sha1']}".encode()
                          + Path(__file__).read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("iso", type=Path, help="path to your own disc image")
    parser.add_argument("--dest", type=Path, default=DISC)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        iso_path = args.iso.expanduser().resolve()
        if not iso_path.is_file():
            raise Missing(f"ISO not found: {iso_path} -> ./rx disc <path-to-iso>")
        try:
            import pycdlib  # noqa: F401
        except ImportError:
            raise Missing("pycdlib not installed -> ./rx setup")
        dest = args.dest.expanduser().resolve()
        default_dest = dest == DISC.resolve()
        if default_dest and ELF.is_file() and STAMP.is_file() and STAMP.read_text().strip() == stamp_key(iso_path):
            print("disc up to date: extracted/ matches this ISO (delete build/disc.stamp to force)")
            return 0
        if file_digest(iso_path, "sha1") != GAME["iso_sha1"]:
            raise Fail(f"ISO SHA-1 mismatch for {iso_path.name}; expected {GAME['title']} ({GAME['id']}), {GAME['iso_sha1']}")
        extract(iso_path, dest, args.report.expanduser().resolve())
        if default_dest:
            STAMP.parent.mkdir(parents=True, exist_ok=True)
            STAMP.write_text(stamp_key(iso_path) + "\n")
    except Missing as exc:
        print(exc, file=sys.stderr)
        return 2
    except Fail as exc:
        print(f"disc failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
