// SPDX-License-Identifier: GPL-3.0-or-later
// Retail-verified VBlank polling hook.
#pragma once

#include <cstdint>
#include <cstdio>
#include <functional>
#include <string>

namespace host::pollidle {

// The loop at 0x3C1900..0x3C1998 re-reads the poll word (s1+0xBC, cleared by the VBlank-start
// interrupt handler sub_003C4ED0) and calls the callback in s1+0xCC when it is non-null. The hook
// before the back-edge `bnel` at kHook may sleep only when the loop would iterate again with
// nothing to call, and never in deterministic (estimated-cycle) timing.
inline constexpr uint32_t kHook = 0x3C1994u;
inline constexpr uint32_t kLoop = 0x3C1900u;  // where the back-edge continues
inline constexpr uint32_t kPollOffset = 0xBCu;
inline constexpr uint32_t kCallbackOffset = 0xCCu;

inline bool shouldSleep(bool wallClock, uint32_t pollWord, uint32_t callback) {
    return wallClock && pollWord != 0u && callback == 0u;
}

struct Word {
    uint32_t address;
    uint32_t value;
};

// Guest instruction words the hook depends on, from the retail SLES_530.64.
inline constexpr Word kExpected[] = {
    // 0x3C18D0 sub_003C18D0 entry: s0 = a0, s1 = *(s0 + 8) + 0x770
    {0x3C18D0, 0x27BDFFD0}, {0x3C18DC, 0x0080802D}, {0x3C18E4, 0x8E020008}, {0x3C18E8, 0x24510770},
    // first poll and callback load
    {0x3C18EC, 0x8E2200BC}, {0x3C18F0, 0x5040002B}, {0x3C18F8, 0x8E2200CC},
    // loop head: call the callback when non-null
    {0x3C1900, 0x10400003}, {0x3C1908, 0x0040F809},
    // back-edge: reload the poll word, branch-likely loads the callback in its delay slot
    {0x3C1990, 0x8E2200BC}, {0x3C1994, 0x5440FFDA}, {0x3C1998, 0x8E2200CC},
    // loop exit
    {0x3C199C, 0x8E030008},
};

inline bool verify(const std::function<bool(uint32_t, uint32_t &)> &readWord, std::string *why = nullptr) {
    for (const Word &w : kExpected) {
        uint32_t got = 0;
        if (!readWord(w.address, got) || got != w.value) {
            if (why) {
                char buf[96];
                std::snprintf(buf, sizeof(buf), "word at 0x%x is 0x%08x, expected 0x%08x", w.address, got, w.value);
                *why = buf;
            }
            return false;
        }
    }
    return true;
}

}  // namespace host::pollidle
