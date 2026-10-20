"""Position-independent VU1 program generation and upload-chain extraction."""

import importlib.util
import pathlib
import struct
import sys
import unittest

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import vu1recomp

spec = importlib.util.spec_from_file_location("extract_programs", TOOLS / "extract-programs.py")
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)

NOP = 0x8000033C
UPPER_NOP = 0x000002FF
E_BIT = 0x40000000


def pair(lower, upper):
    return struct.pack("<II", lower, upper)


def vu1recomp_bal(pc, target, link):
    return 0x42000000 | (link << 16) | (((target - (pc + 8)) // 8) & 0x7FF)


def jr(reg):
    return 0x48000000 | (reg << 11)


class RelocGenerationTests(unittest.TestCase):
    def program(self):
        # 0: E-bit stub, 16: BAL vi1 -> 40, 24: NOP, 32: E-bit end + delay slot, 40: JR vi1.
        words = pair(NOP, UPPER_NOP | E_BIT) + pair(NOP, UPPER_NOP)
        words += pair(vu1recomp_bal(16, 40, 1), UPPER_NOP) + pair(NOP, UPPER_NOP)
        words += pair(NOP, UPPER_NOP | E_BIT) + pair(NOP, UPPER_NOP)
        words += pair(jr(1), UPPER_NOP) + pair(NOP, UPPER_NOP)
        return words

    def test_base_relative_links_targets_and_exit(self):
        source, stats = vu1recomp.generate_reloc_program("probe", self.program())
        self.assertIn("const uint32_t base = ctx.relocBase;", source)
        self.assertIn("switch ((entryPc - base) & 0x3fffu)", source)
        self.assertIn("(base >> 3) + 4", source)          # BAL at 0x10 links to pc 0x20 (slot 4)
        self.assertIn("switch ((brt - base) & 0x3fffu)", source)
        self.assertIn("st.pc = base + 0x", source)
        self.assertIn("Vu1RecProgramRegistrar kRegistrar(\"probe\", 8u,", source)
        self.assertEqual(stats['entries'][0], 0)
        self.assertNotIn("codePair", source)              # whole-program match, no per-pair guard

    def test_execution_past_the_program_fails_closed(self):
        words = pair(NOP, UPPER_NOP) + pair(NOP, UPPER_NOP)   # falls off the end
        source, _ = vu1recomp.generate_reloc_program("falloff", words)
        self.assertIn("unsupported guarded pair at pc 0x0010", source)

    def test_rejects_oversized_program(self):
        with self.assertRaises(vu1recomp.GenError):
            vu1recomp.generate_reloc_program("big", bytes(vu1recomp.VU1_CODE_SIZE + 8))


class UploadChainTests(unittest.TestCase):
    @staticmethod
    def tag(qwc, tid, addr=0):
        return struct.pack("<II", qwc | (tid << 28), addr) + bytes(8)

    def test_odd_final_mpg_spills_into_next_packet(self):
        code = b"".join(struct.pack("<Q", 0x1111 * (i + 1)) for i in range(3))   # 3 slots
        mpg = struct.pack("<I", 0x4A000000 | (3 << 16))
        base = 0x100
        data = bytearray(0x400)
        # CNT qwc=1 carrying [NOP, NOP, NOP, MPG num=3]; REF of one qword (2 slots); CNT whose
        # first 8 payload bytes supply the odd third slot.
        cnt = self.tag(1, 1) + bytes(12) + mpg
        ref = self.tag(1, 3, 0x300)
        tail = self.tag(1, 1) + struct.pack("<Q", 0xABCD) + bytes(8)
        data[base:base + len(cnt) + len(ref) + len(tail)] = cnt + ref + tail
        data[0x300:0x310] = code[:16]
        space = extract.Space([(0, bytes(data))])
        payload = extract.read_chain_payload(space, base, 3)
        self.assertEqual(payload, code[:16] + struct.pack("<Q", 0xABCD))

    def test_refpack_literal_stream(self):
        stream = bytes([0x10, 0xFB, 0, 0, 3]) + bytes([0xE0 | 0, ]) + b"\0" * 4
        # 0xE0 is a 4-byte literal run; size field says 3, so the decoder must reject the mismatch.
        with self.assertRaises(ValueError):
            extract.refpack(stream + bytes([0xFC]))


if __name__ == "__main__":
    unittest.main()
