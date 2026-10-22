// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cstdint>

extern "C" {
// Opens an existing FIFO for nonblocking diagnostic input. A null or invalid
// path disables the helper; ordinary files are never accepted.
void ps2x_diagnostic_input_configure(const char* path);
// Reads bounded FIFO input and expires stale masks. Call from the host poll loop.
void ps2x_diagnostic_input_poll();
void ps2x_diagnostic_input_set_provider(uint32_t (*provider)());
// Reads button bits 1..18 and diagnostic left-stick directions 19..22 from the latest mask.
int ps2x_diagnostic_input_key_down(uint32_t key);
void ps2x_diagnostic_input_shutdown();
}
