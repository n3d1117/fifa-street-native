#!/usr/bin/env python3
"""Tests for conservative, code-only VU branch-table target discovery."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import vu1recomp


def put_pair(code: bytearray, pc: int, lower: int, upper: int = 0) -> None:
    code[pc:pc + 4] = lower.to_bytes(4, "little")
    code[pc + 4:pc + 8] = upper.to_bytes(4, "little")


def branch(op_hi: int, pc: int, target: int) -> int:
    delta = (target - (pc + 8)) & 0x3FFF
    if delta & 7:
        raise ValueError("branch target must be pair-aligned")
    return (op_hi << 25) | ((delta >> 3) & 0x7FF)


class StaticBranchTargetTests(unittest.TestCase):
    def test_finds_repeated_branch_arms_with_vu1_bounds(self):
        code = bytearray(vu1recomp.VU1_CODE_SIZE)
        for pc in (0x100, 0x110, 0x120):
            put_pair(code, pc, branch(0x20, pc, 0x180))
        self.assertEqual(vu1recomp.find_static_branch_table_targets(bytes(code)),
                         [0x100, 0x110, 0x120])

    def test_requires_three_consecutive_arms_same_opcode_and_target(self):
        code = bytearray(vu1recomp.VU1_CODE_SIZE)
        put_pair(code, 0x100, branch(0x20, 0x100, 0x180))
        put_pair(code, 0x110, branch(0x20, 0x110, 0x180))
        self.assertEqual(vu1recomp.find_static_branch_table_targets(bytes(code)), [])
        put_pair(code, 0x120, branch(0x21, 0x120, 0x180))  # BAL differs from B.
        self.assertEqual(vu1recomp.find_static_branch_table_targets(bytes(code)), [])

    def test_ignores_i_bit_and_respects_vu0_code_size(self):
        code = bytearray(vu1recomp.VU0_CODE_SIZE)
        for pc in (0x100, 0x110, 0x120):
            put_pair(code, pc, branch(0x20, pc, 0x180))
        put_pair(code, 0x110, branch(0x20, 0x110, 0x180), 0x80000000)
        for pc in (0x200, 0x210, 0x220):
            put_pair(code, pc, branch(0x20, pc, 0x280))
        self.assertEqual(vu1recomp.find_static_branch_table_targets(bytes(code), "vu0"),
                         [0x200, 0x210, 0x220])
        self.assertEqual(vu1recomp.Generator(bytes(code), [0], "vu0",
                                             unit="vu0").static_branch_targets, [])
        with self.assertRaisesRegex(vu1recomp.GenError, "VU0 image size"):
            vu1recomp.find_static_branch_table_targets(bytes(vu1recomp.VU1_CODE_SIZE), "vu0")

    def test_dynamic_branch_switch_includes_discovered_arms(self):
        code = bytearray(vu1recomp.VU1_CODE_SIZE)
        put_pair(code, 0, (0x24 << 25) | (1 << 21))  # JR vi1
        put_pair(code, 8, 0x8000033C)                # branch delay pair
        for pc in (0x100, 0x110, 0x120):
            put_pair(code, pc, branch(0x20, pc, 0x180))
        put_pair(code, 0x180, 0x8000033C, 0x400002FF)  # E-bit, followed by delay pair
        put_pair(code, 0x188, 0x8000033C)

        gen = vu1recomp.Generator(bytes(code), [0], "synthetic")
        gen.build()
        dynamic_terms = [node.term for node in gen.nodes
                         if node.term is not None and node.term[0] == "dyn"]
        self.assertTrue(dynamic_terms)
        labels = {pc for term in dynamic_terms for pc, _ in term[1]}
        self.assertTrue({0x100, 0x110, 0x120}.issubset(labels))


if __name__ == "__main__":
    unittest.main()
