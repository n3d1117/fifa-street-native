#include "PadRumble.h"

#include <cassert>
#include <cstdint>
#include <cstdio>

int main()
{
    const uint8_t align[6] = {0xFFu, 1u, 0u, 0xFFu, 0xFFu, 0xFFu};
    const uint8_t data[6] = {0xFFu, 0xA6u, 0x03u, 0u, 0u, 0u};
    uint8_t small = 0u;
    uint8_t large = 0u;
    ps2x_decode_pad_actuators(align, data, small, large);
    assert(small == 1u && large == 0xA6u);

    const uint8_t stopped[6] = {0u, 0u, 0u, 0u, 0u, 0u};
    ps2x_decode_pad_actuators(align, stopped, small, large);
    assert(small == 0u && large == 0u);
    std::puts("pad rumble decode tests passed");
}
