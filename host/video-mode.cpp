// SPDX-License-Identifier: GPL-3.0-or-later
#include "video-mode.hpp"
#include "elf-words.hpp"
#include "settings.hpp"
#include "ps2_runtime.h"
#include "ps2_guest_macros.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <vector>

namespace {
PS2Runtime::RecompiledFunction g_setup = nullptr;

void rendererSetup(uint8_t* rdram, R5900Context* ctx, PS2Runtime* runtime) {
    SET_GPR_U32(ctx, 5, 0);
    SET_GPR_U32(ctx, 7, 448);
    g_setup(rdram, ctx, runtime);
}
} // namespace

bool ps2x_video_mode_install(PS2Runtime& runtime, const std::filesystem::path& elfPath, host::VideoMode mode) {
    if (mode != host::VideoMode::NTSC) return false;
    std::ifstream file(elfPath, std::ios::binary);
    const std::vector<uint8_t> elf((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    uint32_t failedAddress = 0;
    if (!host::video::verify([&](uint32_t address, uint32_t& word) {
            return host::elfWord(elf, address, word);
        }, failedAddress)) {
        std::fprintf(stderr, "[video-mode] refused: retail ELF mismatch at 0x%x\n", failedAddress);
        return false;
    }
    g_setup = runtime.lookupFunction(host::video::kModeSetup);
    if (!g_setup || !runtime.replaceFunction(host::video::kModeSetup, rendererSetup)) {
        std::fprintf(stderr, "[video-mode] refused: no generated mode setup\n");
        return false;
    }
    return true;
}
