#include "gamepad-input.hpp"

#include <SDL3/SDL.h>

#include <algorithm>
#include <mutex>

namespace {
std::mutex s_snapshot_mutex;
Ps2xGamepadSnapshot s_snapshot{};
SDL_Gamepad* s_gamepad = nullptr;
bool s_initialized = false;
bool s_active = true;

void publish_empty(bool connected = false)
{
    std::lock_guard<std::mutex> lock(s_snapshot_mutex);
    s_snapshot = {};
    s_snapshot.connected = connected ? 1 : 0;
}

void close_gamepad()
{
    if (s_gamepad) {
        SDL_CloseGamepad(s_gamepad);
        s_gamepad = nullptr;
    }
}

void open_first_gamepad()
{
    if (s_gamepad || !s_initialized || !s_active)
        return;

    int count = 0;
    SDL_JoystickID* ids = SDL_GetGamepads(&count);
    if (!ids)
        return;

    for (int i = 0; i < count; ++i) {
        SDL_Gamepad* candidate = SDL_OpenGamepad(ids[i]);
        if (candidate) {
            s_gamepad = candidate;
            break;
        }
    }
    SDL_free(ids);
}

void discard_input_events(Uint32 first, Uint32 last)
{
    // The native host is not an SDL event-loop client. Keep just these input
    // queues bounded without consuming SDL events from unrelated categories.
    constexpr int kMaxDrainPerRange = 256;
    SDL_Event events[kMaxDrainPerRange];
    (void)SDL_PeepEvents(events, kMaxDrainPerRange, SDL_GETEVENT, first, last);
}

bool button(SDL_GamepadButton b)
{
    return s_gamepad && SDL_GetGamepadButton(s_gamepad, b);
}

int16_t axis(SDL_GamepadAxis a)
{
    return s_gamepad ? SDL_GetGamepadAxis(s_gamepad, a) : 0;
}
} // namespace

extern "C" int ps2x_gamepad_init(void)
{
    if (s_initialized)
        return 1;
    if (!SDL_InitSubSystem(SDL_INIT_GAMEPAD)) {
        publish_empty();
        return 0;
    }
    s_initialized = true;
    s_active = true;
    SDL_PumpEvents();
    open_first_gamepad();
    publish_empty(s_gamepad != nullptr);
    return 1;
}

extern "C" void ps2x_gamepad_poll(void)
{
    if (!s_initialized)
        return;

    SDL_PumpEvents();
    discard_input_events(SDL_EVENT_JOYSTICK_AXIS_MOTION,
                         SDL_EVENT_JOYSTICK_UPDATE_COMPLETE);
    discard_input_events(SDL_EVENT_GAMEPAD_AXIS_MOTION,
                         SDL_EVENT_GAMEPAD_STEAM_HANDLE_UPDATED);
    if (!s_active) {
        publish_empty();
        return;
    }
    if (s_gamepad && !SDL_GamepadConnected(s_gamepad)) {
        close_gamepad();
        publish_empty();
    }
    open_first_gamepad();

    Ps2xGamepadSnapshot next{};
    next.connected = s_gamepad ? 1 : 0;
    if (s_gamepad) {
        next.buttons[PS2X_PAD_CROSS] = button(SDL_GAMEPAD_BUTTON_SOUTH);
        next.buttons[PS2X_PAD_CIRCLE] = button(SDL_GAMEPAD_BUTTON_EAST);
        next.buttons[PS2X_PAD_SQUARE] = button(SDL_GAMEPAD_BUTTON_WEST);
        next.buttons[PS2X_PAD_TRIANGLE] = button(SDL_GAMEPAD_BUTTON_NORTH);
        next.buttons[PS2X_PAD_UP] = button(SDL_GAMEPAD_BUTTON_DPAD_UP);
        next.buttons[PS2X_PAD_DOWN] = button(SDL_GAMEPAD_BUTTON_DPAD_DOWN);
        next.buttons[PS2X_PAD_LEFT] = button(SDL_GAMEPAD_BUTTON_DPAD_LEFT);
        next.buttons[PS2X_PAD_RIGHT] = button(SDL_GAMEPAD_BUTTON_DPAD_RIGHT);
        next.buttons[PS2X_PAD_L1] = button(SDL_GAMEPAD_BUTTON_LEFT_SHOULDER);
        next.buttons[PS2X_PAD_R1] = button(SDL_GAMEPAD_BUTTON_RIGHT_SHOULDER);
        constexpr int kTriggerThreshold = 16000;
        next.buttons[PS2X_PAD_L2] = axis(SDL_GAMEPAD_AXIS_LEFT_TRIGGER) > kTriggerThreshold;
        next.buttons[PS2X_PAD_R2] = axis(SDL_GAMEPAD_AXIS_RIGHT_TRIGGER) > kTriggerThreshold;
        next.buttons[PS2X_PAD_START] = button(SDL_GAMEPAD_BUTTON_START);
        next.buttons[PS2X_PAD_SELECT] = button(SDL_GAMEPAD_BUTTON_BACK);
        next.buttons[PS2X_PAD_L3] = button(SDL_GAMEPAD_BUTTON_LEFT_STICK);
        next.buttons[PS2X_PAD_R3] = button(SDL_GAMEPAD_BUTTON_RIGHT_STICK);
        next.axes[0] = axis(SDL_GAMEPAD_AXIS_LEFTX);
        next.axes[1] = axis(SDL_GAMEPAD_AXIS_LEFTY);
        next.axes[2] = axis(SDL_GAMEPAD_AXIS_RIGHTX);
        next.axes[3] = axis(SDL_GAMEPAD_AXIS_RIGHTY);
    }
    std::lock_guard<std::mutex> lock(s_snapshot_mutex);
    s_snapshot = next;
}

extern "C" void ps2x_gamepad_set_active(int active)
{
    s_active = active != 0;
    if (!s_active)
        publish_empty();
    else if (s_initialized)
        open_first_gamepad();
}

extern "C" void ps2x_gamepad_shutdown(void)
{
    if (!s_initialized)
        return;
    close_gamepad();
    SDL_QuitSubSystem(SDL_INIT_GAMEPAD);
    s_initialized = false;
    publish_empty();
}

extern "C" void ps2x_gamepad_read(Ps2xGamepadSnapshot* out_snapshot)
{
    if (!out_snapshot)
        return;
    std::lock_guard<std::mutex> lock(s_snapshot_mutex);
    *out_snapshot = s_snapshot;
}
