// SPDX-License-Identifier: GPL-3.0-or-later
#import <Foundation/Foundation.h>

#include "settings.hpp"

namespace host {
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
NSString* plist_path(const std::string& dir) {
    return [[NSString stringWithUTF8String:dir.c_str()] stringByAppendingPathComponent:@"settings.plist"];
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
        s.sharperImage = flag(plist, @"sharperImage");
        if (const int scale = valid_render_scale(plist[@"renderScale"])) s.renderScale = scale;
        return s;
    }
}

void apply_env_overrides(Settings& s, EnvLookup lookup) {
    auto get = [&](const char* name) -> const char* {
        const char* v = lookup(name);
        return v && v[0] ? v : nullptr;
    };
    if (const char* scale = get("PS2X_SCALE"); scale && scale[0] >= '1' && scale[0] <= '4' && !scale[1])
        s.renderScale = scale[0] - '0';
    if (const char* sharp = get("PS2X_SHARP"); sharp && std::string(sharp) == "1") s.sharperImage = true;
    if (const char* wall = get("PS2X_WALLCLOCK"); wall && std::string(wall) != "1") s.wallClock = false;
}

bool save_settings_file(const std::string& user_dir, const Settings& s) {
    @autoreleasepool {
        if (user_dir.empty()) return false;
        NSMutableDictionary* plist = [NSMutableDictionary dictionaryWithDictionary:(read_dictionary(user_dir) ?: @{})];
        plist[@"renderScale"] = @(s.renderScale);
        plist[@"sharperImage"] = @(s.sharperImage);
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

}  // namespace host
