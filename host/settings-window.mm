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
- (void)unlockAllVenuesChanged:(NSButton*)sender;
- (void)renderScaleChanged:(NSPopUpButton*)sender;
- (void)displayAspectChanged:(NSPopUpButton*)sender;
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
NSButton* s_unlockAllVenuesButton;
NSPopUpButton* s_renderScalePopup;
NSPopUpButton* s_displayAspectPopup;
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

void refreshControls() {
    [s_sharperImageButton setState:s_pending.sharperImage ? NSControlStateValueOn : NSControlStateValueOff];
    [s_renderScalePopup selectItemWithTag:s_pending.renderScale];
    [s_unlockAllVenuesButton setState:s_pending.unlockAllVenues ? NSControlStateValueOn : NSControlStateValueOff];
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
    const CGFloat height = 370;
    s_window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, width, height)
        styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable)
        backing:NSBackingStoreBuffered defer:NO];
    [s_window setTitle:@"Settings"];
    [s_window setReleasedWhenClosed:NO];
    [s_window setDelegate:s_actions];
    [s_window center];
    NSView* content = [s_window contentView];

    NSTextField* aspectLabel = [NSTextField labelWithString:@"Display aspect"];
    [aspectLabel setFrame:NSMakeRect(22, 324, 210, 22)];
    [content addSubview:aspectLabel];
    s_displayAspectPopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 320, 220, 28) pullsDown:NO];
    const char* aspectTitles[] = {"Auto (follow game option)", "4:3", "16:9"};
    for (NSInteger aspect = 0; aspect < 3; ++aspect) {
        [s_displayAspectPopup addItemWithTitle:[NSString stringWithUTF8String:aspectTitles[aspect]]];
        [[s_displayAspectPopup lastItem] setTag:aspect];
    }
    [s_displayAspectPopup setTarget:s_actions];
    [s_displayAspectPopup setAction:@selector(displayAspectChanged:)];
    [content addSubview:s_displayAspectPopup];

    NSTextField* scaleLabel = [NSTextField labelWithString:@"Internal render scale"];
    [scaleLabel setFrame:NSMakeRect(22, 274, 210, 22)];
    [content addSubview:scaleLabel];
    s_renderScalePopup = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(250, 270, 180, 28) pullsDown:NO];
    const char* scaleTitles[] = {"Native (1×)", "2×", "3×", "4×"};
    for (int scale = 1; scale <= 4; ++scale) {
        [s_renderScalePopup addItemWithTitle:[NSString stringWithUTF8String:scaleTitles[scale - 1]]];
        [[s_renderScalePopup lastItem] setTag:scale];
    }
    [s_renderScalePopup setTarget:s_actions];
    [s_renderScalePopup setAction:@selector(renderScaleChanged:)];
    [content addSubview:s_renderScalePopup];
    [content addSubview:detailLabel(@"Higher scales sharpen 3D geometry; textures and movies keep their original resolution.",
                                    NSMakeRect(22, 228, width - 44, 36))];

    s_sharperImageButton = [NSButton checkboxWithTitle:@"Sharper image" target:s_actions
                                                action:@selector(sharperImageChanged:)];
    [s_sharperImageButton setFrame:NSMakeRect(22, 192, width - 44, 24)];
    [content addSubview:s_sharperImageButton];
    [content addSubview:detailLabel(@"Contrast-adaptive sharpening and sharper display scaling.",
                                    NSMakeRect(46, 166, width - 68, 24))];

    s_unlockAllVenuesButton = [NSButton checkboxWithTitle:@"Cheat: unlock all venues" target:s_actions
                                                   action:@selector(unlockAllVenuesChanged:)];
    [s_unlockAllVenuesButton setFrame:NSMakeRect(22, 122, width - 44, 24)];
    [content addSubview:s_unlockAllVenuesButton];
    [content addSubview:detailLabel(@"The game's own venue cheat. Off plays the game as shipped.",
                                    NSMakeRect(46, 96, width - 68, 24))];

    s_status = [NSTextField labelWithString:@"Changes apply after restarting the game."];
    [s_status setFrame:NSMakeRect(22, 52, width - 44, 20)];
    [s_status setTextColor:[NSColor secondaryLabelColor]];
    [content addSubview:s_status];
    NSButton* save = [NSButton buttonWithTitle:@"Save" target:s_actions action:@selector(saveSettings:)];
    [save setFrame:NSMakeRect(width - 112, 12, 90, 30)];
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
- (void)unlockAllVenuesChanged:(NSButton*)sender {
    s_pending.unlockAllVenues = [sender state] == NSControlStateValueOn;
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)renderScaleChanged:(NSPopUpButton*)sender {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected >= 1 && selected <= 4) s_pending.renderScale = static_cast<int>(selected);
    setStatus(@"Unsaved changes. Save them, then restart the game to apply.");
}
- (void)displayAspectChanged:(NSPopUpButton*)sender {
    const NSInteger selected = [[sender selectedItem] tag];
    if (selected >= 0 && selected <= 2) s_pending.displayAspect = static_cast<host::DisplayAspect>(selected);
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
    std::fprintf(stderr, "[settings] scale=%d sharp=%d wallClock=%d unlockAllVenues=%d\n",
                 settings.renderScale, settings.sharperImage, settings.wallClock, settings.unlockAllVenues);
}

const Settings& active_settings() { return s_active; }

const std::string& user_directory() { return s_userDirectory; }

void show_settings_window() {
    if ([NSThread isMainThread]) showWindowOnMain();
    else dispatch_async(dispatch_get_main_queue(), ^{ showWindowOnMain(); });
}

}  // namespace host
