// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cstdint>

extern "C" {
// Opens an existing FIFO for nonblocking diagnostic input. A null or invalid
// path disables the helper; ordinary files are never accepted.
void tarzan_diagnostic_input_configure(const char* path);
// Reads bounded FIFO input and expires stale masks. Call from the host poll loop.
void tarzan_diagnostic_input_poll();
// Returns whether a stable TarzanMetalKey (1..18) is held in the latest mask.
int tarzan_diagnostic_input_key_down(uint32_t key);
void tarzan_diagnostic_input_shutdown();
}
