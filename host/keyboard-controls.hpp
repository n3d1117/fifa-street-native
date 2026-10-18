// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <cstdint>

// Stable action ids align with TarzanMetalKey in metal-host.hpp.
enum KeyboardControlsAction : uint32_t {
    KEYBOARD_ACTION_UP = 1, KEYBOARD_ACTION_DOWN, KEYBOARD_ACTION_LEFT,
    KEYBOARD_ACTION_RIGHT, KEYBOARD_ACTION_CROSS_PRIMARY,
    KEYBOARD_ACTION_CROSS_ALTERNATE, KEYBOARD_ACTION_CIRCLE_PRIMARY,
    KEYBOARD_ACTION_CIRCLE_ALTERNATE, KEYBOARD_ACTION_SQUARE_PRIMARY,
    KEYBOARD_ACTION_SQUARE_ALTERNATE, KEYBOARD_ACTION_TRIANGLE_PRIMARY,
    KEYBOARD_ACTION_TRIANGLE_ALTERNATE, KEYBOARD_ACTION_L1, KEYBOARD_ACTION_R1,
    KEYBOARD_ACTION_L2, KEYBOARD_ACTION_R2, KEYBOARD_ACTION_START,
    KEYBOARD_ACTION_SELECT
};

extern "C" {
// Loads <user_directory>/keyboard.plist when present. Missing/invalid files
// leave the built-in defaults active. This function does not write a file.
void keyboard_controls_configure(const char* user_directory);
// Returns the live physical macOS keyCode for an action, or 0xffff if invalid.
uint16_t keyboard_controls_keycode(uint32_t action);
// Opens the controls panel; safe to request from another thread (UI is marshalled to main).
void keyboard_controls_show();
// Explicitly saves current bindings atomically; returns 1 on success.
int keyboard_controls_save();
// Restores built-in bindings in memory only. The user must explicitly save.
void keyboard_controls_reset_defaults();
}
