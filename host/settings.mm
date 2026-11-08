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
                quality.renderScale = scale < 2 ? 2 : (scale > 6 ? 6 : scale);
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
        s.rumble = flag(plist, @"rumble");
        s.videoMode = [plist[@"videoMode"] isKindOfClass:[NSString class]] &&
            [plist[@"videoMode"] isEqualToString:@"pal"] ? VideoMode::PAL : VideoMode::NTSC;
        s.displayAspect = display_aspect(plist[@"displayAspect"]);
        if (plist) {
            if (const int scale = valid_render_scale(plist[@"renderScale"])) s.renderScale = scale;
            if (const int aniso = valid_anisotropy(plist[@"anisotropy"]); aniso >= 0) s.anisotropy = aniso;
        }
        if (const int intensity = valid_rumble_intensity(plist[@"rumbleIntensity"]); intensity >= 0) s.rumbleIntensity = intensity;
        return s;
    }
}

bool save_settings_file(const std::string& user_dir, const Settings& s) {
    @autoreleasepool {
        if (user_dir.empty()) return false;
        NSMutableDictionary* plist = [NSMutableDictionary dictionary];
        plist[@"renderScale"] = @(s.renderScale);
        plist[@"anisotropy"] = @(s.anisotropy);
        plist[@"rumble"] = @(s.rumble);
        plist[@"rumbleIntensity"] = @(s.rumbleIntensity);
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
