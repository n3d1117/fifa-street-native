// SPDX-License-Identifier: GPL-3.0-or-later
#import <Foundation/Foundation.h>

#include <cctype>
#include <string>

#include "settings.hpp"

#if TARGET_OS_IPHONE
// Metal names the GPU on iOS and tvOS; macOS also needs the screen geometry.
#import <Metal/Metal.h>
#else
#import <AppKit/AppKit.h>
#endif

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
int valid_rumble_intensity(id value) {
    if (!is_integer_number(value)) return -1;
    const NSInteger level = [value integerValue];
    return level >= 25 && level <= 100 ? static_cast<int>(level) : -1;
}
DisplayAspect display_aspect(id value) {
    if (![value isKindOfClass:[NSString class]]) return Settings{}.displayAspect;
    if ([value isEqualToString:@"4:3"]) return DisplayAspect::FourThree;
    if ([value isEqualToString:@"16:9"]) return DisplayAspect::SixteenNine;
    if ([value isEqualToString:@"auto"]) return DisplayAspect::Auto;
    return Settings{}.displayAspect;
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
// Launch-time quality defaults. Only two classes of machine have been measured:
//   Apple A13 (iPhone 11, 1125x2436): 2x holds 60 Hz (58.9-59.2 fps, GPU 6.3 ms
//     p50 / 8.0 ms p95); 3x is measurably worse (late frames +5.8 pp), so A12/A13
//     class parts start at 2x.
//   Apple A15 (Apple TV 4K): 3x fits the 60 Hz budget at 10.9 ms GPU p50 and 5x
//     dropped frames, so A14 and newer start at 3x.
// Anisotropy 16 is smoothness-neutral at 2x on the A13 (GPU +1.4 ms, fps and
// late frames within noise) and is the sharper setting, so mobile starts at 16.
// macOS picks the scale whose render height covers the display's pixel height,
// clamped to the measured-safe range: this M1 renders 4x at 60 fps with zero
// underruns, and every scale from 1x to 5x was measured at 60.0 fps.
int apple_gpu_tier(NSString* name) {
    // "Apple A13 GPU", "Apple A15 GPU", "Apple M1", "Apple M2 Pro", "Apple GPU".
    const char* text = name.UTF8String;
    if (!text) return 0;
    for (const char* p = text; *p; ++p) {
        if (*p != 'A' && *p != 'M') continue;
        if (p != text && std::isalnum(static_cast<unsigned char>(p[-1]))) continue;
        const char* digit = p + 1;
        if (!std::isdigit(static_cast<unsigned char>(*digit))) continue;
        int value = 0;
        while (std::isdigit(static_cast<unsigned char>(*digit))) value = value * 10 + (*digit++ - '0');
        return *p == 'M' ? 100 + value : value;
    }
    return 0;
}
}  // namespace

HardwareQuality detect_hardware_quality() {
    @autoreleasepool {
        HardwareQuality quality;
#if defined(TARGET_OS_TV) && TARGET_OS_TV
        quality.renderScale = 3;
        quality.anisotropy = 8;
#elif defined(TARGET_OS_IPHONE) && TARGET_OS_IPHONE
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        const int tier = device ? apple_gpu_tier(device.name) : 0;
        quality.renderScale = (tier >= 14 && tier < 100) || tier >= 100 ? 3 : 2;
        quality.anisotropy = 16;
#else
        quality.renderScale = 2;
        quality.anisotropy = 8;
        NSScreen* screen = [NSScreen mainScreen];
        if (screen) {
            const NSRect pixels = [screen convertRectToBacking:screen.frame];
            const int height = static_cast<int>(pixels.size.height);
            if (height > 0) {
                const int scale = (height + 447) / 448;
                quality.renderScale = scale < 2 ? 2 : (scale > 4 ? 4 : scale);
            }
        }
#endif
        return quality;
    }
}

Settings load_settings_file(const std::string& user_dir) {
    @autoreleasepool {
        Settings s;
        NSDictionary* plist = read_dictionary(user_dir);
        const HardwareQuality quality = detect_hardware_quality();
        s.renderScale = quality.renderScale;
        s.anisotropy = quality.anisotropy;
        // Installs that predate the detected defaults keep whatever they saved;
        // older settings.plist files are migrated once, so a phone that saved the
        // old 1x default picks up 2x instead of staying soft forever.
        const NSInteger version = is_integer_number(plist[@"defaultsVersion"])
            ? [plist[@"defaultsVersion"] integerValue] : 0;
        const bool adoptStoredQuality = plist != nil && version >= kDefaultsVersion;
        s.sharperImage = flag(plist, @"sharperImage");
        s.rumble = flag(plist, @"rumble");
        s.fxaa = flag(plist, @"fxaa");
        s.smoothUiTextures = flag(plist, @"smoothUiTextures");
        s.unlockAllVenues = flag(plist, @"unlockAllVenues");
        s.videoMode = [plist[@"videoMode"] isKindOfClass:[NSString class]] &&
            [plist[@"videoMode"] isEqualToString:@"pal"] ? VideoMode::PAL : VideoMode::NTSC;
        s.displayAspect = display_aspect(plist[@"displayAspect"]);
        if (adoptStoredQuality) {
            if (const int scale = valid_render_scale(plist[@"renderScale"])) s.renderScale = scale;
            if (const int aniso = valid_anisotropy(plist[@"anisotropy"]); aniso >= 0) s.anisotropy = aniso;
        }
        if (const int intensity = valid_rumble_intensity(plist[@"rumbleIntensity"]); intensity >= 0) s.rumbleIntensity = intensity;
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
    if (const char* ui = get("PS2X_UI_UPSCALE")) {
        if (std::string(ui) == "0") s.smoothUiTextures = false;
        else if (std::string(ui) == "1") s.smoothUiTextures = true;
    }
    if (const char* rumble = get("PS2X_RUMBLE")) {
        if (std::string(rumble) == "0") s.rumble = false;
        else if (std::string(rumble) == "1") s.rumble = true;
    }
    if (const char* intensity = get("PS2X_RUMBLE_INTENSITY")) {
        const std::string value(intensity);
        try {
            const int level = std::stoi(value);
            if (level >= 25 && level <= 100 && std::to_string(level) == value) s.rumbleIntensity = level;
        } catch (...) {}
    }
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
        plist[@"defaultsVersion"] = @(kDefaultsVersion);
        plist[@"sharperImage"] = @(s.sharperImage);
        plist[@"rumble"] = @(s.rumble);
        plist[@"rumbleIntensity"] = @(s.rumbleIntensity);
        plist[@"fxaa"] = @(s.fxaa);
        plist[@"smoothUiTextures"] = @(s.smoothUiTextures);
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
