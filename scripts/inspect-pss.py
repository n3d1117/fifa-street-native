#!/usr/bin/env python3
"""Inventory MPEG program-stream packets without scanning inside PES payloads."""
import argparse
import json
from collections import Counter
from pathlib import Path


LENGTH_PREFIXED = {0xBB, 0xBC, 0xBE, 0xBF, 0xF0, 0xF1, 0xF2, 0xF8, 0xFF}


def is_program_stream_id(value):
    return value in {0xB9, 0xBA, 0xBB, 0xBC, 0xBE, 0xBF} or 0xBD <= value <= 0xEF or value in {0xF0, 0xF1, 0xF2, 0xF8, 0xFF}


def parse_pes_header(packet):
    """Match the MPEG.cpp parsePesHeader cursor rules; packet starts at 00 00 01 id."""
    size = len(packet)
    pos = 6
    if size <= 6:
        return size
    if size >= 9 and packet[pos] & 0xC0 == 0x80:
        return min(size, 9 + packet[pos + 2])
    while pos < size and packet[pos] == 0xFF:
        pos += 1
    if pos + 1 < size and packet[pos] & 0xC0 == 0x40:
        pos += 2
    if pos >= size:
        return size
    marker = packet[pos] & 0xF0
    if marker == 0x20 and pos + 5 <= size:
        pos += 5
    elif marker == 0x30 and pos + 10 <= size:
        pos += 10
    elif packet[pos] == 0x0F:
        pos += 1
    return min(size, pos)


def scan(data):
    packets = []
    counts = Counter()
    payload_totals = Counter()
    pos = 0
    program_end_offset = None
    malformed = []
    while pos + 4 <= len(data):
        if data[pos:pos + 3] != b"\x00\x00\x01":
            malformed.append({"offset": pos, "reason": "expected program-stream start code"})
            # Keep recovery structural: find the next prefix only after a damaged boundary.
            nxt = data.find(b"\x00\x00\x01", pos + 1)
            if nxt < 0:
                break
            pos = nxt
            continue
        stream_id = data[pos + 3]
        if stream_id == 0xB9:
            program_end_offset = pos
            packets.append({"stream_id": "0xb9", "offset": pos, "length": 4, "payload_length": 0})
            pos += 4
            continue
        if stream_id == 0xBA:
            if pos + 12 > len(data):
                malformed.append({"offset": pos, "reason": "truncated pack header"})
                break
            if data[pos + 4] & 0xC0 == 0x40:
                if pos + 14 > len(data):
                    malformed.append({"offset": pos, "reason": "truncated MPEG-2 pack header"})
                    break
                pack_len = 14 + (data[pos + 13] & 7)
            else:
                pack_len = 12
            if pos + pack_len > len(data):
                malformed.append({"offset": pos, "reason": "truncated MPEG-2 pack stuffing"})
                break
            packets.append({"stream_id": "0xba", "offset": pos, "length": pack_len, "payload_length": 0})
            pos += pack_len
            continue
        if pos + 6 > len(data):
            malformed.append({"offset": pos, "reason": "truncated packet length"})
            break
        packet_length = int.from_bytes(data[pos + 4:pos + 6], "big")
        if stream_id in LENGTH_PREFIXED:
            packet_len = 6 + packet_length
            if pos + packet_len > len(data):
                malformed.append({"offset": pos, "reason": "truncated length-prefixed packet"})
                break
            packets.append({"stream_id": f"0x{stream_id:02x}", "offset": pos,
                            "length": packet_len, "payload_length": packet_length})
            counts[stream_id] += 1
            payload_totals[stream_id] += packet_length
            pos += packet_len
            continue

        if packet_length:
            packet_len = 6 + packet_length
            if pos + packet_len > len(data):
                malformed.append({"offset": pos, "reason": "truncated PES packet"})
                break
        else:
            # An unbounded PES ends at the next program-stream start code. No
            # prefix search occurs inside any length-bounded PES packet.
            packet_end = pos + 6
            while packet_end + 4 <= len(data):
                packet_end = data.find(b"\x00\x00\x01", packet_end)
                if packet_end < 0:
                    break
                if packet_end + 4 <= len(data) and is_program_stream_id(data[packet_end + 3]):
                    break
                packet_end += 3
            if packet_end < 0:
                malformed.append({"offset": pos, "reason": "unbounded PES has no following start code"})
                break
            packet_len = packet_end - pos
        packet = data[pos:pos + packet_len]
        payload_start = parse_pes_header(packet)
        payload_len = max(0, packet_len - payload_start)
        record = {"stream_id": f"0x{stream_id:02x}", "offset": pos,
                  "length": packet_len, "payload_offset": pos + payload_start,
                  "payload_length": payload_len,
                  "first32_payload_hex": packet[payload_start:payload_start + 32].hex()}
        packets.append(record)
        counts[stream_id] += 1
        payload_totals[stream_id] += payload_len
        pos += packet_len

    audio = [packet for packet in packets if packet["stream_id"] == "0xbd" and packet.get("payload_length", 0)]
    return {
        "file_size": len(data),
        "program_end_offset": program_end_offset,
        "packets": packets,
        "packet_counts_by_stream_id": {f"0x{k:02x}": v for k, v in sorted(counts.items())},
        "payload_bytes_by_stream_id": {f"0x{k:02x}": v for k, v in sorted(payload_totals.items())},
        "first_private_stream_1_audio": (audio[0] if audio else None),
        "first_audio_offset_mod_65536": (audio[0]["payload_offset"] % 65536 if audio else None),
        "malformed_boundaries": malformed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path,
                        default=Path("extracted/disc/GAMEDATA/VIDEO/LOGO.PSS"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = scan(args.input.read_bytes())
    report["input"] = str(args.input)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
