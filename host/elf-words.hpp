// SPDX-License-Identifier: GPL-3.0-or-later
// Reads a 32-bit guest word from the retail ELF image through its PT_LOAD segments, so a
// host hook can refuse to install when the code it depends on is not the verified retail code.
#pragma once

#include <cstdint>
#include <cstring>
#include <vector>

namespace host {

inline bool elfWord(const std::vector<uint8_t> &elf, uint32_t address, uint32_t &out) {
    if (elf.size() < 0x34) return false;
    uint32_t phoff;
    uint16_t phnum;
    std::memcpy(&phoff, &elf[0x1c], 4);
    std::memcpy(&phnum, &elf[0x2c], 2);
    for (uint16_t i = 0; i < phnum; ++i) {
        const size_t at = phoff + 32u * i;
        if (at + 32 > elf.size()) return false;
        uint32_t type, off, vaddr, filesz;
        std::memcpy(&type, &elf[at], 4);
        std::memcpy(&off, &elf[at + 4], 4);
        std::memcpy(&vaddr, &elf[at + 8], 4);
        std::memcpy(&filesz, &elf[at + 16], 4);
        if (type == 1 && address >= vaddr && address + 4 <= vaddr + filesz && off + (address - vaddr) + 4 <= elf.size()) {
            std::memcpy(&out, &elf[off + (address - vaddr)], 4);
            return true;
        }
    }
    return false;
}

}  // namespace host
