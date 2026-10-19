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
        all.renderScale = 3;
        all.sharperImage = true;
        assert(host::save_settings_file(dir, all));
        assert(host::load_settings_file(dir) == all);

        // Unknown keys are preserved.
        NSMutableDictionary* plist = [NSMutableDictionary dictionaryWithContentsOfFile:path];
        plist[@"futureOption"] = @"keep me";
        assert([plist writeToFile:path atomically:YES]);
        assert(host::save_settings_file(dir, Settings{}));
        NSDictionary* after = [NSDictionary dictionaryWithContentsOfFile:path];
        assert([after[@"futureOption"] isEqual:@"keep me"]);

        // Invalid values fall back to defaults.
        NSDictionary* bad = @{@"renderScale": @9, @"sharperImage": @1};
        assert([bad writeToFile:path atomically:YES]);
        assert(host::load_settings_file(dir) == Settings{});

        // Debug overrides apply on top of the file.
        Settings s;
        g_env = {{"PS2X_SCALE", "4"}, {"PS2X_SHARP", "1"}, {"PS2X_WALLCLOCK", "0"}};
        host::apply_env_overrides(s, fake_env);
        assert(s.renderScale == 4 && s.sharperImage && !s.wallClock);
        Settings invalid;
        g_env = {{"PS2X_SCALE", "7"}, {"PS2X_SHARP", "yes"}, {"PS2X_WALLCLOCK", "1"}};
        host::apply_env_overrides(invalid, fake_env);
        assert(invalid == Settings{});

        [[NSFileManager defaultManager] removeItemAtPath:directory error:nil];
    }
    std::puts("settings tests passed");
    return 0;
}
