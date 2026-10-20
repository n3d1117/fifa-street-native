// SPDX-License-Identifier: GPL-3.0-or-later
// The diagnostic boot-to-match decisions: inert without PS2X_BOOT_MATCH, inert in the
// real save directory, refused when a guest word differs, and a trigger that fires once.
#include "boot-match.hpp"

#include <cassert>
#include <cstdio>

using namespace host::bootmatch;

int main() {
    Config cfg;
    // Without the variable nothing is enabled, whatever the directory.
    assert(!parse(nullptr, "/tmp/iso", "/Users/x/Library/Application Support/FIFAStreetPS2", cfg));
    // Never in the real save directory, even when explicitly requested.
    assert(!parse("", "/real", "/real", cfg));
    assert(!parse("venue=3", "", "/real", cfg));
    // Isolated directory: defaults, then an explicit venue; bad values stay inert.
    assert(parse("", "/tmp/iso", "/real", cfg) && cfg.venue == 3);
    assert(parse("venue=7", "/tmp/iso", "/real", cfg) && cfg.venue == 7);
    assert(!parse("venue=x", "/tmp/iso", "/real", cfg));
    assert(!parse("venue=99", "/tmp/iso", "/real", cfg));

    // Every dependent word must match; one wrong word refuses the install.
    auto exact = [](uint32_t a, uint32_t &v) {
        for (const Word &w : kExpected) if (w.address == a) { v = w.value; return true; }
        return false;
    };
    assert(verify(exact));
    auto flipped = [&](uint32_t a, uint32_t &v) {
        const bool ok = exact(a, v);
        if (a == 0x2E5220) v ^= 1u;
        return ok;
    };
    std::string why;
    assert(!verify(flipped, &why) && why.find("0x2e5220") != std::string::npos);
    assert(!verify([](uint32_t, uint32_t &) { return false; }));

    // The trigger waits for resources, then for the settle ticks, and fires exactly once.
    Trigger t(3);
    for (int i = 0; i < 100; ++i) assert(!t.update(false));
    assert(!t.update(true) && !t.update(true) && t.update(true));
    for (int i = 0; i < 100; ++i) assert(!t.update(true));
    assert(t.fired());
    std::puts("boot-match: ok");
    return 0;
}
