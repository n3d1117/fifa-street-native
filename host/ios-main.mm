// SPDX-License-Identifier: GPL-3.0-or-later
// Process entry for iOS and tvOS. UIApplicationMain keeps the main thread; once
// the window exists the game runs on a 16 MB-stack thread inside ps2x_app_run.
// Bundle layout mirrors the macOS .app Resources: <App>.app/game/<boot ELF> with
// the disc data beside it. Saves and settings live in the platform's
// writable container directory (see userBaseDirectory).
#import <UIKit/UIKit.h>

#include "app.hpp"
#include "ios-host.h"

extern "C" void ps2x_show_error_alert(const char* message);

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <pthread.h>

#ifndef PS2X_APP_EXECUTABLE
#define PS2X_APP_EXECUTABLE "FIFAStreetPS2"
#endif

namespace {
std::string s_elfPath;
std::string s_userDir;

// tvOS denies creating Documents and Application Support inside the app's own
// data container: both fail with NSFileWriteNoPermissionError (513, EPERM) even
// though the container path resolves. Library/Caches is writable there and the
// system may purge it. iOS uses Application Support.
NSString* userBaseDirectory() {
#if TARGET_OS_TV
    return NSSearchPathForDirectoriesInDomains(NSCachesDirectory, NSUserDomainMask, YES).firstObject;
#else
    return NSSearchPathForDirectoriesInDomains(NSApplicationSupportDirectory, NSUserDomainMask,
        YES).firstObject;
#endif
}

// Where game data sits when the bundle carries none (./rx device push-data).
NSString* pushedGameDirectory() {
#if TARGET_OS_TV
    return [userBaseDirectory() stringByAppendingPathComponent:@"game"];
#else
    return [NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject
        stringByAppendingPathComponent:@"game"];
#endif
}

void reportDirectoryFailure(NSString* path, NSError* error) {
    // A Foundation code alone cannot separate a sandbox denial from a POSIX one.
    NSError* underlying = error.userInfo[NSUnderlyingErrorKey];
    NSString* message = [NSString stringWithFormat:@"Cannot create %@: %@ (code %ld%@)", path,
        [error localizedDescription], (long)[error code],
        underlying ? [NSString stringWithFormat:@", underlying %ld", (long)[underlying code]] : @""];
    NSLog(@"[ios] user directory failed: %@", message);
    ps2x_show_error_alert([message UTF8String]);
}

void* gameThreadMain(void*) {
    Ps2xAppConfig config{s_elfPath.c_str(), s_userDir.c_str()};
    const int status = ps2x_app_run(&config);
    std::fflush(stdout);
    std::fflush(stderr);
    std::_Exit(status);
}

void startGameThread() {
    NSString* bundle = [[NSBundle mainBundle] bundlePath];
    std::filesystem::path game = std::filesystem::path([bundle fileSystemRepresentation]) / "game";
    if (!std::filesystem::is_directory(game)) {
        NSString* pushed = pushedGameDirectory();
        game = std::filesystem::path([pushed fileSystemRepresentation]);
    }
    s_elfPath = (game / std::filesystem::path(PS2X_DEFAULT_BOOT_ELF).filename()).string();
    NSString* base = userBaseDirectory();
    if (!base) {
        ps2x_show_error_alert("This device reports no writable application data directory.");
        return;
    }
    NSString* dir = [base stringByAppendingPathComponent:@PS2X_APP_EXECUTABLE];
    NSError* directoryError = nil;
    if (![[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES
        attributes:nil error:&directoryError]) {
        reportDirectoryFailure(dir, directoryError);
        return;
    }
    s_userDir = [dir fileSystemRepresentation];
    // The GS bridge's PCSX2 data root must be writable too; Application Support is
    // not on tvOS.
    setenv("PS2X_PCSX2_DATA_ROOT", [base fileSystemRepresentation], 1);

    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setstacksize(&attr, 16u * 1024u * 1024u);
    pthread_t thread;
    if (pthread_create(&thread, &attr, gameThreadMain, nullptr) == 0) pthread_detach(thread);
    else std::fprintf(stderr, "[ios] could not start the game thread\n");
    pthread_attr_destroy(&attr);
}
}

@interface Ps2xAppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow* window;
@end
@implementation Ps2xAppDelegate
#if TARGET_OS_IOS
- (UIInterfaceOrientationMask)application:(UIApplication*)application
    supportedInterfaceOrientationsForWindow:(UIWindow*)window {
    return UIInterfaceOrientationMaskLandscape;
}
#endif
- (BOOL)application:(UIApplication*)application didFinishLaunchingWithOptions:(NSDictionary*)options {
    [application setIdleTimerDisabled:YES];
    self.window = [[UIWindow alloc] initWithFrame:[[UIScreen mainScreen] bounds]];
    [self.window setBackgroundColor:[UIColor blackColor]];
    [self.window setRootViewController:ps2x_ios_root_controller()];
    [self.window makeKeyAndVisible];
    [self.window layoutIfNeeded];
    startGameThread();
    return YES;
}
@end

int main(int argc, char* argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass([Ps2xAppDelegate class]));
    }
}
