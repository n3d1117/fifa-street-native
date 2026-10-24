// SPDX-License-Identifier: GPL-3.0-or-later
// File layer of the Settings struct: defaults, validation, round trip of every
// field, preservation of unknown keys and the env overrides.
#import <Foundation/Foundation.h>

#include "settings.hpp"

#include <cassert>
#include <cstdio>
#include <map>
#include <string>

using host::Settings;

static std::map<std::string, std::string> g_env;
static const char* fake_env(const char* name) {
    const auto it = g_env.find(name);
    return it == g_env.end() ? nullptr : it->second.c_str();
}

int main() {
    @autoreleasepool {
        NSString* directory = [NSTemporaryDirectory() stringByAppendingPathComponent:
            [NSString stringWithFormat:@"settings-%@", [[NSUUID UUID] UUIDString]]];
        const std::string dir = directory.UTF8String;
        NSString* path = [directory stringByAppendingPathComponent:@"settings.plist"];

        // Missing file: the launch defaults for this machine, not the struct's
        // fallback values, because quality now follows the detected hardware.
        const host::HardwareQuality detected = host::detect_hardware_quality();
        assert(detected.renderScale >= 1 && detected.renderScale <= 6);
        {
            const Settings fresh = host::load_settings_file(dir);
            assert(fresh.renderScale == detected.renderScale);
            assert(fresh.anisotropy == detected.anisotropy);
        }
        assert(Settings{}.videoMode == host::VideoMode::NTSC);
        assert(Settings{}.displayAspect == host::DisplayAspect::SixteenNine);
        for (const char* aspect : {"auto", "4:3"}) {
            Settings selected;
            g_env = {{"PS2X_ASPECT", aspect}};
            host::apply_env_overrides(selected, fake_env);
            assert(selected.displayAspect == (std::string(aspect) == "auto" ? host::DisplayAspect::Auto : host::DisplayAspect::FourThree));
            assert(host::save_settings_file(dir, selected));
            assert(host::load_settings_file(dir) == selected);
        }

        // Every field survives save -> load.
        Settings all;
        all.videoMode = host::VideoMode::NTSC;
        all.renderScale = 3;
        all.anisotropy = 8;
        all.sharperImage = true;
        all.rumble = true;
        all.rumbleIntensity = 75;
        all.fxaa = true;
        all.smoothUiTextures = true;
        all.unlockAllVenues = true;
        all.displayAspect = host::DisplayAspect::SixteenNine;
        assert(host::save_settings_file(dir, all));
        assert(host::load_settings_file(dir) == all);

        // 5x and 6x are accepted from the settings file; 7x is rejected above.
        for (int scale : {5, 6}) {
            NSDictionary* stored = @{@"renderScale": @(scale), @"defaultsVersion": @(host::kDefaultsVersion)};
            assert([stored writeToFile:path atomically:YES]);
            assert(host::load_settings_file(dir).renderScale == scale);
        }

        // Unknown keys are preserved.
        NSMutableDictionary* plist = [NSMutableDictionary dictionaryWithContentsOfFile:path];
        plist[@"futureOption"] = @"keep me";
        assert([plist writeToFile:path atomically:YES]);
        assert(host::save_settings_file(dir, Settings{}));
        NSDictionary* after = [NSDictionary dictionaryWithContentsOfFile:path];
        assert([after[@"futureOption"] isEqual:@"keep me"]);
        assert([after[@"fxaa"] boolValue] == NO);

        // Invalid values fall back to defaults.
        NSDictionary* bad = @{@"renderScale": @7, @"anisotropy": @3, @"sharperImage": @1, @"displayAspect": @"stretch", @"videoMode": @"invalid", @"defaultsVersion": @(host::kDefaultsVersion)};
        assert([bad writeToFile:path atomically:YES]);
        {
            const Settings coerced = host::load_settings_file(dir);
            assert(coerced.renderScale == detected.renderScale);
            assert(coerced.anisotropy == detected.anisotropy);
        }
        // A settings.plist without the defaults marker keeps the detected quality
        // even when it stored the old defaults: that is the one-way migration.
        NSDictionary* legacy = @{@"renderScale": @1, @"anisotropy": @0};
        assert([legacy writeToFile:path atomically:YES]);
        {
            const Settings migrated = host::load_settings_file(dir);
            assert(migrated.renderScale == detected.renderScale);
            assert(migrated.anisotropy == detected.anisotropy);
        }
        NSDictionary* kept = @{@"renderScale": @1, @"anisotropy": @0, @"defaultsVersion": @(host::kDefaultsVersion)};
        assert([kept writeToFile:path atomically:YES]);
        {
            const Settings stored = host::load_settings_file(dir);
            assert(stored.renderScale == 1 && stored.anisotropy == 0);
        }

        // Debug overrides apply on top of the file.
        Settings s;
        g_env = {{"PS2X_VIDEO_MODE", "ntsc"}, {"PS2X_SCALE", "6"}, {"PS2X_ANISO", "16"}, {"PS2X_SHARP", "1"}, {"PS2X_FXAA", "1"}, {"PS2X_UI_UPSCALE", "1"}, {"PS2X_RUMBLE", "1"}, {"PS2X_RUMBLE_INTENSITY", "75"}, {"PS2X_WALLCLOCK", "0"}, {"PS2X_CHEAT_UNLOCK_VENUES", "1"}, {"PS2X_ASPECT", "16:9"}};
        host::apply_env_overrides(s, fake_env);
        assert(s.videoMode == host::VideoMode::NTSC && s.renderScale == 6 && s.anisotropy == 16 && s.sharperImage && s.fxaa && !s.wallClock && s.unlockAllVenues &&
               s.displayAspect == host::DisplayAspect::SixteenNine && s.rumble && s.rumbleIntensity == 75 && s.smoothUiTextures);
        Settings explicitOff = all;
        g_env = {{"PS2X_ANISO", "0"}, {"PS2X_RUMBLE", "0"}};
        host::apply_env_overrides(explicitOff, fake_env);
        assert(explicitOff.anisotropy == 0 && !explicitOff.rumble && explicitOff.smoothUiTextures);
        g_env = {{"PS2X_UI_UPSCALE", "0"}};
        host::apply_env_overrides(explicitOff, fake_env);
        assert(!explicitOff.smoothUiTextures);
        Settings invalid;
        g_env = {{"PS2X_VIDEO_MODE", "NTSC"}, {"PS2X_SCALE", "7"}, {"PS2X_ANISO", "3"}, {"PS2X_SHARP", "yes"}, {"PS2X_FXAA", "yes"}, {"PS2X_WALLCLOCK", "1"}, {"PS2X_CHEAT_UNLOCK_VENUES", "yes"}, {"PS2X_ASPECT", "squash"}};
        host::apply_env_overrides(invalid, fake_env);
        assert(invalid == Settings{});

        Settings ntsc = all;
        for (const char* scale : {"5", "6"}) {
            Settings accepted;
            g_env = {{"PS2X_SCALE", scale}};
            host::apply_env_overrides(accepted, fake_env);
            assert(accepted.renderScale == scale[0] - '0');
        }
        g_env = {{"PS2X_VIDEO_MODE", "pal"}};
        host::apply_env_overrides(ntsc, fake_env);
        assert(ntsc.videoMode == host::VideoMode::PAL);
        assert(host::save_settings_file(dir, ntsc));
        assert(host::load_settings_file(dir) == ntsc);
        ntsc.videoMode = host::VideoMode::NTSC;
        for (const char* value : {"", "invalid", "NTSC", "60"}) {
            g_env = {{"PS2X_VIDEO_MODE", value}};
            host::apply_env_overrides(ntsc, fake_env);
            assert(ntsc.videoMode == host::VideoMode::NTSC);
        }
        for (id value in @[@YES, @60, @[], @{}]) {
            assert([@{@"videoMode": value} writeToFile:path atomically:YES]);
            assert(host::load_settings_file(dir).videoMode == host::VideoMode::NTSC);
        }
        assert([@{@"videoMode": @"pal"} writeToFile:path atomically:YES]);
        assert(host::load_settings_file(dir).videoMode == host::VideoMode::PAL);

        [[NSFileManager defaultManager] removeItemAtPath:directory error:nil];
    }
    std::puts("settings tests passed");
    return 0;
}
