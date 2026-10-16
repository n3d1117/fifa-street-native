// SPDX-License-Identifier: GPL-3.0-or-later
#import <Foundation/Foundation.h>

#include "keyboard-controls.hpp"

#include <cassert>
#include <cstdio>

int main() {
    @autoreleasepool {
        NSString* directory = [NSTemporaryDirectory() stringByAppendingPathComponent:
            [NSString stringWithFormat:@"keyboard-controls-%@", [[NSUUID UUID] UUIDString]]];
        NSString* plistPath = [directory stringByAppendingPathComponent:@"keyboard.plist"];
        assert([[NSFileManager defaultManager] createDirectoryAtPath:directory
            withIntermediateDirectories:YES attributes:nil error:nil]);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_UP) == 126);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CROSS_PRIMARY) == 7);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CROSS_ALTERNATE) == 49);
        assert(keyboard_controls_keycode(0) == 0xffff);

        keyboard_controls_configure(directory.UTF8String);
        assert(![[NSFileManager defaultManager] fileExistsAtPath:plistPath]);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CIRCLE_PRIMARY) == 8);

        NSMutableDictionary* bindings = [NSMutableDictionary dictionary];
        for (uint32_t action = 1; action <= 18; ++action)
            bindings[[NSString stringWithFormat:@"%u", action]] = @(keyboard_controls_keycode(action));
        // A duplicate physical key is valid: aliases may intentionally share keys.
        bindings[@"6"] = bindings[@"5"];
        NSDictionary* persisted = @{ @"version": @1, @"bindings": bindings };
        assert([persisted writeToFile:plistPath atomically:YES]);
        keyboard_controls_configure(directory.UTF8String);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CROSS_PRIMARY) == 7);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CROSS_ALTERNATE) == 7);

        keyboard_controls_reset_defaults();
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CROSS_ALTERNATE) == 49);
        assert(keyboard_controls_save() == 1);
        NSString* backup = [plistPath stringByAppendingString:@".bak"];
        assert([[NSFileManager defaultManager] fileExistsAtPath:backup]);
        keyboard_controls_configure(directory.UTF8String);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CROSS_PRIMARY) == 7);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CROSS_ALTERNATE) == 49);

        NSMutableDictionary* invalidBindings = [bindings mutableCopy];
        invalidBindings[@"5"] = @(70000);
        NSDictionary* invalid = @{ @"version": @1, @"bindings": invalidBindings };
        assert([invalid writeToFile:plistPath atomically:YES]);
        keyboard_controls_configure(directory.UTF8String);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CROSS_PRIMARY) == 7);
        assert(keyboard_controls_keycode(KEYBOARD_ACTION_CROSS_ALTERNATE) == 49);
        [[NSFileManager defaultManager] removeItemAtPath:directory error:nil];
    }
    std::puts("keyboard-controls tests passed");
    return 0;
}
