// SPDX-License-Identifier: GPL-3.0-or-later
#include "autopilot.hpp"
#include "diagnostic-input.hpp"
#include "elf-words.hpp"
#include "perf-log.hpp"
#include "ps2_runtime.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>

namespace {
constexpr uint32_t kHudClock = 0x271E30;
constexpr uint32_t kRamSize = 0x02000000;
PS2Runtime::RecompiledFunction s_hud = nullptr;
host::autopilot::ClockProgress s_clock;
std::atomic<bool> s_gameplay{false};
std::atomic<bool> s_matchStarted{false};
std::atomic<unsigned> s_state{0};
bool s_drive = false;
std::chrono::steady_clock::time_point s_epoch;
std::chrono::steady_clock::time_point s_clockLogAt;

void logClock(float seconds) {
    ps2x_perf_set_guest_clock(seconds);
    const auto now = std::chrono::steady_clock::now();
    const double wall = std::chrono::duration<double>(now - s_epoch).count();
    if (s_clockLogAt.time_since_epoch().count() != 0 && now - s_clockLogAt < std::chrono::seconds(2)) return;
    s_clockLogAt = now;
    std::fprintf(stderr, "[autopilot] clock_seconds=%.3f wall_seconds=%.3f\n", seconds, wall);
}

void transition(unsigned state, const char* label) {
    unsigned previous = s_state.load(std::memory_order_relaxed);
    while (previous < state) {
        if (s_state.compare_exchange_weak(previous, state, std::memory_order_relaxed)) {
            std::fprintf(stderr, "[autopilot] state=%s\n", label);
            return;
        }
    }
}

uint32_t inputMask() {
    const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - s_epoch).count();
    return host::autopilot::mask(static_cast<uint64_t>(ms), s_gameplay.load(std::memory_order_acquire));
}

uint32_t readWord(const uint8_t* ram, uint32_t address) {
    uint32_t word;
    std::memcpy(&word, ram + address, sizeof(word));
    return word;
}
bool valid(uint32_t pointer, uint32_t bytes) {
    return pointer >= 0x00100000 && pointer <= kRamSize - bytes && (pointer & 3) == 0;
}

void hudClock(uint8_t* ram, R5900Context* ctx, PS2Runtime* runtime) {
    s_hud(ram, ctx, runtime);
    if (s_gameplay.load(std::memory_order_relaxed)) {
        const uint32_t root = readWord(ram, 0x47CDFC);
        if (valid(root, 0x10)) {
            const uint32_t owner = readWord(ram, root + 0x0C);
            if (valid(owner, 0xC8)) {
                float seconds;
                std::memcpy(&seconds, ram + owner + 0xC4, sizeof(seconds));
                logClock(seconds);
            }
        }
        return;
    }
    if (s_drive && !s_matchStarted.load(std::memory_order_acquire)) return;
    const uint32_t root = readWord(ram, 0x47CDFC);
    if (!valid(root, 0x10)) { s_clock.reset(); return; }
    const uint32_t owner = readWord(ram, root + 0x0C);
    if (!valid(owner, 0xC8)) { s_clock.reset(); return; }
    float seconds;
    std::memcpy(&seconds, ram + owner + 0xC4, sizeof(seconds));
    if (!s_clock.sample(owner, seconds)) return;
    s_gameplay.store(true, std::memory_order_release);
    transition(4, "gameplay");
    logClock(seconds);
}
}

bool ps2x_autopilot_install(PS2Runtime& runtime, const std::filesystem::path& elfPath, bool bootMatchArmed) {
    const char* requested = std::getenv("PS2X_AUTOPILOT");
    const char* perf = std::getenv("PS2X_PERF_LOG");
    s_drive = requested && std::strcmp(requested, "match") == 0;
    if (s_drive && !bootMatchArmed) {
        std::fprintf(stderr, "[autopilot] refused: PS2X_BOOT_MATCH hook is not armed\n");
        s_drive = false;
    }
    if (!s_drive && !(perf && std::strcmp(perf, "1") == 0)) return false;
    s_epoch = std::chrono::steady_clock::now();
    s_clockLogAt = {};
    std::ifstream file(elfPath, std::ios::binary);
    const std::vector<uint8_t> elf((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    uint32_t failedAddress = 0;
    if (!host::autopilot::verify([&](uint32_t address, uint32_t& word) {
            return host::elfWord(elf, address, word);
        }, failedAddress)) {
        std::fprintf(stderr, "[autopilot] refused: retail ELF mismatch at 0x%x\n", failedAddress);
        s_drive = false;
        return false;
    }
    s_hud = runtime.lookupFunction(kHudClock);
    if (!s_hud || !runtime.replaceFunction(kHudClock, hudClock)) {
        std::fprintf(stderr, "[autopilot] refused: HUD clock function unavailable\n");
        s_drive = false;
        return false;
    }
    if (s_drive) {
        transition(1, "language");
        ps2x_diagnostic_input_set_provider(inputMask);
    }
    return true;
}

void ps2x_autopilot_front_end() {
    if (s_drive) transition(2, "front-end");
}
void ps2x_autopilot_match_started() {
    if (!s_drive) return;
    s_clock.reset();
    s_matchStarted.store(true, std::memory_order_release);
    transition(3, "match-intro");
}
bool ps2x_autopilot_gameplay() { return s_gameplay.load(std::memory_order_acquire); }
