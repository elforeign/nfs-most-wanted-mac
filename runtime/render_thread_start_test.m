// Production render_thread.m with pthread_create forced to fail: the first command buffer request must come back as a
// plain queue buffer (no recorder with nobody to replay it) and rt_on must be 0.
#include <stdio.h>
#include <errno.h>
#include <pthread.h>
#include <time.h>
#define pthread_create(t, a, f, x) EAGAIN
#ifndef RT_RECORDER_SOURCE
#define RT_RECORDER_SOURCE "render_thread.m"
#endif
#include RT_RECORDER_SOURCE

id<MTLDevice> mtl;
id<MTLCommandQueue> queue;
int diag_on;
uint64_t host_ticks(void) { return clock_gettime_nsec_np(CLOCK_UPTIME_RAW); }
void diag_event(const char *fmt, ...) { (void)fmt; }

@interface FakeQueue : NSObject
- (id)commandBuffer;
@end
@implementation FakeQueue
- (id)commandBuffer { return [NSObject new]; }
@end

int main(void) {
    @autoreleasepool {
        queue = (id)[FakeQueue new];
        id cb = rt_command_buffer();
        int recorder = [cb isKindOfClass:[RTCmd class]];
        printf("recorder=%d rt_on=%d\n", recorder, rt_on);
        return recorder || rt_on != 0;
    }
}
