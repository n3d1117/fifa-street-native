"""Tests for VU flag liveness and conservative fallback."""

import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import vu1recomp


class Graph:
    def __init__(self, nodes):
        self.nodes = nodes
        self.name = "synthetic"
        self.entry_nodes = {}
        self.failed_entries = {}

    @staticmethod
    def node_targets(node):
        return [node.term[1]] if node.term and node.term[0] == "goto" else []


def fmac_node(lower):
    pair = vu1recomp.decode_pair(lower.to_bytes(4, "little") + bytes(4), 0)
    return vu1recomp.Node(0, 0, vu1recomp.AState(), "pre", pair=pair,
                          term=("end", [], 0), new_slots={"fmac": 0})


class FlagLivenessTests(unittest.TestCase):
    def test_mac_read_after_write_keeps_fmac_flags(self):
        producer = fmac_node(0)
        producer.post_ops = [("commit_mac", 0)]
        producer.term = ("goto", 1)
        reader = fmac_node(0x18 << 25)  # FMEQ reads MAC after the FMAC commit.
        reader.new_slots = {}
        g = Graph([producer, reader])
        vu1recomp.compute_flag_liveness(g, set())
        self.assertTrue(g.nodes[0].flag_need["body"][("def_fmac", 0)])

    def test_dead_fmac_flag_write_is_elided(self):
        g = Graph([fmac_node(0)])
        vu1recomp.compute_flag_liveness(g, set())
        self.assertFalse(g.nodes[0].flag_need["body"][("def_fmac", 0)])

    def test_unanalysable_target_forces_all_flags_live(self):
        g = Graph([SimpleNamespace(term=("dyn", []))])
        self.assertTrue(vu1recomp.liveness_is_uncertain(g))
        info = {"g": Graph([]), "conts": [], "cont_g": None, "cont_error": None,
                "uncertain": True}
        self.assertEqual(vu1recomp.compute_live_exit([info])[0], set(vu1recomp.FLAG_REGS))


if __name__ == "__main__":
    unittest.main()
