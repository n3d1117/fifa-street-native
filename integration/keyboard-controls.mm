// SPDX-License-Identifier: GPL-3.0-or-later
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>

#include "keyboard-controls.hpp"

#include <array>
#include <mutex>

@interface KeyboardControlsActions : NSObject <NSWindowDelegate>
- (void)keyboardControlsCapture:(NSButton*)sender;
- (void)keyboardControlsReset:(id)sender;
- (void)keyboardControlsSave:(id)sender;
@end

namespace {
constexpr size_t kActionCount = 18;
constexpr uint16_t kNoKey = 0xffff;
constexpr std::array<uint16_t, kActionCount> kDefaults = {
    126, 125, 123, 124, // arrows
    7, 49, 8, 53, 6, 82, 9, 83, // Cross/Circle/Square/Triangle primary and alternate
    12, 14, 56, 60, 36, 48 // L1/R1/L2/R2/Start/Select
};
constexpr std::array<const char*, kActionCount> kLabels = {
    "Up", "Down", "Left", "Right", "Cross — primary", "Cross — alternate",
    "Circle — primary", "Circle — alternate", "Square — primary", "Square — alternate",
    "Triangle — primary", "Triangle — alternate", "L1", "R1", "L2", "R2", "Start", "Select"
};
std::mutex s_mutex;
std::array<uint16_t, kActionCount> s_bindings = kDefaults;
NSString* s_userDirectory;
NSWindow* s_window;
NSTextField* s_status;
std::array<NSButton*, kActionCount> s_buttons{};
uint32_t s_capturingAction{};
id s_keyMonitor;
KeyboardControlsActions* s_actions;

NSString* keyName(uint16_t code) {
    switch (code) {
    case 0: return @"A"; case 1: return @"S"; case 2: return @"D"; case 3: return @"F";
    case 4: return @"H"; case 5: return @"G"; case 6: return @"Z"; case 7: return @"X";
    case 8: return @"C"; case 9: return @"V"; case 11: return @"B"; case 12: return @"Q";
    case 13: return @"W"; case 14: return @"E"; case 15: return @"R"; case 16: return @"Y";
    case 17: return @"T"; case 18: return @"1"; case 19: return @"2"; case 20: return @"3";
    case 21: return @"4"; case 22: return @"6"; case 23: return @"5"; case 24: return @"=";
    case 25: return @"9"; case 26: return @"7"; case 27: return @"-"; case 28: return @"8";
    case 29: return @"0"; case 30: return @"]"; case 31: return @"O"; case 32: return @"U";
    case 33: return @"["; case 34: return @"I"; case 35: return @"P"; case 36: return @"Return";
    case 37: return @"L"; case 38: return @"J"; case 39: return @"'"; case 40: return @"K";
    case 41: return @";"; case 42: return @"\\"; case 43: return @","; case 44: return @"/";
    case 45: return @"N"; case 46: return @"M"; case 47: return @"."; case 48: return @"Tab";
    case 49: return @"Space"; case 50: return @"`"; case 51: return @"Delete"; case 53: return @"Escape";
    case 54: return @"Right Command"; case 55: return @"Command"; case 56: return @"Left Shift";
    case 57: return @"Caps Lock"; case 58: return @"Left Option"; case 59: return @"Left Control";
    case 60: return @"Right Shift"; case 61: return @"Right Option"; case 62: return @"Right Control";
    case 71: return @"Keypad Clear"; case 76: return @"Keypad Enter"; case 78: return @"Keypad -";
    case 81: return @"Keypad ="; case 82: return @"Keypad 0"; case 83: return @"Keypad 1";
    case 84: return @"Keypad 2"; case 85: return @"Keypad 3"; case 86: return @"Keypad 4";
    case 87: return @"Keypad 5"; case 88: return @"Keypad 6"; case 89: return @"Keypad 7";
    case 91: return @"Keypad 8"; case 92: return @"Keypad 9"; case 96: return @"F5";
    case 97: return @"F6"; case 98: return @"F7"; case 99: return @"F3"; case 100: return @"F8";
    case 101: return @"F9"; case 103: return @"F11"; case 105: return @"F13"; case 107: return @"F14";
    case 109: return @"F10"; case 111: return @"F12"; case 113: return @"F15"; case 114: return @"Help";
    case 115: return @"Home"; case 116: return @"Page Up"; case 117: return @"Forward Delete";
    case 118: return @"F4"; case 119: return @"End"; case 120: return @"F2"; case 121: return @"Page Down";
    case 122: return @"F1"; case 123: return @"Left Arrow"; case 124: return @"Right Arrow";
    case 125: return @"Down Arrow"; case 126: return @"Up Arrow";
    default: return [NSString stringWithFormat:@"Key %u", code];
    }
}

NSUInteger indexForAction(uint32_t action) { return action >= 1 && action <= kActionCount ? action - 1 : kActionCount; }

void setStatus(NSString* text) { if (s_status) [s_status setStringValue:text ?: @""]; }
void refreshButtons() {
    std::lock_guard<std::mutex> lock(s_mutex);
    for (size_t i = 0; i < kActionCount; ++i)
        [s_buttons[i] setTitle:keyName(s_bindings[i])];
}

bool loadBindings(NSString* directory) {
    if (!directory || [directory length] == 0) return false;
    NSString* path = [directory stringByAppendingPathComponent:@"keyboard.plist"];
    NSDictionary* plist = [NSDictionary dictionaryWithContentsOfFile:path];
    if (![plist isKindOfClass:[NSDictionary class]]) return false;
    if (![plist[@"version"] isKindOfClass:[NSNumber class]] || [plist[@"version"] unsignedIntegerValue] != 1)
        return false;
    NSDictionary* entries = plist[@"bindings"];
    if (![entries isKindOfClass:[NSDictionary class]]) return false;
    std::array<uint16_t, kActionCount> loaded{};
    for (size_t i = 0; i < kActionCount; ++i) {
        NSString* key = [NSString stringWithFormat:@"%zu", i + 1];
        id value = entries[key];
        if (![value isKindOfClass:[NSNumber class]]) return false;
        unsigned long long code = [value unsignedLongLongValue];
        if (code >= kNoKey) return false;
        loaded[i] = static_cast<uint16_t>(code);
    }
    {
        std::lock_guard<std::mutex> lock(s_mutex);
        s_bindings = loaded; // Duplicate physical keys are intentionally valid.
    }
    return true;
}

void finishCapture(uint16_t code) {
    const NSUInteger index = indexForAction(s_capturingAction);
    if (index >= kActionCount) return;
    {
        std::lock_guard<std::mutex> lock(s_mutex);
        s_bindings[index] = code;
    }
    s_capturingAction = 0;
    if (s_keyMonitor) {
        [NSEvent removeMonitor:s_keyMonitor];
        s_keyMonitor = nil;
    }
    refreshButtons();
    setStatus(@"Binding updated. Click Save to keep it.");
}

void startCapture(uint32_t action) {
    s_capturingAction = action;
    setStatus(@"Press any key to bind. Escape can also be assigned.");
    if (s_keyMonitor) [NSEvent removeMonitor:s_keyMonitor];
    s_keyMonitor = [NSEvent addLocalMonitorForEventsMatchingMask:(NSEventMaskKeyDown | NSEventMaskFlagsChanged)
        handler:^NSEvent*(NSEvent* event) {
            finishCapture(static_cast<uint16_t>([event keyCode]));
            return [event type] == NSEventTypeKeyDown ? nil : event;
        }];
}

void setDefaults() {
    {
        std::lock_guard<std::mutex> lock(s_mutex);
        s_bindings = kDefaults;
    }
    void (^updateUI)(void) = ^{
        refreshButtons();
        setStatus(@"Default bindings restored in memory. Click Save to keep them.");
    };
    if ([NSThread isMainThread]) updateUI();
    else dispatch_async(dispatch_get_main_queue(), updateUI);
}

void showPanelOnMain() {
    if (!s_actions) s_actions = [KeyboardControlsActions new];
    if (s_window) { [s_window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES]; return; }
    const CGFloat width = 440, rowHeight = 32, top = 64;
    const CGFloat height = top + rowHeight * kActionCount + 92;
    s_window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, width, height)
        styleMask:(NSWindowStyleMaskTitled | NSWindowStyleMaskClosable)
        backing:NSBackingStoreBuffered defer:NO];
    [s_window setTitle:@"Keyboard Controls"];
    [s_window setDelegate:s_actions];
    [s_window center];
    NSView* content = [s_window contentView];
    for (size_t i = 0; i < kActionCount; ++i) {
        CGFloat y = height - top - (i + 1) * rowHeight;
        NSTextField* label = [NSTextField labelWithString:[NSString stringWithUTF8String:kLabels[i]]];
        [label setFrame:NSMakeRect(20, y + 6, 230, 20)];
        [content addSubview:label];
        NSButton* field = [NSButton buttonWithTitle:@"" target:nil action:nil];
        [field setFrame:NSMakeRect(270, y + 2, 150, 26)];
        [field setBezelStyle:NSBezelStyleRounded];
        [field setTag:static_cast<NSInteger>(i + 1)];
        [field setTarget:s_actions];
        [field setAction:@selector(keyboardControlsCapture:)];
        s_buttons[i] = field;
        [content addSubview:field];
    }
    s_status = [NSTextField labelWithString:@"Click a binding to change it. Changes apply immediately."];
    [s_status setFrame:NSMakeRect(20, 48, 400, 20)];
    [content addSubview:s_status];
    NSButton* reset = [NSButton buttonWithTitle:@"Reset Defaults" target:nil action:nil];
    [reset setFrame:NSMakeRect(20, 12, 135, 28)];
    [reset setTarget:s_actions]; [reset setAction:@selector(keyboardControlsReset:)];
    [content addSubview:reset];
    NSButton* save = [NSButton buttonWithTitle:@"Save" target:nil action:nil];
    [save setFrame:NSMakeRect(width - 120, 12, 100, 28)];
    [save setTarget:s_actions]; [save setAction:@selector(keyboardControlsSave:)];
    [content addSubview:save];
    refreshButtons();
    [s_window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
}
}

@implementation KeyboardControlsActions
- (void)windowWillClose:(NSNotification*)notification {
    (void)notification;
    if (s_keyMonitor) { [NSEvent removeMonitor:s_keyMonitor]; s_keyMonitor = nil; }
    s_capturingAction = 0;
    s_window = nil;
    s_status = nil;
    s_buttons.fill(nil);
}
- (void)keyboardControlsCapture:(NSButton*)sender { startCapture(static_cast<uint32_t>([sender tag])); }
- (void)keyboardControlsReset:(id)sender { (void)sender; setDefaults(); }
- (void)keyboardControlsSave:(id)sender {
    (void)sender;
    setStatus(keyboard_controls_save() ? @"Bindings saved." : @"Could not save keyboard.plist.");
}
@end

extern "C" void keyboard_controls_configure(const char* userDirectory) {
    @autoreleasepool {
        s_userDirectory = userDirectory ? [[NSString stringWithUTF8String:userDirectory] copy] : nil;
        {
            std::lock_guard<std::mutex> lock(s_mutex);
            s_bindings = kDefaults;
        }
        if (s_userDirectory && !loadBindings(s_userDirectory) &&
            [[NSFileManager defaultManager] fileExistsAtPath:[s_userDirectory stringByAppendingPathComponent:@"keyboard.plist"]])
            NSLog(@"Ignoring invalid keyboard controls plist; defaults remain active.");
    }
}

extern "C" uint16_t keyboard_controls_keycode(uint32_t action) {
    const NSUInteger index = indexForAction(action);
    if (index >= kActionCount) return kNoKey;
    std::lock_guard<std::mutex> lock(s_mutex);
    return s_bindings[index];
}

extern "C" void keyboard_controls_show() {
    if ([NSThread isMainThread]) showPanelOnMain();
    else dispatch_async(dispatch_get_main_queue(), ^{ showPanelOnMain(); });
}

extern "C" void keyboard_controls_reset_defaults() { setDefaults(); }

extern "C" int keyboard_controls_save() {
    @autoreleasepool {
        if (!s_userDirectory || [s_userDirectory length] == 0) return 0;
        NSURL* destination = [NSURL fileURLWithPath:[s_userDirectory stringByAppendingPathComponent:@"keyboard.plist"]];
        NSMutableDictionary* entries = [NSMutableDictionary dictionaryWithCapacity:kActionCount];
        {
            std::lock_guard<std::mutex> lock(s_mutex);
            for (size_t i = 0; i < kActionCount; ++i)
                entries[[NSString stringWithFormat:@"%zu", i + 1]] = @(s_bindings[i]);
        }
        NSDictionary* root = @{ @"version": @1, @"bindings": entries };
        NSError* error = nil;
        NSData* data = [NSPropertyListSerialization dataWithPropertyList:root format:NSPropertyListXMLFormat_v1_0 options:0 error:&error];
        if (!data) { NSLog(@"Keyboard controls serialization failed: %@", error); return 0; }

        NSFileManager* fm = [NSFileManager defaultManager];
        NSString* directory = [destination.path stringByDeletingLastPathComponent];
        if (![fm createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:nil error:&error]) {
            NSLog(@"Keyboard controls directory creation failed: %@", error); return 0;
        }
        if ([fm fileExistsAtPath:destination.path]) {
            NSString* backup = [destination.path stringByAppendingString:@".bak"];
            NSData* previous = [NSData dataWithContentsOfURL:destination options:0 error:&error];
            if (!previous || ![previous writeToURL:[NSURL fileURLWithPath:backup]
                                               options:NSDataWritingAtomic error:&error]) {
                NSLog(@"Keyboard controls backup failed: %@", error); return 0;
            }
        }
        if (![data writeToURL:destination options:NSDataWritingAtomic error:&error]) {
            NSLog(@"Keyboard controls save failed: %@", error); return 0;
        }
        return 1;
    }
}
