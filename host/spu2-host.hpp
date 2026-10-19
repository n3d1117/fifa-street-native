// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

// Installs the pinned PCSX2 SPU2 core into the IOP host hooks. The core still
// advances when no SDL audio device is available.
bool ps2x_spu2_host_start();
void ps2x_spu2_host_stop();
