// SPDX-License-Identifier: GPL-3.0-or-later
#include "experimental-settings.hpp"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
extern "C" void tarzan_experiments_trace_clock(uint64_t total, uint64_t delta) {
    static const bool enabled = [] {
        const char* value = std::getenv("TARZAN_TRACE_GAME_CLOCK");
        return value && std::strcmp(value, "1") == 0;
    }();
    if (!enabled) return;
    static thread_local auto start = std::chrono::steady_clock::now();
    static thread_local uint64_t base = total, updates = 0;
    ++updates;
    const auto now = std::chrono::steady_clock::now();
    const double ms = std::chrono::duration<double, std::milli>(now - start).count();
    if (total < base) { base = total; start = now; updates = 0; return; }
    if (ms >= 5000.0) {
        std::fprintf(stderr, "[game-clock] host_ms=%.3f game_ticks=%llu delta=%llu updates=%llu target=%d enabled=%d\n",
            ms, (unsigned long long)(total - base), (unsigned long long)delta,
            (unsigned long long)updates, tarzan_experiments_target_fps(), tarzan_experiments_high_fps());
        start = now; base = total; updates = 0;
    }
}
