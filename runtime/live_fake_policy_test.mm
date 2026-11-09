// A real stream key never goes to the loopback fake ingest. Builds the production poster (no LIVESTREAM_TESTING) and
// creates posters only: no request, session or socket is opened. Driver: live_fake_policy_test.py.
// Measured: fixed real_host=a.upload.youtube.com fake_host=127.0.0.1:4567 (exit 0); the original env-driven
// destination real_host=127.0.0.1 (exit 1).
#include <Foundation/Foundation.h>
#include <deque>
#include <mutex>
#include <algorithm>
#include <memory>
#include <string>
#include <vector>
#include <functional>
#include <cstdio>
#include <cstdarg>
#include <cstdlib>
extern "C" void report_note(const char *, ...) {}
#include "live_seg.h"
// Poster's members are private by class default: open them for this read-only inspection.
#define class struct
#include LIVE_POLICY_SOURCE
#undef class
static int failures=0;
static void require(bool ok,const char *what) { if(!ok) { printf("FAIL live_fake_policy: %s\n",what); ++failures; } }
int main() {
    @autoreleasepool {
        setenv("NFS_TEST_LIVE_FAKE","4567",1);
        auto real=live::Poster::create("real-key");
        require(!!real,"a real key creates a poster");
        NSString *host=real?real->state->base.host:@"-";
        printf("real_host=%s\n",host.UTF8String);
        require([host isEqualToString:@"a.upload.youtube.com"] && [real->state->base.scheme isEqualToString:@"https"],"a real key goes to YouTube with the fake switch set");
        auto named=live::Poster::create("fake-ingest-key");
        require(named && [named->state->base.host isEqualToString:@"a.upload.youtube.com"],"the fixed key typed as a real key still goes to YouTube");
        real.reset(); named.reset();
        require(livestream_start_fake(4567)==1,"fake start accepted");
        auto fake=live::currentStream;
        NSString *fakeHost=fake?fake->state->base.host:@"-";
        printf("fake_host=%s:%ld\n",fakeHost.UTF8String,(long)(fake?fake->state->base.port.integerValue:0));
        require([fakeHost isEqualToString:@"127.0.0.1"] && fake->state->base.port.integerValue==4567,"livestream_start_fake goes to the loopback port");
        livestream_stop();
        require(livestream_start_fake(0)==0 && livestream_start_fake(70000)==0,"invalid fake ports are refused");
        auto after=live::Poster::create("real-key");
        require(after && [after->state->base.host isEqualToString:@"a.upload.youtube.com"],"the fake arm does not outlive its start");
    }
    printf("%s checks failures=%d\n",failures?"FAIL":"PASS",failures);
    return failures?1:0;
}
