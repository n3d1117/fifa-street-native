// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cmath>
#include <cstdint>
#include <filesystem>

class PS2Runtime;

namespace host::autopilot {
struct Word { uint32_t address, value; };
constexpr Word kExpected[] = {
    {0x271E30, 0x27BDFF50}, {0x271E34, 0x3C020048},
    {0x271E54, 0x8C43CDFC}, {0x271E5C, 0x8C62000C}, {0x271E68, 0xC44000C4},
    {0x271E94, 0x46030000}, {0x271E98, 0x46000124},
    {0x271FA8, 0x02A2001A}, {0x271FB8, 0x00003012}, {0x271FBC, 0x00003810},
    {0x272030, 0x0C10E9BE}, {0x272034, 0x24A5AD38}, {0x27203C, 0x8E6401E8},
    {0x272050, 0x00C0F809},
    {0x1D4A98, 0x8CA205F0}, {0x1D4AA4, 0x8C53000C},
    {0x1D4BE4, 0x8EA2E470}, {0x1D4BFC, 0xC440D92C},
    {0x1D4BF0, 0x3C013C88}, {0x1D4BF4, 0x34218889},
    {0x1D4C08, 0x46020002}, {0x1D4C34, 0xE66100C4},
    {0x4FAD38, 0x253A6425}, {0x4FAD3C, 0x0064322E},
};
template<class ReadWord>
bool verify(ReadWord readWord, uint32_t& failedAddress) {
    for (const auto& expected : kExpected) {
        uint32_t word = 0;
        if (!readWord(expected.address, word) || word != expected.value) {
            failedAddress = expected.address;
            return false;
        }
    }
    return true;
}

class ClockProgress {
public:
    bool sample(uint32_t owner, float seconds) {
        if (!owner || !std::isfinite(seconds) || seconds < 0 || seconds > 86400) {
            reset();
            return false;
        }
        if (owner != owner_ || seconds < last_) {
            owner_ = owner;
            last_ = seconds;
            advances_ = 0;
            return false;
        }
        if (seconds - last_ >= 0.5f) {
            last_ = seconds;
            ++advances_;
        }
        return advances_ >= 2 && seconds > 0;
    }
    void reset() { owner_ = 0; last_ = 0; advances_ = 0; }
private:
    uint32_t owner_ = 0;
    float last_ = 0;
    unsigned advances_ = 0;
};

inline uint32_t mask(uint64_t milliseconds, bool gameplay) {
    if (!gameplay) return milliseconds % 2000 < 80 ? 1u << 5 : 0;
    constexpr uint32_t keys = (1u << 5) | (1u << 9) | (1u << 7) |
        (1u << 11) | (1u << 13) | (1u << 14);
    return milliseconds % 120 < 80 ? keys : 0;
}
}

bool ps2x_autopilot_install(PS2Runtime&, const std::filesystem::path&, bool bootMatchArmed);
void ps2x_autopilot_front_end();
void ps2x_autopilot_match_started();
bool ps2x_autopilot_gameplay();
