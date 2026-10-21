// SPDX-License-Identifier: GPL-3.0-or-later
// The one settings struct. Loaded once at startup from <user dir>/settings.plist
// (the file the Settings window writes), then the debug environment overrides
// are applied on top (see docs/internals.md). Everything else reads this
// struct; nothing else calls getenv for behaviour.
#pragma once

#include <string>

namespace host {

enum class VideoMode { PAL, NTSC };

enum class DisplayAspect { Auto, FourThree, SixteenNine };

struct Settings {
    VideoMode videoMode = VideoMode::PAL;
    int renderScale = 1;           // 1..4
    bool sharperImage = false;
    bool wallClock = true;         // pace guest time from the host clock (PS2X_WALLCLOCK=0 turns it off)
    bool unlockAllVenues = false;  // cheat: raise the game's own venue cheat flag
    DisplayAspect displayAspect = DisplayAspect::Auto;

    friend bool operator==(const Settings&, const Settings&) = default;
};

using EnvLookup = const char* (*)(const char* name);

// Reads the plist. A missing file or invalid value leaves that option at its
// default; unknown keys are ignored. Never throws.
Settings load_settings_file(const std::string& user_dir);

// Debug overrides, applied after the file: PS2X_SCALE=1..4, PS2X_SHARP=1, PS2X_WALLCLOCK=0,
// PS2X_CHEAT_UNLOCK_VENUES=1, PS2X_ASPECT=auto|4:3|16:9, PS2X_VIDEO_MODE=pal|ntsc.
void apply_env_overrides(Settings& settings, EnvLookup lookup);

// Writes every user option, keeping keys this build does not know about. Atomic replace.
bool save_settings_file(const std::string& user_dir, const Settings& settings);

// Process-wide active settings (settings-window.mm). configure loads the file
// plus the overrides once, before the runtime starts; later calls are ignored.
void configure_settings(const char* user_dir);
const Settings& active_settings();
// The user directory given to configure_settings (empty when none).
const std::string& user_directory();
// Opens the Settings window; safe to call from any thread.
void show_settings_window();

}  // namespace host
