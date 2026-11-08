// SPDX-License-Identifier: GPL-3.0-or-later
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

#include "settings.hpp"

#include <atomic>
#include <string>

@interface HostSettingsActions : NSObject <NSWindowDelegate>
- (void)rumbleChanged:(NSButton*)sender;
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
NSButton* s_rumbleButton;
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
    [s_rumbleButton setState:s_pending.rumble ? NSControlStateValueOn : NSControlStateValueOff];
    [s_renderScalePopup selectItemWithTag:s_pending.renderScale];
    [s_anisotropyPopup selectItemWithTag:s_pending.anisotropy];
    [s_rumbleIntensityPopup selectItemWithTag:s_pending.rumbleIntensity];
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
    const CGFloat height = 500;
    s_window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, width, height)
        styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable)
        backing:NSBackingStoreBuffered defer:NO];
    [s_window setTitle:@"Settings"];
    [s_window setReleasedWhenClosed:NO];
    [s_window setDelegate:s_actions];
    [s_window center];
    NSView* content = [s_window contentView];

    [content addSubview:sectionLabel(@"Video", 454)];
    NSTextField* modeLabel = [NSTextField labelWithString:@"Video mode"];
    [modeLabel setFrame:NSMakeRect(22, 422, 210, 22)];
    [content addSubview:modeLabel];
    s_videoModePopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 418, 248, 28) pullsDown:NO];
    [s_videoModePopup addItemWithTitle:@"NTSC 60 Hz (default)"];
    [[s_videoModePopup lastItem] setTag:static_cast<NSInteger>(host::VideoMode::NTSC)];
    [s_videoModePopup addItemWithTitle:@"PAL 50 Hz (original)"];
    [[s_videoModePopup lastItem] setTag:static_cast<NSInteger>(host::VideoMode::PAL)];
    [s_videoModePopup setTarget:s_actions];
    [s_videoModePopup setAction:@selector(videoModeChanged:)];
    [content addSubview:s_videoModePopup];
    [content addSubview:detailLabel(@"NTSC uses the game's own 60 Hz mode. Applies next launch.",
                                    NSMakeRect(22, 394, width - 44, 20))];

    NSTextField* aspectLabel = [NSTextField labelWithString:@"Display aspect"];
    [aspectLabel setFrame:NSMakeRect(22, 358, 210, 22)];
    [content addSubview:aspectLabel];
    s_displayAspectPopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 354, 248, 28) pullsDown:NO];
    const char* aspectTitles[] = {"Auto (GS mode)", "4:3", "16:9 (default)"};
    for (NSInteger aspect = 0; aspect < 3; ++aspect) {
        [s_displayAspectPopup addItemWithTitle:[NSString stringWithUTF8String:aspectTitles[aspect]]];
        [[s_displayAspectPopup lastItem] setTag:aspect];
    }
    [s_displayAspectPopup setTarget:s_actions];
    [s_displayAspectPopup setAction:@selector(displayAspectChanged:)];
    [content addSubview:s_displayAspectPopup];

    [content addSubview:sectionLabel(@"Rendering", 310)];
    NSTextField* scaleLabel = [NSTextField labelWithString:@"Internal render scale"];
    [scaleLabel setFrame:NSMakeRect(22, 278, 210, 22)];
    [content addSubview:scaleLabel];
    s_renderScalePopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 274, 248, 28) pullsDown:NO];
    const char* scaleTitles[] = {"Native (1×)", "2×", "3×", "4×", "5×", "6×"};
    for (int scale = 1; scale <= 6; ++scale) {
        [s_renderScalePopup addItemWithTitle:[NSString stringWithUTF8String:scaleTitles[scale - 1]]];
        [[s_renderScalePopup lastItem] setTag:scale];
    }
    [s_renderScalePopup setTarget:s_actions];
    [s_renderScalePopup setAction:@selector(renderScaleChanged:)];
    [content addSubview:s_renderScalePopup];
    [content addSubview:detailLabel(@"Resolution changes apply after restarting the game.",
                                    NSMakeRect(22, 250, width - 44, 20))];

    NSTextField* anisoLabel = [NSTextField labelWithString:@"Anisotropic filtering"];
    [anisoLabel setFrame:NSMakeRect(22, 218, 210, 22)];
    [content addSubview:anisoLabel];
    s_anisotropyPopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 214, 180, 28) pullsDown:NO];
    const char* anisoTitles[] = {"Off", "2x", "4x", "8x", "16x"};
    const NSInteger anisoValues[] = {0, 2, 4, 8, 16};
    for (NSInteger index = 0; index < 5; ++index) {
        [s_anisotropyPopup addItemWithTitle:[NSString stringWithUTF8String:anisoTitles[index]]];
        [[s_anisotropyPopup lastItem] setTag:anisoValues[index]];
    }
    [s_anisotropyPopup setTarget:s_actions];
    [s_anisotropyPopup setAction:@selector(anisotropyChanged:)];
    [content addSubview:s_anisotropyPopup];

    [content addSubview:sectionLabel(@"Controller", 186)];
    s_rumbleButton = [NSButton checkboxWithTitle:@"Controller rumble" target:s_actions
                                            action:@selector(rumbleChanged:)];
    [s_rumbleButton setFrame:NSMakeRect(22, 152, 200, 24)];
    [content addSubview:s_rumbleButton];
    NSTextField* rumbleLabel = [NSTextField labelWithString:@"Rumble intensity"];
    [rumbleLabel setFrame:NSMakeRect(250, 152, 140, 22)];
    [content addSubview:rumbleLabel];
    s_rumbleIntensityPopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(375, 148, 123, 28) pullsDown:NO];
    for (NSInteger intensity : {25, 50, 75, 100}) {
        [s_rumbleIntensityPopup addItemWithTitle:[NSString stringWithFormat:@"%ld%%", (long)intensity]];
        [[s_rumbleIntensityPopup lastItem] setTag:intensity];
    }
    [s_rumbleIntensityPopup setTarget:s_actions];
    [s_rumbleIntensityPopup setAction:@selector(rumbleIntensityChanged:)];
    [content addSubview:s_rumbleIntensityPopup];
    [content addSubview:detailLabel(@"Rumble is off by default. Controller settings apply on next launch.",
                                    NSMakeRect(22, 124, width - 44, 20))];

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
- (void)rumbleChanged:(NSButton*)sender {
    s_pending.rumble = [sender state] == NSControlStateValueOn;
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
    s_active = settings;
    s_pending = settings;
}

const Settings& active_settings() { return s_active; }

const std::string& user_directory() { return s_userDirectory; }

void show_settings_window() {
    if ([NSThread isMainThread]) showWindowOnMain();
    else dispatch_async(dispatch_get_main_queue(), ^{ showWindowOnMain(); });
}

}  // namespace host
