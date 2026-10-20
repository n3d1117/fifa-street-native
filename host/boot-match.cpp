// SPDX-License-Identifier: GPL-3.0-or-later
// Diagnostic boot-to-match (PS2X_BOOT_MATCH, set only by `./rx run --match`): once the
// front end has loaded its resources, do what its Play button does. The front end's
// per-frame update (0x2ECA38) is wrapped, never altered: the original runs first, then
// at most once the venue is stored as `setvenuebyid` (0x2E64C0) stores it and the
// `Playgame` method (0x2E5220) is called, which raises the flag the game flow polls.
// Inert without the variable, in the real save directory, or if any dependent guest
// instruction word differs from the retail ELF.
#include "boot-match.hpp"
#include "elf-words.hpp"
#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"

#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <vector>

namespace {

constexpr uint32_t kFrontEndUpdate = 0x2ECA38u;
constexpr uint32_t kPlaygame = 0x2E5220u;
constexpr uint32_t kGameSetupPtr = 0x47CE00u;  // GameSetup*
constexpr uint32_t kResourceSlot = 0x47EFECu;  // FE resource slot filled once the front end has loaded
constexpr uint32_t kVenueOffset = 0xA0u;
constexpr uint32_t kSetControl = 0x12AE00u;  // GameSetup::SetControl(setup, side, mode)

PS2Runtime::RecompiledFunction g_update = nullptr;
host::bootmatch::Config g_config;
host::bootmatch::Trigger g_trigger;
uint32_t g_ticks = 0;
const auto g_armedAt = std::chrono::steady_clock::now();
long sinceArmedMs() {
    return static_cast<long>(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - g_armedAt).count());
}

void callGuest(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime, uint32_t entry,
               uint32_t a0 = 0, uint32_t a1 = 0, uint32_t a2 = 0) {
    PS2Runtime::RecompiledFunction fn = runtime->lookupFunction(entry);
    if (!fn) return;
    const R5900Context saved = *ctx;
    SET_GPR_U32(ctx, 29, (GPR_U32(ctx, 29) - 0x100u) & ~0xFu);
    SET_GPR_U32(ctx, 31, saved.pc);
    SET_GPR_U32(ctx, 4, a0);
    SET_GPR_U32(ctx, 5, a1);
    SET_GPR_U32(ctx, 6, a2);
    ctx->in_delay_slot = false;
    ctx->pc = entry;
    fn(rdram, ctx, runtime);
    *ctx = saved;
}

void frontEndUpdate(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime) {
    g_update(rdram, ctx, runtime);
    ++g_ticks;
    uint32_t setup = 0, slot = 0;
    std::memcpy(&setup, rdram + kGameSetupPtr, 4);
    std::memcpy(&slot, rdram + kResourceSlot, 4);
    if (!g_trigger.update(setup != 0 && slot != 0)) return;
    const uint32_t venue = static_cast<uint32_t>(g_config.venue);
    std::memcpy(rdram + setup + kVenueOffset, &venue, 4);
    std::fprintf(stderr, "[boot-match] start update=%u venue=%d ms=%ld\n", g_ticks, g_config.venue, sinceArmedMs());
    // Side 1 (home) takes the first pad exactly as the Pick Sides handler (0x2E5B80) assigns it.
    callGuest(rdram, ctx, runtime, kSetControl, setup, 0, 0);
    callGuest(rdram, ctx, runtime, kPlaygame);
}

}  // namespace

// Returns true when the hook is installed.
bool ps2x_boot_match_install(PS2Runtime &runtime, const std::filesystem::path &elfPath,
                             const std::filesystem::path &userDir, const std::filesystem::path &realUserDir) {
    if (!host::bootmatch::parse(std::getenv("PS2X_BOOT_MATCH"), userDir.string(), realUserDir.string(), g_config)) {
        if (std::getenv("PS2X_BOOT_MATCH"))
            std::fprintf(stderr, "[boot-match] refused: bad arguments or the real save directory\n");
        return false;
    }
    std::ifstream file(elfPath, std::ios::binary);
    const std::vector<uint8_t> elf((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    std::string why;
    if (!host::bootmatch::verify([&](uint32_t a, uint32_t &v) { return host::elfWord(elf, a, v); }, &why)) {
        std::fprintf(stderr, "[boot-match] refused: %s\n", why.c_str());
        return false;
    }
    g_update = runtime.lookupFunction(kFrontEndUpdate);
    if (!g_update || !runtime.replaceFunction(kFrontEndUpdate, &frontEndUpdate)) {
        std::fprintf(stderr, "[boot-match] refused: no generated function at 0x%x\n", kFrontEndUpdate);
        return false;
    }
    std::fprintf(stderr, "[boot-match] armed venue=%d\n", g_config.venue);
    return true;
}
