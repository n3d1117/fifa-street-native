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
    for (int scale = 1; scale <= 6; ++scale)
        if ([value compare:@(scale)] == NSOrderedSame) return scale;
    return 0;
}
int valid_anisotropy(id value) {
    if (!is_integer_number(value)) return -1;
    for (int level : {0, 2, 4, 8, 16})
        if ([value compare:@(level)] == NSOrderedSame) return level;
    return -1;
}
DisplayAspect display_aspect(id value) {
    if (![value isKindOfClass:[NSString class]]) return DisplayAspect::Auto;
    if ([value isEqualToString:@"4:3"]) return DisplayAspect::FourThree;
    if ([value isEqualToString:@"16:9"]) return DisplayAspect::SixteenNine;
    return DisplayAspect::Auto;
}
NSString* display_aspect_name(DisplayAspect aspect) {
    switch (aspect) {
        case DisplayAspect::FourThree: return @"4:3";
        case DisplayAspect::SixteenNine: return @"16:9";
        case DisplayAspect::Auto: return @"auto";
    }
    return @"auto";
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
        s.fxaa = flag(plist, @"fxaa");
        s.unlockAllVenues = flag(plist, @"unlockAllVenues");
        s.videoMode = [plist[@"videoMode"] isKindOfClass:[NSString class]] &&
            [plist[@"videoMode"] isEqualToString:@"ntsc"] ? VideoMode::NTSC : VideoMode::PAL;
        s.displayAspect = display_aspect(plist[@"displayAspect"]);
        if (const int scale = valid_render_scale(plist[@"renderScale"])) s.renderScale = scale;
        if (const int aniso = valid_anisotropy(plist[@"anisotropy"]); aniso >= 0) s.anisotropy = aniso;
        return s;
    }
}

void apply_env_overrides(Settings& s, EnvLookup lookup) {
    auto get = [&](const char* name) -> const char* {
        const char* v = lookup(name);
        return v && v[0] ? v : nullptr;
    };
    if (const char* scale = get("PS2X_SCALE"); scale && scale[0] >= '1' && scale[0] <= '6' && !scale[1])
        s.renderScale = scale[0] - '0';
    if (const char* aniso = get("PS2X_ANISO")) {
        const std::string value(aniso);
        if (value == "0" || value == "2" || value == "4" || value == "8" || value == "16")
            s.anisotropy = std::stoi(value);
    }
    if (const char* sharp = get("PS2X_SHARP"); sharp && std::string(sharp) == "1") s.sharperImage = true;
    if (const char* fxaa = get("PS2X_FXAA"); fxaa && std::string(fxaa) == "1") s.fxaa = true;
    if (const char* wall = get("PS2X_WALLCLOCK"); wall && std::string(wall) != "1") s.wallClock = false;
    if (const char* venues = get("PS2X_CHEAT_UNLOCK_VENUES"); venues && std::string(venues) == "1") s.unlockAllVenues = true;
    if (const char* mode = get("PS2X_VIDEO_MODE")) {
        if (std::string(mode) == "pal") s.videoMode = VideoMode::PAL;
        else if (std::string(mode) == "ntsc") s.videoMode = VideoMode::NTSC;
    }
    if (const char* aspect = get("PS2X_ASPECT")) {
        const std::string value(aspect);
        if (value == "auto") s.displayAspect = DisplayAspect::Auto;
        else if (value == "4:3") s.displayAspect = DisplayAspect::FourThree;
        else if (value == "16:9") s.displayAspect = DisplayAspect::SixteenNine;
    }
}

bool save_settings_file(const std::string& user_dir, const Settings& s) {
    @autoreleasepool {
        if (user_dir.empty()) return false;
        NSMutableDictionary* plist = [NSMutableDictionary dictionaryWithDictionary:(read_dictionary(user_dir) ?: @{})];
        plist[@"renderScale"] = @(s.renderScale);
        plist[@"anisotropy"] = @(s.anisotropy);
        plist[@"sharperImage"] = @(s.sharperImage);
        plist[@"fxaa"] = @(s.fxaa);
        plist[@"unlockAllVenues"] = @(s.unlockAllVenues);
        plist[@"videoMode"] = s.videoMode == VideoMode::NTSC ? @"ntsc" : @"pal";
        plist[@"displayAspect"] = display_aspect_name(s.displayAspect);
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
