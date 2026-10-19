// SPDX-License-Identifier: GPL-3.0-or-later
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

#include "experimental-settings.hpp"
#include "settings.hpp"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <string>

@interface TarzanExperimentalSettingsActions : NSObject <NSWindowDelegate>
- (void)settingChanged:(NSButton*)sender;
- (void)renderScaleChanged:(NSPopUpButton*)sender;
- (void)saveSettings:(id)sender;
@end

namespace {
// Active settings: written once by tarzan_experiments_configure before any
// guest thread exists, then read-only (the saved language is the exception).
tarzan::Settings s_active;
std::atomic<int> s_savedLanguage{-1};
// What the Settings window edits; main thread only.
tarzan::Settings s_pending;
std::atomic<bool> s_configured{false};
std::mutex s_settingsMutex;
std::string s_userDirectory;
NSWindow* s_window;
NSButton* s_highFPSButton;
NSButton* s_hdWidescreenButton;
NSButton* s_sharperImageButton;
NSButton* s_rememberLanguageButton;
NSButton* s_skipLogosButton;
NSButton* s_extraLives99Button;
NSButton* s_unlimitedSpearsButton;
NSButton* s_unlockAllMissionsButton;
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
constexpr NSInteger kUnlockAllMissionsTag = 8;

void setStatus(NSString* value) {
    if (s_status) [s_status setStringValue:value ?: @""];
}

void refreshControls() {
    if (s_highFPSButton)
        [s_highFPSButton setState:s_pending.highFps ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_hdWidescreenButton)
        [s_hdWidescreenButton setState:s_pending.hdWidescreen ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_sharperImageButton)
        [s_sharperImageButton setState:s_pending.sharperImage ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_rememberLanguageButton)
        [s_rememberLanguageButton setState:s_pending.rememberLanguage ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_skipLogosButton)
        [s_skipLogosButton setState:s_pending.skipLogos ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_extraLives99Button)
        [s_extraLives99Button setState:s_pending.extraLives99 ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_unlimitedSpearsButton)
        [s_unlimitedSpearsButton setState:s_pending.unlimitedSpears ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_unlockAllMissionsButton)
        [s_unlockAllMissionsButton setState:s_pending.unlockAllMissions ? NSControlStateValueOn : NSControlStateValueOff];
    if (s_renderScalePopup)
        [s_renderScalePopup selectItemWithTag:s_pending.renderScale];
}

void updatePendingFromControl(NSButton* sender) {
    const bool enabled = [sender state] == NSControlStateValueOn;
    switch ([sender tag]) {
    case kHighFPSTag: s_pending.highFps = enabled; break;
    case kHDWidescreenTag: s_pending.hdWidescreen = enabled; break;
    case kSharperImageTag: s_pending.sharperImage = enabled; break;
    case kRememberLanguageTag: s_pending.rememberLanguage = enabled; break;
    case kSkipLogosTag: s_pending.skipLogos = enabled; break;
    case kExtraLives99Tag: s_pending.extraLives99 = enabled; break;
    case kUnlimitedSpearsTag: s_pending.unlimitedSpears = enabled; break;
    case kUnlockAllMissionsTag: s_pending.unlockAllMissions = enabled; break;
    }
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}

void updatePendingRenderScaleFromControl(NSPopUpButton* sender) {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected >= 1 && selected <= 4)
        s_pending.renderScale = static_cast<int>(selected);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}

bool saveSettings() {
    std::lock_guard<std::mutex> lock(s_settingsMutex);
    tarzan::Settings toSave = s_pending;
    toSave.lastLanguage = s_savedLanguage.load(std::memory_order_relaxed);
    return tarzan::save_settings_file(s_userDirectory, toSave);
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

    s_highFPSButton = [NSButton checkboxWithTitle:@"60 FPS (smoother, uses more CPU)" target:s_actions action:@selector(settingChanged:)];
    [s_highFPSButton setTag:kHighFPSTag];
    [s_highFPSButton setFrame:NSMakeRect(22, 398, 350, 24)];
    [content addSubview:s_highFPSButton];
    NSTextField* fpsDetail = [NSTextField labelWithString:@"Off keeps the original 25 FPS. Some scenes may not reach 60."];
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

    for (NSView* subview in [content subviews]) {
        NSRect frame = [subview frame];
        frame.origin.y += 54;
        [subview setFrame:frame];
    }

    s_unlockAllMissionsButton = [NSButton checkboxWithTitle:@"Unlock all missions" target:s_actions action:@selector(settingChanged:)];
    [s_unlockAllMissionsButton setTag:kUnlockAllMissionsTag];
    [s_unlockAllMissionsButton setFrame:NSMakeRect(22, 152, 546, 24)];
    [content addSubview:s_unlockAllMissionsButton];
    NSTextField* unlockDetail = [NSTextField labelWithString:@"Show and allow every mission on the map. Saved progress is not changed."];
    [unlockDetail setFrame:NSMakeRect(46, 132, 510, 20)];
    [[unlockDetail cell] setWraps:YES];
    [[unlockDetail cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [unlockDetail setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:unlockDetail];

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
    s_userDirectory = userDirectory ? userDirectory : "";
    tarzan::Settings settings = tarzan::load_settings_file(s_userDirectory);
    tarzan::apply_env_overrides(settings, [](const char* name) -> const char* { return std::getenv(name); });
    s_active = settings;
    s_pending = settings;
    std::fprintf(stderr, "[tarzan-settings] highFps=%d target=%d widescreen=%d scale=%d sharp=%d rememberLanguage=%d "
                         "lastLanguage=%d skipLogos=%d lives99=%d spears=%d unlockMissions=%d wallClock=%d\n",
                 settings.highFps, settings.targetFps, settings.hdWidescreen, settings.renderScale,
                 settings.sharperImage, settings.rememberLanguage, settings.lastLanguage, settings.skipLogos,
                 settings.extraLives99, settings.unlimitedSpears, settings.unlockAllMissions, settings.wallClock);
    s_savedLanguage.store(settings.lastLanguage, std::memory_order_relaxed);
}

extern "C" const char* tarzan_experiments_user_directory() {
    return s_userDirectory.empty() ? nullptr : s_userDirectory.c_str();
}

extern "C" void tarzan_experiments_show() {
    if ([NSThread isMainThread]) showWindowOnMain();
    else dispatch_async(dispatch_get_main_queue(), ^{ showWindowOnMain(); });
}

extern "C" int tarzan_experiments_high_fps() { return s_active.highFps ? 1 : 0; }
extern "C" int tarzan_experiments_target_fps() { return s_active.targetFps; }
extern "C" int tarzan_experiments_hd_widescreen() { return s_active.hdWidescreen ? 1 : 0; }
extern "C" int tarzan_experiments_render_scale() { return s_active.renderScale; }
extern "C" int tarzan_experiments_sharper_image() { return s_active.sharperImage ? 1 : 0; }
extern "C" int tarzan_experiments_wall_clock() { return s_active.wallClock ? 1 : 0; }
extern "C" int tarzan_experiments_remember_language() { return s_active.rememberLanguage ? 1 : 0; }
extern "C" int tarzan_experiments_saved_language() {
    if (!s_active.rememberLanguage) return -1;
    const int language = s_savedLanguage.load(std::memory_order_relaxed);
    return language >= 0 && language <= 4 ? language : -1;
}
extern "C" int tarzan_experiments_skip_logos() { return s_active.skipLogos ? 1 : 0; }
extern "C" int tarzan_experiments_extra_lives_99() { return s_active.extraLives99 ? 1 : 0; }
extern "C" int tarzan_experiments_unlimited_spears() { return s_active.unlimitedSpears ? 1 : 0; }
extern "C" int tarzan_experiments_unlock_all_missions() { return s_active.unlockAllMissions ? 1 : 0; }

extern "C" void tarzan_experiments_accept_language(uint32_t languageID) {
    if (languageID > 4u) return;
    s_savedLanguage.store(static_cast<int>(languageID), std::memory_order_relaxed);
    if (!s_active.rememberLanguage || s_userDirectory.empty()) return;
    std::lock_guard<std::mutex> lock(s_settingsMutex);
    (void)tarzan::save_last_language(s_userDirectory, static_cast<int>(languageID));
}
