// SPDX-License-Identifier: GPL-3.0-or-later
// The one settings struct. Loaded once at startup from
// <user dir>/experimental-settings.plist (the file the Settings window writes),
// then a short list of debug environment overrides is applied on top (see
// docs/internals.md). Everything else reads this struct; nothing else calls
// getenv for behaviour.
#pragma once

#include <string>

namespace tarzan {

struct Settings {
    bool highFps = false;
    int targetFps = 60;            // 60 or 120
    bool hdWidescreen = false;
    int renderScale = 1;           // 1..4
    bool sharperImage = false;
    bool rememberLanguage = false;
    int lastLanguage = -1;         // 0..4 while rememberLanguage, else -1
    bool skipLogos = false;
    bool extraLives99 = false;
    bool unlimitedSpears = false;
    bool wallClock = true;         // pace guest time from the host clock (PS2X_WALLCLOCK=0 turns it off)

    friend bool operator==(const Settings&, const Settings&) = default;
};

using EnvLookup = const char* (*)(const char* name);

// Reads the plist. A missing file or invalid value leaves that option at its
// default; unknown keys are ignored. Never throws.
Settings load_settings_file(const std::string& user_dir);

// Debug overrides, applied after the file: TARZAN_EXPERIMENT_HD=1,
// TARZAN_EXPERIMENT_FPS=60|120, TARZAN_EXPERIMENT_SCALE=1..4,
// TARZAN_EXPERIMENT_SHARP=1, PS2X_WALLCLOCK=0.
void apply_env_overrides(Settings& settings, EnvLookup lookup);

// Writes every user option, keeping keys this build does not know about.
// lastLanguage is stored only while rememberLanguage is on. Atomic replace.
bool save_settings_file(const std::string& user_dir, const Settings& settings);

// Records the accepted language, rewriting only that key and only when the
// file already has rememberLanguage on.
bool save_last_language(const std::string& user_dir, int language);

}  // namespace tarzan
