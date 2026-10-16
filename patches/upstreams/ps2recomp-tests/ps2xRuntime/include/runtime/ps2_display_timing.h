#pragma once

#include <cstdint>
#include <limits>

class PS2Memory;

// Returns the effective guest VBlank period, including the opt-in cadence
// experiment, using the same GS mode as EeScheduler.
uint64_t ps2VsyncPeriodMicroseconds(const PS2Memory &memory);

namespace ps2x::display_timing
{
    constexpr uint64_t kQ32One = 1ull << 32u;

    inline uint64_t mpegPtsDeltaToVsyncQ32(uint64_t delta90k, uint64_t periodUs)
    {
        if (periodUs == 0u || delta90k > std::numeric_limits<uint64_t>::max() / 1000000u)
        {
            return std::numeric_limits<uint64_t>::max();
        }

        const uint64_t numerator = delta90k * 1000000u;
        const uint64_t denominator = 90000u * periodUs;
        const uint64_t whole = numerator / denominator;
        const uint64_t remainder = numerator % denominator;
        if (whole > std::numeric_limits<uint64_t>::max() / kQ32One)
        {
            return std::numeric_limits<uint64_t>::max();
        }

        const uint64_t wholeQ32 = whole * kQ32One;
        const uint64_t remainderQ32 =
            (remainder * kQ32One + denominator / 2u) / denominator;
        return wholeQ32 > std::numeric_limits<uint64_t>::max() - remainderQ32
                   ? std::numeric_limits<uint64_t>::max()
                   : wholeQ32 + remainderQ32;
    }

    // MPEG sequence-header intervals are stored in NTSC 59.94-Hz field ticks.
    inline uint64_t canonicalMpegTicksToVsyncQ32(uint64_t ticksQ32, uint64_t periodUs)
    {
        if (periodUs == 0u)
        {
            return std::numeric_limits<uint64_t>::max();
        }

        // (1001 / 60000 seconds per canonical tick) / guest period.
        uint64_t numerator = 1001000000u;
        uint64_t denominator = 60000u * periodUs;
        uint64_t a = numerator;
        uint64_t b = denominator;
        while (b != 0u)
        {
            const uint64_t remainder = a % b;
            a = b;
            b = remainder;
        }
        numerator /= a;
        denominator /= a;

        const uint64_t whole = ticksQ32 / denominator;
        const uint64_t remainder = ticksQ32 % denominator;
        if (whole > std::numeric_limits<uint64_t>::max() / numerator)
        {
            return std::numeric_limits<uint64_t>::max();
        }
        const uint64_t wholeScaled = whole * numerator;
        const uint64_t remainderScaled = (remainder * numerator + denominator / 2u) / denominator;
        return wholeScaled > std::numeric_limits<uint64_t>::max() - remainderScaled
                   ? std::numeric_limits<uint64_t>::max()
                   : wholeScaled + remainderScaled;
    }
}
