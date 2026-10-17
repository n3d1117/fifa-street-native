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
TARZAN_ELF_SHA256 = "75bf2d1f8f286f69586b921477585452877179d8cbcd5a536da5dfd010195b13"
TARZAN_LIGHT_WRITERS = {
    0x3FBFE0, 0x3FC010, 0x3FC02C, 0x3FC0B4, 0x3FC0D0, 0x3FC154, 0x3FC170,
}
TARZAN_LIGHT_EXTRA_WRITERS = {0x3FB61C: 0x31A8, 0x3FB69C: 0x3308}
TARZAN_MATERIAL_TARGETS = {0x2A50, 0x27D8, 0x2888, 0x2228, 0x2450,
                           0x2358, 0x2270, 0x05F8, 0x0A40, 0x0F10}
TARZAN_MATERIAL_EXTRA_WRITERS = {
    0x3DE190: (0x3138, 3, 4, 0x3DE18C),
    0x3DE390: (0x0830, 6, 6, 0x3DE38C),
    0x3DE338: (0x22B8, 3, 6, 0x3DE334),
    0x3DE28C: (0x0F80, 4, 4, 0x3DE284),
    0x3DE4B4: (0x2AE0, 3, 6, 0x3DE4AC),
}


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


def read_virtual(data: bytes, segments: list[dict], address: int, size: int) -> bytes:
    """Read file-backed bytes from one validated PT_LOAD virtual range."""
    for seg in segments:
        delta = address - seg["vaddr"]
        if 0 <= delta <= seg["filesz"] and size <= seg["filesz"] - delta:
            off = seg["offset"] + delta
            return data[off:off + size]
    raise ValueError(f"virtual range {address:#x}+{size:#x} is not file-backed")


def encode_bal(pc: int, target: int, link_register: int) -> int:
    """Encode VU BAL using its signed, 8-byte-scaled, 14-bit PC space."""
    if pc & 7 or target & 7 or not 0 <= pc < 0x4000 or not 0 <= target < 0x4000:
        raise ValueError("VU BAL PC/target must be aligned addresses in 0..0x3fff")
    if not 0 <= link_register < 32:
        raise ValueError("invalid VU link register")
    delta = (target - (pc + 8)) & 0x3FFF
    if delta >= 0x2000:
        delta -= 0x4000
    if delta & 7 or not -0x2000 <= delta < 0x2000:
        raise ValueError("VU BAL target is out of range")
    immediate = delta // 8
    if not -1024 <= immediate <= 1023:
        raise ValueError("VU BAL displacement exceeds signed 11-bit range")
    return 0x42000000 | (link_register << 16) | (immediate & 0x7FF)


def patchable_vu_slot(code: bytes, pc: int) -> bool:
    """Accept only upper NOP plus lower NOP/B/BAL source instructions."""
    if pc & 7 or pc < 0 or pc + 8 > len(code):
        return False
    lower, upper = struct.unpack_from("<II", code, pc)
    return upper == 0x000002FF and (lower == 0x8000033C or lower & 0xFC000000 == 0x40000000)


def validate_addiu_literal(data: bytes, segments: list[dict], pc: int, target: int,
                           base_register: int, dest_register: int, lui_pc: int) -> None:
    lui = struct.unpack("<I", read_virtual(data, segments, lui_pc, 4))[0]
    addiu = struct.unpack("<I", read_virtual(data, segments, pc, 4))[0]
    if (lui != (0x3C000000 | (base_register << 16)) or addiu >> 26 != 9
            or (addiu >> 21) & 31 != base_register or (addiu >> 16) & 31 != dest_register
            or addiu & 0xFFFF != target):
        raise ValueError(f"unexpected static target writer at {pc:#x}")


def tarzan_patch_candidates(data: bytes, segments: list[dict], source_code: bytes) -> dict:
    """Derive guarded light/material BAL target pools from this exact game ELF."""
    digest = hashlib.sha256(data).hexdigest()
    if digest != TARZAN_ELF_SHA256:
        raise ValueError("--tarzan-patches requires the validated Tarzan ELF SHA-256")

    light_targets = {}
    for pc in sorted(TARZAN_LIGHT_WRITERS):
        lui, addiu = struct.unpack("<II", read_virtual(data, segments, pc - 4, 8))
        # LUI a0,0; ADDIU a1,a0,imm. Keep this strict so a changed ELF fails closed.
        if lui != 0x3C040000 or addiu >> 26 != 9 or (addiu >> 21) & 31 != 4 or (addiu >> 16) & 31 != 5:
            raise ValueError(f"unexpected light target writer at {pc:#x}")
        light_targets[pc] = addiu & 0xFFFF
    for pc, target in TARZAN_LIGHT_EXTRA_WRITERS.items():
        validate_addiu_literal(data, segments, pc, target, 2, 3, pc - 4)
        light_targets[pc] = target

    material_targets = {}
    for pc in range(0x3DD86C, 0x3DD960, 4):
        lui, addiu = struct.unpack("<II", read_virtual(data, segments, pc, 8))
        reg = (lui >> 16) & 31
        imm = addiu & 0xFFFF
        if (lui >> 26 == 15 and (lui & 0xFFFF) == 0 and addiu >> 26 == 9
                and (addiu >> 21) & 31 == reg and (addiu >> 16) & 31 == 4
                and 0x500 <= imm <= 0x4000 and imm % 8 == 0):
            material_targets[pc] = imm
    if set(material_targets.values()) != TARZAN_MATERIAL_TARGETS or len(material_targets) != 10:
        raise ValueError(f"material target table changed: {sorted(set(material_targets.values()))}")
    for pc, (target, base_register, dest_register, lui_pc) in TARZAN_MATERIAL_EXTRA_WRITERS.items():
        validate_addiu_literal(data, segments, pc, target, base_register, dest_register, lui_pc)
        material_targets[pc] = target

    groups = {
        "light": {"targets": light_targets, "patch_pcs": list(range(0x3138, 0x3199, 0x10)), "link_register": 14},
        "material": {"targets": material_targets, "patch_pcs": list(range(0xE8, 0x259, 0x10)), "link_register": 15},
    }
    candidates = []
    for group_name, group in groups.items():
        patch_pcs = [pc for pc in group["patch_pcs"] if patchable_vu_slot(source_code, pc)]
        if not patch_pcs:
            raise ValueError(f"no validated source BAL slots for {group_name}")
        for source_pc, target in group["targets"].items():
            code = bytearray(source_code)
            words = {}
            for pc in patch_pcs:
                word = encode_bal(pc, target, group["link_register"])
                struct.pack_into("<I", code, pc, word)
                words[f"{pc:#x}"] = f"{word:#010x}"
            name = f"{group_name}-target-{target:04x}-from-{source_pc:06x}.vu1"
            candidates.append({"group": group_name, "source_ee_pc": source_pc,
                               "target_vu_pc": target, "link_register": group["link_register"],
                               "patch_pcs": patch_pcs, "words": words,
                               "file": name, "code": bytes(code)})
    base_pcs = sorted({pc for group in groups.values() for pc in group["patch_pcs"]
                       if patchable_vu_slot(source_code, pc)})
    candidates.append({"group": "base", "source_ee_pc": None, "target_vu_pc": None,
                       "link_register": None, "patch_pcs": base_pcs,
                       "words": {f"{pc:#x}": f"{struct.unpack_from('<I', source_code, pc)[0]:#010x}"
                                 for pc in base_pcs},
                       "file": "base-main.vu1", "code": source_code})
    return {"groups": groups, "candidates": candidates}


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


def extract(elf_path: Path, out_dir: Path, banks: list[Path], tarzan_patches: bool = False) -> dict:
    data = elf_path.read_bytes()
    if tarzan_patches and hashlib.sha256(data).hexdigest() != TARZAN_ELF_SHA256:
        raise ValueError("--tarzan-patches requires the validated Tarzan ELF SHA-256")
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
    patch_manifest = None
    if tarzan_patches:
        source = next((c for c in chains if c["packets"][0]["destination"] == 0 and c["slots"] == 2028), None)
        if source is None:
            raise ValueError("validated source main VU1 chain (destination 0, 2028 slots) not found")
        source_code = source["code"] + struct.pack("<II", 0x8000033C, 0x000002FF) * (VU1_SLOTS - source["slots"])
        static = tarzan_patch_candidates(data, segments, source_code)
        patch_dir = out_dir / "tarzan-patch-candidates"
        patch_dir.mkdir(parents=True, exist_ok=True)
        patch_manifest = {"source_chain_slots": source["slots"], "source_chain_sha256": hashlib.sha256(source["code"]).hexdigest(),
                          "groups": {}, "candidates": []}
        for group_name, group in static["groups"].items():
            patch_manifest["groups"][group_name] = {
                "source_ee_pcs": [f"{pc:#x}" for pc in sorted(group["targets"])],
                "target_vu_pcs": [f"{target:#x}" for target in sorted(set(group["targets"].values()))],
                "entry_vu_pcs": [f"{pc:#x}" for pc in group["patch_pcs"]],
            }
        for candidate in static["candidates"]:
            (patch_dir / candidate["file"]).write_bytes(candidate["code"])
            patch_manifest["candidates"].append({k: v for k, v in candidate.items() if k != "code"})
    manifest = {"input_elf": str(elf_path), "input_size": len(data),
                "input_sha256": hashlib.sha256(data).hexdigest(),
                "pt_load_count": len(segments), "chain_count": len(records), "chains": records}
    if tarzan_patches:
        manifest["tarzan_patch_candidates"] = patch_manifest
    (out_dir / "coverage.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("elf", type=Path, help="local ELF32 little-endian MIPS executable")
    ap.add_argument("--output", type=Path, default=Path("runtime/vu1-static-elf"))
    ap.add_argument("--compare-bank", action="append", type=Path, default=[],
                    help="directory of local full 16 KiB .vu1 banks (repeatable)")
    ap.add_argument("--tarzan-patches", action="store_true",
                    help="derive guarded candidate VU BAL patches (requires the exact validated Tarzan ELF)")
    args = ap.parse_args()
    report = extract(args.elf, args.output, args.compare_bank, args.tarzan_patches)
    print(f"extracted {report['chain_count']} VU1 chains to {args.output}")
    print(f"coverage: {args.output / 'coverage.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
