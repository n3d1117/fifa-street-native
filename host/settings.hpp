// SPDX-License-Identifier: GPL-3.0-or-later
// The one settings struct. Loaded once at startup from <user dir>/settings.plist
// (the file the Settings window writes), then the debug environment overrides
// are applied on top (see docs/internals.md). Everything else reads this
// struct; nothing else calls getenv for behaviour.
#pragma once

#include <string>

#if defined(__APPLE__)
#include <TargetConditionals.h>
#endif

namespace host {

enum class VideoMode { PAL, NTSC };

enum class DisplayAspect { Auto, FourThree, SixteenNine };

// The starting render scale and anisotropy for this machine, resolved at launch
// from the GPU (iOS/tvOS) or the display's pixel height (macOS); see
// detect_hardware_quality in settings.mm for the measured table. The values in
// the struct are only the fallback when detection is unavailable.
struct HardwareQuality {
    int renderScale = 1;  // 1..6
    int anisotropy = 0;   // 0, 2, 4, 8 or 16
};

HardwareQuality detect_hardware_quality();

struct Settings {
    VideoMode videoMode = VideoMode::NTSC;
    int renderScale = 1;           // 1..6
    int anisotropy = 0;            // 0, 2, 4, 8 or 16
    bool sharperImage = false;
    bool rumble = false;
    int rumbleIntensity = 100;    // 25, 50, 75 or 100 percent
    bool fxaa = false;
    bool smoothUiTextures = false;
    bool wallClock = true;         // pace guest time from the host clock (PS2X_WALLCLOCK=0 turns it off)
    bool unlockAllVenues = false;  // cheat: raise the game's own venue cheat flag
    DisplayAspect displayAspect = DisplayAspect::SixteenNine;

    friend bool operator==(const Settings&, const Settings&) = default;
};

using EnvLookup = const char* (*)(const char* name);

// Reads the plist. A missing file or invalid value leaves that option at its
// default; unknown keys are ignored. Never throws.
// Bumped when the detected defaults change in a way existing installs should
// adopt once, even though they already saved a settings.plist.
constexpr int kDefaultsVersion = 2;

Settings load_settings_file(const std::string& user_dir);

// Debug overrides, applied after the file: PS2X_SCALE=1..6, PS2X_ANISO=0|2|4|8|16, PS2X_SHARP=1, PS2X_FXAA=1, PS2X_RUMBLE=0|1, PS2X_RUMBLE_INTENSITY=25|50|75|100, PS2X_WALLCLOCK=0,
// PS2X_CHEAT_UNLOCK_VENUES=1, PS2X_ASPECT=auto|4:3|16:9, PS2X_VIDEO_MODE=pal|ntsc, PS2X_UI_UPSCALE=1.
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
