#pragma once

#include <cstdint>

// SDL is touched only by init/poll/shutdown/set_active on the Cocoa main
// thread. Readers on the EE thread receive a coherent, lock-protected snapshot.
enum TarzanGamepadButton : uint32_t {
    TARZAN_PAD_CROSS = 0,
    TARZAN_PAD_CIRCLE,
    TARZAN_PAD_SQUARE,
    TARZAN_PAD_TRIANGLE,
    TARZAN_PAD_UP,
    TARZAN_PAD_DOWN,
    TARZAN_PAD_LEFT,
    TARZAN_PAD_RIGHT,
    TARZAN_PAD_L1,
    TARZAN_PAD_R1,
    TARZAN_PAD_L2,
    TARZAN_PAD_R2,
    TARZAN_PAD_START,
    TARZAN_PAD_SELECT,
    TARZAN_PAD_L3,
    TARZAN_PAD_R3,
    TARZAN_PAD_BUTTON_COUNT
};

struct TarzanGamepadSnapshot {
    uint8_t connected;
    uint8_t buttons[TARZAN_PAD_BUTTON_COUNT];
    int16_t axes[4]; // left X/Y, right X/Y; SDL signed range [-32768, 32767]
};

extern "C" {
int tarzan_gamepad_init(void);          // Cocoa main thread
void tarzan_gamepad_poll(void);         // Cocoa main thread, once per host poll
void tarzan_gamepad_set_active(int active); // clear state while app is unfocused
void tarzan_gamepad_shutdown(void);     // Cocoa main thread
void tarzan_gamepad_read(TarzanGamepadSnapshot* out_snapshot); // any thread
}
