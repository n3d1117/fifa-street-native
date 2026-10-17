#!/usr/bin/env python3
"""Extract contiguous VIF MPG microcode uploads from a local MIPS ELF.

The packet recognition follows the documented approach in
source/fukami/tools/vu-aot/vu_aot_gen.py (GPL-3.0, see source/fukami/LICENSE):
an aligned VIF NOP, an MPG tag, and its inline VU words. This script is an
independent, bounds-checked parser and writes only local .vu1 files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

PT_LOAD = 1
VU1_SLOTS = 2048


def load_segments(data: bytes) -> list[dict]:
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file or truncated ELF32 header")
    if data[4] != 1 or data[5] != 1 or data[6] != 1:
        raise ValueError("expected ELF32 little-endian version 1")
    machine = struct.unpack_from("<H", data, 18)[0]
    if machine != 8:
        raise ValueError(f"expected MIPS ELF (e_machine=8), got {machine}")
    ehsize, phentsize, phnum = struct.unpack_from("<HHH", data, 40)[0], *struct.unpack_from("<HH", data, 42)
    phoff = struct.unpack_from("<I", data, 28)[0]
    if ehsize < 52 or phentsize < 32:
        raise ValueError("invalid ELF header/program-header size")
    if phoff > len(data) or phnum > (len(data) - phoff) // phentsize:
        raise ValueError("program-header table exceeds file bounds")
    segments = []
    for i in range(phnum):
        off = phoff + i * phentsize
        p_type, p_offset, p_vaddr, _p_paddr, p_filesz, p_memsz, _flags, _align = struct.unpack_from("<IIIIIIII", data, off)
        if p_type != PT_LOAD:
            continue
        if (p_filesz > p_memsz or p_offset > len(data) or p_filesz > len(data) - p_offset
                or p_vaddr + p_memsz > 0x1_0000_0000):
            raise ValueError(f"PT_LOAD[{i}] has invalid file/memory bounds")
        segments.append({"index": i, "offset": p_offset, "vaddr": p_vaddr,
                         "filesz": p_filesz, "memsz": p_memsz})
    return segments


def find_chains(data: bytes, segments: list[dict]) -> list[dict]:
    """Find plausible VIF MPG chains, never reading outside a PT_LOAD."""
    found: list[dict] = []
    for seg in segments:
        start, end = seg["offset"], seg["offset"] + seg["filesz"]
        packets = []
        # Scan 8-byte boundaries relative to the ELF load segment.
        first = start + ((-start) & 7)
        for nop_off in range(first, end - 8 + 1, 8):
            mpg_off = nop_off + 4
            if struct.unpack_from("<I", data, nop_off)[0] != 0:
                continue
            tag = struct.unpack_from("<I", data, mpg_off)[0]
            if (tag >> 24) & 0x7F != 0x4A:
                continue
            count = (tag >> 16) & 0xFF or 256
            dest = tag & 0xFFFF
            payload_off = mpg_off + 4
            payload_size = count * 8
            if dest + count > VU1_SLOTS or payload_off > end or payload_size > end - payload_off:
                continue
            packets.append({"nop_offset": nop_off, "mpg_offset": mpg_off,
                            "payload_offset": payload_off, "count": count,
                            "destination": dest, "tag": tag})

        chains: list[list[dict]] = []
        for packet in packets:
            if chains:
                prev = chains[-1][-1]
                contiguous_bytes = prev["payload_offset"] + prev["count"] * 8 == packet["nop_offset"]
                contiguous_dest = prev["destination"] + prev["count"] == packet["destination"]
                if contiguous_bytes and contiguous_dest:
                    chains[-1].append(packet)
                    continue
            chains.append([packet])

        for chain in chains:
            code = b"".join(data[p["payload_offset"]:p["payload_offset"] + p["count"] * 8]
                            for p in chain)
            slots = len(code) // 8
            nops = e_bits = 0
            for pos in range(0, len(code), 8):
                lower, upper = struct.unpack_from("<II", code, pos)
                nops += upper == 0x000002FF or lower == 0x8000033C
                e_bits += bool(upper & 0x40000000)
            # Same plausibility guard as the referenced extractor: VU NOPs
            # and at least one E-bit distinguish code from accidental MPG data.
            if not e_bits or nops * 20 < slots:
                continue
            found.append({"segment": seg, "packets": chain, "code": code,
                          "slots": slots, "nop_slots": nops, "e_bit_slots": e_bits})
    return found


def compare_image(code: bytes, packets: list[dict], image: bytes) -> dict:
    """Compare uploaded source words against their destination ranges in a VU1 bank."""
    if len(image) != VU1_SLOTS * 8:
        return {"valid_bank_size": False, "size": len(image)}
    exact = True
    differing = []
    for packet in packets:
        off = packet["destination"] * 8
        size = packet["count"] * 8
        src = packet["payload_offset"]
        # source bytes are concatenated in packet order in `code`
        chain_off = sum(prev["count"] * 8 for prev in packets[:packets.index(packet)])
        expected = code[chain_off:chain_off + size]
        actual = image[off:off + size]
        if actual != expected:
            exact = False
            first = next((j for j, (a, b) in enumerate(zip(actual, expected)) if a != b), size)
            last = max((j + 1 for j, (a, b) in enumerate(zip(actual, expected)) if a != b), default=0)
            differing.append({"destination_start": packet["destination"],
                              "destination_end": packet["destination"] + packet["count"],
                              "different_byte_start": first, "different_byte_end": last})
    return {"valid_bank_size": True, "exact_chain_match": exact,
            "differing_ranges": differing}


def extract(elf_path: Path, out_dir: Path, banks: list[Path]) -> dict:
    data = elf_path.read_bytes()
    segments = load_segments(data)
    chains = find_chains(data, segments)
    out_dir.mkdir(parents=True, exist_ok=True)
    bank_images = {}
    for bank in banks:
        # Catalogs store fixed 16 KiB code banks named by FNV hash.
        for path in sorted(bank.glob("*.vu1")):
            bank_images[f"{bank.name}/{path.name}"] = path.read_bytes()

    records = []
    for i, chain in enumerate(chains):
        packets, code = chain["packets"], chain["code"]
        digest = hashlib.sha256(code).hexdigest()
        name = f"chain-{i:03d}-dst-{packets[0]['destination']:04x}-{digest[:16]}.vu1"
        (out_dir / name).write_bytes(code)
        source_start = packets[0]["nop_offset"]
        record = {
            "index": i, "file": name, "source_file_offset": source_start,
            "source_virtual_address": chain["segment"]["vaddr"] + source_start - chain["segment"]["offset"],
            "segment_index": chain["segment"]["index"], "destination_start": packets[0]["destination"],
            "destination_end": packets[-1]["destination"] + packets[-1]["count"],
            "size_bytes": len(code), "slots": chain["slots"], "sha256": digest,
            "nop_slots": chain["nop_slots"], "e_bit_slots": chain["e_bit_slots"],
            "packets": [{"source_file_offset": p["nop_offset"],
                         "source_virtual_address": chain["segment"]["vaddr"] + p["nop_offset"] - chain["segment"]["offset"],
                         "destination": p["destination"], "slots": p["count"]}
                        for p in packets],
            "bank_comparisons": {label: compare_image(code, packets, image)
                                  for label, image in bank_images.items()},
        }
        records.append(record)
    manifest = {"input_elf": str(elf_path), "input_size": len(data),
                "input_sha256": hashlib.sha256(data).hexdigest(),
                "pt_load_count": len(segments), "chain_count": len(records), "chains": records}
    (out_dir / "coverage.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("elf", type=Path, help="local ELF32 little-endian MIPS executable")
    ap.add_argument("--output", type=Path, default=Path("runtime/vu1-static-elf"))
    ap.add_argument("--compare-bank", action="append", type=Path, default=[],
                    help="directory of local full 16 KiB .vu1 banks (repeatable)")
    args = ap.parse_args()
    report = extract(args.elf, args.output, args.compare_bank)
    print(f"extracted {report['chain_count']} VU1 chains to {args.output}")
    print(f"coverage: {args.output / 'coverage.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
