#import <Cocoa/Cocoa.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ImageIO/ImageIO.h>
#import <Vision/Vision.h>
#include <unistd.h>
int main(int argc,const char **argv){ @autoreleasepool {
 if(argc!=4 && argc!=5)return 2; int pid=atoi(argv[1]);NSString *dir=[NSString stringWithUTF8String:argv[2]];int count=atoi(argv[3]);NSString *title=argc==5?[NSString stringWithUTF8String:argv[4]]:nil;
 [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
 NSArray *ws=CFBridgingRelease(CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly,kCGNullWindowID));CGWindowID wid=0;
 double largest=0;
 for(NSDictionary *w in ws)if([w[(id)kCGWindowOwnerPID] intValue]==pid && [w[(id)kCGWindowLayer] intValue]==0 && (!title || [w[(id)kCGWindowName] isEqualToString:title])){
  CGRect bounds=CGRectZero;
  if(CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)w[(id)kCGWindowBounds], &bounds)){
   double area=bounds.size.width*bounds.size.height;
   if(area>largest){largest=area;wid=[w[(id)kCGWindowNumber] unsignedIntValue];}
  }
 }
 if(!wid){fprintf(stderr,"No window for PID %d\n",pid);return 3;}
 for(int i=0;i<count;i++){@autoreleasepool {
 CGImageRef img=CGWindowListCreateImage(CGRectNull,kCGWindowListOptionIncludingWindow,wid,kCGWindowImageBoundsIgnoreFraming);
 if(!img)return 4;
 NSURL *url=[NSURL fileURLWithPath:[dir stringByAppendingPathComponent:[NSString stringWithFormat:@"window-%03d.png",i]]];
 CGImageDestinationRef out=CGImageDestinationCreateWithURL((__bridge CFURLRef)url,CFSTR("public.png"),1,NULL);
 CGImageDestinationAddImage(out,img,NULL);CGImageDestinationFinalize(out);CFRelease(out);
 {CGColorSpaceRef cs=CGColorSpaceCreateDeviceGray();uint8_t px[256];CGContextRef g=CGBitmapContextCreate(px,16,16,8,16,cs,kCGImageAlphaNone);CGContextSetInterpolationQuality(g,kCGInterpolationMedium);CGContextDrawImage(g,CGRectMake(0,0,16,16),img);double sum=0;for(int k=0;k<256;k++)sum+=px[k];printf("frame %d luma=%.4f\n",i,sum/256/255);CGContextRelease(g);CGColorSpaceRelease(cs);}
 {VNRecognizeTextRequest *request=[VNRecognizeTextRequest new];request.recognitionLevel=VNRequestTextRecognitionLevelAccurate;request.usesLanguageCorrection=NO;VNImageRequestHandler *handler=[[VNImageRequestHandler alloc] initWithCGImage:img options:@{}];NSError *error=nil;if([handler performRequests:@[request] error:&error])for(VNRecognizedTextObservation *observation in request.results){VNRecognizedText *candidate=[[observation topCandidates:1] firstObject];if(candidate){CGRect box=observation.boundingBox;printf("ocr=%.3f,%.3f:%.3f,%.3f:%s\n",box.origin.x,box.origin.y,box.size.width,box.size.height,candidate.string.UTF8String);}}}
 CGImageRelease(img);
 usleep(10000);
 }}printf("Captured %d window frames\n",count);
}return 0;}
