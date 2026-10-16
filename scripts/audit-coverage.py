#!/usr/bin/env python3
"""Measure mapped ELF translation coverage; never equate it with playthrough coverage."""
import csv
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def merged_size(ranges):
    total = 0
    end = 0
    for lo, hi in sorted(ranges):
        total += max(0, hi - max(lo, end))
        end = max(end, hi)
    return total


def main():
    quality = json.loads((ROOT / 'reports/generated-quality.json').read_text())
    with Path(quality['symbol_map']).open() as stream:
        rows = list(csv.DictReader(stream))
    ranges = [(int(r['Start'], 0), int(r['End'], 0)) for r in rows]
    source = Path(quality['output'])
    stub_text = (source / 'ps2_recompiled_stubs.h').read_text()
    stub_addresses = {int(x, 16) for x in re.findall(r'void \w+_0x([0-9a-f]+)\(', stub_text)}
    data = Path(quality['input_elf']).read_bytes()
    assert data[:6] == b'\x7fELF\x01\x01', 'Expected little-endian ELF32'
    shoff = struct.unpack_from('<I', data, 32)[0]
    shsize, shcount = struct.unpack_from('<HH', data, 46)
    executable = []
    for i in range(shcount):
        _, _, flags, address, _, size = struct.unpack_from('<6I', data, shoff + i * shsize)
        if flags & 4 and size:
            executable.append((address, address + size))
    intersections = [(max(a, lo), min(b, hi)) for a, b in ranges
                     for lo, hi in executable if max(a, lo) < min(b, hi)]
    executable_bytes = merged_size(executable)
    covered_bytes = merged_size(intersections)
    report = {
        'elf_sha256': quality['input_elf_sha256'],
        'mapped_functions': quality['function_count'],
        'translated_functions': quality['recompiled'],
        'runtime_replacement_functions': quality['stubs'],
        'translated_fraction_of_mapped_functions_percent': round(100 * quality['recompiled'] / quality['function_count'], 2),
        'skipped_functions': quality['skipped'],
        'decode_failures': quality['decode_failures'],
        'executable_section_bytes': executable_bytes,
        'mapped_executable_section_bytes': covered_bytes,
        'mapped_executable_bytes_percent': round(100 * covered_bytes / executable_bytes, 2),
        'runtime_replacement_mapped_bytes': merged_size([(a, b) for a, b in ranges if a in stub_addresses]),
        'readable_source_decompilation_percent': None,
        'full_game_playthrough_percent': None,
        'limitations': [
            'Function/byte coverage measures mapped main-ELF translation, not correctness.',
            'Executable sections can contain alignment padding and embedded data.',
            'IOP firmware and uncatalogued VU microprograms still use interpreter paths.',
            'Native runtime replacements require independent behavioral verification.',
            'First-mission evidence does not establish later-level compatibility or full completion.'
        ]
    }
    destination = ROOT / 'reports/recompilation-coverage.json'
    destination.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
