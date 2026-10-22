// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cstdint>

namespace host::video {

inline constexpr uint32_t kModeSetup = 0x295218u;

struct Word { uint32_t address, value; };
inline constexpr Word kExpected[] = {
    {0x295218u, 0x27BDFF90u},
    {0x29521Cu, 0x44866000u},
    {0x295228u, 0x44876800u},
    {0x295244u, 0x0080902Du},
    {0x29524Cu, 0x00A0982Du},
    {0x295258u, 0xAE460450u},
    {0x29525Cu, 0xAE470454u},
    {0x295264u, 0xAE53044Cu},
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

} // namespace host::video
