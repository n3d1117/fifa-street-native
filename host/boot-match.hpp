// SPDX-License-Identifier: GPL-3.0-or-later
// Diagnostic boot-to-match (PS2X_BOOT_MATCH): the pure decisions, kept free of the
// runtime so a test can prove the mechanism is inert unless explicitly enabled.
#pragma once

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <string>

namespace host::bootmatch {

// Venue ids are what the front end's `setvenuebyid` stores at GameSetup+0xA0; 3 is the
// venue the front end's default Select Venue screen leaves highlighted (Marseille).
struct Config {
    int venue = 3;
};

// Enabled only when the environment variable is present and the user directory is not
// the real save directory. `args` is "" or "venue=<id>".
inline bool parse(const char *env, const std::string &userDir, const std::string &realDir, Config &out) {
    out = Config{};
    if (!env) return false;
    if (userDir.empty() || userDir == realDir) return false;
    const char *p = std::strstr(env, "venue=");
    if (p) {
        char *end = nullptr;
        const long v = std::strtol(p + 6, &end, 10);
        if (end == p + 6 || v < 0 || v > 63) return false;
        out.venue = static_cast<int>(v);
    }
    return true;
}

struct Word {
    uint32_t address;
    uint32_t value;
};

// Guest instruction words the hook depends on, from the retail SLES_530.64.
inline constexpr Word kExpected[] = {
    // 0x2ECA38 FrontEnd per-frame update (prologue, delta-time scale, message pump call)
    {0x2ECA38, 0x27BDFFE0}, {0x2ECA3C, 0x7FB00010}, {0x2ECA54, 0x3C01447A}, {0x2ECA80, 0x0C0B8624},
    // 0x2E5220 Playgame method: *(*0x47CD5C + 0x18) = 1
    {0x2E5220, 0x3C020048}, {0x2E5224, 0x24040001}, {0x2E5228, 0x8C43CD5C}, {0x2E522C, 0x03E00008},
    {0x2E5230, 0xAC640018},
    // 0x2E64C0 setvenuebyid: GameSetup (*0x47CE00) + 0xA0 = venue id
    {0x2E64E8, 0x8C64CE00}, {0x2E64EC, 0xAC8200A0},
    // 0x12AE00 GameSetup::SetControl(setup, side, mode): setup + 0xA4 + side * 4 = mode
    {0x12AE00, 0x27BDFFB0}, {0x12AE30, 0xAC4600A4},
    // 0x2E5BE8 setcontrol (the front end's Pick Sides handler) calls it with GameSetup in a0
    {0x2E5BEC, 0x0C04AB80}, {0x2E5BF0, 0x8C44CE00},
    // 0x230378 FE resource-slot table: entry 2 points at slot 0x47EFEC; 0x230B30 reads it
    {0x2303B4, 0x24C6EFEC}, {0x230B34, 0x3C020054}, {0x230B3C, 0x244274A8},
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

// Fires exactly once: after the front end has loaded its resources (slot nonzero) and
// has run `settleTicks` further updates.
class Trigger {
public:
    explicit Trigger(uint32_t settleTicks = 30) : settle_(settleTicks) {}
    bool update(bool resourcesReady) {
        if (fired_ || !resourcesReady) return false;
        if (++waited_ < settle_) return false;
        fired_ = true;
        return true;
    }
    bool fired() const { return fired_; }

private:
    uint32_t settle_;
    uint32_t waited_ = 0;
    bool fired_ = false;
};

}  // namespace host::bootmatch
