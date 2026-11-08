// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <string>

#if defined(__APPLE__)
#include <TargetConditionals.h>
#endif

namespace host {

enum class VideoMode { PAL, NTSC };

enum class DisplayAspect { Auto, FourThree, SixteenNine };

// The starting render scale and anisotropy for this machine, resolved at launch
// from the GPU (iOS/tvOS) or display height (macOS). The values in
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
    bool rumble = false;
    int rumbleIntensity = 100;    // 25, 50, 75 or 100 percent
    DisplayAspect displayAspect = DisplayAspect::SixteenNine;

    friend bool operator==(const Settings&, const Settings&) = default;
};

// Reads the plist. A missing file or invalid value leaves that option at its
// default; unknown keys are ignored. Never throws.
Settings load_settings_file(const std::string& user_dir);

// Writes supported options with an atomic replace.
bool save_settings_file(const std::string& user_dir, const Settings& settings);

// Process-wide active settings (settings-window.mm). configure loads the file
// once, before the runtime starts; later calls are ignored.
void configure_settings(const char* user_dir);
const Settings& active_settings();
// The user directory given to configure_settings (empty when none).
const std::string& user_directory();
// Opens the Settings window; safe to call from any thread.
void show_settings_window();

}  // namespace host
