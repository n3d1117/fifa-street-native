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

    std::puts("video mode tests passed");
}
