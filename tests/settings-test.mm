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

        // Missing file: defaults.
        assert(host::load_settings_file(dir) == Settings{});

        // Every field survives save -> load.
        Settings all;
        all.videoMode = host::VideoMode::NTSC;
        all.renderScale = 3;
        all.anisotropy = 8;
        all.sharperImage = true;
        all.rumble = true;
        all.rumbleIntensity = 75;
        all.fxaa = true;
        all.unlockAllVenues = true;
        all.displayAspect = host::DisplayAspect::SixteenNine;
        assert(host::save_settings_file(dir, all));
        assert(host::load_settings_file(dir) == all);

        // 5x and 6x are accepted from the settings file; 7x is rejected above.
        for (int scale : {5, 6}) {
            assert([@{@"renderScale": @(scale)} writeToFile:path atomically:YES]);
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
        NSDictionary* bad = @{@"renderScale": @7, @"anisotropy": @3, @"sharperImage": @1, @"displayAspect": @"stretch", @"videoMode": @"invalid"};
        assert([bad writeToFile:path atomically:YES]);
        assert(host::load_settings_file(dir) == Settings{});

        // Debug overrides apply on top of the file.
        Settings s;
        g_env = {{"PS2X_VIDEO_MODE", "ntsc"}, {"PS2X_SCALE", "6"}, {"PS2X_ANISO", "16"}, {"PS2X_SHARP", "1"}, {"PS2X_FXAA", "1"}, {"PS2X_RUMBLE", "1"}, {"PS2X_RUMBLE_INTENSITY", "75"}, {"PS2X_WALLCLOCK", "0"}, {"PS2X_CHEAT_UNLOCK_VENUES", "1"}, {"PS2X_ASPECT", "16:9"}};
        host::apply_env_overrides(s, fake_env);
        assert(s.videoMode == host::VideoMode::NTSC && s.renderScale == 6 && s.anisotropy == 16 && s.sharperImage && s.fxaa && !s.wallClock && s.unlockAllVenues &&
               s.displayAspect == host::DisplayAspect::SixteenNine && s.rumble && s.rumbleIntensity == 75);
        Settings explicitOff = all;
        g_env = {{"PS2X_ANISO", "0"}, {"PS2X_RUMBLE", "0"}};
        host::apply_env_overrides(explicitOff, fake_env);
        assert(explicitOff.anisotropy == 0 && !explicitOff.rumble);
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
            assert(host::load_settings_file(dir).videoMode == host::VideoMode::PAL);
        }

        [[NSFileManager defaultManager] removeItemAtPath:directory error:nil];
    }
    std::puts("settings tests passed");
    return 0;
}
