#import <Foundation/Foundation.h>
#import <AppKit/AppKit.h>
#import <Metal/Metal.h>

// Validate pinned redistributable shaders on the actual target GPU/OS before
// spending time compiling the GS bridge. No game data or window is required.
int main(int argc, const char **argv) {
  @autoreleasepool {
    if (argc != 2 && argc != 3) return 2;
    if (argc == 3) {
      if (!freopen(argv[2], "w", stdout)) return 2;
      dup2(fileno(stdout), STDERR_FILENO);
    }
    [NSApplication sharedApplication];
    [NSApp finishLaunching];
    id<MTLDevice> device = MTLCreateSystemDefaultDevice();
    if (!device) { fprintf(stderr, "No Metal device\n"); return 1; }
    printf("Metal device: %s\n", device.name.UTF8String);
    for (NSString *name in @[@"default.metallib", @"Metal22.metallib", @"Metal23.metallib"]) {
      NSString *path = [[NSString stringWithUTF8String:argv[1]] stringByAppendingPathComponent:name];
      NSError *error = nil;
      id<MTLLibrary> library = [device newLibraryWithFile:path error:&error];
      if (!library || !library.functionNames.count) {
        fprintf(stderr, "%s: %s\n", name.UTF8String, error.localizedDescription.UTF8String);
        return 1;
      }
      printf("%s: loaded %lu functions\n", name.UTF8String, (unsigned long)library.functionNames.count);
    }
  }
  return 0;
}
