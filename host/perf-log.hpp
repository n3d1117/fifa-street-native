// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <filesystem>
#include <atomic>
#include "runtime/ps2_perf.h"

void ps2x_perf_start(const std::filesystem::path& userDirectory,
    bool (*gameplay)(), Ps2PerfSnapshot (*snapshot)());
void ps2x_perf_stop();
namespace host::perf {
inline bool enabled = false;
inline std::atomic<double> gsSubmitSeconds{0};
inline std::atomic<double> guestClockSeconds{-1};
}
inline bool ps2x_perf_enabled() { return host::perf::enabled; }
inline void ps2x_perf_gs_submit(double seconds) {
    host::perf::gsSubmitSeconds.fetch_add(seconds, std::memory_order_relaxed);
}
inline void ps2x_perf_set_guest_clock(double seconds) {
    host::perf::guestClockSeconds.store(seconds, std::memory_order_relaxed);
}
