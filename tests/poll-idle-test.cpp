// SPDX-License-Identifier: GPL-3.0-or-later
// The vblank-poll sleep decisions: PS2X_POLL_IDLE modes, sleeping only for a non-zero poll word
// with no callback in wall-clock timing (never in deterministic timing), and the ELF-word guard.
#include "poll-idle.hpp"

#include <cassert>
#include <cstdio>

using namespace host::pollidle;

int main() {
    assert(parse(nullptr) == Mode::On && parse("1") == Mode::On && parse("") == Mode::On);
    assert(parse("0") == Mode::Off && parse("verify") == Mode::Verify);

    // Sleep only when the loop would iterate again with nothing to call.
    assert(shouldSleep(Mode::On, true, 1u, 0u) && shouldSleep(Mode::Verify, true, 1u, 0u));
    assert(!shouldSleep(Mode::Off, true, 1u, 0u));
    assert(!shouldSleep(Mode::On, true, 0u, 0u));           // the loop exits
    assert(!shouldSleep(Mode::On, true, 1u, 0x3C1000u));    // a registered callback runs every pass
    assert(!shouldSleep(Mode::On, false, 1u, 0u));          // PS2X_WALLCLOCK=0 never sleeps
    assert(!shouldSleep(Mode::Verify, false, 1u, 0u));

    // Every dependent word must match; one wrong word refuses the install.
    auto exact = [](uint32_t a, uint32_t &v) {
        for (const Word &w : kExpected) if (w.address == a) { v = w.value; return true; }
        return false;
    };
    assert(verify(exact));
    bool hasHook = false;
    for (const Word &w : kExpected) hasHook |= w.address == kHook && w.value == 0x5440FFDAu;
    assert(hasHook);
    for (const Word &flip : kExpected) {
        auto flipped = [&](uint32_t a, uint32_t &v) {
            const bool ok = exact(a, v);
            if (a == flip.address) v ^= 1u;
            return ok;
        };
        std::string why;
        assert(!verify(flipped, &why));
        char want[16];
        std::snprintf(want, sizeof(want), "0x%x", flip.address);
        assert(why.find(want) != std::string::npos);
    }
    assert(!verify([](uint32_t, uint32_t &) { return false; }));
    std::puts("poll-idle: ok");
    return 0;
}
