#!/usr/bin/env python3
"""Inspect an IOP crash/budget PC using an ELF or a captured RAM image.

Run with tools/python/bin/python (capstone and pyelftools are installed there).
ELF bytes are unrelocated; use --ram for the exact live instruction stream.
"""
import argparse
from pathlib import Path

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
from elftools.elf.elffile import ELFFile


def number(value):
    return int(value, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--elf", type=Path)
    source.add_argument("--ram", type=Path)
    parser.add_argument("--load-base", type=number, default=0)
    parser.add_argument("--address", type=number, required=True)
    parser.add_argument("--before", type=number, default=64)
    parser.add_argument("--after", type=number, default=128)
    args = parser.parse_args()
    if not (0 <= args.before <= 4096 and 4 <= args.after <= 4096):
        parser.error("--before must be 0..4096; --after must be 4..4096")
    physical_pc = args.address & 0x1fffffff
    begin = max(0, physical_pc - args.before) & ~3
    end = (physical_pc + args.after + 3) & ~3
    if args.ram:
        ram = args.ram.read_bytes()
        if physical_pc >= len(ram):
            parser.error("address lies outside the RAM capture")
        code = ram[begin:end]
    else:
        with args.elf.open("rb") as stream:
            elf = ELFFile(stream)
            segments = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
            if not segments:
                parser.error("ELF contains no loadable segments")
            lowest = min(s["p_vaddr"] for s in segments)
            delta = args.load_base - lowest
            segment = next((s for s in segments if
                            s["p_vaddr"] + delta <= physical_pc <
                            s["p_vaddr"] + delta + s["p_filesz"]), None)
            if segment is None:
                parser.error("address lies outside file-backed ELF code")
            start = segment["p_vaddr"] + delta
            begin = max(begin, start)
            code = segment.data()[begin-start:end-start]
            print("ELF bytes before relocation; absolute jump operands may need relocation.")
            symbols = elf.get_section_by_name(".symtab")
            if symbols:
                functions = [s for s in symbols.iter_symbols() if
                             s["st_info"]["type"] == "STT_FUNC" and s.name and
                             s["st_value"] + delta <= physical_pc]
                if functions:
                    symbol = max(functions, key=lambda s: s["st_value"])
                    print(f"Nearest function: {symbol.name} + 0x{physical_pc-symbol['st_value']-delta:x}")
    decoder = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    decoder.skipdata = True
    for instruction in decoder.disasm(code, begin):
        marker = ">" if instruction.address == physical_pc else " "
        print(f"{marker} {instruction.address:08x}  {instruction.bytes.hex():8}  "
              f"{instruction.mnemonic:8} {instruction.op_str}")


if __name__ == "__main__":
    main()
