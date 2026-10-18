// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstdint>

extern "C" {
void tarzan_experiments_trace_clock(uint64_t total, uint64_t delta);
int tarzan_experiments_widescreen_camera(const uint8_t* rdram, uint32_t guest_sp);
// Loads the Settings struct once (settings.hpp): <user_directory>/experimental-settings.plist
// plus the documented environment overrides. Later calls are ignored. Call before
// the runtime starts; the active values are then read-only for the game thread.
void tarzan_experiments_configure(const char* user_directory);
// Opens the native settings window; safe to call from any thread.
void tarzan_experiments_show();
int tarzan_experiments_high_fps();
int tarzan_experiments_target_fps();
int tarzan_experiments_hd_widescreen();
int tarzan_experiments_render_scale();
int tarzan_experiments_sharper_image();
int tarzan_experiments_wall_clock();
// The user directory given to configure (nullptr when none).
const char* tarzan_experiments_user_directory();
// Startup selection uses the guest menu and its normal confirm/callback path.
uint32_t tarzan_experiments_boot_language_item(const uint8_t* rdram, uint32_t menu, uint32_t original_item);
void tarzan_experiments_boot_language_menu(const uint8_t* rdram, uint32_t menu);
int tarzan_experiments_boot_language_confirm();
int tarzan_experiments_boot_language_hidden();
void tarzan_experiments_language_accepted(uint32_t language_id);
int tarzan_experiments_remember_language();
// Returns the last validated language ID (0..4), or -1 while remembering is disabled/invalid.
int tarzan_experiments_saved_language();
int tarzan_experiments_skip_logos();
int tarzan_experiments_extra_lives_99();
int tarzan_experiments_unlimited_spears();
int tarzan_experiments_builtin_cheat(const uint8_t* rdram, uint32_t owner);
int tarzan_experiments_skip_startup_logo(const uint8_t* rdram, uint32_t name);
// Call only after the guest accepts a language selection.
void tarzan_experiments_accept_language(uint32_t language_id);
}
