// SPDX-License-Identifier: GPL-3.0-or-later
// Process entry for iOS and tvOS. UIApplicationMain keeps the main thread; once
// the window exists the game runs on a 16 MB-stack thread inside ps2x_app_run.
// Bundle layout mirrors the macOS .app Resources: <App>.app/game/<boot ELF> with
// the disc data beside it. Saves and settings live in Application Support.
#import <UIKit/UIKit.h>

#include "app.hpp"
#include "ios-host.h"

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
        NSURL* documents = [[NSFileManager defaultManager] URLForDirectory:NSDocumentDirectory
            inDomain:NSUserDomainMask appropriateForURL:nil create:YES error:nil];
        game = std::filesystem::path([[documents path] fileSystemRepresentation]) / "game";
    }
    s_elfPath = (game / std::filesystem::path(PS2X_DEFAULT_BOOT_ELF).filename()).string();
    NSURL* support = [[NSFileManager defaultManager] URLForDirectory:NSApplicationSupportDirectory
        inDomain:NSUserDomainMask appropriateForURL:nil create:YES error:nil];
    NSString* dir = [[support path] stringByAppendingPathComponent:@PS2X_APP_EXECUTABLE];
    [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES
        attributes:nil error:nil];
    s_userDir = [dir fileSystemRepresentation];

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
