#!/usr/bin/env python3
"""Inventory and safely extract a PS2 ISO9660 disc image."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path, PurePosixPath

import pycdlib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "extracted" / "disc"
DEFAULT_REPORT = ROOT / "reports" / "disc-inventory.json"


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
    info = {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if len(data) < 52 or data[:4] != b"\x7fELF":
        info["error"] = "boot file is not a complete ELF32 header"
        return info
    elf_class, encoding = data[4], data[5]
    if elf_class != 1 or encoding not in (1, 2):
        info["error"] = f"unsupported ELF class/encoding: {elf_class}/{encoding}"
        return info
    endian = "<" if encoding == 1 else ">"
    header = struct.unpack_from(endian + "HHIIIIIHHHHHH", data, 16)
    e_type, e_machine, e_version, entry, phoff, shoff, flags, ehsize, phentsize, phnum, shentsize, shnum, shstrndx = header
    info.update({
        "class": "ELF32", "endianness": "little" if encoding == 1 else "big",
        "type": e_type, "machine_id": e_machine,
        "architecture": {8: "MIPS/PS2 R5900 (EM_MIPS)"}.get(e_machine, f"ELF machine {e_machine}"),
        "entry_point": f"0x{entry:08x}", "flags": f"0x{flags:08x}",
        "program_header_count": phnum, "section_header_count": shnum,
    })
    segments = []
    if phentsize >= 32 and phoff + phnum * phentsize <= len(data):
        for idx in range(phnum):
            p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = struct.unpack_from(endian + "IIIIIIII", data, phoff + idx * phentsize)
            segments.append({"index": idx, "type": p_type, "offset": p_offset, "vaddr": f"0x{p_vaddr:08x}", "paddr": f"0x{p_paddr:08x}", "file_size": p_filesz, "memory_size": p_memsz, "flags": f"0x{p_flags:x}", "alignment": p_align})
    info["segments"] = segments
    sections = []
    raw_sections = []
    if shentsize >= 40 and shoff + shnum * shentsize <= len(data):
        for idx in range(shnum):
            sh = struct.unpack_from(endian + "IIIIIIIIII", data, shoff + idx * shentsize)
            raw_sections.append(sh)
        names = b""
        if shstrndx < len(raw_sections):
            name_sec = raw_sections[shstrndx]
            start, size = name_sec[4], name_sec[5]
            if start + size <= len(data):
                names = data[start:start + size]
        for idx, sh in enumerate(raw_sections):
            name_off, kind, sec_flags, addr, offset, size, link, info_idx, align, entsize = sh
            end = names.find(b"\0", name_off) if name_off < len(names) else -1
            name = names[name_off:end].decode("ascii", "replace") if end >= 0 else ""
            sections.append({"index": idx, "name": name, "type": kind, "flags": f"0x{sec_flags:x}", "address": f"0x{addr:08x}", "offset": offset, "size": size, "alignment": align, "entry_size": entsize})
    info["sections"] = sections
    info["symbol_sections"] = [
        {"name": sec["name"], "type": sec["type"], "symbol_count": (sec["size"] // sec["entry_size"] if sec["entry_size"] else None)}
        for sec in sections if sec["type"] in (2, 11)
    ]
    info["symbol_count"] = sum(x["symbol_count"] or 0 for x in info["symbol_sections"])
    return info


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path, help="Path to your own PS2 PAL disc image")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    iso_path = args.iso.expanduser().resolve()
    dest = args.dest.expanduser().resolve()
    report_path = args.report.expanduser().resolve()
    if not iso_path.is_file():
        parser.error(f"ISO does not exist: {iso_path}")
    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    try:
        volume = iso.pvd.volume_identifier.decode("ascii", "replace").strip()
        records = []
        collisions = set()
        for dirpath, _dirs, files in iso.walk(iso_path="/"):
            for filename in files:
                source_path = (dirpath.rstrip("/") + "/" + filename) if dirpath != "/" else "/" + filename
                relative = safe_relative(source_path)
                key = relative.as_posix().casefold()
                if key in collisions:
                    raise RuntimeError(f"duplicate path after stripping ISO version suffix: {relative}")
                collisions.add(key)
                rec = iso.get_record(iso_path=source_path)
                records.append({"iso_path": source_path, "relative_path": relative.as_posix(), "size_bytes": rec.data_length})

        # Extract boot metadata first, then the rest of the disc.
        priority = lambda r: (0 if r["relative_path"].upper() == "SYSTEM.CNF" else 1 if re.fullmatch(r"SLES_[0-9.]+", Path(r["relative_path"]).name, re.I) else 2, r["relative_path"].casefold())
        records.sort(key=priority)
        dest.mkdir(parents=True, exist_ok=True)
        for record in records:
            target = (dest / record["relative_path"]).resolve()
            if dest != target and dest not in target.parents:
                raise RuntimeError(f"path escaped extraction root: {record['relative_path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if not target.is_file() or target.stat().st_size != record["size_bytes"]:
                    raise RuntimeError(f"existing extraction path conflicts or has wrong size: {target}")
                continue
            iso.get_file_from_iso(local_path=str(target), iso_path=record["iso_path"])

        system_file = next((dest / r["relative_path"] for r in records if Path(r["relative_path"]).name.upper() == "SYSTEM.CNF"), None)
        system_text = system_file.read_text("ascii", errors="replace") if system_file else ""
        boot_match = re.search(r"(?im)^\s*BOOT2?\s*=\s*cdrom0:\\([^;\s]+)", system_text)
        boot_relative = boot_match.group(1).replace("\\", "/") if boot_match else None
        if boot_relative:
            boot_relative = safe_relative("/" + boot_relative).as_posix()
        boot_record = next((r for r in records if boot_relative and r["relative_path"].casefold() == boot_relative.casefold()), None)
        if boot_record is None:
            boot_record = next((r for r in records if re.fullmatch(r"SLES_[0-9.]+", Path(r["relative_path"]).name, re.I)), None)
            boot_relative = boot_record["relative_path"] if boot_record else None
        boot_info = elf_report(dest / boot_record["relative_path"]) if boot_record else None

        ext_counts = {}
        for record in records:
            suffix = Path(record["relative_path"]).suffix.lower() or "[no extension]"
            ext_counts[suffix] = ext_counts.get(suffix, 0) + 1
        special = [r for r in records if re.search(r"(?i)(\.irx$|\.fat$|\.\d{3}$|\.h4m$|\.elf$)", r["relative_path"])]
        report = {
            "source_iso": str(iso_path), "iso_size_bytes": iso_path.stat().st_size,
            "iso_sha256": sha256_file(iso_path),
            "volume_identifier": volume, "volume_space_size_sectors": iso.pvd.space_size,
            "extraction_root": str(dest), "file_count": len(records),
            "total_file_bytes": sum(r["size_bytes"] for r in records),
            "extension_counts": dict(sorted(ext_counts.items())),
            "system_cnf": {"path": str(system_file) if system_file else None, "contents": system_text},
            "boot_elf_path": str(dest / boot_record["relative_path"]) if boot_record else None,
            "boot_elf_iso_path": boot_record["iso_path"] if boot_record else None,
            "boot_elf": boot_info,
            "irx_overlays_and_payload_assets": special,
            "files": records,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: report[k] for k in ("volume_identifier", "file_count", "total_file_bytes", "system_cnf", "boot_elf_path", "boot_elf", "irx_overlays_and_payload_assets")}, indent=2))
    finally:
        iso.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
