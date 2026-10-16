#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>

#include <errno.h>
#include <stdbool.h>
#include <string.h>
#include <limits.h>
#include <stdlib.h>
#include <time.h>

static void usage(const char *program)
{
    fprintf(stderr, "Usage: %s --pid <positive-pid> --key-code <0..127> --hold-ms <1..3000>\n", program);
}

static bool parseBounded(const char *text, long minValue, long maxValue, long *result)
{
    if (!text || !*text)
        return false;
    errno = 0;
    char *end = NULL;
    const long value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value < minValue || value > maxValue)
        return false;
    *result = value;
    return true;
}

int main(int argc, const char *argv[])
{
    long pidValue = 0;
    long keyCodeValue = -1;
    long holdMsValue = 0;
    bool havePid = false;
    bool haveKeyCode = false;
    bool haveHoldMs = false;

    for (int i = 1; i < argc; ++i)
    {
        if (strcmp(argv[i], "--pid") == 0 && i + 1 < argc)
            havePid = parseBounded(argv[++i], 1, INT_MAX, &pidValue);
        else if (strcmp(argv[i], "--key-code") == 0 && i + 1 < argc)
            haveKeyCode = parseBounded(argv[++i], 0, 127, &keyCodeValue);
        else if (strcmp(argv[i], "--hold-ms") == 0 && i + 1 < argc)
            haveHoldMs = parseBounded(argv[++i], 1, 3000, &holdMsValue);
        else
        {
            usage(argv[0]);
            return 2;
        }
    }

    if (!havePid || !haveKeyCode || !haveHoldMs)
    {
        usage(argv[0]);
        return 2;
    }

    @autoreleasepool
    {
        if (!AXIsProcessTrusted())
        {
            fprintf(stderr,
                    "Accessibility permission is required to send keyboard events. Enable it for this terminal/app in System Settings > Privacy & Security > Accessibility, then retry.\n");
            return 3;
        }

        NSRunningApplication *target = [NSRunningApplication runningApplicationWithProcessIdentifier:(pid_t)pidValue];
        if (!target || target.terminated)
        {
            fprintf(stderr, "No running application found for PID %ld.\n", pidValue);
            return 4;
        }

        const BOOL activated = [target activateWithOptions:(NSApplicationActivateIgnoringOtherApps |
                                                            NSApplicationActivateAllWindows)];
        if (!activated)
        {
            fprintf(stderr, "Could not activate target PID %ld.\n", pidValue);
            return 5;
        }

        bool isFrontmost = false;
        for (int attempt = 0; attempt < 25; ++attempt)
        {
            NSRunningApplication *frontmost = NSWorkspace.sharedWorkspace.frontmostApplication;
            if (target.isActive && frontmost.processIdentifier == (pid_t)pidValue)
            {
                isFrontmost = true;
                break;
            }
            struct timespec pause = {0, 100000000};
            nanosleep(&pause, NULL);
        }
        if (!isFrontmost)
        {
            fprintf(stderr, "Target PID %ld did not become frontmost; no key was sent.\n", pidValue);
            return 6;
        }

        CGEventRef keyDown = CGEventCreateKeyboardEvent(NULL, (CGKeyCode)keyCodeValue, true);
        CGEventRef keyUp = CGEventCreateKeyboardEvent(NULL, (CGKeyCode)keyCodeValue, false);
        if (!keyDown || !keyUp)
        {
            if (keyDown) CFRelease(keyDown);
            if (keyUp) CFRelease(keyUp);
            fprintf(stderr, "Could not create keyboard events.\n");
            return 7;
        }

        CGEventPost(kCGHIDEventTap, keyDown);
        struct timespec hold = {(time_t)(holdMsValue / 1000), (long)(holdMsValue % 1000) * 1000000L};
        nanosleep(&hold, NULL);
        CGEventPost(kCGHIDEventTap, keyUp);
        CFRelease(keyDown);
        CFRelease(keyUp);
        return 0;
    }
}
