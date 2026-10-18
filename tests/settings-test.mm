// SPDX-License-Identifier: GPL-3.0-or-later
// File layer of the Settings struct: defaults, validation, round trip of every
// field, preservation of unknown keys, language rules and the env overrides.
#import <Foundation/Foundation.h>

#include "settings.hpp"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>

using tarzan::Settings;

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
        NSString* path = [directory stringByAppendingPathComponent:@"experimental-settings.plist"];

        // Missing file: defaults.
        assert(tarzan::load_settings_file(dir) == Settings{});

        // Every field survives save -> load.
        Settings all;
        all.highFps = true; all.targetFps = 120; all.hdWidescreen = true; all.renderScale = 3;
        all.sharperImage = true; all.rememberLanguage = true; all.lastLanguage = 4;
        all.skipLogos = true; all.extraLives99 = true; all.unlimitedSpears = true;
        all.unlockAllMissions = true;
        assert(tarzan::save_settings_file(dir, all));
        assert(tarzan::load_settings_file(dir) == all);

        // Each boolean/choice individually (catches a key mapped to the wrong field).
        for (int field = 0; field < 8; ++field) {
            Settings one;
            bool* flags[] = {&one.highFps, &one.hdWidescreen, &one.sharperImage, &one.rememberLanguage,
                             &one.skipLogos, &one.extraLives99, &one.unlimitedSpears, &one.unlockAllMissions};
            *flags[field] = true;
            if (field == 3) one.lastLanguage = 2;
            assert(tarzan::save_settings_file(dir, one));
            Settings loaded = tarzan::load_settings_file(dir);
            assert(loaded == one);
        }

        // Unknown keys are preserved; lastLanguage is dropped when remembering is off.
        NSMutableDictionary* plist = [NSMutableDictionary dictionaryWithContentsOfFile:path];
        plist[@"futureOption"] = @"keep me";
        assert([plist writeToFile:path atomically:YES]);
        Settings off;
        off.lastLanguage = 3;
        assert(tarzan::save_settings_file(dir, off));
        NSDictionary* after = [NSDictionary dictionaryWithContentsOfFile:path];
        assert([after[@"futureOption"] isEqual:@"keep me"]);
        assert(after[@"lastLanguage"] == nil);
        assert([after[@"rememberLanguage"] isEqual:@NO]);

        // Language is recorded only when the file already remembers it.
        assert(!tarzan::save_last_language(dir, 2));
        Settings remember;
        remember.rememberLanguage = true;
        assert(tarzan::save_settings_file(dir, remember));
        assert(tarzan::save_last_language(dir, 2));
        assert(tarzan::load_settings_file(dir).lastLanguage == 2);
        assert(!tarzan::save_last_language(dir, 5));

        // Invalid values fall back; old files without renderScale derive it from widescreen.
        NSDictionary* bad = @{@"highFPS": @1, @"targetFPS": @90, @"renderScale": @9, @"hdWidescreen": @YES,
                              @"rememberLanguage": @YES, @"lastLanguage": @7};
        assert([bad writeToFile:path atomically:YES]);
        Settings loaded = tarzan::load_settings_file(dir);
        assert(!loaded.highFps && loaded.targetFps == 60 && loaded.renderScale == 1);
        assert(loaded.hdWidescreen && loaded.rememberLanguage && loaded.lastLanguage == -1);
        NSDictionary* oldFormat = @{@"hdWidescreen": @YES};
        assert([oldFormat writeToFile:path atomically:YES]);
        assert(tarzan::load_settings_file(dir).renderScale == 2);

        // Debug overrides apply on top of the file.
        Settings s;
        g_env = {{"TARZAN_EXPERIMENT_HD", "1"}, {"TARZAN_EXPERIMENT_FPS", "120"},
                 {"TARZAN_EXPERIMENT_SCALE", "4"}, {"TARZAN_EXPERIMENT_SHARP", "1"}, {"PS2X_WALLCLOCK", "0"}};
        tarzan::apply_env_overrides(s, fake_env);
        assert(s.hdWidescreen && s.highFps && s.targetFps == 120 && s.renderScale == 4 &&
               s.sharperImage && !s.wallClock);
        Settings invalid;
        g_env = {{"TARZAN_EXPERIMENT_HD", "yes"}, {"TARZAN_EXPERIMENT_FPS", "90"},
                 {"TARZAN_EXPERIMENT_SCALE", "7"}, {"PS2X_WALLCLOCK", "1"}};
        tarzan::apply_env_overrides(invalid, fake_env);
        assert(invalid == Settings{});

        [[NSFileManager defaultManager] removeItemAtPath:directory error:nil];
    }
    std::puts("settings tests passed");
    return 0;
}
