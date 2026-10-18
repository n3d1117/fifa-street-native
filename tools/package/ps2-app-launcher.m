// SPDX-License-Identifier: GPL-3.0-or-later
#import <Foundation/Foundation.h>

#include <fcntl.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int logMessage(NSString* logPath, NSString* message) {
    const char* path = [logPath fileSystemRepresentation];
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0600);
    if (fd < 0) {
        fprintf(stderr, "%s\n", message.UTF8String ?: "Tarzan launcher failed");
        return -1;
    }
    dprintf(fd, "%s\n", message.UTF8String ?: "Tarzan launcher failed");
    close(fd);
    return 0;
}

int main(int argc, char* argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        NSFileManager* files = [NSFileManager defaultManager];
        NSString* support = [[NSHomeDirectory() stringByAppendingPathComponent:
            @"Library/Application Support/TarzanFreeridePS2"] stringByStandardizingPath];
        NSString* logs = [support stringByAppendingPathComponent:@"Logs"];
        NSError* error = nil;
        if (![files createDirectoryAtPath:logs withIntermediateDirectories:YES attributes:nil error:&error]) {
            fprintf(stderr, "Cannot create Tarzan support directory: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }

        NSString* logPath = [logs stringByAppendingPathComponent:@"native.log"];
        int logFd = open([logPath fileSystemRepresentation], O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (logFd < 0) {
            fprintf(stderr, "Cannot open Tarzan log: %s\n", [logPath fileSystemRepresentation]);
            return 1;
        }
        if (dup2(logFd, STDOUT_FILENO) < 0 || dup2(logFd, STDERR_FILENO) < 0) {
            close(logFd);
            return 1;
        }
        close(logFd);

        NSString* contents = [[[NSBundle mainBundle] bundleURL] URLByAppendingPathComponent:@"Contents"].path;
        NSString* runner = [contents stringByAppendingPathComponent:@"Frameworks/PS2Recomp/ps2EntryRunner"];
        NSString* game = [contents stringByAppendingPathComponent:@"Resources/game/SLES_503.50"];
        if (![files isExecutableFileAtPath:runner] || ![files isReadableFileAtPath:game]) {
            logMessage(logPath, [NSString stringWithFormat:@"Required app resource is missing (runner=%@, game=%@).", runner, game]);
            return 1;
        }

        const char* executable = [runner fileSystemRepresentation];
        const char* userDirectory = [support fileSystemRepresentation];
        const char* gamePath = [game fileSystemRepresentation];
        char* const childArgv[] = {
            (char*)executable,
            (char*)"--user-dir",
            (char*)userDirectory,
            (char*)gamePath,
            NULL
        };
        execv(executable, childArgv);
        logMessage(logPath, [NSString stringWithFormat:@"Unable to launch native runner: %s", strerror(errno)]);
        return 1;
    }
}
