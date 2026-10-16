#!/usr/bin/env python3
"""Focused checks for the VU0 size, mask, and operation gates."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import vu1recomp


def put_pair(code: bytearray, pc: int, lower: int, upper: int) -> None:
    code[pc:pc + 4] = lower.to_bytes(4, "little")
    code[pc + 4:pc + 8] = upper.to_bytes(4, "little")


class Vu0GenerationTests(unittest.TestCase):
    def test_vu0_generates_with_4k_data_mask_and_separate_registrar(self):
        code = bytearray(vu1recomp.VU0_CODE_SIZE)
        # LQI vi1, vf1 followed by E-bit and its required delay pair.
        lqi = 0x8000037C | (1 << 16) | (1 << 11) | (0xF << 21)
        put_pair(code, 0x00, lqi, 0x000002FF)
        put_pair(code, 0x08, 0x8000033C, 0x400002FF)
        put_pair(code, 0x10, 0x8000033C, 0x000002FF)

        src, stats = vu1recomp.generate_image(bytes(code), [0], "0123456789abcdef", unit="vu0")
        self.assertEqual(stats["entries"], [0])
        self.assertIn("runtime/ps2_vu0_recomp_support.h", src)
        self.assertIn("Vu0RecContext &ctx", src)
        self.assertIn("Vu0RecRegistrar kRegistrar", src)
        self.assertIn("& 0x0FFFu", src)
        self.assertNotIn("vu1rec_kick_", src)

    def test_vu0_rejects_reachable_non_lqi_lower_instruction(self):
        code = bytearray(vu1recomp.VU0_CODE_SIZE)
        put_pair(code, 0x00, 0x10020000, 0x000002FF)  # IADDIU
        with self.assertRaisesRegex(vu1recomp.GenError, "outside the supported LQI/NOP subset"):
            vu1recomp.Generator(bytes(code), [0], "synthetic", unit="vu0").build()

    def test_vu1_default_sizes_are_unchanged(self):
        code = bytes(vu1recomp.VU1_CODE_SIZE)
        gen = vu1recomp.Generator(code, [0], "synthetic")
        self.assertEqual(gen.code_size, 0x4000)
        self.assertEqual(gen.pc_mask, 0x3FFF)
        self.assertEqual(gen.data_mask, 0x3FFF)


if __name__ == "__main__":
    unittest.main()
