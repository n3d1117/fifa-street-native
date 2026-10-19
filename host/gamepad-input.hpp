#pragma once

#include <cstdint>

// SDL is touched only by init/poll/shutdown/set_active on the Cocoa main
// thread. Readers on the EE thread receive a coherent, lock-protected snapshot.
enum Ps2xGamepadButton : uint32_t {
    PS2X_PAD_CROSS = 0,
    PS2X_PAD_CIRCLE,
    PS2X_PAD_SQUARE,
    PS2X_PAD_TRIANGLE,
    PS2X_PAD_UP,
    PS2X_PAD_DOWN,
    PS2X_PAD_LEFT,
    PS2X_PAD_RIGHT,
    PS2X_PAD_L1,
    PS2X_PAD_R1,
    PS2X_PAD_L2,
    PS2X_PAD_R2,
    PS2X_PAD_START,
    PS2X_PAD_SELECT,
    PS2X_PAD_L3,
    PS2X_PAD_R3,
    PS2X_PAD_BUTTON_COUNT
};

struct Ps2xGamepadSnapshot {
    uint8_t connected;
    uint8_t buttons[PS2X_PAD_BUTTON_COUNT];
    int16_t axes[4]; // left X/Y, right X/Y; SDL signed range [-32768, 32767]
};

extern "C" {
int ps2x_gamepad_init(void);          // Cocoa main thread
void ps2x_gamepad_poll(void);         // Cocoa main thread, once per host poll
void ps2x_gamepad_set_active(int active); // clear state while app is unfocused
void ps2x_gamepad_shutdown(void);     // Cocoa main thread
void ps2x_gamepad_read(Ps2xGamepadSnapshot* out_snapshot); // any thread
}
