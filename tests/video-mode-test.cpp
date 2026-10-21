// SPDX-License-Identifier: GPL-3.0-or-later
#include "video-mode.hpp"

#include <array>
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    std::map<uint32_t, uint32_t> words;
    for (const auto& w : host::video::kExpected) words[w.address] = w.value;
    const auto read = [&](uint32_t address, uint32_t& value) {
        auto it = words.find(address);
        if (it == words.end()) return false;
        value = it->second;
        return true;
    };
    uint32_t failed = 0;
    assert(host::video::verify(read, failed));
    for (const auto& w : host::video::kExpected) {
        words[w.address] ^= 1;
        assert(!host::video::verify(read, failed) && failed == w.address);
        words.erase(w.address);
        assert(!host::video::verify(read, failed) && failed == w.address);
        words[w.address] = w.value;
    }

    // Retail constructor state. Selector-only NTSC leaves 512 lines: the
    // black-pitch reproduction. Changing mode must update height together.
    std::array<uint8_t, 0x600> owner;
    owner.fill(0xA5);
    const uint32_t pal = 1, dimension = 512;
    std::memcpy(owner.data() + 0x44c, &pal, 4);
    std::memcpy(owner.data() + 0x450, &dimension, 4);
    std::memcpy(owner.data() + 0x454, &dimension, 4);
    auto expected = owner;
    const uint32_t ntsc = 0, height = 448;
    std::memcpy(expected.data() + 0x44c, &ntsc, 4);
    std::memcpy(expected.data() + 0x454, &height, 4);
    host::video::selectNtsc(owner.data());
    assert(owner == expected);
    std::puts("video mode tests passed");
}
