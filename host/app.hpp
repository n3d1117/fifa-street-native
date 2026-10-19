// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

// Library entry of the game. The process main thread runs AppKit (window,
// events); the runtime starts the guest on its own thread inside ps2x_app_run.
extern "C" {
struct Ps2xAppConfig {
    const char* elf_path;  // game executable
    const char* user_dir;  // settings, saves (memcards/), logs
};
// Blocks until the game quits; returns the process exit status (0 = clean).
int ps2x_app_run(const Ps2xAppConfig* config);
// Thread-safe: ask the running game to quit as if the window were closed.
void ps2x_app_stop(void);
}
