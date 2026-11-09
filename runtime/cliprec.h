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
int cliprec_live_start(void);
void cliprec_live_stop(void);
// 0 Off, 1 Starting, 2 Live, 3 Stopping, 4 terminal capture failure.
int cliprec_live_state(void);
void replay_arm_action(int unused);
void replay_save_action(int unused);
void replay_status(char *buf, int size);
void replay_save_status(char *buf, int size);
// Headroom belongs to the captured frame, sampled before asynchronous presentation.
#ifdef __OBJC__
void cliprec_present(id<MTLCommandBuffer> __unsafe_unretained cb, id<MTLTexture> __unsafe_unretained src, id<CAMetalDrawable> __unsafe_unretained drawable, float headroom);
#endif
#ifdef __cplusplus
}
#endif
