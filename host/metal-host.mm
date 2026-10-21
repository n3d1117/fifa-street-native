// SPDX-License-Identifier: GPL-3.0-or-later
#import <AppKit/AppKit.h>
#import <QuartzCore/CAMetalLayer.h>

#include "metal-host.hpp"
#include "metal-gs-adapter.hpp"
#include "keyboard-controls.hpp"
#include "settings.hpp"
#include "gamepad-input.hpp"
#include "diagnostic-input.hpp"
#include "spu2-host.hpp"

#include <memory>
#include <span>
#include <array>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdlib>
#include <cstring>

namespace {
NSWindow* s_window;
NSView* s_view;
NSView* s_container;
CAMetalLayer* s_layer;
RrvPcsx2GsBridgeSurface s_surface{};
std::atomic_bool s_should_close{false};
bool s_fullscreen{};
CGFloat s_game_aspect = 4.0 / 3.0;
thread_local std::unique_ptr<host::MetalGsAdapter> s_gs;
std::array<std::atomic_bool, 19> s_keys{};
std::atomic_bool s_lifecycle_pending{false};
std::atomic<uint32_t> s_resize_width{};
std::atomic<uint32_t> s_resize_height{};
std::atomic<float> s_resize_scale{1.0F};
std::atomic<uint64_t> s_resize_generation{};

void update_surface_size() {
    if (!s_window || !s_view || !s_layer) return;
    const CGFloat scale = [s_window backingScaleFactor];
    [s_layer setContentsScale:scale];
    const NSRect bounds = [s_view convertRectToBacking:[s_view bounds]];
    const uint32_t width = std::max(1u, static_cast<uint32_t>(std::llround(bounds.size.width)));
    const uint32_t height = std::max(1u, static_cast<uint32_t>(std::llround(bounds.size.height)));
    [s_layer setDrawableSize:CGSizeMake(width, height)];
    s_surface.width_pixels = width;
    s_surface.height_pixels = height;
    s_surface.backing_scale = static_cast<float>(scale);
    s_resize_width.store(width, std::memory_order_relaxed);
    s_resize_height.store(height, std::memory_order_relaxed);
    s_resize_scale.store(static_cast<float>(scale), std::memory_order_relaxed);
    s_resize_generation.fetch_add(1, std::memory_order_release);
}

void layout_game_view() {
    if (!s_container || !s_view) return;
    const NSRect bounds = [s_container bounds];
    if (!s_fullscreen) {
        [s_view setFrame:bounds];
    } else {
        CGFloat width = bounds.size.width;
        CGFloat height = width / s_game_aspect;
        if (height > bounds.size.height) {
            height = bounds.size.height;
            width = height * s_game_aspect;
        }
        [s_view setFrame:NSMakeRect(NSMidX(bounds) - width / 2.0,
                                    NSMidY(bounds) - height / 2.0, width, height)];
    }
    update_surface_size();
}

void apply_pending_surface_resize() {
    if (!s_gs) return;
    static thread_local uint64_t applied_generation = 0;
    const uint64_t generation = s_resize_generation.load(std::memory_order_acquire);
    if (generation == applied_generation) return;
    const uint32_t width = s_resize_width.load(std::memory_order_relaxed);
    const uint32_t height = s_resize_height.load(std::memory_order_relaxed);
    const float scale = s_resize_scale.load(std::memory_order_relaxed);
    try {
        s_gs->resize(width, height, scale);
        applied_generation = generation;
    } catch (const std::exception& e) {
        NSLog(@"Metal GS resize failed: %s", e.what());
    }
}

}

@interface HostWindowDelegate : NSObject <NSWindowDelegate, NSApplicationDelegate>
- (void)showKeyboardControls:(id)sender;
- (void)showSettings:(id)sender;
- (void)windowDidResignKey:(NSNotification*)notification;
- (void)windowDidBecomeKey:(NSNotification*)notification;
- (void)requestClose:(id)sender;
- (void)windowDidResize:(NSNotification*)notification;
- (void)windowDidChangeBackingProperties:(NSNotification*)notification;
- (void)windowDidEnterFullScreen:(NSNotification*)notification;
- (void)windowDidExitFullScreen:(NSNotification*)notification;
@end
@implementation HostWindowDelegate
// Dock/AppleScript/logout quit must take the same orderly path as the Quit menu:
// exit() under a running guest thread would destroy live mutexes.
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication*)sender {
    (void)sender; s_should_close.store(true); return NSTerminateCancel;
}
- (BOOL)windowShouldClose:(id)sender { (void)sender; s_should_close.store(true); return NO; }
- (void)showKeyboardControls:(id)sender {
    (void)sender;
    for (auto& key : s_keys) key.store(false, std::memory_order_relaxed);
    keyboard_controls_show();
}
- (void)showSettings:(id)sender {
    (void)sender;
    for (auto& key : s_keys) key.store(false, std::memory_order_relaxed);
    host::show_settings_window();
}
- (void)windowDidResignKey:(NSNotification*)notification {
    (void)notification;
    for (auto& key : s_keys) key.store(false, std::memory_order_relaxed);
    ps2x_gamepad_set_active(0);
}
- (void)windowDidBecomeKey:(NSNotification*)notification {
    (void)notification;
    ps2x_gamepad_set_active(1);
}
- (void)requestClose:(id)sender { (void)sender; s_should_close.store(true); }
- (void)windowDidResize:(NSNotification*)notification {
    (void)notification;
    layout_game_view();
}
- (void)windowDidChangeBackingProperties:(NSNotification*)notification {
    (void)notification;
    layout_game_view();
}
- (void)windowDidEnterFullScreen:(NSNotification*)notification {
    (void)notification;
    s_fullscreen = true;
    layout_game_view();
}
- (void)windowDidExitFullScreen:(NSNotification*)notification {
    (void)notification;
    s_fullscreen = false;
    layout_game_view();
}
@end
static HostWindowDelegate* s_delegate;
static id<NSObject> s_playbackActivity;
static id s_resignObserver;
static id s_becomeObserver;

extern "C" int ps2x_metal_host_start(const char* title, uint32_t width, uint32_t height) {
    if (!std::getenv("RRV_PCSX2_GS_PIPELINE_MANIFEST")) {
        NSString* manifest = [[[NSBundle mainBundle] bundlePath] stringByAppendingPathComponent:@"Contents/Frameworks/PCSX2GSBridge/resources/pipelines-fifa.txt"];
        if ([[NSFileManager defaultManager] fileExistsAtPath:manifest])
            setenv("RRV_PCSX2_GS_PIPELINE_MANIFEST", [manifest fileSystemRepresentation], 0);
    }
    @autoreleasepool {
        // The launcher normally configured settings already; this is a no-op then.
        host::configure_settings(nullptr);
        const host::Settings& settings = host::active_settings();
        ps2x_gamepad_set_rumble_settings(settings.rumble, static_cast<uint8_t>(settings.rumbleIntensity));
        s_game_aspect = settings.displayAspect == host::DisplayAspect::SixteenNine ? 16.0 / 9.0 : 4.0 / 3.0;
        keyboard_controls_configure(host::user_directory().empty() ? nullptr : host::user_directory().c_str());
        ps2x_diagnostic_input_configure(std::getenv("PS2X_DIAGNOSTIC_INPUT_FIFO"));
        // The GS bridge (a separate library) takes its options from RRV_* variables;
        // they are derived here from the Settings struct, never set by users.
        const char* aspect = settings.displayAspect == host::DisplayAspect::SixteenNine ? "16:9" :
            settings.displayAspect == host::DisplayAspect::FourThree ? "4:3" : "auto";
        setenv("RRV_PCSX2_GS_ASPECT", aspect, 1);
        if (settings.sharperImage) {
            setenv("RRV_PCSX2_GS_CAS", "50", 1);
            setenv("RRV_PCSX2_GS_PRESENT_FILTER", "sharp", 1);
        }
        setenv("RRV_PCSX2_GS_FXAA", settings.fxaa ? "1" : "0", 1);
        setenv("RRV_PCSX2_GS_UI_UPSCALE", settings.smoothUiTextures && settings.renderScale > 1 ? "1" : "0", 1);
        if (settings.renderScale > 1) {
            const char scaleValue[] = {static_cast<char>('0' + settings.renderScale), 0};
            setenv("RRV_PCSX2_GS_UPSCALE", scaleValue, 1);
        }
        if (settings.anisotropy > 0) {
            const std::string anisoValue = std::to_string(settings.anisotropy);
            setenv("RRV_PCSX2_GS_ANISO", anisoValue.c_str(), 1);
        }

        NSApplication* app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        [app finishLaunching];
        s_resignObserver = [[NSNotificationCenter defaultCenter]
            addObserverForName:NSApplicationDidResignActiveNotification object:app queue:[NSOperationQueue mainQueue]
            usingBlock:^(NSNotification*) {
                for (auto& key : s_keys) key.store(false, std::memory_order_relaxed);
                ps2x_gamepad_set_active(0);
            }];
        s_becomeObserver = [[NSNotificationCenter defaultCenter]
            addObserverForName:NSApplicationDidBecomeActiveNotification object:app queue:[NSOperationQueue mainQueue]
            usingBlock:^(NSNotification*) { ps2x_gamepad_set_active(1); }];
        s_window = [[NSWindow alloc] initWithContentRect:NSMakeRect(100, 100, width, height)
            styleMask:(NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable)
            backing:NSBackingStoreBuffered defer:NO];
        if (!s_window) return 0;
        if (!ps2x_gamepad_init())
            NSLog(@"SDL3 gamepad initialization failed; keyboard input remains available");
        if (!ps2x_spu2_host_start()) {
            NSLog(@"Native SPU2 core initialization failed");
            return 0;
        }
        // ARC owns the global reference. Keep close from releasing the window
        // behind that reference; stop() closes first, then clears it explicitly.
        [s_window setReleasedWhenClosed:NO];
        [s_window setTitle:[NSString stringWithUTF8String:title ? title : PS2X_APP_NAME]];
        [s_window setContentAspectRatio:NSMakeSize(s_game_aspect, 1.0)];
        [s_window setContentSize:NSMakeSize(std::max<CGFloat>(480.0, height * s_game_aspect),
                                             std::max<CGFloat>(360.0, height))];
        [s_window setContentMinSize:NSMakeSize(360.0 * s_game_aspect, 360.0)];
        [s_window setCollectionBehavior:[s_window collectionBehavior] | NSWindowCollectionBehaviorFullScreenPrimary];
        s_delegate = [HostWindowDelegate new];
        [s_window setDelegate:s_delegate];
        [app setDelegate:s_delegate];
        NSMenu* mainMenu = [NSMenu new];
        NSMenuItem* appItem = [NSMenuItem new];
        [mainMenu addItem:appItem];
        NSMenu* appMenu = [[NSMenu alloc] initWithTitle:@PS2X_APP_NAME];
        [appMenu addItem:[[NSMenuItem alloc] initWithTitle:@"Settings…"
            action:@selector(showSettings:) keyEquivalent:@","]];
        [appMenu addItem:[[NSMenuItem alloc] initWithTitle:@"Keyboard Controls…"
            action:@selector(showKeyboardControls:) keyEquivalent:@""]];
        [appMenu addItem:[NSMenuItem separatorItem]];
        [appMenu addItem:[[NSMenuItem alloc] initWithTitle:@"Quit " PS2X_APP_NAME
            action:@selector(requestClose:) keyEquivalent:@"q"]];
        [appItem setSubmenu:appMenu];
        NSMenuItem* viewItem = [[NSMenuItem alloc] initWithTitle:@"View" action:nil keyEquivalent:@""];
        [mainMenu addItem:viewItem];
        NSMenu* viewMenu = [[NSMenu alloc] initWithTitle:@"View"];
        NSMenuItem* fullscreenItem = [[NSMenuItem alloc] initWithTitle:@"Enter Full Screen"
            action:@selector(toggleFullScreen:) keyEquivalent:@"f"];
        [fullscreenItem setKeyEquivalentModifierMask:NSEventModifierFlagControl | NSEventModifierFlagCommand];
        [fullscreenItem setTarget:s_window];
        [viewMenu addItem:fullscreenItem];
        [viewItem setSubmenu:viewMenu];
        [NSApp setMainMenu:mainMenu];
        for (NSMenuItem* item in [appMenu itemArray])
            if ([item action] == @selector(showSettings:) || [item action] == @selector(showKeyboardControls:) || [item action] == @selector(requestClose:))
                [item setTarget:s_delegate];
        s_container = [[NSView alloc] initWithFrame:[[s_window contentView] bounds]];
        [s_container setWantsLayer:YES];
        [s_container setAutoresizingMask:NSViewWidthSizable | NSViewHeightSizable];
        [[s_container layer] setBackgroundColor:[[NSColor blackColor] CGColor]];
        s_view = [[NSView alloc] initWithFrame:[s_container bounds]];
        [s_view setAutoresizingMask:NSViewWidthSizable | NSViewHeightSizable];
        [s_view setWantsLayer:YES];
        s_layer = [CAMetalLayer layer];
        [s_layer setPixelFormat:MTLPixelFormatBGRA8Unorm];
        [s_layer setFramebufferOnly:YES];
        [s_layer setContentsScale:[s_window backingScaleFactor]];
        [s_layer setBackgroundColor:[[NSColor blackColor] CGColor]];
        [s_view setLayer:s_layer];
        [s_container addSubview:s_view];
        [s_window setContentView:s_container];
        [s_window makeKeyAndOrderFront:nil];
        [app activateIgnoringOtherApps:YES];
        s_surface = {};
        s_surface.struct_size = sizeof(s_surface);
        s_surface.kind = RRV_PCSX2_GS_SURFACE_MACOS_VIEW_METAL_LAYER;
        s_surface.flags = RRV_PCSX2_GS_SURFACE_FLAG_CALLER_OWNS_HANDLES |
                          RRV_PCSX2_GS_SURFACE_FLAG_MAIN_THREAD_PREPARED;
        s_surface.native_view = (__bridge void*)s_view;
        s_surface.native_layer = (__bridge void*)s_layer;
        layout_game_view();
        char error[512]{};
        if (!rrv_pcsx2_gs_bridge_prepare_owner_surface(&s_surface, error, sizeof(error))) {
            NSLog(@"Metal GS surface preparation failed: %s", error);
            return 0;
        }
        s_playbackActivity = [[NSProcessInfo processInfo]
                beginActivityWithOptions:NSActivityUserInitiated
                reason:@"real-time gameplay"];
        return 1;
    }
}
extern "C" void ps2x_metal_host_poll(void) {
    @autoreleasepool {
        NSEvent* event;
        while ((event = [NSApp nextEventMatchingMask:NSEventMaskAny untilDate:[NSDate dateWithTimeIntervalSinceNow:1.0/240.0]
            inMode:NSDefaultRunLoopMode dequeue:YES])) {
            // PID-targeted diagnostic key events can have no AppKit window.
            // A focused settings/remapping window must never control the game.
            if (([NSApp keyWindow] == nil || [NSApp keyWindow] == s_window) &&
                ([event type] == NSEventTypeKeyDown || [event type] == NSEventTypeKeyUp ||
                 [event type] == NSEventTypeFlagsChanged)) {
                bool down = [event type] == NSEventTypeKeyDown;
                if ([event type] == NSEventTypeFlagsChanged) {
                    const NSUInteger code = [event keyCode];
                    const NSEventModifierFlags flags = [event modifierFlags];
                    if (code == 56 || code == 60) down = (flags & NSEventModifierFlagShift) != 0;
                    else if (code == 54 || code == 55) down = (flags & NSEventModifierFlagCommand) != 0;
                    else if (code == 58 || code == 61) down = (flags & NSEventModifierFlagOption) != 0;
                    else if (code == 59 || code == 62) down = (flags & NSEventModifierFlagControl) != 0;
                    else if (code == 57) down = (flags & NSEventModifierFlagCapsLock) != 0;
                }
                for (uint32_t k=1; k<=18; ++k)
                    if (keyboard_controls_keycode(k) == [event keyCode]) s_keys[k].store(down, std::memory_order_relaxed);
            }
            [NSApp sendEvent:event];
        }
        ps2x_diagnostic_input_poll();
        ps2x_gamepad_poll();
        [NSApp updateWindows];
        if (s_lifecycle_pending.load(std::memory_order_acquire))
            (void)rrv_pcsx2_gs_bridge_pump_main_thread();
    }
}
extern "C" int ps2x_metal_host_should_close(void) { return s_should_close.load() ? 1 : 0; }
extern "C" void ps2x_metal_host_request_close(void) { s_should_close.store(true); }
extern "C" void ps2x_metal_host_stop(void) {
    @autoreleasepool {
        if (s_playbackActivity) {
            [[NSProcessInfo processInfo] endActivity:s_playbackActivity];
            s_playbackActivity = nil;
        }
        if (s_resignObserver) [[NSNotificationCenter defaultCenter] removeObserver:s_resignObserver];
        if (s_becomeObserver) [[NSNotificationCenter defaultCenter] removeObserver:s_becomeObserver];
        s_resignObserver=nil;
        s_becomeObserver=nil;
        ps2x_spu2_host_stop();
        ps2x_diagnostic_input_shutdown();
        ps2x_gamepad_shutdown();
        [s_window setDelegate:nil];
        [NSApp setDelegate:nil];
        [NSApp setMainMenu:nil];
        [s_window close]; s_window=nil; s_view=nil; s_layer=nil; s_delegate=nil;
    }
}
extern "C" int ps2x_metal_gs_create(void) {
    @autoreleasepool {
        if (!s_view || !s_layer || s_gs) return 0;
        host::MetalGsSurface surface{};
        surface.ns_view = (__bridge void*)s_view; surface.metal_layer = (__bridge void*)s_layer;
        surface.width_pixels = s_surface.width_pixels; surface.height_pixels = s_surface.height_pixels;
        surface.backing_scale = s_surface.backing_scale;
        s_lifecycle_pending.store(true, std::memory_order_release);
        try { s_gs = std::make_unique<host::MetalGsAdapter>(host::MetalGsAdapter::create(surface)); }
        catch (const std::exception& e) {
            s_lifecycle_pending.store(false, std::memory_order_release);
            NSLog(@"Metal GS create failed: %s", e.what()); return 0;
        }
        s_lifecycle_pending.store(false, std::memory_order_release);
        return 1;
    }
}
extern "C" void ps2x_metal_gs_destroy(void) {
    if (!s_gs) return;
    s_lifecycle_pending.store(true, std::memory_order_release);
    s_gs.reset();
    s_lifecycle_pending.store(false, std::memory_order_release);
}
extern "C" int ps2x_metal_gs_submit(uint32_t path, const uint8_t* bytes, uint32_t size) {
    if (!s_gs || (!bytes && size)) return 0;
    apply_pending_surface_resize();
    try { s_gs->submit(path, std::span<const uint8_t>(bytes, size)); return 1; }
    catch (const std::exception& e) { NSLog(@"Metal GIF submit failed: %s", e.what()); return 0; }
}
extern "C" int ps2x_metal_gs_reset(void) {
    if (!s_gs) return 0;
    apply_pending_surface_resize();
    try { s_gs->reset(); return 1; }
    catch (const std::exception& e) { NSLog(@"Metal GS reset failed: %s", e.what()); return 0; }
}
extern "C" int ps2x_metal_gs_write_csr(uint64_t authoritative_csr,
                                          uint32_t high_word_write_mask,
                                          int write_high_word) {
    if (!s_gs) return 0;
    apply_pending_surface_resize();
    try {
        s_gs->write_csr(authoritative_csr, high_word_write_mask, write_high_word != 0);
        return 1;
    } catch (const std::exception& e) {
        NSLog(@"Metal GS CSR synchronization failed: %s", e.what());
        return 0;
    }
}
extern "C" int ps2x_metal_gs_vsync(const uint64_t* regs, uint64_t tick, uint32_t parity) {
    if (!s_gs || !regs) return 0;
    apply_pending_surface_resize();
    std::array<uint64_t, RRV_PCSX2_GS_BRIDGE_PRIV_REG_COUNT> copy{};
    std::copy_n(regs, copy.size(), copy.begin());
    try { s_gs->vsync(copy, tick, parity); return 1; }
    catch (const std::exception& e) { NSLog(@"Metal GS vsync failed: %s", e.what()); return 0; }
}
extern "C" int ps2x_metal_gs_readback(uint64_t* csr, uint64_t* siglblid) {
    if (!s_gs || !csr || !siglblid) return 0;
    apply_pending_surface_resize();
    try { const auto r=s_gs->readback(); *csr=r.csr; *siglblid=r.siglblid; return 1; }
    catch (const std::exception& e) { NSLog(@"Metal GS readback failed: %s", e.what()); return 0; }
}
extern "C" uint32_t ps2x_metal_gs_read_fifo(uint8_t* dst, uint32_t byte_count) {
    if (!s_gs || !dst || byte_count == 0) return 0;
    return s_gs->read_fifo(std::span<uint8_t>(dst, byte_count));
}
extern "C" int ps2x_metal_gs_read_local_memory(uint8_t* dst, uint32_t byte_count,
                                                  uint64_t bitbltbuf, uint64_t trxpos,
                                                  uint64_t trxreg) {
    if (!s_gs || !dst || byte_count == 0) return 0;
    try {
        s_gs->read_local_memory(std::span<uint8_t>(dst, byte_count), bitbltbuf, trxpos, trxreg);
        return 1;
    } catch (const std::exception& e) {
        NSLog(@"Metal GS local-to-host read failed: %s", e.what());
        return 0;
    }
}
extern "C" int ps2x_metal_gs_capabilities(uint32_t* renderer, uint32_t* flags) {
    if (!s_gs || !renderer || !flags) return 0;
    *renderer = s_gs->capabilities().renderer_kind;
    *flags = s_gs->capabilities().capability_flags;
    return 1;
}
extern "C" int ps2x_metal_key_down(uint32_t key) {
    return (key < 19 && s_keys[key].load(std::memory_order_relaxed)) || ps2x_diagnostic_input_key_down(key);
}
