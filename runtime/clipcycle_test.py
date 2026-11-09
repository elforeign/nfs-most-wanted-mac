#!/usr/bin/env python3
"""Video clip start/stop/restart uses the production callbacks, no GPU or window.
Original callback: starts=1, folder requests=1, failures=2 (exit 1).
Fixed callback: starts=2, folder requests=0, failures=0 (exit 0).
Explicit Finder row: folder requests=1, Movies/NFSMW, SET_ACTION order 46.
Pinned original fragment is SHA-256 checked; offline_tests.sh discovers this test.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
ORIGINAL = r'''
void cliprec_action(int unused) {
    (void)unused;
    std::lock_guard<std::mutex> lock(control);
    ClipSession *s = current;
    if (s && !dispatch_group_wait(s->finished,DISPATCH_TIME_NOW)) {
        if (s->published && s->destination && [NSFileManager.defaultManager fileExistsAtPath:s->destination]) {
            NSURL *url = [NSURL fileURLWithPath:s->destination];
            dispatch_async(dispatch_get_main_queue(), ^{ [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[url]]; });
            current=nil; // The next activation can start another clip.
            return;
        }
    } else if (s) { active.store(false,std::memory_order_release); dispatch_async(s->work, ^{ [s stop]; }); return; }
    static dispatch_once_t once;
    dispatch_once(&once, ^{ atexit(cliprec_shutdown); });
    current = [ClipSession new];
    int resolution = settings_get("display.clip_resolution");
    current->resolution = resolution >= 0 && resolution <= 2 ? (unsigned)resolution : CLIPREC_DEFAULT_RESOLUTION;
    active.store(true,std::memory_order_release);
}
'''
assert hashlib.sha256(ORIGINAL.lstrip().encode()).hexdigest() == \
    'f3caf17430c72fabc607c91ecf97ba25ab9e84c95a4ae033fae1e47a938cfbf8'
source = (RT / 'cliprec.mm').read_text()
callbacks = source[source.index('void cliprec_action(int unused) {'):]
notice = re.search(r'            NSString \*name = destination.lastPathComponent;\n.*portmenu_status.*\n', source)[0]
settings = (RT / 'settings_port.c').read_text()
rows = settings[settings.index('static Setting s_cliprec ='):settings.index('// The key is never a Setting.text value')]
assert 'REG(s_clips_open);' in settings
assert not re.search(r'NSWorkspace|portmenu_open_folder|activateFileViewer', (RT / 'render_present.m').read_text())
assert 'NSWorkspace' not in source and source.count('portmenu_open_folder(') == 2

HARNESS = r'''
#import <Foundation/Foundation.h>
#import <dispatch/dispatch.h>
#include <atomic>
#include <mutex>
#include <cstdio>
#include <cstdarg>
#include "settings.h"
#include "cliprec.h"
static unsigned starts, folders, stops, failures, notices;
static char savedNotice[256];
static void portmenu_status(const char *fmt, ...) {
    ++notices; va_list args; va_start(args,fmt);
    vsnprintf(savedNotice,sizeof savedNotice,fmt,args); va_end(args);
}
static NSString *savedPath, *scratchHome;
static void check(bool ok) { if (!ok) ++failures; }
static NSString *fakeHomeDirectory(void) { return scratchHome; }
#define NSHomeDirectory fakeHomeDirectory
// Both the old direct reveal and the explicit row go through the folder spy.
extern "C" void portmenu_open_folder(const char *path) {
    ++folders;
    check([[NSString stringWithUTF8String:path] isEqualToString:
        [scratchHome stringByAppendingPathComponent:@"Movies/NFSMW"]]);
}
@interface WorkspaceSpy : NSObject
+ (instancetype)sharedWorkspace;
- (void)activateFileViewerSelectingURLs:(NSArray *)urls;
@end
@implementation WorkspaceSpy
+ (instancetype)sharedWorkspace { static WorkspaceSpy *w; if (!w) w=[self new]; return w; }
- (void)activateFileViewerSelectingURLs:(NSArray *)urls {
    (void)urls;
    NSString *folder=[scratchHome stringByAppendingPathComponent:@"Movies/NFSMW"];
    portmenu_open_folder(folder.fileSystemRepresentation);
}
@end
#define NSWorkspace WorkspaceSpy
static void immediate(dispatch_queue_t queue, dispatch_block_t block) { (void)queue; block(); }
#define dispatch_async immediate
static std::atomic<bool> active{false};
static std::mutex control;
@interface ClipSession : NSObject {
@public
    dispatch_group_t finished;
    dispatch_queue_t work;
    bool published, replayOnly, fileRequested;
    std::atomic<unsigned> retiringFiles;
    NSString *destination, *error, *codec;
    unsigned resolution, width, height;
    double started;
    std::atomic<bool> stopping, ready;
    std::atomic<unsigned> poolDrops, writerDrops, unshown, cadenceDrops;
}
- (void)stop;
@end
@implementation ClipSession
- (instancetype)init {
    if ((self=[super init])) {
        retiringFiles=0; ++starts; finished=dispatch_group_create(); dispatch_group_enter(finished);
        work=dispatch_get_main_queue(); destination=savedPath;
    }
    return self;
}
- (void)stop { ++stops; stopping=true; }
@end
static ClipSession *current;
static __attribute__((unused)) bool cliprec_shared_action(ClipSession *) { return false; }
extern "C" void cliprec_shutdown(void) {}
int settings_get(const char *id) { check(!strcmp(id,"display.clip_resolution")); return 1; }
@CALLBACKS@
static void notify_saved(NSString *destination) {
@NOTICE@
}
@ROWS@
int main(int argc, char **argv) { @autoreleasepool {
    if (argc!=2) return 2;
    scratchHome=[NSString stringWithUTF8String:argv[1]];
    savedPath=[scratchHome stringByAppendingPathComponent:@"saved.mp4"];
    [@"clip" writeToFile:savedPath atomically:YES encoding:NSUTF8StringEncoding error:nil];
    check(s_cliprec.type==SET_ACTION && s_cliprec.apply==cliprec_action);
    check(s_clips_open.type==SET_ACTION && s_clips_open.order==46 && s_clips_open.category==SET_DISPLAY);
    check(!strcmp(s_clips_open.label,"Show clips in Finder"));
    check(s_clips_open.apply==cliprec_open_folder);
    s_cliprec.apply(0);
    ClipSession *first=current;
    check(active && starts==1 && folders==0);
    s_cliprec.apply(0);
    check(!active && stops==1 && folders==0);
    char text[256]; cliprec_status(text,sizeof text); check(!strcmp(text,"Saving..."));
    // Press while finalizing: stay stopped and leave Finder alone.
    s_cliprec.apply(0);
    check(!active && starts==1 && folders==0);
    first->published=true;
    notify_saved(first->destination);
    check(notices==1 && !strcmp(savedNotice,"Clip saved: saved.mp4"));
    dispatch_group_leave(first->finished);
    cliprec_status(text,sizeof text); check(!strcmp(text,"Record clip"));
    s_cliprec.apply(0);
    check(active && current!=first && starts==2);
    check(folders==0);
    printf("starts=%u folder_requests=%u failures=%u\n",starts,folders,failures);
    unsigned before=folders;
    s_clips_open.apply(0);
    check(folders==before+1);
    check([NSFileManager.defaultManager fileExistsAtPath:
        [scratchHome stringByAppendingPathComponent:@"Movies/NFSMW"]]);
    printf("explicit_folder_requests=%u total_failures=%u\n",folders-before,failures);
    return failures ? 1 : 0;
} }
'''

with tempfile.TemporaryDirectory(prefix='clipcycle-') as scratch:
    tmp = Path(scratch)
    for label, actions, expected in [('fixed', callbacks, 0), ('original', ORIGINAL.lstrip() +
            callbacks[callbacks.index('void cliprec_open_folder('):], 1)]:
        code = HARNESS.replace('@CALLBACKS@', actions).replace('@ROWS@', rows).replace('@NOTICE@', notice)
        path = tmp / (label + '.mm')
        path.write_text(code)
        exe = tmp / label
        subprocess.run(['clang++', '-std=c++17', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                        '-I', str(RT), str(path), '-framework', 'Foundation', '-o', str(exe)], check=True)
        result = subprocess.run([str(exe), scratch], capture_output=True, text=True)
        print(label + ': ' + result.stdout.strip())
        assert result.returncode == expected, (label, result.returncode, result.stderr)
        assert ('starts=2 folder_requests=0 failures=0' if label == 'fixed' else
                'starts=1 folder_requests=1 failures=2') in result.stdout
print('clipcycle_test PASS: fixed restarts without Finder; original callback rejected')
