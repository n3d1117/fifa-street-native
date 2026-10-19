#import <AppKit/AppKit.h>

#include "gamepad-input.hpp"

#include <cstdio>

int main()
{
    @autoreleasepool {
        NSApplication* app = [NSApplication sharedApplication];
        [app finishLaunching];
        if (!ps2x_gamepad_init()) {
            std::fprintf(stderr, "SDL gamepad initialization failed\n");
            return 1;
        }
        ps2x_gamepad_poll();
        Ps2xGamepadSnapshot snapshot{};
        ps2x_gamepad_read(&snapshot);
        std::printf("SDL gamepad subsystem initialized; controller %s\n",
                    snapshot.connected ? "connected" : "not connected");
        ps2x_gamepad_set_active(0);
        ps2x_gamepad_read(&snapshot);
        const bool cleared = snapshot.connected == 0;
        ps2x_gamepad_set_active(1);
        ps2x_gamepad_shutdown();
        if (!cleared) {
            std::fprintf(stderr, "focus-loss state clear failed\n");
            return 2;
        }
        std::puts("focus-loss clear and clean shutdown passed");
        return 0;
    }
}
