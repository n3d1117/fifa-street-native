"""Catalogs cannot prove persisted flags are dead in an uncaptured program."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import vu1recomp


class FullFlagsTests(unittest.TestCase):
    def test_catalog_fast_preserves_flags_even_with_empty_live_exit(self):
        code = bytearray(vu1recomp.VU1_CODE_SIZE)
        # ADD.x vf1,vf2,vf3 followed by E-bit NOP and its delay pair.
        code[0:4] = (0x8000033C).to_bytes(4, "little")
        upper = (8 << 21) | (3 << 16) | (2 << 11) | (1 << 6) | 0x28
        code[4:8] = upper.to_bytes(4, "little")
        for pc, upper in ((8, 0x400002FF), (16, 0x000002FF)):
            code[pc:pc + 4] = (0x8000033C).to_bytes(4, "little")
            code[pc + 4:pc + 8] = upper.to_bytes(4, "little")
        source, stats = vu1recomp.generate_image(bytes(code), [0], "synthetic", set())
        self.assertEqual(set(stats["live_exit"]), set(vu1recomp.FLAG_REGS))
        self.assertEqual(stats["fmac_flag_producers_dead"], 0)
        fast = source.split("static void vu1rec_synthetic_full", 1)[0]
        for flag in vu1recomp.FLAG_REGS:
            self.assertIn(f"st.{flag} = {flag};", fast)
        self.assertIn("&vu1rec_synthetic_fast, &vu1rec_synthetic_full", source)


if __name__ == "__main__":
    unittest.main()
