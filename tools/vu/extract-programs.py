#!/usr/bin/env python3
"""Derive the VU1 microprograms the game uploads from the disc and boot ELF.

EAGL microprograms ("...__EAGLMicroCode" objects) are DMA chains of VIF MPG
uploads. They are linked into the boot ELF (a few static ones) and into the
`mat.o` member of `DATA/INITSTAZ.BIG` (BIGF wrapper holding a refpack-compressed
BIGF, whose `mat.o` is a MIPS relocatable). This tool walks every upload chain
the way VIF1 sees it (DMA tag payloads only, TTE off), checks the MPG word
counts against the descriptor, and writes one raw program per distinct
microcode image:

  <out>/index.txt        "<name> <slots> <fnv1a64 of words>"
  <out>/<name>.vup       slots * 8 bytes, the words as VIF1 stores them in micro memory

An odd-sized final MPG consumes the first 8 bytes of the next DMA packet's
payload (the stream is qword packed), so those words are part of the program.
The games assembles banks by placing these programs at varying micro-memory
bases; position independence is handled by the generator (`vu1recomp.py
--programs`), not here. Output is disc-derived: keep it out of git.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
MAX_SLOTS = 2048


def fnv1a64(data: bytes) -> int:
    h = FNV_OFFSET
    for b in data:
        h = ((h ^ b) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return h


def refpack(buf: bytes, pos: int = 0) -> bytes:
    flags, magic = buf[pos], buf[pos + 1]
    if magic != 0xFB:
        raise ValueError("not a refpack stream")
    pos += 2
    width = 4 if flags & 0x80 else 3
    if flags & 0x01:
        pos += width
    size = int.from_bytes(buf[pos:pos + width], "big")
    pos += width
    out = bytearray()
    while True:
        c = buf[pos]
        pos += 1
        if c < 0x80:
            c1 = buf[pos]
            pos += 1
            plain = c & 3
            out += buf[pos:pos + plain]
            pos += plain
            ref = len(out) - (((c & 0x60) << 3) + c1 + 1)
            for i in range((c & 0x1C) // 4 + 3):
                out.append(out[ref + i])
        elif c < 0xC0:
            c1, c2 = buf[pos], buf[pos + 1]
            pos += 2
            plain = (c1 >> 6) & 3
            out += buf[pos:pos + plain]
            pos += plain
            ref = len(out) - (((c1 & 0x3F) << 8) + c2 + 1)
            for i in range((c & 0x3F) + 4):
                out.append(out[ref + i])
        elif c < 0xE0:
            c1, c2, c3 = buf[pos], buf[pos + 1], buf[pos + 2]
            pos += 3
            plain = c & 3
            out += buf[pos:pos + plain]
            pos += plain
            ref = len(out) - (((c & 0x10) << 12) + (c1 << 8) + c2 + 1)
            for i in range(((c & 0x0C) << 6) + c3 + 5):
                out.append(out[ref + i])
        elif c < 0xFC:
            plain = ((c & 0x1F) << 2) + 4
            out += buf[pos:pos + plain]
            pos += plain
        else:
            plain = c & 3
            out += buf[pos:pos + plain]
            break
    if len(out) != size:
        raise ValueError(f"refpack size {len(out)} != header {size}")
    return bytes(out)


def bigf_members(buf: bytes) -> dict[str, bytes]:
    if buf[:4] != b"BIGF":
        raise ValueError("not a BIGF archive")
    count = int.from_bytes(buf[8:12], "big")
    pos, members = 16, {}
    for _ in range(count):
        off = int.from_bytes(buf[pos:pos + 4], "big")
        size = int.from_bytes(buf[pos + 4:pos + 8], "big")
        end = buf.index(b"\0", pos + 8)
        members[buf[pos + 8:end].decode()] = buf[off:off + size]
        pos = end + 1
    return members


def elf32(buf: bytes):
    if buf[:4] != b"\x7fELF" or buf[4:7] != b"\x01\x01\x01":
        raise ValueError("expected ELF32 little-endian")
    return buf


def segments(elf: bytes):
    phoff = struct.unpack_from("<I", elf, 28)[0]
    phnum = struct.unpack_from("<H", elf, 44)[0]
    out = []
    for i in range(phnum):
        t, off, va, _pa, fsz, _msz, _fl, _al = struct.unpack_from("<IIIIIIII", elf, phoff + i * 32)
        if t == 1:
            out.append((va, off, fsz))
    return out


class Space:
    """Address space resolver: bytes at virtual addresses (ELF) or section offsets (object)."""

    def __init__(self, regions):
        self.regions = regions  # [(base, bytes)]

    def read(self, addr: int, size: int) -> bytes:
        for base, data in self.regions:
            if base <= addr and addr + size <= base + len(data):
                return data[addr - base:addr - base + size]
        raise ValueError(f"unmapped read {addr:#x}+{size:#x}")


def find_descriptors(space: Space):
    """EAGLMicroCode descriptor: {slots, n, 0xffffffff, chain, chain_end, ...}."""
    found = []
    for base, data in space.regions:
        for off in range(0, len(data) - 31, 4):
            d = struct.unpack_from("<8I", data, off)
            if d[2] != 0xFFFFFFFF or not 0 < d[0] <= MAX_SLOTS or d[3] & 15 or d[4] <= d[3]:
                continue
            found.append((base + off, d[0], d[3]))
    return found


def read_chain_payload(space: Space, chain: int, slots: int) -> bytes:
    """Concatenated MPG payload of the chain starting at `chain`, `slots` words long."""
    stream = bytearray()
    addr = chain
    for _ in range(64):
        tag = struct.unpack("<Q", space.read(addr, 8))[0]
        qwc, tid = tag & 0xFFFF, (tag >> 28) & 7
        ptr = (tag >> 32) & 0xFFFFFFFF
        if tid == 1:        # CNT
            stream += space.read(addr + 16, qwc * 16)
            addr += 16 + qwc * 16
        elif tid == 3:      # REF
            stream += space.read(ptr, qwc * 16)
            addr += 16
        elif tid == 2:      # NEXT
            stream += space.read(addr + 16, qwc * 16)
            addr = ptr
        elif tid in (0, 7):  # REFE, END
            stream += space.read(ptr if tid == 0 else addr + 16, qwc * 16)
            break
        else:
            raise ValueError(f"unexpected DMA tag id {tid} at {addr:#x}")
        words = parse_mpg(bytes(stream), slots)
        if words is not None:
            return words
    raise ValueError(f"chain at {chain:#x}: no complete MPG upload of {slots} slots")


def parse_mpg(stream: bytes, slots: int):
    """Return the MPG payload when `stream` holds all `slots` words, else None."""
    pos, payload = 0, bytearray()
    while pos + 4 <= len(stream):
        word = struct.unpack_from("<I", stream, pos)[0]
        pos += 4
        cmd, num = (word >> 24) & 0x7F, (word >> 16) & 0xFF
        if cmd == 0x4A:
            n = num or 256
            if pos + n * 8 > len(stream):
                return None
            payload += stream[pos:pos + n * 8]
            pos += n * 8
            if len(payload) == slots * 8:
                return bytes(payload)
        elif cmd == 0x20 or cmd == 0x30 or cmd == 0x31:   # STMASK/STROW/STCOL carry words
            pos += 16 if cmd in (0x30, 0x31) else 4
        elif cmd in (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x10, 0x11, 0x13, 0x14, 0x15, 0x17):
            continue
        else:
            raise ValueError(f"unexpected VIF command {cmd:#x}")
    return None


def programs_from(space: Space, label: str):
    out = []
    for addr, slots, chain in find_descriptors(space):
        try:
            code = read_chain_payload(space, chain, slots)
        except (ValueError, struct.error):
            continue
        out.append((f"{label}_{addr:x}", code))
    return out


def object_symbols(obj: bytes):
    """name -> section offset for global object symbols of a relocatable."""
    shoff = struct.unpack_from("<I", obj, 0x20)[0]
    shentsize, shnum = struct.unpack_from("<HH", obj, 0x2E)
    secs = [struct.unpack_from("<IIIIIIIIII", obj, shoff + i * shentsize) for i in range(shnum)]
    symtab = next(s for s in secs if s[1] == 2)
    strtab = secs[symtab[6]]
    names = {}
    for i in range(symtab[5] // 16):
        n, val, _size, info, _o, _shndx = struct.unpack_from("<IIIBBH", obj, symtab[4] + i * 16)
        if n and info == 17:
            end = obj.index(b"\0", strtab[4] + n)
            names[val] = obj[strtab[4] + n:end].decode()
    data = next(s for s in secs if s[1] == 1 and s[2] & 2)
    return names, obj[data[4]:data[4] + data[5]]


def extract(disc: str):
    programs = []
    elf = elf32(open(os.path.join(disc, "SLES_530.64"), "rb").read())
    regions = [(va, elf[off:off + fsz]) for va, off, fsz in segments(elf)]
    programs += programs_from(Space(regions), "elf")

    big = open(os.path.join(disc, "DATA", "INITSTAZ.BIG"), "rb").read()
    outer = bigf_members(big)
    inner = bigf_members(refpack(next(iter(outer.values()))))
    names, data = object_symbols(inner["mat.o"])
    space = Space([(0, data)])
    for addr, slots, chain in find_descriptors(space):
        try:
            code = read_chain_payload(space, chain, slots)
        except (ValueError, struct.error):
            continue
        programs.append((names.get(addr, f"mat_{addr:x}"), code))
    return programs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--disc", required=True, help="extracted disc root (SLES_530.64, DATA/)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    programs = extract(args.disc)
    unique = {}
    for name, code in programs:
        unique.setdefault(fnv1a64(code), (name, code))
    os.makedirs(args.out, exist_ok=True)
    lines = []
    for h, (name, code) in sorted(unique.items(), key=lambda kv: kv[1][0]):
        with open(os.path.join(args.out, f"{name}.vup"), "wb") as f:
            f.write(code)
        lines.append(f"{name} {len(code) // 8} {h:016x}\n")
    text = "".join(lines)
    index = os.path.join(args.out, "index.txt")
    if not os.path.exists(index) or open(index).read() != text:
        with open(index, "w") as f:
            f.write(text)
    keep = {f"{name}.vup" for name, _ in unique.values()} | {"index.txt"}
    for fn in os.listdir(args.out):
        if fn.endswith(".vup") and fn not in keep:
            os.remove(os.path.join(args.out, fn))
    print(f"[vu-programs] {len(unique)} distinct microprograms ({len(programs)} descriptors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
