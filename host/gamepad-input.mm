#include "gamepad-input.hpp"

#include <SDL3/SDL.h>

#include <TargetConditionals.h>
#if defined(TARGET_OS_TV) && TARGET_OS_TV
#import <GameController/GameController.h>
#endif

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>

namespace {
std::mutex s_snapshot_mutex;
Ps2xGamepadSnapshot s_snapshot{};
SDL_Gamepad* s_gamepad = nullptr;
bool s_initialized = false;
bool s_active = true;
std::atomic<uint8_t> s_rumble_small{0};
std::atomic<uint8_t> s_rumble_large{0};
std::atomic<bool> s_rumble_enabled{false};
std::atomic<uint8_t> s_rumble_intensity{100};

void publish_empty(bool connected = false)
{
    std::lock_guard<std::mutex> lock(s_snapshot_mutex);
    s_snapshot = {};
    s_snapshot.connected = connected ? 1 : 0;
}

bool trace_input()
{
    static const bool enabled = [] {
        const char* value = std::getenv("PS2X_TRACE_INPUT");
        return value && value[0] == '1';
    }();
    return enabled;
}

void trace(const char* format, ...)
{
    if (!trace_input())
        return;
    std::va_list args;
    va_start(args, format);
    std::fprintf(stderr, "[input] ");
    std::vfprintf(stderr, format, args);
    std::fputc('\n', stderr);
    std::fflush(stderr);
    va_end(args);
}

// tvOS always reports its Siri Remote as a gamepad, and it enumerates before a
// paired controller, so a first-come choice would leave a real controller unused.
bool is_tv_remote(SDL_Gamepad* gamepad)
{
    const char* name = gamepad ? SDL_GetGamepadName(gamepad) : nullptr;
    if (!name)
        return false;
    return std::strstr(name, "Siri Remote") != nullptr || std::strstr(name, "Apple TV Remote") != nullptr;
}

// Opens a pad from the current device list. With allow_remote false only a real
// controller qualifies; every other candidate is closed again.
SDL_Gamepad* open_preferred_gamepad(bool allow_remote)
{
    int count = 0;
    SDL_JoystickID* ids = SDL_GetGamepads(&count);
    if (!ids)
        return nullptr;
    SDL_Gamepad* remote = nullptr;
    SDL_Gamepad* chosen = nullptr;
    for (int i = 0; i < count; ++i) {
        SDL_Gamepad* candidate = SDL_OpenGamepad(ids[i]);
        if (!candidate)
            continue;
        if (!is_tv_remote(candidate)) {
            chosen = candidate;
            trace("gamepad selected: \"%s\" (ps2 pad 1)",
                SDL_GetGamepadName(candidate) ? SDL_GetGamepadName(candidate) : "?");
            break;
        }
        if (allow_remote && !remote) {
            remote = candidate;
            continue;
        }
        SDL_CloseGamepad(candidate);
    }
    SDL_free(ids);
    if (chosen) {
        if (remote)
            SDL_CloseGamepad(remote);
        return chosen;
    }
    return remote;
}


#if defined(TARGET_OS_TV) && TARGET_OS_TV
// SDL's MFi driver only enumerates the Siri Remote on this tvOS version, so a
// paired PS5/DualSense pad is read straight from GameController. The remote
// keeps working through SDL when no extended gamepad is connected.
GCController* tv_controller()
{
    for (GCController* controller in [GCController controllers])
        if (controller.extendedGamepad)
            return controller;
    return nil;
}

int16_t axis_from_float(float value)
{
    const float clamped = std::clamp(value, -1.0F, 1.0F);
    return static_cast<int16_t>(clamped * 32767.0F);
}

void trace_tv_controllers(const char* when)
{
    if (!trace_input())
        return;
    NSArray<GCController*>* controllers = [GCController controllers];
    trace("%s: %lu GameController(s)", when, static_cast<unsigned long>([controllers count]));
    for (GCController* controller in controllers) {
        const char* vendor = controller.vendorName ? [controller.vendorName UTF8String] : "?";
        const char* category = controller.productCategory ? [controller.productCategory UTF8String] : "?";
        trace("  gc \"%s\" \"%s\" extended=%d micro=%d", vendor, category,
            controller.extendedGamepad != nil, controller.microGamepad != nil);
    }
}

// Fills the PS2 pad from an extended gamepad and reports whether one was present.
bool read_tv_controller(Ps2xGamepadSnapshot& next)
{
    GCController* controller = tv_controller();
    GCExtendedGamepad* pad = controller.extendedGamepad;
    if (!pad) {
        if (trace_input()) {
            static bool traced_none = false;
            if (!traced_none) {
                traced_none = true;
                trace_tv_controllers("poll (no extended gamepad)");
            }
        }
        return false;
    }
    if (trace_input()) {
        static const void* traced = nullptr;
        const void* current = (__bridge const void*)controller;
        if (current != traced) {
            traced = current;
            trace("controller in use: \"%s\" \"%s\"",
                controller.vendorName ? [controller.vendorName UTF8String] : "?",
                controller.productCategory ? [controller.productCategory UTF8String] : "?");
        }
    }
    next = {};
    next.connected = 1;
    next.buttons[PS2X_PAD_CROSS] = pad.buttonA.isPressed;
    next.buttons[PS2X_PAD_CIRCLE] = pad.buttonB.isPressed;
    next.buttons[PS2X_PAD_SQUARE] = pad.buttonX.isPressed;
    next.buttons[PS2X_PAD_TRIANGLE] = pad.buttonY.isPressed;
    next.buttons[PS2X_PAD_UP] = pad.dpad.up.isPressed;
    next.buttons[PS2X_PAD_DOWN] = pad.dpad.down.isPressed;
    next.buttons[PS2X_PAD_LEFT] = pad.dpad.left.isPressed;
    next.buttons[PS2X_PAD_RIGHT] = pad.dpad.right.isPressed;
    next.buttons[PS2X_PAD_L1] = pad.leftShoulder.isPressed;
    next.buttons[PS2X_PAD_R1] = pad.rightShoulder.isPressed;
    next.buttons[PS2X_PAD_L2] = pad.leftTrigger.isPressed;
    next.buttons[PS2X_PAD_R2] = pad.rightTrigger.isPressed;
    next.buttons[PS2X_PAD_START] = pad.buttonMenu.isPressed;
    next.buttons[PS2X_PAD_SELECT] = pad.buttonOptions.isPressed;
    next.buttons[PS2X_PAD_L3] = pad.leftThumbstickButton.isPressed;
    next.buttons[PS2X_PAD_R3] = pad.rightThumbstickButton.isPressed;
    // GameController reports "up" as positive; SDL and the pad state use negative.
    next.axes[0] = axis_from_float(pad.leftThumbstick.xAxis.value);
    next.axes[1] = axis_from_float(-pad.leftThumbstick.yAxis.value);
    next.axes[2] = axis_from_float(pad.rightThumbstick.xAxis.value);
    next.axes[3] = axis_from_float(-pad.rightThumbstick.yAxis.value);
    return true;
}
#endif

void close_gamepad()
{
    if (s_gamepad) {
        (void)SDL_RumbleGamepad(s_gamepad, 0, 0, 0);
        SDL_CloseGamepad(s_gamepad);
        s_gamepad = nullptr;
    }
}

void open_first_gamepad()
{
    if (s_gamepad || !s_initialized || !s_active)
        return;
    s_gamepad = open_preferred_gamepad(true);
    if (s_gamepad && is_tv_remote(s_gamepad))
        trace("gamepad selected: \"%s\" (television remote; no controller connected)",
            SDL_GetGamepadName(s_gamepad) ? SDL_GetGamepadName(s_gamepad) : "?");
}

// tvOS keeps its remote on the device list, so a controller connected or paired
// while the remote is the active pad has to take over.
void prefer_controller_over_remote()
{
    if (!s_gamepad || !s_initialized || !s_active || !is_tv_remote(s_gamepad))
        return;
    static int polls = 0;
    if ((++polls % 60) != 0)
        return;
    SDL_Gamepad* controller = open_preferred_gamepad(false);
    if (!controller)
        return;
    close_gamepad();
    s_gamepad = controller;
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
    if (trace_input()) {
        int count = 0;
        if (SDL_JoystickID* ids = SDL_GetGamepads(&count)) {
            for (int i = 0; i < count; ++i) {
                const char* name = SDL_GetGamepadNameForID(ids[i]);
                trace("device %d/%d: \"%s\"", i + 1, count, name ? name : "?");
            }
            SDL_free(ids);
        } else {
            trace("no gamepad devices");
        }
    }
#if defined(TARGET_OS_TV) && TARGET_OS_TV
    trace_tv_controllers("init");
#endif
    publish_empty(s_gamepad != nullptr);
    return 1;
}

void fill_sdl_snapshot(Ps2xGamepadSnapshot& next)
{
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
        if (s_gamepad) (void)SDL_RumbleGamepad(s_gamepad, 0, 0, 0);
        publish_empty();
        return;
    }
    if (s_gamepad && !SDL_GamepadConnected(s_gamepad)) {
        close_gamepad();
        publish_empty();
    }
    open_first_gamepad();
    prefer_controller_over_remote();

    {
        const uint8_t small = s_rumble_small.load(std::memory_order_relaxed);
        const uint8_t large = s_rumble_large.load(std::memory_order_relaxed);
        const uint8_t intensity = s_rumble_intensity.load(std::memory_order_relaxed);
        const bool enabled = s_rumble_enabled.load(std::memory_order_relaxed);
        const uint16_t low = enabled
            ? static_cast<uint16_t>(static_cast<unsigned>(large) * 257u * static_cast<unsigned>(intensity) / 100u)
            : 0u;
        const uint16_t high = enabled && small ? static_cast<uint16_t>(65535u * static_cast<unsigned>(intensity) / 100u) : 0u;
        if (s_gamepad) (void)SDL_RumbleGamepad(s_gamepad, low, high, 200);
        static const bool traceRumble = [] {
            const char* value = std::getenv("PS2X_TRACE_RUMBLE");
            return value && value[0] == '1';
        }();
        static int lastRumble = -1;
        const int currentRumble = (static_cast<int>(small) << 8) | static_cast<int>(large) |
            (enabled ? (1 << 16) : 0) | (s_gamepad ? (1 << 17) : 0);
        if (traceRumble && currentRumble != lastRumble)
            std::fprintf(stderr, "[rumble-host] small=%u large=%u low=%u high=%u intensity=%u%% device=%s\n", small, large,
                         static_cast<unsigned>(low), static_cast<unsigned>(high), intensity,
                         s_gamepad ? "connected" : "none");
        lastRumble = currentRumble;
    }

    Ps2xGamepadSnapshot next{};
#if defined(TARGET_OS_TV) && TARGET_OS_TV
    if (!read_tv_controller(next)) {
#endif
    fill_sdl_snapshot(next);
#if defined(TARGET_OS_TV) && TARGET_OS_TV
    }
#endif
    if (trace_input()) {
        static auto last = std::chrono::steady_clock::now() - std::chrono::seconds(1);
        const auto now = std::chrono::steady_clock::now();
        if (now - last >= std::chrono::seconds(1)) {
            last = now;
            unsigned bits = 0;
            for (const uint8_t pressed : next.buttons)
                bits = (bits << 1) | (pressed ? 1u : 0u);
            trace("connected=%u buttons=0x%08x axes=%d,%d,%d,%d", static_cast<unsigned>(next.connected), bits,
                next.axes[0], next.axes[1], next.axes[2], next.axes[3]);
        }
    }
    std::lock_guard<std::mutex> lock(s_snapshot_mutex);
    s_snapshot = next;
}

extern "C" void ps2x_gamepad_set_active(int active)
{
    s_active = active != 0;
    if (!s_active) {
        s_rumble_small.store(0, std::memory_order_relaxed);
        s_rumble_large.store(0, std::memory_order_relaxed);
        if (s_gamepad) (void)SDL_RumbleGamepad(s_gamepad, 0, 0, 0);
        publish_empty();
    } else if (s_initialized)
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

extern "C" void ps2x_gamepad_set_rumble(uint8_t small_on, uint8_t large)
{
    s_rumble_small.store(small_on ? 1u : 0u, std::memory_order_relaxed);
    s_rumble_large.store(large, std::memory_order_relaxed);
}

extern "C" void ps2x_gamepad_set_rumble_settings(int enabled, uint8_t intensity_percent)
{
    s_rumble_enabled.store(enabled != 0, std::memory_order_relaxed);
    s_rumble_intensity.store(std::clamp<uint8_t>(intensity_percent, 25u, 100u), std::memory_order_relaxed);
}
