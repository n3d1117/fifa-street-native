// SPDX-License-Identifier: GPL-3.0-or-later
#include "autopilot.hpp"
#include <cassert>
#include <limits>

int main() {
    uint32_t failed = 0;
    const auto readWord = [](uint32_t address, uint32_t& word) {
        for (const auto& expected : host::autopilot::kExpected) {
            if (expected.address == address) { word = expected.value; return true; }
        }
        return false;
    };
    assert(host::autopilot::verify(readWord, failed));
    assert(!host::autopilot::verify([&](uint32_t address, uint32_t& word) {
        if (address == 0x271E68) return false;
        return readWord(address, word);
    }, failed));
    assert(failed == 0x271E68);
    assert(!host::autopilot::verify([&](uint32_t address, uint32_t& word) {
        if (!readWord(address, word)) return false;
        if (address == 0x1D4C34) word ^= 1;
        return true;
    }, failed));
    assert(failed == 0x1D4C34);
    host::autopilot::ClockProgress clock;
    assert(!clock.sample(0x100000, 0));
    assert(!clock.sample(0x100000, 0.1f));
    assert(!clock.sample(0x100000, 0.5f));
    assert(!clock.sample(0x100000, 0.9f));
    assert(clock.sample(0x100000, 1.0f));
    assert(!clock.sample(0x200000, 1.5f));
    assert(!clock.sample(0x200000, 0.0f));
    assert(!clock.sample(0x200000, std::numeric_limits<float>::quiet_NaN()));
    assert(!clock.sample(0x200000, 2.0f));
    assert(!clock.sample(0x200000, 2.5f));
    assert(clock.sample(0x200000, 3.0f));
    assert(host::autopilot::mask(0, false) == 1u << 5);
    assert(host::autopilot::mask(80, false) == 0);
    assert(host::autopilot::mask(2000, false) == 1u << 5);
    assert(host::autopilot::mask(79, true) == 0x6AA0);
    assert(host::autopilot::mask(80, true) == 0);
    assert(host::autopilot::mask(120, true) == 0x6AA0);
}
