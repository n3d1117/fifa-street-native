// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstdint>

// Installs the pinned PCSX2 SPU2 core into the IOP host hooks. The core still
// advances when no SDL audio device is available.
bool ps2x_spu2_host_start();
void ps2x_spu2_host_stop();
void ps2x_spu2_host_pause(bool paused);

uint64_t ps2x_spu2_host_underruns();

struct Spu2HostPerf {
    uint64_t underruns;
    uint64_t ringFrames;
    double advanceGapMs;
    double mixSeconds;
};
Spu2HostPerf ps2x_spu2_host_perf();
