// SPDX-License-Identifier: GPL-3.0-or-later
#import <Foundation/Foundation.h>

#include "settings.hpp"

namespace tarzan {
namespace {
bool is_integer_number(id value) {
    return [value isKindOfClass:[NSNumber class]] &&
        CFGetTypeID((__bridge CFTypeRef)value) == CFNumberGetTypeID() &&
        !CFNumberIsFloatType((__bridge CFNumberRef)value);
}
bool is_boolean(id value) {
    return [value isKindOfClass:[NSNumber class]] &&
        CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID();
}
bool flag(NSDictionary* plist, NSString* key) {
    return is_boolean(plist[key]) && [plist[key] boolValue];
}
int valid_render_scale(id value) {
    if (!is_integer_number(value)) return 0;
    for (int scale = 1; scale <= 4; ++scale)
        if ([value compare:@(scale)] == NSOrderedSame) return scale;
    return 0;
}
int valid_language(id value) {
    if (!is_integer_number(value) || [value compare:@0] == NSOrderedAscending ||
        [value compare:@4] == NSOrderedDescending) return -1;
    return [value intValue];
}
NSString* plist_path(const std::string& dir) {
    return [[NSString stringWithUTF8String:dir.c_str()]
        stringByAppendingPathComponent:@"experimental-settings.plist"];
}
NSDictionary* read_dictionary(const std::string& dir) {
    if (dir.empty()) return nil;
    NSDictionary* plist = [NSDictionary dictionaryWithContentsOfFile:plist_path(dir)];
    return [plist isKindOfClass:[NSDictionary class]] ? plist : nil;
}
}  // namespace

Settings load_settings_file(const std::string& user_dir) {
    @autoreleasepool {
        Settings s;
        NSDictionary* plist = read_dictionary(user_dir);
        s.highFps = flag(plist, @"highFPS");
        s.hdWidescreen = flag(plist, @"hdWidescreen");
        s.sharperImage = flag(plist, @"sharperImage");
        s.rememberLanguage = flag(plist, @"rememberLanguage");
        s.skipLogos = flag(plist, @"skipLogos");
        s.extraLives99 = flag(plist, @"extraLives99");
        s.unlimitedSpears = flag(plist, @"unlimitedSpears");
        s.unlockAllMissions = flag(plist, @"unlockAllMissions");
        s.lastLanguage = s.rememberLanguage ? valid_language(plist[@"lastLanguage"]) : -1;
        id target = plist[@"targetFPS"];
        if (is_integer_number(target)) {
            const int parsed = [target intValue];
            if (parsed == 60) s.targetFps = parsed;  // a stored 120 migrates to 60; 120 is env-only
        }
        id scale = plist[@"renderScale"];
        s.renderScale = scale ? valid_render_scale(scale) : (s.hdWidescreen ? 2 : 1);
        if (s.renderScale == 0) s.renderScale = 1;
        return s;
    }
}

void apply_env_overrides(Settings& s, EnvLookup lookup) {
    auto get = [&](const char* name) -> const char* {
        const char* v = lookup(name);
        return v && v[0] ? v : nullptr;
    };
    auto is = [](const char* v, const char* want) { return v && std::string(v) == want; };
    if (is(get("TARZAN_EXPERIMENT_HD"), "1")) s.hdWidescreen = true;
    if (const char* fps = get("TARZAN_EXPERIMENT_FPS"); is(fps, "60") || is(fps, "120")) {
        s.highFps = true;
        s.targetFps = std::string(fps) == "120" ? 120 : 60;
    }
    if (const char* scale = get("TARZAN_EXPERIMENT_SCALE"); scale && scale[0] >= '1' && scale[0] <= '4' && !scale[1])
        s.renderScale = scale[0] - '0';
    if (is(get("TARZAN_EXPERIMENT_SHARP"), "1")) s.sharperImage = true;
    if (const char* wall = get("PS2X_WALLCLOCK"); wall && std::string(wall) != "1") s.wallClock = false;
}

bool save_settings_file(const std::string& user_dir, const Settings& s) {
    @autoreleasepool {
        if (user_dir.empty()) return false;
        NSMutableDictionary* plist = [NSMutableDictionary dictionaryWithDictionary:(read_dictionary(user_dir) ?: @{})];
        plist[@"highFPS"] = @(s.highFps);
        plist[@"hdWidescreen"] = @(s.hdWidescreen);
        plist[@"targetFPS"] = @(s.targetFps);
        plist[@"renderScale"] = @(s.renderScale);
        plist[@"sharperImage"] = @(s.sharperImage);
        plist[@"skipLogos"] = @(s.skipLogos);
        plist[@"extraLives99"] = @(s.extraLives99);
        plist[@"unlimitedSpears"] = @(s.unlimitedSpears);
        plist[@"unlockAllMissions"] = @(s.unlockAllMissions);
        plist[@"rememberLanguage"] = @(s.rememberLanguage);
        if (s.rememberLanguage && s.lastLanguage >= 0 && s.lastLanguage <= 4)
            plist[@"lastLanguage"] = @(s.lastLanguage);
        else
            [plist removeObjectForKey:@"lastLanguage"];
        NSError* error = nil;
        NSData* data = [NSPropertyListSerialization dataWithPropertyList:plist
            format:NSPropertyListXMLFormat_v1_0 options:0 error:&error];
        if (!data) { NSLog(@"Settings serialization failed: %@", error); return false; }
        NSString* directory = [NSString stringWithUTF8String:user_dir.c_str()];
        if (![[NSFileManager defaultManager] createDirectoryAtPath:directory
                withIntermediateDirectories:YES attributes:nil error:&error]) {
            NSLog(@"Settings directory creation failed: %@", error);
            return false;
        }
        if (![data writeToFile:plist_path(user_dir) options:NSDataWritingAtomic error:&error]) {
            NSLog(@"Settings save failed: %@", error);
            return false;
        }
        return true;
    }
}

bool save_last_language(const std::string& user_dir, int language) {
    @autoreleasepool {
        if (user_dir.empty() || language < 0 || language > 4) return false;
        NSDictionary* current = read_dictionary(user_dir);
        if (!flag(current, @"rememberLanguage")) return false;
        NSMutableDictionary* plist = [NSMutableDictionary dictionaryWithDictionary:current];
        plist[@"lastLanguage"] = @(language);
        NSData* data = [NSPropertyListSerialization dataWithPropertyList:plist
            format:NSPropertyListXMLFormat_v1_0 options:0 error:nil];
        return data && [data writeToFile:plist_path(user_dir) options:NSDataWritingAtomic error:nil];
    }
}

}  // namespace tarzan
