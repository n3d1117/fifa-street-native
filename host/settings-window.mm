// SPDX-License-Identifier: GPL-3.0-or-later
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

#include "settings.hpp"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <string>

@interface HostSettingsActions : NSObject <NSWindowDelegate>
- (void)sharperImageChanged:(NSButton*)sender;
- (void)rumbleChanged:(NSButton*)sender;
- (void)fxaaChanged:(NSButton*)sender;
- (void)smoothUiTexturesChanged:(NSButton*)sender;
- (void)unlockAllVenuesChanged:(NSButton*)sender;
- (void)renderScaleChanged:(NSPopUpButton*)sender;
- (void)anisotropyChanged:(NSPopUpButton*)sender;
- (void)rumbleIntensityChanged:(NSPopUpButton*)sender;
- (void)displayAspectChanged:(NSPopUpButton*)sender;
- (void)videoModeChanged:(NSPopUpButton*)sender;
- (void)saveSettings:(id)sender;
@end

namespace {
// Active settings: written once by configure_settings before any guest thread
// exists, then read-only.
host::Settings s_active;
// What the Settings window edits; main thread only.
host::Settings s_pending;
std::atomic<bool> s_configured{false};
std::string s_userDirectory;
NSWindow* s_window;
NSButton* s_sharperImageButton;
NSButton* s_rumbleButton;
NSButton* s_fxaaButton;
NSButton* s_smoothUiTexturesButton;
NSButton* s_unlockAllVenuesButton;
NSPopUpButton* s_renderScalePopup;
NSPopUpButton* s_anisotropyPopup;
NSPopUpButton* s_rumbleIntensityPopup;
NSPopUpButton* s_displayAspectPopup;
NSPopUpButton* s_videoModePopup;
NSTextField* s_status;
HostSettingsActions* s_actions;

void setStatus(NSString* value) {
    if (s_status) [s_status setStringValue:value ?: @""];
}

NSTextField* detailLabel(NSString* text, NSRect frame) {
    NSTextField* label = [NSTextField labelWithString:text];
    [label setFrame:frame];
    [label setMaximumNumberOfLines:2];
    [[label cell] setWraps:YES];
    [[label cell] setLineBreakMode:NSLineBreakByWordWrapping];
    [label setTextColor:[NSColor secondaryLabelColor]];
    return label;
}

NSTextField* sectionLabel(NSString* text, CGFloat y) {
    NSTextField* label = [NSTextField labelWithString:text];
    [label setFrame:NSMakeRect(22, y, 476, 22)];
    [label setFont:[NSFont systemFontOfSize:13 weight:NSFontWeightSemibold]];
    return label;
}

void refreshControls() {
    [s_sharperImageButton setState:s_pending.sharperImage ? NSControlStateValueOn : NSControlStateValueOff];
    [s_rumbleButton setState:s_pending.rumble ? NSControlStateValueOn : NSControlStateValueOff];
    [s_fxaaButton setState:s_pending.fxaa ? NSControlStateValueOn : NSControlStateValueOff];
    [s_smoothUiTexturesButton setState:s_pending.smoothUiTextures ? NSControlStateValueOn : NSControlStateValueOff];
    [s_renderScalePopup selectItemWithTag:s_pending.renderScale];
    [s_anisotropyPopup selectItemWithTag:s_pending.anisotropy];
    [s_rumbleIntensityPopup selectItemWithTag:s_pending.rumbleIntensity];
    [s_unlockAllVenuesButton setState:s_pending.unlockAllVenues ? NSControlStateValueOn : NSControlStateValueOff];
    [s_videoModePopup selectItemWithTag:static_cast<NSInteger>(s_pending.videoMode)];
    [s_displayAspectPopup selectItemWithTag:static_cast<NSInteger>(s_pending.displayAspect)];
}

void showWindowOnMain() {
    if (!s_actions) s_actions = [HostSettingsActions new];
    if (s_window) {
        refreshControls();
        [s_window makeKeyAndOrderFront:nil];
        [NSApp activateIgnoringOtherApps:YES];
        return;
    }
    const CGFloat width = 520;
    const CGFloat height = 720;
    s_window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, width, height)
        styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable)
        backing:NSBackingStoreBuffered defer:NO];
    [s_window setTitle:@"Settings"];
    [s_window setReleasedWhenClosed:NO];
    [s_window setDelegate:s_actions];
    [s_window center];
    NSView* content = [s_window contentView];

    [content addSubview:sectionLabel(@"Video", 674)];
    NSTextField* modeLabel = [NSTextField labelWithString:@"Video mode"];
    [modeLabel setFrame:NSMakeRect(22, 642, 210, 22)];
    [content addSubview:modeLabel];
    s_videoModePopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 638, 248, 28) pullsDown:NO];
    [s_videoModePopup addItemWithTitle:@"NTSC 60 Hz (default)"];
    [[s_videoModePopup lastItem] setTag:static_cast<NSInteger>(host::VideoMode::NTSC)];
    [s_videoModePopup addItemWithTitle:@"PAL 50 Hz (original)"];
    [[s_videoModePopup lastItem] setTag:static_cast<NSInteger>(host::VideoMode::PAL)];
    [s_videoModePopup setTarget:s_actions];
    [s_videoModePopup setAction:@selector(videoModeChanged:)];
    [content addSubview:s_videoModePopup];
    [content addSubview:detailLabel(@"NTSC uses the game's own 60 Hz mode. Applies next launch.",
                                    NSMakeRect(22, 614, width - 44, 20))];

    NSTextField* aspectLabel = [NSTextField labelWithString:@"Display aspect"];
    [aspectLabel setFrame:NSMakeRect(22, 578, 210, 22)];
    [content addSubview:aspectLabel];
    s_displayAspectPopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 574, 248, 28) pullsDown:NO];
    const char* aspectTitles[] = {"Auto (4:3; choose 16:9 for in-game widescreen)", "4:3", "16:9"};
    for (NSInteger aspect = 0; aspect < 3; ++aspect) {
        [s_displayAspectPopup addItemWithTitle:[NSString stringWithUTF8String:aspectTitles[aspect]]];
        [[s_displayAspectPopup lastItem] setTag:aspect];
    }
    [s_displayAspectPopup setTarget:s_actions];
    [s_displayAspectPopup setAction:@selector(displayAspectChanged:)];
    [content addSubview:s_displayAspectPopup];

    [content addSubview:sectionLabel(@"Enhancements", 530)];
    NSTextField* scaleLabel = [NSTextField labelWithString:@"Internal render scale"];
    [scaleLabel setFrame:NSMakeRect(22, 498, 210, 22)];
    [content addSubview:scaleLabel];
    s_renderScalePopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 494, 248, 28) pullsDown:NO];
    const char* scaleTitles[] = {"Native (1×)", "2×", "3×", "4×", "5× (3200×2560)", "6× (3840×3072)"};
    for (int scale = 1; scale <= 6; ++scale) {
        [s_renderScalePopup addItemWithTitle:[NSString stringWithUTF8String:scaleTitles[scale - 1]]];
        [[s_renderScalePopup lastItem] setTag:scale];
    }
    [s_renderScalePopup setTarget:s_actions];
    [s_renderScalePopup setAction:@selector(renderScaleChanged:)];
    [content addSubview:s_renderScalePopup];
    [content addSubview:detailLabel(@"Higher scales sharpen 3D. Original textures and movies. Next launch.",
                                    NSMakeRect(22, 470, width - 44, 20))];

    NSTextField* anisoLabel = [NSTextField labelWithString:@"Anisotropic filtering"];
    [anisoLabel setFrame:NSMakeRect(22, 438, 210, 22)];
    [content addSubview:anisoLabel];
    s_anisotropyPopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 434, 180, 28) pullsDown:NO];
    const char* anisoTitles[] = {"Off", "2x", "4x", "8x", "16x"};
    const NSInteger anisoValues[] = {0, 2, 4, 8, 16};
    for (NSInteger index = 0; index < 5; ++index) {
        [s_anisotropyPopup addItemWithTitle:[NSString stringWithUTF8String:anisoTitles[index]]];
        [[s_anisotropyPopup lastItem] setTag:anisoValues[index]];
    }
    [s_anisotropyPopup setTarget:s_actions];
    [s_anisotropyPopup setAction:@selector(anisotropyChanged:)];
    [content addSubview:s_anisotropyPopup];

    s_sharperImageButton = [NSButton checkboxWithTitle:@"Sharper image" target:s_actions
                                                action:@selector(sharperImageChanged:)];
    [s_sharperImageButton setFrame:NSMakeRect(22, 394, 220, 24)];
    [content addSubview:s_sharperImageButton];
    s_smoothUiTexturesButton = [NSButton checkboxWithTitle:@"Smooth UI textures (xBRZ)" target:s_actions
                                                     action:@selector(smoothUiTexturesChanged:)];
    [s_smoothUiTexturesButton setFrame:NSMakeRect(250, 394, width - 272, 24)];
    [content addSubview:s_smoothUiTexturesButton];
    [content addSubview:detailLabel(@"Contrast-adaptive sharpening and sharper display scaling.",
                                    NSMakeRect(46, 372, width - 68, 20))];

    s_fxaaButton = [NSButton checkboxWithTitle:@"Anti-aliasing (FXAA)" target:s_actions
                                           action:@selector(fxaaChanged:)];
    [s_fxaaButton setFrame:NSMakeRect(22, 340, width - 44, 24)];
    [content addSubview:s_fxaaButton];
    [content addSubview:detailLabel(@"Enhancement settings apply on next launch.",
                                    NSMakeRect(22, 318, width - 44, 20))];

    [content addSubview:sectionLabel(@"Controller", 278)];
    s_rumbleButton = [NSButton checkboxWithTitle:@"Controller rumble" target:s_actions
                                            action:@selector(rumbleChanged:)];
    [s_rumbleButton setFrame:NSMakeRect(22, 244, 200, 24)];
    [content addSubview:s_rumbleButton];
    NSTextField* rumbleLabel = [NSTextField labelWithString:@"Rumble intensity"];
    [rumbleLabel setFrame:NSMakeRect(250, 244, 140, 22)];
    [content addSubview:rumbleLabel];
    s_rumbleIntensityPopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(375, 240, 123, 28) pullsDown:NO];
    for (NSInteger intensity : {25, 50, 75, 100}) {
        [s_rumbleIntensityPopup addItemWithTitle:[NSString stringWithFormat:@"%ld%%", (long)intensity]];
        [[s_rumbleIntensityPopup lastItem] setTag:intensity];
    }
    [s_rumbleIntensityPopup setTarget:s_actions];
    [s_rumbleIntensityPopup setAction:@selector(rumbleIntensityChanged:)];
    [content addSubview:s_rumbleIntensityPopup];
    [content addSubview:detailLabel(@"Rumble is off by default. Controller settings apply on next launch.",
                                    NSMakeRect(22, 216, width - 44, 20))];

    [content addSubview:sectionLabel(@"Cheats", 174)];
    s_unlockAllVenuesButton = [NSButton checkboxWithTitle:@"Cheat: unlock all venues" target:s_actions
                                                   action:@selector(unlockAllVenuesChanged:)];
    [s_unlockAllVenuesButton setFrame:NSMakeRect(22, 140, width - 44, 24)];
    [content addSubview:s_unlockAllVenuesButton];
    [content addSubview:detailLabel(@"The game's own venue cheat. Off plays the game as shipped.",
                                    NSMakeRect(46, 116, width - 68, 20))];
    [content addSubview:detailLabel(@"Cheat settings apply on next launch.",
                                    NSMakeRect(22, 94, width - 44, 20))];

    s_status = [NSTextField labelWithString:@"Changes apply after restarting the game."];
    [s_status setFrame:NSMakeRect(22, 38, width - 144, 20)];
    [s_status setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:s_status];
    NSButton* save = [NSButton buttonWithTitle:@"Save" target:s_actions action:@selector(saveSettings:)];
    [save setFrame:NSMakeRect(width - 112, 30, 90, 30)];
    [save setKeyEquivalent:@"\r"];
    [content addSubview:save];
    refreshControls();
    [s_window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}
}  // namespace

@implementation HostSettingsActions
- (void)windowWillClose:(NSNotification*)notification {
    (void)notification;
    // Keep the window and its controls alive for reuse.
}
- (void)sharperImageChanged:(NSButton*)sender {
    s_pending.sharperImage = [sender state] == NSControlStateValueOn;
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)rumbleChanged:(NSButton*)sender {
    s_pending.rumble = [sender state] == NSControlStateValueOn;
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)fxaaChanged:(NSButton*)sender {
    s_pending.fxaa = [sender state] == NSControlStateValueOn;
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)smoothUiTexturesChanged:(NSButton*)sender {
    s_pending.smoothUiTextures = [sender state] == NSControlStateValueOn;
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)unlockAllVenuesChanged:(NSButton*)sender {
    s_pending.unlockAllVenues = [sender state] == NSControlStateValueOn;
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)renderScaleChanged:(NSPopUpButton*)sender {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected >= 1 && selected <= 6) s_pending.renderScale = static_cast<int>(selected);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)anisotropyChanged:(NSPopUpButton*)sender {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected == 0 || selected == 2 || selected == 4 || selected == 8 || selected == 16)
        s_pending.anisotropy = static_cast<int>(selected);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)rumbleIntensityChanged:(NSPopUpButton*)sender {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected >= 25 && selected <= 100 && selected % 25 == 0)
        s_pending.rumbleIntensity = static_cast<int>(selected);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)displayAspectChanged:(NSPopUpButton*)sender {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected >= 0 && selected <= 2) s_pending.displayAspect = static_cast<host::DisplayAspect>(selected);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)videoModeChanged:(NSPopUpButton*)sender {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected >= 0 && selected <= 1) s_pending.videoMode = static_cast<host::VideoMode>(selected);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)saveSettings:(id)sender {
    (void)sender;
    setStatus(host::save_settings_file(s_userDirectory, s_pending)
        ? @"Settings saved. Restart the game to apply them." : @"Could not save settings.plist.");
}
@end

namespace host {

void configure_settings(const char* userDirectory) {
    bool expected = false;
    if (!s_configured.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) return;
    s_userDirectory = userDirectory ? userDirectory : "";
    Settings settings = load_settings_file(s_userDirectory);
    apply_env_overrides(settings, [](const char* name) -> const char* { return std::getenv(name); });
    s_active = settings;
    s_pending = settings;
    std::fprintf(stderr, "[settings] videoMode=%s scale=%d anisotropy=%d sharp=%d uiUpscale=%d rumble=%d rumbleIntensity=%d wallClock=%d unlockAllVenues=%d\n",
                 settings.videoMode == VideoMode::NTSC ? "ntsc" : "pal", settings.renderScale, settings.anisotropy, settings.sharperImage, settings.smoothUiTextures, settings.rumble, settings.rumbleIntensity, settings.wallClock, settings.unlockAllVenues);
}

const Settings& active_settings() { return s_active; }

const std::string& user_directory() { return s_userDirectory; }

void show_settings_window() {
    if ([NSThread isMainThread]) showWindowOnMain();
    else dispatch_async(dispatch_get_main_queue(), ^{ showWindowOnMain(); });
}

}  // namespace host
