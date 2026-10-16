// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstdint>

extern "C" {
void tarzan_experiments_trace_clock(uint64_t total, uint64_t delta);
// Loads <user_directory>/experimental-settings.plist once. Missing or invalid
// options remain disabled. The active values are read by the game thread.
void tarzan_experiments_configure(const char* user_directory);
// Opens the native settings window; safe to call from any thread.
void tarzan_experiments_show();
int tarzan_experiments_high_fps();
int tarzan_experiments_target_fps();
int tarzan_experiments_hd_widescreen();
int tarzan_experiments_render_scale();
int tarzan_experiments_sharper_image();
// Diagnostic setters update the active and pending snapshots only. They do not
// update the controls or save the plist.
void tarzan_experiments_set_high_fps(int enabled);
void tarzan_experiments_set_target_fps(int target_fps);
void tarzan_experiments_set_hd_widescreen(int enabled);
void tarzan_experiments_set_render_scale(int render_scale);
void tarzan_experiments_set_sharper_image(int enabled);
}
