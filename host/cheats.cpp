// SPDX-License-Identifier: GPL-3.0-or-later
// Opt-in cheats (Settings > "Cheat: unlock all venues", PS2X_CHEAT_UNLOCK_VENUES=1).
// FIFA Street's cCheatCodeManager handler for its venue code (0x1E8CC8) only stores 1 to
// the word at 0x480684 and shows "All venues unlocked!"; the venue-select code reads that
// word (0x2CD248, 0x2D320C). The front end's per-frame update (0x2ECA38) is wrapped, never
// altered: the original runs first, then the word is raised exactly as the handler does.
// Inert when the setting is off or any dependent guest instruction word differs from the retail ELF.
#include "elf-words.hpp"
#include "ps2_runtime.h"

#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <vector>

namespace {

constexpr uint32_t kFrontEndUpdate = 0x2ECA38u;
constexpr uint32_t kVenueCheatFlag = 0x480684u;

struct Word { uint32_t address, value; };
constexpr Word kExpected[] = {
    {0x2ECA38u, 0x27BDFFE0u},  // front-end update: addiu sp, sp, -0x20
    {0x1E8CCCu, 0x3C020048u},  // venue cheat handler: lui v0, 0x48
    {0x1E8CD4u, 0x24030001u},  //   addiu v1, zero, 1
    {0x1E8CE8u, 0xAC430684u},  //   sw v1, 0x684(v0)
    {0x2CD244u, 0x3C030048u},  // venue select: lui v1, 0x48
    {0x2CD248u, 0x8C620684u},  //   lw v0, 0x684(v1)
    {0x2D3208u, 0x3C020048u},  // venue lookup: lui v0, 0x48
    {0x2D320Cu, 0x8C430684u},  //   lw v1, 0x684(v0)
};

PS2Runtime::RecompiledFunction g_update = nullptr;

void frontEndUpdate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    g_update(rdram, ctx, runtime);
    const uint32_t on = 1;
    std::memcpy(rdram + kVenueCheatFlag, &on, 4);
}

}  // namespace

// Returns true when the cheat is installed.
bool ps2x_cheats_install(PS2Runtime &runtime, const std::filesystem::path &elfPath, bool unlockAllVenues) {
    if (!unlockAllVenues) return false;
    std::ifstream file(elfPath, std::ios::binary);
    const std::vector<uint8_t> elf((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    for (const Word &w : kExpected) {
        uint32_t value = 0;
        if (!host::elfWord(elf, w.address, value) || value != w.value) {
            std::fprintf(stderr, "[cheats] refused: ELF word at 0x%x is not the retail code\n", w.address);
            return false;
        }
    }
    g_update = runtime.lookupFunction(kFrontEndUpdate);
    if (!g_update || !runtime.replaceFunction(kFrontEndUpdate, &frontEndUpdate)) {
        std::fprintf(stderr, "[cheats] refused: no generated function at 0x%x\n", kFrontEndUpdate);
        return false;
    }
    std::fprintf(stderr, "[cheats] unlock all venues: on\n");
    return true;
}
