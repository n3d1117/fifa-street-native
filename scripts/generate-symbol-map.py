#!/usr/bin/env python3
"""Export nonzero, executable ELF32 STT_FUNC symbols in PS2Recomp CSV format."""

from __future__ import annotations

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ELF = ROOT / "extracted/disc/SLES_503.50"
CSV = ROOT / "config/SLES-50350.symbols.csv"
ENTRY_CSV = ROOT / "config/SLES-50350.symbols-entry.csv"
REPORT = ROOT / "reports/symbol-map-audit.json"


def main() -> None:
    data = ELF.read_bytes()
    if data[:6] != b"\x7fELF\x01\x01":
        raise SystemExit("expected ELF32 little-endian input")
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
            raise SystemExit("malformed ELF32 symbol table")
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
        raise SystemExit("duplicate executable function starts; refusing ambiguous map")
    for (start, size, _name), (next_start, _next_size, _next_name) in zip(symbols, symbols[1:]):
        if start + size > next_start:
            raise SystemExit(f"overlapping STT_FUNC extents at 0x{start:x} and 0x{next_start:x}")

    entry_record = None
    if not any(start == entry_point for start, _size, _name in symbols):
        entry_section = next((sec for sec in executable_sections if sec["start"] <= entry_point < sec["end"]), None)
        next_start = min((start for start, _size, _name in symbols if start > entry_point), default=None)
        if entry_section is None or next_start is None or next_start <= entry_point:
            raise SystemExit("ELF entry point is not safely bounded by an executable STT_FUNC")
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
    print(json.dumps({key: report[key] for key in ("symbol_records", "size_nonzero", "elf_entry_point", "synthetic_elf_entry_record", "executable_sections", "start_range", "function_gap_count_over_4_bytes", "largest_gaps")}, indent=2))


if __name__ == "__main__":
    main()
