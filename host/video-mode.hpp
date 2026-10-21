// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cstdint>
#include <cstring>

namespace host::video {

inline constexpr uint32_t kConstructor = 0x294178u;
inline constexpr uint32_t kSelectorOffset = 0x44cu;
inline constexpr uint32_t kHeightOffset = 0x454u;

struct Word { uint32_t address, value; };
inline constexpr Word kExpected[] = {
    {0x294178u, 0x27BDFFC0u}, // renderer constructor
    {0x294188u, 0x0080882Du}, // incoming a0 is the renderer owner
    {0x294268u, 0x24020200u}, // PAL dimensions: 512
    {0x29426Cu, 0xAE30044Cu}, // selector at owner+0x44c
    {0x294270u, 0xAE220454u}, // height at owner+0x454
    {0x29427Cu, 0xAE220450u}, // width at owner+0x450
    {0x295820u, 0x8E260450u}, // width -> a2
    {0x295824u, 0x8E270454u}, // height -> a3
    {0x295828u, 0x8E25044Cu}, // selector -> a1
    {0x29582Cu, 0x0C0A5486u}, // call the game's mode setup
    {0x2952B4u, 0x5260000Fu}, // selector zero takes NTSC branch
    {0x295308u, 0x0C0F13F6u}, // call the game's GS mode setter
};

template<class ReadWord>
bool verify(ReadWord readWord, uint32_t& failedAddress) {
    for (const Word& w : kExpected) {
        uint32_t value = 0;
        if (!readWord(w.address, value) || value != w.value) {
            failedAddress = w.address;
            return false;
        }
    }
    return true;
}

inline void selectNtsc(uint8_t* owner) {
    // The PAL constructor leaves 512 lines even after selecting NTSC. Its wider
    // NTSC GS allocation leaves the pitch with an invalid descriptor (TEX0 = -1).
    // Pair the game's NTSC selector with the NTSC 448-line render height.
    const uint32_t selector = 0, height = 448;
    std::memcpy(owner + kSelectorOffset, &selector, sizeof(selector));
    std::memcpy(owner + kHeightOffset, &height, sizeof(height));
}

} // namespace host::video
