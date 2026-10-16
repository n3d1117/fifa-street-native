#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <Vision/Vision.h>

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc != 2) {
      fprintf(stderr, "usage: ocr-window <image.png>\n");
      return 2;
    }

    NSString *path = [NSString stringWithUTF8String:argv[1]];
    NSURL *url = [NSURL fileURLWithPath:path];
    CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
    if (!source) {
      fprintf(stderr, "could not open image: %s\n", argv[1]);
      return 1;
    }
    CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, NULL);
    CFRelease(source);
    if (!image) {
      fprintf(stderr, "could not decode image: %s\n", argv[1]);
      return 1;
    }

    VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
    request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    request.usesLanguageCorrection = NO;
    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:image options:@{}];
    NSError *error = nil;
    BOOL ok = [handler performRequests:@[request] error:&error];
    CGImageRelease(image);
    if (!ok) {
      fprintf(stderr, "Vision OCR failed: %s\n", error.localizedDescription.UTF8String);
      return 1;
    }

    NSMutableArray *lines = [NSMutableArray array];
    for (VNRecognizedTextObservation *observation in request.results) {
      VNRecognizedText *candidate = [[observation topCandidates:1] firstObject];
      if (!candidate) continue;
      CGRect box = observation.boundingBox;
      [lines addObject:@{
        @"text": candidate.string,
        @"confidence": @(candidate.confidence),
        @"bbox": @{@"x": @(box.origin.x), @"y": @(box.origin.y),
                    @"width": @(box.size.width), @"height": @(box.size.height)}
      }];
    }

    NSData *json = [NSJSONSerialization dataWithJSONObject:lines options:NSJSONWritingPrettyPrinted error:&error];
    if (!json) {
      fprintf(stderr, "JSON encoding failed: %s\n", error.localizedDescription.UTF8String);
      return 1;
    }
    fwrite(json.bytes, 1, json.length, stdout);
    fputc('\n', stdout);
  }
  return 0;
}
