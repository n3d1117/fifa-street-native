#!/usr/bin/env python3
"""Verify, extract and prepare your own Tarzan: Freeride (PAL) disc.

  tools/disc.py <iso>        verify SHA-1s, extract to extracted/disc, then configure
  tools/disc.py              configure from an already-extracted, verified ELF
  tools/disc.py --vu1-only   only regenerate the disc-derived VU1 code (used by CMake)

Configure = symbol map CSVs + local EE config + disc-derived native VU1 code.
Exit codes: 0 ok, 1 verification/generation failed, 2 missing prerequisite.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(os.environ.get("TARZAN_ROOT") or Path(__file__).resolve().parents[1])
DEFAULT_DEST = ROOT / "extracted" / "disc"
DEFAULT_REPORT = ROOT / "reports" / "disc-inventory.json"
ELF = DEFAULT_DEST / "SLES_503.50"
CSV = ROOT / "config/SLES-50350.symbols.csv"
ENTRY_CSV = ROOT / "config/SLES-50350.symbols-entry.csv"
REPORT = ROOT / "reports/symbol-map-audit.json"
ISO_SHA1 = "6efdf922e38e757ce0494d27ead2bf7df491d51f"
ELF_SHA1 = "fcb6d2ef522512ee4f08d0a415b2013248a37c44"


class Fail(Exception):
    """Verification or generation failure (exit 1)."""


class Missing(Exception):
    """Missing prerequisite (exit 2)."""


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1()
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




def extract(iso_path: Path, dest: Path, report_path: Path) -> None:
    try:
        import pycdlib
    except ImportError:
        raise Missing("pycdlib not installed -> ./tz setup")
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
        print(f"extracted {report['file_count']} files, {report['total_file_bytes']:,} bytes to {dest}")
    finally:
        iso.close()


def verify_boot_elf_in_iso(iso_path: Path) -> None:
    import pycdlib
    iso = pycdlib.PyCdlib()
    iso.open(str(iso_path))
    try:
        buf = io.BytesIO()
        iso.get_file_from_iso_fp(buf, iso_path="/SLES_503.50;1")
    finally:
        iso.close()
    got = hashlib.sha1(buf.getvalue()).hexdigest()
    if got != ELF_SHA1:
        raise Fail(f"SLES_503.50 SHA-1 mismatch: got {got}, expected {ELF_SHA1}")


def make_symbol_map() -> None:
    data = ELF.read_bytes()
    if data[:6] != b"\x7fELF\x01\x01":
        raise Fail("expected ELF32 little-endian input")
    header = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    entry_point = header[3]
    shoff, shentsize, shnum, shstrndx = header[5], header[10], header[11], header[12]
    sections = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]
    shstr = sections[shstrndx]
    section_names = data[shstr[4]:shstr[4] + shstr[5]]

    def section_name(offset: int) -> str:
        end = section_names.find(b"\0", offset)
        return section_names[offset:end].decode("ascii", "replace") if end >= 0 else ""

    executable_sections = [
        {"index": index, "name": section_name(sh[0]), "start": sh[3], "end": sh[3] + sh[5], "flags": sh[2]}
        for index, sh in enumerate(sections) if sh[2] & 0x4
    ]
    symbols = []
    for symsec in sections:
        if symsec[1] != 2:
            continue
        strings_sec = sections[symsec[6]]
        strings = data[strings_sec[4]:strings_sec[4] + strings_sec[5]]
        entsize = symsec[9]
        if entsize < 16 or symsec[5] % entsize:
            raise Fail("malformed ELF32 symbol table")
        for off in range(symsec[4], symsec[4] + symsec[5], entsize):
            nameoff, value, size, info, _other, shndx = struct.unpack_from("<IIIBBH", data, off)
            if info & 0xF != 2 or size == 0 or shndx >= len(sections):
                continue
            sec = sections[shndx]
            if not (sec[2] & 0x4 and value >= sec[3] and value + size <= sec[3] + sec[5]):
                continue
            end = strings.find(b"\0", nameoff)
            name = strings[nameoff:end].decode("utf-8", "replace") if end >= 0 else f"symbol_{value:08x}"
            # The upstream loader splits each record at literal commas.
            name = name.replace(",", "_").replace("\r", "_").replace("\n", "_")
            symbols.append((value, size, name))

    symbols.sort(key=lambda item: (item[0], item[1], item[2]))
    if len({start for start, _size, _name in symbols}) != len(symbols):
        raise Fail("duplicate executable function starts; refusing ambiguous map")
    for (start, size, _name), (next_start, _next_size, _next_name) in zip(symbols, symbols[1:]):
        if start + size > next_start:
            raise Fail(f"overlapping STT_FUNC extents at 0x{start:x} and 0x{next_start:x}")

    entry_record = None
    if not any(start == entry_point for start, _size, _name in symbols):
        entry_section = next((sec for sec in executable_sections if sec["start"] <= entry_point < sec["end"]), None)
        next_start = min((start for start, _size, _name in symbols if start > entry_point), default=None)
        if entry_section is None or next_start is None or next_start <= entry_point:
            raise Fail("ELF entry point is not safely bounded by an executable STT_FUNC")
        entry_record = (entry_point, next_start - entry_point, "__elf_entry")

    def write_csv(path: Path, records: list[tuple[int, int, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as output:
            output.write("Name,Start,End,Size\n")
            for start, size, name in records:
                output.write(f"{name},0x{start:08X},0x{start + size:08X},{size}\n")

    CSV.parent.mkdir(parents=True, exist_ok=True)
    write_csv(CSV, symbols)
    entry_symbols = sorted(symbols + ([entry_record] if entry_record else []), key=lambda item: item[0])
    write_csv(ENTRY_CSV, entry_symbols)

    gaps = []
    for (start, size, _), (next_start, _, _) in zip(symbols, symbols[1:]):
        gap_start = start + size
        if next_start - gap_start > 4:
            gaps.append({"from": f"0x{gap_start:08x}", "to": f"0x{next_start:08x}", "size": next_start - gap_start})
    report = {
        "input": str(ELF), "csv": str(CSV), "entry_augmented_csv": str(ENTRY_CSV),
        "symbol_records": len(symbols), "size_nonzero": len(symbols),
        "elf_entry_point": f"0x{entry_point:08x}",
        "synthetic_elf_entry_record": ({"name": entry_record[2], "start": f"0x{entry_record[0]:08x}", "end": f"0x{entry_record[0] + entry_record[1]:08x}", "size": entry_record[1], "end_boundary": "next STT_FUNC"} if entry_record else None),
        "executable_sections": executable_sections,
        "start_range": [f"0x{symbols[0][0]:08x}", f"0x{symbols[-1][0] + symbols[-1][1]:08x}"],
        "function_gap_count_over_4_bytes": len(gaps),
        "largest_gaps": sorted(gaps, key=lambda gap: gap["size"], reverse=True)[:20],
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"symbol map: {len(symbols)} functions, entry {report['elf_entry_point']}")




def configure(vu1_only: bool) -> None:
    if not ELF.is_file():
        raise Missing("extracted/disc/SLES_503.50 missing -> ./tz disc <iso>")
    if not vu1_only:
        got = sha1_file(ELF)
        if got != ELF_SHA1:
            raise Fail(f"extracted SLES_503.50 SHA-1 mismatch: got {got}, expected {ELF_SHA1}")
        (ROOT / "reports").mkdir(exist_ok=True)
        make_symbol_map()
        config = (ROOT / "config/SLES-50350.template.toml").read_text().replace("@ROOT@", str(ROOT))
        (ROOT / "config/SLES-50350.symbolmap-entry.toml").write_text(config)
    vu = ROOT / "runtime/vu1-disc"
    subprocess.run([sys.executable, str(ROOT / "tools/vu/extract-vu-microcode.py"),
                    str(ELF), "--output", str(vu), "--tarzan-patches"], check=True)
    catalog = vu / "catalog"
    catalog.mkdir(exist_ok=True)
    (catalog / "index.txt").write_text("")
    (catalog / "jr_targets.txt").write_text("")
    manifest = json.loads((vu / "coverage.json").read_text())["tarzan_patch_candidates"]
    args = [sys.executable, str(ROOT / "tools/vu/vu1recomp.py"),
            "--catalog", str(catalog), "--out", str(ROOT / "generated/vu1-disc-native"),
            "--guarded", "--entry", "0"]
    for candidate in manifest["candidates"]:
        args.extend(["--pool-code", str(vu / "tarzan-patch-candidates" / candidate["file"])])
    for pc in sorted({pc for group in manifest["groups"].values() for pc in group["entry_vu_pcs"]}):
        args.extend(["--indirect-entry", pc])
    subprocess.run(args, check=True)
    print("disc-derived native VU1 code generated" if vu1_only else
          "local config, symbol maps and disc-derived native VU1 code generated")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("iso", type=Path, nargs="?", help="path to your own PAL disc image")
    parser.add_argument("--vu1-only", action="store_true", help="regenerate VU1 without rewriting EE configuration")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    try:
        if args.iso is not None and not args.vu1_only:
            iso_path = args.iso.expanduser().resolve()
            if not iso_path.is_file():
                raise Missing(f"ISO not found: {iso_path} -> ./tz disc <path-to-iso>")
            try:
                import pycdlib  # noqa: F401
            except ImportError:
                raise Missing("pycdlib not installed -> ./tz setup")
            if sha1_file(iso_path) != ISO_SHA1:
                raise Fail(f"ISO SHA-1 mismatch for {iso_path.name}; expected Disney's Tarzan - Freeride (Europe), {ISO_SHA1}")
            verify_boot_elf_in_iso(iso_path)
            extract(iso_path, args.dest.expanduser().resolve(), args.report.expanduser().resolve())
        elif not args.vu1_only and not ELF.is_file():
            raise Missing("extracted/disc/SLES_503.50 missing -> ./tz disc <iso>")
        configure(args.vu1_only)
    except Missing as exc:
        print(exc, file=sys.stderr)
        return 2
    except (Fail, subprocess.CalledProcessError) as exc:
        print(f"disc failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
