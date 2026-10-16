#import <Cocoa/Cocoa.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ImageIO/ImageIO.h>
#include <unistd.h>
int main(int argc,const char **argv){ @autoreleasepool {
 if(argc!=4)return 2; int pid=atoi(argv[1]);NSString *dir=[NSString stringWithUTF8String:argv[2]];int count=atoi(argv[3]);
 [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
 NSArray *ws=CFBridgingRelease(CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly,kCGNullWindowID));CGWindowID wid=0;
 double largest=0;
 for(NSDictionary *w in ws)if([w[(id)kCGWindowOwnerPID] intValue]==pid && [w[(id)kCGWindowLayer] intValue]==0){
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
 CGImageDestinationAddImage(out,img,NULL);CGImageDestinationFinalize(out);CFRelease(out);CGImageRelease(img);
 usleep(10000);
 }}printf("Captured %d window frames\n",count);
}return 0;}
