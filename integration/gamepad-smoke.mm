#import <AppKit/AppKit.h>

#include "gamepad-input.hpp"

#include <cstdio>

int main()
{
    @autoreleasepool {
        NSApplication* app = [NSApplication sharedApplication];
        [app finishLaunching];
        if (!tarzan_gamepad_init()) {
            std::fprintf(stderr, "SDL gamepad initialization failed\n");
            return 1;
        }
        tarzan_gamepad_poll();
        TarzanGamepadSnapshot snapshot{};
        tarzan_gamepad_read(&snapshot);
        std::printf("SDL gamepad subsystem initialized; controller %s\n",
                    snapshot.connected ? "connected" : "not connected");
        tarzan_gamepad_set_active(0);
        tarzan_gamepad_read(&snapshot);
        const bool cleared = snapshot.connected == 0;
        tarzan_gamepad_set_active(1);
        tarzan_gamepad_shutdown();
        if (!cleared) {
            std::fprintf(stderr, "focus-loss state clear failed\n");
            return 2;
        }
        std::puts("focus-loss clear and clean shutdown passed");
        return 0;
    }
}
