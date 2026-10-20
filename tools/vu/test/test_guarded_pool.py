"""Focused checks for guarded multi-image VU1 graph selection."""

import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import vu1recomp


NOP = 0x8000033C
UPPER_NOP = 0x000002FF


def put_pair(code, pc, lower, upper):
    code[pc:pc + 4] = lower.to_bytes(4, "little")
    code[pc + 4:pc + 8] = upper.to_bytes(4, "little")


class GuardedPoolTests(unittest.TestCase):
    def test_distinct_uploads_keep_separate_graphs_and_exact_guards(self):
        first = bytearray(vu1recomp.VU1_CODE_SIZE)
        second = bytearray(vu1recomp.VU1_CODE_SIZE)
        for code in (first, second):
            for pc in range(0, len(code), 8):
                put_pair(code, pc, NOP, UPPER_NOP)
            put_pair(code, 8, NOP, UPPER_NOP | 0x40000000)
        put_pair(second, 0, NOP, UPPER_NOP | 0x00010000)
        source, stats = vu1recomp.generate_guarded_pool([bytes(first), bytes(second)], [0], {})
        self.assertEqual(source.count('const Vu1RecRegistrar kRegistrar('), 1)
        self.assertEqual(source.count('static void vu1rec_guarded_program_'), 2)
        for code in (first, second):
            signature = int.from_bytes(code[:8], 'little')
            self.assertIn(f'case 0x{signature:016x}ull: return vu1rec_guarded_program_', source)
        self.assertEqual(source.count('default: ctx.failPc = 0x0000u; goto FAIL;'), 2)
        self.assertIn('ctx.failed = true; ctx.failPc = 0;', source)
        self.assertEqual(stats['entries'], [0, 24])

    def test_selector_dispatches_distinct_pairs_and_unknown_fails(self):
        first = bytearray(vu1recomp.VU1_CODE_SIZE)
        second = bytearray(vu1recomp.VU1_CODE_SIZE)
        for code in (first, second):
            for pc in range(0, len(code), 8):
                put_pair(code, pc, NOP, UPPER_NOP)
            put_pair(code, 8, NOP, UPPER_NOP | 0x40000000)  # E-bit pair
        put_pair(second, 0, NOP, UPPER_NOP | 0x00010000)  # still NOP, distinct exact word

        gen = vu1recomp.Generator(bytes(first), [0], "guarded", unit="vu1",
                                  pool_codes=[bytes(first), bytes(second)])
        gen.build()
        selector = gen.nodes[gen.entry_nodes[0]]
        self.assertIsNone(selector.pair)
        self.assertEqual(len(selector.alternatives), 2)
        self.assertEqual({gen.nodes[n].pair.upper for n in selector.alternatives},
                         {UPPER_NOP, UPPER_NOP | 0x00010000})

        source, _ = vu1recomp.generate_image(bytes(first), [0], "guarded",
                                             set(vu1recomp.FLAG_REGS), {}, "vu1",
                                             [bytes(first), bytes(second)])
        self.assertIn("if (!ctx.code)", source)
        self.assertIn("std::memcpy(&codePair, ctx.code + 0x0000u", source)
        self.assertIn("default: ctx.failPc = 0x0000u; goto FAIL;", source)
        self.assertIn("const Vu1RecRegistrar kRegistrar(0ull", source)
        self.assertIn(", true);", source)
        self.assertIn("st.mac = mac; st.status = status; st.clip = clip;", source)
        self.assertEqual(source.count("static void vu1rec_guarded_full("), 1)
        self.assertNotIn("static void vu1rec_guarded_fast(", source)
        self.assertIn("&vu1rec_guarded_full, &vu1rec_guarded_full", source)

    def test_regular_single_image_graph_remains_concrete(self):
        code = bytearray(vu1recomp.VU1_CODE_SIZE)
        for pc in range(0, len(code), 8):
            put_pair(code, pc, NOP, UPPER_NOP)
        put_pair(code, 8, NOP, UPPER_NOP | 0x40000000)
        gen = vu1recomp.Generator(bytes(code), [0], "regular")
        gen.build()
        self.assertIsNotNone(gen.nodes[gen.entry_nodes[0]].pair)
        self.assertFalse(gen.guarded)

    def test_static_jr_delay_fallthrough_is_guarded_entry(self):
        code = bytearray(vu1recomp.VU1_CODE_SIZE)
        for pc in range(0, len(code), 8):
            put_pair(code, pc, NOP, UPPER_NOP)
        put_pair(code, 0, 0x24 << 25, UPPER_NOP)  # unconditional JR
        put_pair(code, 8, NOP, UPPER_NOP)         # delay pair
        put_pair(code, 0x10, NOP, UPPER_NOP | 0x40000000)
        gen = vu1recomp.Generator(bytes(code), [0], "guarded", unit="vu1",
                                  pool_codes=[bytes(code)])
        gen.build()
        self.assertIn(0x10, gen.static_indirect_entries)
        self.assertIn(0x10, gen.entry_nodes)

    def test_reachable_unsupported_variant_is_a_transactional_reject_node(self):
        good = bytearray(vu1recomp.VU1_CODE_SIZE)
        bad = bytearray(vu1recomp.VU1_CODE_SIZE)
        for code in (good, bad):
            for pc in range(0, len(code), 8):
                put_pair(code, pc, NOP, UPPER_NOP)
            put_pair(code, 8, NOP, UPPER_NOP | 0x40000000)
        put_pair(bad, 0, 0xFFFFFFFF, UPPER_NOP)  # reserved lower instruction
        gen = vu1recomp.Generator(bytes(good), [0], "guarded",
                                  pool_codes=[bytes(good), bytes(bad)])
        gen.build()
        selector = gen.nodes[gen.entry_nodes[0]]
        rejects = [gen.nodes[n] for n in selector.alternatives if gen.nodes[n].rejected]
        self.assertEqual(len(rejects), 1)

        source, _ = vu1recomp.generate_image(bytes(good), [0], "guarded",
                                             set(vu1recomp.FLAG_REGS), {}, "vu1",
                                             [bytes(good), bytes(bad)])
        self.assertIn("unsupported guarded pair at pc 0x0000", source)
        self.assertIn("ctx.failPc = 0x0000u; goto FAIL;", source)

    def test_explicit_guarded_entry_parser_checks_alignment_and_vu1_bounds(self):
        self.assertEqual(vu1recomp.parse_vu1_entry("0x1010"), 0x1010)
        self.assertEqual(vu1recomp.parse_vu1_entry("4080"), 4080)
        for value in ("0x1011", "-8", "0x4000", "junk"):
            with self.subTest(value=value), self.assertRaises(vu1recomp.argparse.ArgumentTypeError):
                vu1recomp.parse_vu1_entry(value)

    def test_guarded_cli_adds_repeatable_indirect_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            catalog = root / "catalog"
            catalog.mkdir()
            (catalog / "index.txt").write_text("")
            code_path = root / "code.vu1"
            code = bytearray(vu1recomp.VU1_CODE_SIZE)
            for pc in range(0, len(code), 8):
                put_pair(code, pc, NOP, UPPER_NOP | 0x40000000)
            put_pair(code, 0, 0x24 << 25, UPPER_NOP)  # JR
            put_pair(code, 8, NOP, UPPER_NOP)
            code_path.write_bytes(code)
            out = root / "generated"
            argv = ["vu1recomp.py", "--unit", "vu1", "--catalog", str(catalog),
                    "--out", str(out), "--guarded", "--entry", "0",
                    "--indirect-entry", "0x0030", "--pool-code", str(code_path)]
            with patch.object(vu1recomp.sys, "argv", argv):
                self.assertEqual(vu1recomp.main(), 0)
            generated = (out / "vu1rec_guarded.cpp").read_text()
            self.assertIn("case 0x0030u: goto", generated)
            entries = next(line for line in generated.splitlines() if 'kEntries[]' in line)
            self.assertNotIn('0x0030u', entries)


if __name__ == "__main__":
    unittest.main()
