import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "extract-vu-microcode.py"
SPEC = importlib.util.spec_from_file_location("extract_vu_microcode", SCRIPT)
EXTRACTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXTRACTOR)


def make_elf(segment_payload: bytes) -> bytes:
    phoff, seg_off = 52, 84
    header = bytearray(52)
    header[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<H", header, 16, 2)  # ET_EXEC
    struct.pack_into("<H", header, 18, 8)  # EM_MIPS
    struct.pack_into("<I", header, 20, 1)
    struct.pack_into("<I", header, 28, phoff)
    struct.pack_into("<H", header, 40, 52)
    struct.pack_into("<HHH", header, 42, 32, 1, 0)
    phdr = struct.pack("<IIIIIIII", 1, seg_off, 0x200000, 0, len(segment_payload),
                       len(segment_payload), 5, 4)
    return bytes(header) + phdr + segment_payload


def mpg(count: int, destination: int) -> bytes:
    tag = (0x4A << 24) | ((count & 0xFF) << 16) | destination
    return struct.pack("<II", 0, tag)


class ExtractVuMicrocodeTests(unittest.TestCase):
    def test_contiguous_packets_join_and_record_exact_source_metadata(self):
        # PT_LOAD begins at file offset 84 (4 mod 8), so the first aligned NOP
        # is at 88. Each instruction is a VU NOP with E set in its upper word.
        slot = struct.pack("<II", 0x8000033C, 0x40000000)
        payload = bytearray(36)
        payload[4:12] = mpg(1, 0)
        payload[12:20] = slot
        payload[20:28] = mpg(1, 1)
        payload[28:36] = slot
        segments = EXTRACTOR.load_segments(make_elf(bytes(payload)))
        chains = EXTRACTOR.find_chains(make_elf(bytes(payload)), segments)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["slots"], 2)
        self.assertEqual([p["destination"] for p in chains[0]["packets"]], [0, 1])
        self.assertEqual(chains[0]["code"], slot + slot)

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "game.elf"
            source.write_bytes(make_elf(bytes(payload)))
            output = Path(tmp) / "runtime-output"
            report = EXTRACTOR.extract(source, output, [])
            self.assertEqual(report["chain_count"], 1)
            entry = report["chains"][0]
            self.assertEqual(entry["source_file_offset"], 88)
            self.assertEqual(entry["destination_end"], 2)
            self.assertEqual((output / entry["file"]).read_bytes(), slot + slot)

    def test_truncated_payload_is_rejected_without_reading_next_file_bytes(self):
        payload = bytearray(16)
        payload[4:8] = struct.pack("<I", (0x4A << 24) | (2 << 16))
        data = make_elf(bytes(payload))
        segments = EXTRACTOR.load_segments(data)
        self.assertEqual(EXTRACTOR.find_chains(data, segments), [])

    def test_invalid_load_segment_bounds_fail_closed(self):
        data = bytearray(make_elf(b"\0" * 8))
        struct.pack_into("<I", data, 52 + 16, 0x1000)  # p_filesz beyond EOF
        with self.assertRaisesRegex(ValueError, "PT_LOAD"):
            EXTRACTOR.load_segments(bytes(data))

    def test_invalid_program_header_table_fails_closed(self):
        data = bytearray(make_elf(b"\0" * 8))
        struct.pack_into("<I", data, 28, len(data) - 4)
        with self.assertRaisesRegex(ValueError, "program-header table"):
            EXTRACTOR.load_segments(bytes(data))


if __name__ == "__main__":
    unittest.main()
