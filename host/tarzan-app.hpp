// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

// Library entry of the game. The process main thread runs AppKit (window,
// events); the runtime starts the guest on its own thread inside tarzan_run.
extern "C" {
struct TarzanConfig {
    const char* elf_path;  // game executable (SLES_503.50)
    const char* user_dir;  // settings, saves (memcards/), logs
};
// Blocks until the game quits; returns the process exit status (0 = clean).
int tarzan_run(const TarzanConfig* config);
// Thread-safe: ask the running game to quit as if the window were closed.
void tarzan_stop(void);
}
