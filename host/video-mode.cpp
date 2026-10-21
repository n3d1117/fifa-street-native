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
PS2Runtime::RecompiledFunction g_constructor = nullptr;

void rendererConstructor(uint8_t* rdram, R5900Context* ctx, PS2Runtime* runtime) {
    const uint32_t owner = GPR_U32(ctx, 4);
    g_constructor(rdram, ctx, runtime);
    host::video::selectNtsc(rdram + owner);
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
    g_constructor = runtime.lookupFunction(host::video::kConstructor);
    if (!g_constructor || !runtime.replaceFunction(host::video::kConstructor, rendererConstructor)) {
        std::fprintf(stderr, "[video-mode] refused: no generated renderer constructor\n");
        return false;
    }
    std::fprintf(stderr, "[video-mode] NTSC: game's selector 0, render height 448\n");
    return true;
}
