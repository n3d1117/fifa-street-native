// SPDX-License-Identifier: GPL-3.0-or-later
// UIKit counterpart of metal-host.mm for iOS and tvOS. UIApplicationMain owns the
// main thread (ios-main.mm); the game runs on its own thread inside ps2x_app_run,
// so ps2x_metal_host_* calls arrive from that thread and marshal UI work to main.
// SDL (gamepad, audio) is initialised without SDL video or SDL_main.
#import <UIKit/UIKit.h>
#import <AVFoundation/AVFoundation.h>
#import <QuartzCore/CAMetalLayer.h>

#define SDL_MAIN_HANDLED
#include <SDL3/SDL_main.h>

#include "ios-host.h"
#include "metal-host.hpp"
#include "metal-gs-adapter.hpp"
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
#include <cstdio>
#include <cstring>
#include <string>
#include <unistd.h>

namespace {
UIView* s_view;
CAMetalLayer* s_layer;
RrvPcsx2GsBridgeSurface s_surface{};
std::atomic_bool s_should_close{false};
std::atomic_bool s_lifecycle_pending{false};
std::atomic<uint32_t> s_resize_width{};
std::atomic<uint32_t> s_resize_height{};
std::atomic<float> s_resize_scale{1.0F};
std::atomic<uint64_t> s_resize_generation{};
NSTimer* s_inputTimer;
id s_resignObserver;
id s_becomeObserver;

void on_main(dispatch_block_t block) {
    if ([NSThread isMainThread]) block();
    else dispatch_sync(dispatch_get_main_queue(), block);
}

void update_surface_size() {
    if (!s_view || !s_layer) return;
    UIScreen* screen = [[s_view window] screen] ?: [UIScreen mainScreen];
    const CGFloat scale = [screen nativeScale];
    [s_layer setContentsScale:scale];
    const CGSize bounds = [s_view bounds].size;
    const uint32_t width = std::max(1u, static_cast<uint32_t>(std::llround(bounds.width * scale)));
    const uint32_t height = std::max(1u, static_cast<uint32_t>(std::llround(bounds.height * scale)));
    [s_layer setDrawableSize:CGSizeMake(width, height)];
    s_surface.width_pixels = width;
    s_surface.height_pixels = height;
    s_surface.backing_scale = static_cast<float>(scale);
    s_resize_width.store(width, std::memory_order_relaxed);
    s_resize_height.store(height, std::memory_order_relaxed);
    s_resize_scale.store(static_cast<float>(scale), std::memory_order_relaxed);
    s_resize_generation.fetch_add(1, std::memory_order_release);
}
}

#include "metal-host-gs.hpp"

@interface Ps2xMetalView : UIView
@end
@implementation Ps2xMetalView
+ (Class)layerClass { return [CAMetalLayer class]; }
@end

@interface Ps2xRootController : UIViewController
@end
@implementation Ps2xRootController
- (void)loadView {
    Ps2xMetalView* view = [[Ps2xMetalView alloc] initWithFrame:[[UIScreen mainScreen] bounds]];
    [view setBackgroundColor:[UIColor blackColor]];
    [view setMultipleTouchEnabled:NO];
    CAMetalLayer* layer = (CAMetalLayer*)[view layer];
    [layer setPixelFormat:MTLPixelFormatBGRA8Unorm];
    [layer setFramebufferOnly:YES];
    [layer setBackgroundColor:[[UIColor blackColor] CGColor]];
    s_view = view;
    s_layer = layer;
    self.view = view;
}
- (void)viewDidLayoutSubviews {
    [super viewDidLayoutSubviews];
    update_surface_size();
}
- (BOOL)prefersStatusBarHidden { return YES; }
#if TARGET_OS_IOS
- (BOOL)prefersHomeIndicatorAutoHidden { return YES; }
- (UIInterfaceOrientationMask)supportedInterfaceOrientations { return UIInterfaceOrientationMaskLandscape; }
#endif
@end

UIViewController* ps2x_ios_root_controller(void) {
    return [Ps2xRootController new];
}

extern "C" void ps2x_show_error_alert(const char* message) {
    NSString* text = [NSString stringWithUTF8String:message ? message : "Unknown launch error"];
    dispatch_async(dispatch_get_main_queue(), ^{
        UIViewController* top = [[[UIApplication sharedApplication] keyWindow] rootViewController];
        if (!top) return;
        UIAlertController* alert = [UIAlertController alertControllerWithTitle:@"FIFA Street could not start"
            message:text preferredStyle:UIAlertControllerStyleAlert];
        [alert addAction:[UIAlertAction actionWithTitle:@"OK" style:UIAlertActionStyleDefault handler:nil]];
        [top presentViewController:alert animated:YES completion:nil];
    });
}

// Settings: no Settings window on touch/TV hosts; the file and debug overrides
// still apply (same logic as settings-window.mm).
namespace {
host::Settings s_active;
std::atomic<bool> s_configured{false};
std::string s_userDirectory;
}

namespace host {
void configure_settings(const char* userDirectory) {
    bool expected = false;
    if (!s_configured.compare_exchange_strong(expected, true, std::memory_order_acq_rel)) return;
    s_userDirectory = userDirectory ? userDirectory : "";
    Settings settings = load_settings_file(s_userDirectory);
    apply_env_overrides(settings, [](const char* name) -> const char* { return std::getenv(name); });
    s_active = settings;
    std::fprintf(stderr, "[settings] scale=%d anisotropy=%d sharp=%d uiUpscale=%d rumble=%d rumbleIntensity=%d wallClock=%d unlockAllVenues=%d\n",
                 settings.renderScale, settings.anisotropy, settings.sharperImage, settings.smoothUiTextures, settings.rumble, settings.rumbleIntensity, settings.wallClock, settings.unlockAllVenues);
}
const Settings& active_settings() { return s_active; }
const std::string& user_directory() { return s_userDirectory; }
void show_settings_window() {}
}  // namespace host

extern "C" int ps2x_metal_host_start(const char*, uint32_t, uint32_t) {
    if (!std::getenv("RRV_PCSX2_GS_PIPELINE_MANIFEST")) {
        NSString* manifest = [[[NSBundle mainBundle] bundlePath]
            stringByAppendingPathComponent:@"Frameworks/PCSX2GSBridge/resources/pipelines-fifa.txt"];
        if ([[NSFileManager defaultManager] fileExistsAtPath:manifest])
            setenv("RRV_PCSX2_GS_PIPELINE_MANIFEST", [manifest fileSystemRepresentation], 0);
    }
    @autoreleasepool {
        host::configure_settings(nullptr);
        const host::Settings& settings = host::active_settings();
        ps2x_gamepad_set_rumble_settings(settings.rumble, static_cast<uint8_t>(settings.rumbleIntensity));
        ps2x_diagnostic_input_configure(std::getenv("PS2X_DIAGNOSTIC_INPUT_FIFO"));
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

        // The app owns UIApplicationMain; tell SDL its entry point is handled.
        SDL_SetMainReady();

        NSError* sessionError = nil;
        AVAudioSession* session = [AVAudioSession sharedInstance];
        if (![session setCategory:AVAudioSessionCategoryPlayback error:&sessionError] ||
            ![session setActive:YES error:&sessionError])
            NSLog(@"AVAudioSession setup failed: %@", sessionError);

        __block int ok = 0;
        on_main(^{
            if (!s_view || !s_layer) return;
            [[s_view window] layoutIfNeeded];
            if (!ps2x_gamepad_init())
                NSLog(@"SDL3 gamepad initialization failed");
            s_resignObserver = [[NSNotificationCenter defaultCenter]
                addObserverForName:UIApplicationWillResignActiveNotification object:nil
                queue:[NSOperationQueue mainQueue]
                usingBlock:^(NSNotification*) { ps2x_gamepad_set_active(0); }];
            s_becomeObserver = [[NSNotificationCenter defaultCenter]
                addObserverForName:UIApplicationDidBecomeActiveNotification object:nil
                queue:[NSOperationQueue mainQueue]
                usingBlock:^(NSNotification*) { ps2x_gamepad_set_active(1); }];
            // SDL gamepad and diagnostic input are polled on main (as on macOS).
            s_inputTimer = [NSTimer scheduledTimerWithTimeInterval:1.0 / 240.0 repeats:YES
                block:^(NSTimer*) {
                    ps2x_diagnostic_input_poll();
                    ps2x_gamepad_poll();
                }];
            s_surface = {};
            s_surface.struct_size = sizeof(s_surface);
            s_surface.kind = RRV_PCSX2_GS_SURFACE_MACOS_VIEW_METAL_LAYER;
            s_surface.flags = RRV_PCSX2_GS_SURFACE_FLAG_CALLER_OWNS_HANDLES |
                              RRV_PCSX2_GS_SURFACE_FLAG_MAIN_THREAD_PREPARED;
            s_surface.native_view = (__bridge void*)s_view;
            s_surface.native_layer = (__bridge void*)s_layer;
            update_surface_size();
            char error[512]{};
            if (!rrv_pcsx2_gs_bridge_prepare_owner_surface(&s_surface, error, sizeof(error))) {
                NSLog(@"Metal GS surface preparation failed: %s", error);
                return;
            }
            ok = 1;
        });
        if (!ok) return 0;
        if (!ps2x_spu2_host_start()) {
            NSLog(@"Native SPU2 core initialization failed");
            return 0;
        }
        return 1;
    }
}
// UIKit runs on the real main thread. This is called from the game thread's host
// loop, which only needs pacing (macOS blocks up to 1/240 s waiting for events).
extern "C" void ps2x_metal_host_poll(void) { usleep(4166); }
extern "C" int ps2x_metal_host_should_close(void) { return s_should_close.load() ? 1 : 0; }
extern "C" void ps2x_metal_host_request_close(void) { s_should_close.store(true); }
extern "C" void ps2x_metal_host_stop(void) {
    @autoreleasepool {
        ps2x_spu2_host_stop();
        on_main(^{
            [s_inputTimer invalidate];
            s_inputTimer = nil;
            if (s_resignObserver) [[NSNotificationCenter defaultCenter] removeObserver:s_resignObserver];
            if (s_becomeObserver) [[NSNotificationCenter defaultCenter] removeObserver:s_becomeObserver];
            s_resignObserver = nil;
            s_becomeObserver = nil;
            ps2x_diagnostic_input_shutdown();
            ps2x_gamepad_shutdown();
        });
    }
}
extern "C" int ps2x_metal_key_down(uint32_t key) {
    return ps2x_diagnostic_input_key_down(key);
}
