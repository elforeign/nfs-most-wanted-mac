// SDR video and stereo AAC game-mix recording; actions are transient.
// The audio timeline estimates mixer playback, excluding receiver/display latency.
#pragma once
#ifdef __OBJC__
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#endif
#ifdef __cplusplus
extern "C" {
#endif
// Choice index shared with the F10 row; change this constant to change the default.
enum { CLIPREC_DEFAULT_RESOLUTION = 1 };
void cliprec_action(int unused);
void cliprec_open_folder(int unused);
void cliprec_status(char *buf, int size);
void cliprec_stop(void);
void cliprec_shutdown(void); // also registered with atexit on first start
// Headroom belongs to the captured frame, sampled before asynchronous presentation.
#ifdef __OBJC__
void cliprec_present(id<MTLCommandBuffer> __unsafe_unretained cb, id<MTLTexture> __unsafe_unretained src, id<CAMetalDrawable> __unsafe_unretained drawable, float headroom);
#endif
#ifdef __cplusplus
}
#endif
