// SPDX-License-Identifier: GPL-3.0-or-later
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

#include "experimental-settings.hpp"

#include <atomic>

@interface TarzanExperimentalSettingsActions : NSObject <NSWindowDelegate>
- (void)settingChanged:(NSButton*)sender;
- (void)targetChanged:(NSPopUpButton*)sender;
- (void)saveSettings:(id)sender;
@end

namespace {
std::atomic<bool> s_highFPS{false};
std::atomic<bool> s_hdWidescreen{false};
std::atomic<bool> s_pendingHighFPS{false};
std::atomic<bool> s_pendingHDWidescreen{false};
std::atomic<int> s_targetFPS{60};
std::atomic<int> s_pendingTargetFPS{60};
std::atomic<bool> s_configured{false};
NSString* s_userDirectory;
NSWindow* s_window;
NSButton* s_highFPSButton;
NSButton* s_hdWidescreenButton;
NSPopUpButton* s_targetFPSPopup;
NSTextField* s_status;
TarzanExperimentalSettingsActions* s_actions;

constexpr NSInteger kHighFPSTag = 1;
constexpr NSInteger kHDWidescreenTag = 2;

bool isBoolean(id value) {
    return [value isKindOfClass:[NSNumber class]] &&
        CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID();
}

NSDictionary* loadSettings(NSString* directory) {
    if (!directory || directory.length == 0) return nil;
    NSString* path = [directory stringByAppendingPathComponent:@"experimental-settings.plist"];
    NSDictionary* plist = [NSDictionary dictionaryWithContentsOfFile:path];
    return [plist isKindOfClass:[NSDictionary class]] ? plist : nil;
}

void setStatus(NSString* value) {
    if (s_status) [s_status setStringValue:value ?: @""];
}

void refreshControls() {
    if (s_highFPSButton)
        [s_highFPSButton setState:s_pendingHighFPS.load(std::memory_order_relaxed) ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_hdWidescreenButton)
        [s_hdWidescreenButton setState:s_pendingHDWidescreen.load(std::memory_order_relaxed) ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_targetFPSPopup)
        [s_targetFPSPopup selectItemWithTag:s_pendingTargetFPS.load(std::memory_order_relaxed)];
}

void updatePendingFromControl(NSButton* sender) {
    const bool enabled = [sender state] == NSControlStateValueOn;
    if ([sender tag] == kHighFPSTag)
        s_pendingHighFPS.store(enabled, std::memory_order_relaxed);
    else if ([sender tag] == kHDWidescreenTag)
        s_pendingHDWidescreen.store(enabled, std::memory_order_relaxed);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}

void updatePendingTargetFromControl(NSPopUpButton* sender) {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected == 60 || selected == 120)
        s_pendingTargetFPS.store(static_cast<int>(selected), std::memory_order_relaxed);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}

bool saveSettings() {
    if (!s_userDirectory || s_userDirectory.length == 0) return false;
    NSDictionary* plist = @{
        @"highFPS": @(s_pendingHighFPS.load(std::memory_order_relaxed)),
        @"hdWidescreen": @(s_pendingHDWidescreen.load(std::memory_order_relaxed)),
        @"targetFPS": @(s_pendingTargetFPS.load(std::memory_order_relaxed))
    };
    NSError* error = nil;
    NSData* data = [NSPropertyListSerialization dataWithPropertyList:plist
        format:NSPropertyListXMLFormat_v1_0 options:0 error:&error];
    if (!data) {
        NSLog(@"Experimental settings serialization failed: %@", error);
        return false;
    }
    NSFileManager* manager = [NSFileManager defaultManager];
    if (![manager createDirectoryAtPath:s_userDirectory withIntermediateDirectories:YES attributes:nil error:&error]) {
        NSLog(@"Experimental settings directory creation failed: %@", error);
        return false;
    }
    NSString* path = [s_userDirectory stringByAppendingPathComponent:@"experimental-settings.plist"];
    if (![data writeToFile:path options:NSDataWritingAtomic error:&error]) {
        NSLog(@"Experimental settings save failed: %@", error);
        return false;
    }
    return true;
}

void showWindowOnMain() {
    if (!s_actions) s_actions = [TarzanExperimentalSettingsActions new];
    if (s_window) {
        refreshControls();
        [s_window makeKeyAndOrderFront:nil];
        [NSApp activateIgnoringOtherApps:YES];
        return;
    }

    const CGFloat width = 590;
    const CGFloat height = 344;
    s_window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, width, height)
        styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable)
        backing:NSBackingStoreBuffered defer:NO];
    [s_window setTitle:@"Experimental Settings"];
    [s_window setReleasedWhenClosed:NO];
    [s_window setDelegate:s_actions];
    [s_window center];
    NSView* content = [s_window contentView];

    NSTextField* heading = [NSTextField labelWithString:@"Experimental"];
    [heading setFont:[NSFont boldSystemFontOfSize:15]];
    [heading setFrame:NSMakeRect(22, 294, 546, 22)];
    [content addSubview:heading];

    s_highFPSButton = [NSButton checkboxWithTitle:@"Higher frame rate" target:s_actions action:@selector(settingChanged:)];
    [s_highFPSButton setTag:kHighFPSTag];
    [s_highFPSButton setFrame:NSMakeRect(22, 250, 350, 24)];
    [content addSubview:s_highFPSButton];
    s_targetFPSPopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(406, 248, 150, 28) pullsDown:NO];
    [s_targetFPSPopup addItemWithTitle:@"60 FPS"];
    [[s_targetFPSPopup lastItem] setTag:60];
    [s_targetFPSPopup addItemWithTitle:@"120 FPS"];
    [[s_targetFPSPopup lastItem] setTag:120];
    [s_targetFPSPopup setTarget:s_actions];
    [s_targetFPSPopup setAction:@selector(targetChanged:)];
    [content addSubview:s_targetFPSPopup];
    NSTextField* fpsDetail = [NSTextField labelWithString:@"Experimental; target may not be reached in all scenes."];
    [fpsDetail setFrame:NSMakeRect(46, 216, 510, 24)];
    [fpsDetail setMaximumNumberOfLines:2];
    [[fpsDetail cell] setWraps:YES];
    [[fpsDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [fpsDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:fpsDetail];

    s_hdWidescreenButton = [NSButton checkboxWithTitle:@"Higher resolution and widescreen (16:9)" target:s_actions action:@selector(settingChanged:)];
    [s_hdWidescreenButton setTag:kHDWidescreenTag];
    [s_hdWidescreenButton setFrame:NSMakeRect(22, 166, 546, 24)];
    [content addSubview:s_hdWidescreenButton];
    NSTextField* hdDetail = [NSTextField labelWithString:@"Uses 2× internal render scale and correct 16:9 camera framing. Some 4:3 movies and UI may be letterboxed."];
    [hdDetail setFrame:NSMakeRect(46, 119, 510, 38)];
    [hdDetail setMaximumNumberOfLines:2];
    [[hdDetail cell] setWraps:YES];
    [[hdDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [hdDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:hdDetail];

    s_status = [NSTextField labelWithString:@"Changes apply after restarting the game."];
    [s_status setFrame:NSMakeRect(22, 62, 546, 20)];
    [s_status setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:s_status];
    NSButton* save = [NSButton buttonWithTitle:@"Save" target:s_actions action:@selector(saveSettings:)];
    [save setFrame:NSMakeRect(width - 112, 16, 90, 30)];
    [save setKeyEquivalent:@"\r"];
    [content addSubview:save];

    refreshControls();
    [s_window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}
}

@implementation TarzanExperimentalSettingsActions
- (void)windowWillClose:(NSNotification*)notification {
    (void)notification;
    s_window = nil;
    s_highFPSButton = nil;
    s_hdWidescreenButton = nil;
    s_targetFPSPopup = nil;
    s_status = nil;
}
- (void)settingChanged:(NSButton*)sender { updatePendingFromControl(sender); }
- (void)targetChanged:(NSPopUpButton*)sender { updatePendingTargetFromControl(sender); }
- (void)saveSettings:(id)sender {
    (void)sender;
    if (saveSettings())
        setStatus(@"Settings saved. Restart the game to apply them.");
    else
        setStatus(@"Could not save experimental-settings.plist.");
}
@end

extern "C" void tarzan_experiments_configure(const char* userDirectory) {
    bool expected = false;
    if (!s_configured.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) return;
    @autoreleasepool {
        s_userDirectory = userDirectory ? [[NSString stringWithUTF8String:userDirectory] copy] : nil;
        NSDictionary* plist = loadSettings(s_userDirectory);
        const bool highFPS = isBoolean(plist[@"highFPS"]) && [plist[@"highFPS"] boolValue];
        const bool hdWidescreen = isBoolean(plist[@"hdWidescreen"]) && [plist[@"hdWidescreen"] boolValue];
        int targetFPS = 60;
        id targetValue = plist[@"targetFPS"];
        if ([targetValue isKindOfClass:[NSNumber class]] &&
            CFGetTypeID((__bridge CFTypeRef)targetValue) == CFNumberGetTypeID() &&
            !CFNumberIsFloatType((__bridge CFNumberRef)targetValue)) {
            const int parsed = [targetValue intValue];
            if (parsed == 60 || parsed == 120) targetFPS = parsed;
        }
        s_highFPS.store(highFPS, std::memory_order_relaxed);
        s_hdWidescreen.store(hdWidescreen, std::memory_order_relaxed);
        s_targetFPS.store(targetFPS, std::memory_order_relaxed);
        s_pendingHighFPS.store(highFPS, std::memory_order_relaxed);
        s_pendingHDWidescreen.store(hdWidescreen, std::memory_order_relaxed);
        s_pendingTargetFPS.store(targetFPS, std::memory_order_relaxed);
    }
}

extern "C" void tarzan_experiments_show() {
    if ([NSThread isMainThread]) showWindowOnMain();
    else dispatch_async(dispatch_get_main_queue(), ^{ showWindowOnMain(); });
}

extern "C" int tarzan_experiments_high_fps() { return s_highFPS.load(std::memory_order_relaxed) ? 1 : 0; }
extern "C" int tarzan_experiments_target_fps() { return s_targetFPS.load(std::memory_order_relaxed); }
extern "C" int tarzan_experiments_hd_widescreen() { return s_hdWidescreen.load(std::memory_order_relaxed) ? 1 : 0; }

extern "C" void tarzan_experiments_set_high_fps(int enabled) {
    const bool value = enabled != 0;
    s_highFPS.store(value, std::memory_order_relaxed);
    s_pendingHighFPS.store(value, std::memory_order_relaxed);
}

extern "C" void tarzan_experiments_set_target_fps(int targetFPS) {
    if (targetFPS != 60 && targetFPS != 120) return;
    s_targetFPS.store(targetFPS, std::memory_order_relaxed);
    s_pendingTargetFPS.store(targetFPS, std::memory_order_relaxed);
}

extern "C" void tarzan_experiments_set_hd_widescreen(int enabled) {
    const bool value = enabled != 0;
    s_hdWidescreen.store(value, std::memory_order_relaxed);
    s_pendingHDWidescreen.store(value, std::memory_order_relaxed);
}
