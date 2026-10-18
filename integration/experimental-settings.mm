// SPDX-License-Identifier: GPL-3.0-or-later
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

#include "experimental-settings.hpp"

#include <atomic>
#include <mutex>

@interface TarzanExperimentalSettingsActions : NSObject <NSWindowDelegate>
- (void)settingChanged:(NSButton*)sender;
- (void)targetChanged:(NSPopUpButton*)sender;
- (void)renderScaleChanged:(NSPopUpButton*)sender;
- (void)saveSettings:(id)sender;
@end

namespace {
std::atomic<bool> s_highFPS{false};
std::atomic<bool> s_hdWidescreen{false};
std::atomic<bool> s_pendingHighFPS{false};
std::atomic<bool> s_pendingHDWidescreen{false};
std::atomic<bool> s_sharperImage{false};
std::atomic<bool> s_pendingSharperImage{false};
std::atomic<bool> s_rememberLanguage{false};
std::atomic<bool> s_pendingRememberLanguage{false};
std::atomic<bool> s_skipLogos{false};
std::atomic<bool> s_pendingSkipLogos{false};
std::atomic<bool> s_extraLives99{false};
std::atomic<bool> s_pendingExtraLives99{false};
std::atomic<bool> s_unlimitedSpears{false};
std::atomic<bool> s_pendingUnlimitedSpears{false};
std::atomic<int> s_savedLanguage{-1};
std::atomic<int> s_targetFPS{60};
std::atomic<int> s_pendingTargetFPS{60};
std::atomic<int> s_renderScale{1};
std::atomic<int> s_pendingRenderScale{1};
std::atomic<bool> s_configured{false};
std::mutex s_settingsMutex;
NSString* s_userDirectory;
NSWindow* s_window;
NSButton* s_highFPSButton;
NSButton* s_hdWidescreenButton;
NSButton* s_sharperImageButton;
NSButton* s_rememberLanguageButton;
NSButton* s_skipLogosButton;
NSButton* s_extraLives99Button;
NSButton* s_unlimitedSpearsButton;
NSPopUpButton* s_targetFPSPopup;
NSPopUpButton* s_renderScalePopup;
NSTextField* s_status;
TarzanExperimentalSettingsActions* s_actions;

constexpr NSInteger kHighFPSTag = 1;
constexpr NSInteger kHDWidescreenTag = 2;
constexpr NSInteger kSharperImageTag = 3;
constexpr NSInteger kRememberLanguageTag = 4;
constexpr NSInteger kSkipLogosTag = 5;
constexpr NSInteger kExtraLives99Tag = 6;
constexpr NSInteger kUnlimitedSpearsTag = 7;

bool isIntegerNumber(id value) {
    return [value isKindOfClass:[NSNumber class]] &&
        CFGetTypeID((__bridge CFTypeRef)value) == CFNumberGetTypeID() &&
        !CFNumberIsFloatType((__bridge CFNumberRef)value);
}

int validRenderScale(id value) {
    if (!isIntegerNumber(value)) return 0;
    for (int scale = 1; scale <= 4; ++scale)
        if ([value compare:@(scale)] == NSOrderedSame) return scale;
    return 0;
}

int validLanguage(id value) {
    if (!isIntegerNumber(value) || [value compare:@0] == NSOrderedAscending ||
        [value compare:@4] == NSOrderedDescending) return -1;
    return [value intValue];
}

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
    if (s_sharperImageButton)
        [s_sharperImageButton setState:s_pendingSharperImage.load(std::memory_order_relaxed) ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_rememberLanguageButton)
        [s_rememberLanguageButton setState:s_pendingRememberLanguage.load(std::memory_order_relaxed) ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_skipLogosButton)
        [s_skipLogosButton setState:s_pendingSkipLogos.load(std::memory_order_relaxed) ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_extraLives99Button)
        [s_extraLives99Button setState:s_pendingExtraLives99.load(std::memory_order_relaxed) ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_unlimitedSpearsButton)
        [s_unlimitedSpearsButton setState:s_pendingUnlimitedSpears.load(std::memory_order_relaxed) ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_targetFPSPopup)
        [s_targetFPSPopup selectItemWithTag:s_pendingTargetFPS.load(std::memory_order_relaxed)];
    if (s_renderScalePopup)
        [s_renderScalePopup selectItemWithTag:s_pendingRenderScale.load(std::memory_order_relaxed)];
}

void updatePendingFromControl(NSButton* sender) {
    const bool enabled = [sender state] == NSControlStateValueOn;
    if ([sender tag] == kHighFPSTag)
        s_pendingHighFPS.store(enabled, std::memory_order_relaxed);
    else if ([sender tag] == kHDWidescreenTag)
        s_pendingHDWidescreen.store(enabled, std::memory_order_relaxed);
    else if ([sender tag] == kSharperImageTag)
        s_pendingSharperImage.store(enabled, std::memory_order_relaxed);
    else if ([sender tag] == kRememberLanguageTag)
        s_pendingRememberLanguage.store(enabled, std::memory_order_relaxed);
    else if ([sender tag] == kSkipLogosTag)
        s_pendingSkipLogos.store(enabled, std::memory_order_relaxed);
    else if ([sender tag] == kExtraLives99Tag)
        s_pendingExtraLives99.store(enabled, std::memory_order_relaxed);
    else if ([sender tag] == kUnlimitedSpearsTag)
        s_pendingUnlimitedSpears.store(enabled, std::memory_order_relaxed);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}

void updatePendingTargetFromControl(NSPopUpButton* sender) {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected == 60 || selected == 120)
        s_pendingTargetFPS.store(static_cast<int>(selected), std::memory_order_relaxed);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}

void updatePendingRenderScaleFromControl(NSPopUpButton* sender) {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected >= 1 && selected <= 4)
        s_pendingRenderScale.store(static_cast<int>(selected), std::memory_order_relaxed);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}

bool writeSettingsDictionary(NSDictionary* plist) {
    if (!s_userDirectory || s_userDirectory.length == 0) return false;
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

bool saveSettings() {
    std::lock_guard<std::mutex> lock(s_settingsMutex);
    if (!s_userDirectory || s_userDirectory.length == 0) return false;
    NSMutableDictionary* plist = [NSMutableDictionary dictionaryWithDictionary:(loadSettings(s_userDirectory) ?: @{})];
    plist[@"highFPS"] = @(s_pendingHighFPS.load(std::memory_order_relaxed));
    plist[@"hdWidescreen"] = @(s_pendingHDWidescreen.load(std::memory_order_relaxed));
    plist[@"targetFPS"] = @(s_pendingTargetFPS.load(std::memory_order_relaxed));
    plist[@"renderScale"] = @(s_pendingRenderScale.load(std::memory_order_relaxed));
    plist[@"sharperImage"] = @(s_pendingSharperImage.load(std::memory_order_relaxed));
    plist[@"skipLogos"] = @(s_pendingSkipLogos.load(std::memory_order_relaxed));
    plist[@"extraLives99"] = @(s_pendingExtraLives99.load(std::memory_order_relaxed));
    plist[@"unlimitedSpears"] = @(s_pendingUnlimitedSpears.load(std::memory_order_relaxed));
    const bool remember = s_pendingRememberLanguage.load(std::memory_order_relaxed);
    plist[@"rememberLanguage"] = @(remember);
    const int language = s_savedLanguage.load(std::memory_order_relaxed);
    if (remember && language >= 0 && language <= 4)
        plist[@"lastLanguage"] = @(language);
    else
        [plist removeObjectForKey:@"lastLanguage"];
    return writeSettingsDictionary(plist);
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
    const CGFloat height = 720;
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
    [heading setFrame:NSMakeRect(22, 442, 546, 22)];
    [content addSubview:heading];

    s_highFPSButton = [NSButton checkboxWithTitle:@"Higher frame rate" target:s_actions action:@selector(settingChanged:)];
    [s_highFPSButton setTag:kHighFPSTag];
    [s_highFPSButton setFrame:NSMakeRect(22, 398, 350, 24)];
    [content addSubview:s_highFPSButton];
    s_targetFPSPopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(406, 396, 150, 28) pullsDown:NO];
    [s_targetFPSPopup addItemWithTitle:@"60 FPS"];
    [[s_targetFPSPopup lastItem] setTag:60];
    [s_targetFPSPopup addItemWithTitle:@"120 FPS"];
    [[s_targetFPSPopup lastItem] setTag:120];
    [s_targetFPSPopup setTarget:s_actions];
    [s_targetFPSPopup setAction:@selector(targetChanged:)];
    [content addSubview:s_targetFPSPopup];
    NSTextField* fpsDetail = [NSTextField labelWithString:@"Experimental; target may not be reached in all scenes."];
    [fpsDetail setFrame:NSMakeRect(46, 364, 510, 24)];
    [fpsDetail setMaximumNumberOfLines:2];
    [[fpsDetail cell] setWraps:YES];
    [[fpsDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [fpsDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:fpsDetail];

    s_hdWidescreenButton = [NSButton checkboxWithTitle:@"Widescreen (16:9)" target:s_actions action:@selector(settingChanged:)];
    [s_hdWidescreenButton setTag:kHDWidescreenTag];
    [s_hdWidescreenButton setFrame:NSMakeRect(22, 334, 546, 24)];
    [content addSubview:s_hdWidescreenButton];
    NSTextField* hdDetail = [NSTextField labelWithString:@"Correct 16:9 camera framing. Some 4:3 movies and UI may be letterboxed."];
    [hdDetail setFrame:NSMakeRect(46, 304, 510, 28)];
    [hdDetail setMaximumNumberOfLines:2];
    [[hdDetail cell] setWraps:YES];
    [[hdDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [hdDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:hdDetail];

    NSTextField* scaleLabel = [NSTextField labelWithString:@"Internal render scale"];
    [scaleLabel setFrame:NSMakeRect(22, 262, 210, 22)];
    [content addSubview:scaleLabel];
    s_renderScalePopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 258, 180, 28) pullsDown:NO];
    const char* scaleTitles[] = {"Native (1×)", "2×", "3×", "4×"};
    for (int scale = 1; scale <= 4; ++scale) {
        [s_renderScalePopup addItemWithTitle:[NSString stringWithUTF8String:scaleTitles[scale - 1]]];
        [[s_renderScalePopup lastItem] setTag:scale];
    }
    [s_renderScalePopup setTarget:s_actions];
    [s_renderScalePopup setAction:@selector(renderScaleChanged:)];
    [content addSubview:s_renderScalePopup];
    NSTextField* scaleDetail = [NSTextField labelWithString:@"Higher scales sharpen 3D geometry; textures and movies keep their original resolution. Higher scales use more GPU."];
    [scaleDetail setFrame:NSMakeRect(22, 202, 546, 46)];
    [scaleDetail setMaximumNumberOfLines:2];
    [[scaleDetail cell] setWraps:YES];
    [[scaleDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [scaleDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:scaleDetail];

    s_sharperImageButton = [NSButton checkboxWithTitle:@"Sharper image" target:s_actions action:@selector(settingChanged:)];
    [s_sharperImageButton setTag:kSharperImageTag];
    [s_sharperImageButton setFrame:NSMakeRect(22, 152, 546, 24)];
    [content addSubview:s_sharperImageButton];
    NSTextField* sharperImageDetail = [NSTextField labelWithString:@"Contrast-adaptive sharpening and sharper display scaling. Original artwork remains unchanged."];
    [sharperImageDetail setFrame:NSMakeRect(46, 116, 510, 28)];
    [sharperImageDetail setMaximumNumberOfLines:2];
    [[sharperImageDetail cell] setWraps:YES];
    [[sharperImageDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [sharperImageDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:sharperImageDetail];

    for (NSView* subview in [content subviews]) {
        NSRect frame = [subview frame];
        frame.origin.y += 76;
        [subview setFrame:frame];
    }

    s_rememberLanguageButton = [NSButton checkboxWithTitle:@"Remember last language" target:s_actions action:@selector(settingChanged:)];
    [s_rememberLanguageButton setTag:kRememberLanguageTag];
    [s_rememberLanguageButton setFrame:NSMakeRect(22, 162, 546, 24)];
    [content addSubview:s_rememberLanguageButton];
    NSTextField* languageDetail = [NSTextField labelWithString:@"Reuse your last choice and skip language selection on launch."];
    [languageDetail setFrame:NSMakeRect(46, 142, 510, 20)];
    [languageDetail setMaximumNumberOfLines:2];
    [[languageDetail cell] setWraps:YES];
    [[languageDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [languageDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:languageDetail];

    // Move all earlier controls up one more row; leave status and Save anchored below.
    for (NSView* subview in [content subviews]) {
        NSRect frame = [subview frame];
        frame.origin.y += 76;
        [subview setFrame:frame];
    }

    s_skipLogosButton = [NSButton checkboxWithTitle:@"Skip Disney / Ubisoft logos" target:s_actions action:@selector(settingChanged:)];
    [s_skipLogosButton setTag:kSkipLogosTag];
    [s_skipLogosButton setFrame:NSMakeRect(22, 184, 546, 24)];
    [content addSubview:s_skipLogosButton];
    NSTextField* logosDetail = [NSTextField labelWithString:@"Boot without branding movies. Story movies still play."];
    [logosDetail setFrame:NSMakeRect(46, 164, 510, 20)];
    [logosDetail setMaximumNumberOfLines:2];
    [[logosDetail cell] setWraps:YES];
    [[logosDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [logosDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:logosDetail];

    // Move all earlier controls up one more row; leave status and Save anchored below.
    for (NSView* subview in [content subviews]) {
        NSRect frame = [subview frame];
        frame.origin.y += 76;
        [subview setFrame:frame];
    }

    s_extraLives99Button = [NSButton checkboxWithTitle:@"99 lives (built-in cheat)" target:s_actions action:@selector(settingChanged:)];
    [s_extraLives99Button setTag:kExtraLives99Tag];
    [s_extraLives99Button setFrame:NSMakeRect(22, 206, 546, 24)];
    [content addSubview:s_extraLives99Button];
    NSTextField* livesDetail = [NSTextField labelWithString:@"Apply the original 99-lives cheat when selecting a mission."];
    [livesDetail setFrame:NSMakeRect(46, 186, 510, 20)];
    [[livesDetail cell] setWraps:YES];
    [[livesDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [livesDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:livesDetail];

    s_unlimitedSpearsButton = [NSButton checkboxWithTitle:@"Unlimited spears (built-in cheat)" target:s_actions action:@selector(settingChanged:)];
    [s_unlimitedSpearsButton setTag:kUnlimitedSpearsTag];
    [s_unlimitedSpearsButton setFrame:NSMakeRect(22, 152, 546, 24)];
    [content addSubview:s_unlimitedSpearsButton];
    NSTextField* spearsDetail = [NSTextField labelWithString:@"Use the original unlimited-spears cheat."];
    [spearsDetail setFrame:NSMakeRect(46, 132, 510, 20)];
    [[spearsDetail cell] setWraps:YES];
    [[spearsDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [spearsDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:spearsDetail];

    s_status = [NSTextField labelWithString:@"Changes apply after restarting the game."];
    [s_status setFrame:NSMakeRect(22, 70, 420, 20)];
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
    // Keep the window and its controls alive for reuse. NSWindow releases itself
    // on close by default; ARC clients must disable that behavior.
}
- (void)settingChanged:(NSButton*)sender { updatePendingFromControl(sender); }
- (void)targetChanged:(NSPopUpButton*)sender { updatePendingTargetFromControl(sender); }
- (void)renderScaleChanged:(NSPopUpButton*)sender { updatePendingRenderScaleFromControl(sender); }
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
        const bool sharperImage = isBoolean(plist[@"sharperImage"]) && [plist[@"sharperImage"] boolValue];
        const bool rememberLanguage = isBoolean(plist[@"rememberLanguage"]) && [plist[@"rememberLanguage"] boolValue];
        const bool skipLogos = isBoolean(plist[@"skipLogos"]) && [plist[@"skipLogos"] boolValue];
        const bool extraLives99 = isBoolean(plist[@"extraLives99"]) && [plist[@"extraLives99"] boolValue];
        const bool unlimitedSpears = isBoolean(plist[@"unlimitedSpears"]) && [plist[@"unlimitedSpears"] boolValue];
        const int savedLanguage = rememberLanguage ? validLanguage(plist[@"lastLanguage"]) : -1;
        int targetFPS = 60;
        id targetValue = plist[@"targetFPS"];
        if ([targetValue isKindOfClass:[NSNumber class]] &&
            CFGetTypeID((__bridge CFTypeRef)targetValue) == CFNumberGetTypeID() &&
            !CFNumberIsFloatType((__bridge CFNumberRef)targetValue)) {
            const int parsed = [targetValue intValue];
            if (parsed == 60 || parsed == 120) targetFPS = parsed;
        }
        id renderScaleValue = plist[@"renderScale"];
        int renderScale = renderScaleValue ? validRenderScale(renderScaleValue) : (hdWidescreen ? 2 : 1);
        if (renderScale == 0) renderScale = 1;
        s_highFPS.store(highFPS, std::memory_order_relaxed);
        s_hdWidescreen.store(hdWidescreen, std::memory_order_relaxed);
        s_sharperImage.store(sharperImage, std::memory_order_relaxed);
        s_rememberLanguage.store(rememberLanguage, std::memory_order_relaxed);
        s_pendingRememberLanguage.store(rememberLanguage, std::memory_order_relaxed);
        s_skipLogos.store(skipLogos, std::memory_order_relaxed);
        s_pendingSkipLogos.store(skipLogos, std::memory_order_relaxed);
        s_extraLives99.store(extraLives99, std::memory_order_relaxed);
        s_pendingExtraLives99.store(extraLives99, std::memory_order_relaxed);
        s_unlimitedSpears.store(unlimitedSpears, std::memory_order_relaxed);
        s_pendingUnlimitedSpears.store(unlimitedSpears, std::memory_order_relaxed);
        s_savedLanguage.store(savedLanguage, std::memory_order_relaxed);
        s_targetFPS.store(targetFPS, std::memory_order_relaxed);
        s_renderScale.store(renderScale, std::memory_order_relaxed);
        s_pendingHighFPS.store(highFPS, std::memory_order_relaxed);
        s_pendingHDWidescreen.store(hdWidescreen, std::memory_order_relaxed);
        s_pendingSharperImage.store(sharperImage, std::memory_order_relaxed);
        s_pendingTargetFPS.store(targetFPS, std::memory_order_relaxed);
        s_pendingRenderScale.store(renderScale, std::memory_order_relaxed);
    }
}

extern "C" void tarzan_experiments_show() {
    if ([NSThread isMainThread]) showWindowOnMain();
    else dispatch_async(dispatch_get_main_queue(), ^{ showWindowOnMain(); });
}

extern "C" int tarzan_experiments_high_fps() { return s_highFPS.load(std::memory_order_relaxed) ? 1 : 0; }
extern "C" int tarzan_experiments_target_fps() { return s_targetFPS.load(std::memory_order_relaxed); }
extern "C" int tarzan_experiments_hd_widescreen() { return s_hdWidescreen.load(std::memory_order_relaxed) ? 1 : 0; }
extern "C" int tarzan_experiments_render_scale() { return s_renderScale.load(std::memory_order_relaxed); }
extern "C" int tarzan_experiments_sharper_image() { return s_sharperImage.load(std::memory_order_relaxed) ? 1 : 0; }
extern "C" int tarzan_experiments_remember_language() { return s_rememberLanguage.load(std::memory_order_relaxed) ? 1 : 0; }
extern "C" int tarzan_experiments_saved_language() {
    if (!s_rememberLanguage.load(std::memory_order_relaxed)) return -1;
    const int language = s_savedLanguage.load(std::memory_order_relaxed);
    return language >= 0 && language <= 4 ? language : -1;
}
extern "C" int tarzan_experiments_skip_logos() { return s_skipLogos.load(std::memory_order_relaxed) ? 1 : 0; }
extern "C" int tarzan_experiments_extra_lives_99() { return s_extraLives99.load(std::memory_order_relaxed) ? 1 : 0; }
extern "C" int tarzan_experiments_unlimited_spears() { return s_unlimitedSpears.load(std::memory_order_relaxed) ? 1 : 0; }

extern "C" void tarzan_experiments_accept_language(uint32_t languageID) {
    if (languageID > 4u) return;
    s_savedLanguage.store(static_cast<int>(languageID), std::memory_order_relaxed);
    if (!s_rememberLanguage.load(std::memory_order_relaxed) || !s_userDirectory) return;
    @autoreleasepool {
        std::lock_guard<std::mutex> lock(s_settingsMutex);
        if (!s_userDirectory || s_userDirectory.length == 0) return;
        NSMutableDictionary* plist = [NSMutableDictionary dictionaryWithDictionary:(loadSettings(s_userDirectory) ?: @{})];
        id option = plist[@"rememberLanguage"];
        if (!isBoolean(option) || ![option boolValue]) return;
        plist[@"lastLanguage"] = @(languageID);
        (void)writeSettingsDictionary(plist);
    }
}

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

extern "C" void tarzan_experiments_set_render_scale(int renderScale) {
    if (renderScale < 1 || renderScale > 4) return;
    s_renderScale.store(renderScale, std::memory_order_relaxed);
    s_pendingRenderScale.store(renderScale, std::memory_order_relaxed);
}

extern "C" void tarzan_experiments_set_sharper_image(int enabled) {
    const bool value = enabled != 0;
    s_sharperImage.store(value, std::memory_order_relaxed);
    s_pendingSharperImage.store(value, std::memory_order_relaxed);
}
