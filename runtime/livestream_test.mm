// Abort controls, four yes processes: 14/20 failed before (concurrent only),
// 0/40 after in each of segment/playlist/five/concurrent/retry, plus 0/40 in
// each graceful counterpart. Socket handshake holds the response through Stop.
// Restored graceful abort control: rejected 1/1 (deadline, not immediate Stop).
// Whole-process key scan, unchanged baseline/acceptance: 0/0 matches after
// Save, Go, two uploads, Stop and Forget (7.83 GB, 260 readable VM regions,
// 0 read errors). Skip cancel's poster erase: 1 match, port.poster, inside a
// 1280-byte DefaultMallocZone allocation. Generated input/pattern/readback
// storage is the only excluded VM allocation; output never contains the key.
// This is one process snapshot, not proof about swap, crash dumps or all
// AppKit/CFNetwork behavior. Named-object quit/Stop checks remain separate.
// Early global snapshot control: final ACKs=0 in saved stats vs fixed 1; bytes 752/0.
// Graceful completion: reviewed abort cancelled=1/finals=0; fixed held segment,
// playlist and five queued segments cancelled=failed=0, exactly one final ACK.
// Combined Stop timeout 5.669 s; final retry 2 retries/1 ACK; key residue 0/512 bytes.
// Join-deadline reset: 6.673 s FAIL vs 5.669 s PASS; pair reset: 1.244 s FAIL vs 0.233 s PASS.
// False ACK on stalled final is rejected (finals=0, finalized=1); healthy finalized=1/finals=1.
// Shared drain: five queued items, fixed deadline/cancelled=1/finals=0;
// disabled common timer posts five and a final (cancelled=0/finals=1), FAIL.
// Concurrent global Stop, original/fixed: second premature return 1/0 in
// both plain and poster-queue reentry cases (FAIL/PASS); first returns 0/0
// early, held key scans 1/1, final residue 0/0. Both fixed stoppers finish
// within 2 s of releasing the queue; queue reentry does not deadlock.
// Hardening original/fixed: 5/5 on-queue Stop/failure scans find 1/0 sentinel
// matches each; external Stop prematurely returns 1/0 times, final residue 0/0.
// Original lifetime premise: livestream.mm:45,57 retains the NSString key from
// Go until queued cancellation (:74,:97), which releases without overwriting.
// :136-141 query items/encoded query/URL/request live until the send pool drains;
// :50 task retains URL/request copies until completion (:168), while CFNetwork's
// own session copies have framework-controlled lifetime and cannot be zeroed.
// :296 start's std::string copy is already scrubbed before returning.
// Tests retain the stopped state and scan the full owned allocation; fixture
// input and inaccessible Foundation/CFNetwork allocations are outside the scan.
// Livefix original/fixed: 7 segments/0 playlists vs 7/7, names 6/7 unique;
// protocol/session checks 7/0 failures; queue count 6/5, detached Stop notes 0/1.
// Original 202/500/401 and playlist deadline controls fail; fixed pass.
// Shared 400 ms deadline: fixed 411 ms PASS (550 ms limit, 150 ms margin).
// Playlist timer-reset mutation: 696 ms FAIL new check; 701 ms PASS old 2 s check.
// HTTPS: 13 legacy + 10 protocol cases, 6/6 ordered pairs; Off 0 sessions/0 sockets.
// Original/fixed: 180450-tick cut posts 0/1; oversized segment stops/drops+continues.
// Legacy unbounded/fixed response reads 524288/5120 bytes (segment + playlist); posted 1/0, failed 0/1.
// Byte cap broken/fixed: 18874368/16777216; raw/URL key leaks 1/12 vs 0.
// Recording/replay: coupled 1/100, fixed 100/100. Driver pins original callbacks.
#define LIVESTREAM_TESTING 1
#include "livestream.h"
#include "live_encode.h"
#import <Foundation/Foundation.h>
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <thread>
#include <sys/socket.h>
#include <poll.h>
#include <unistd.h>
#include <cstdarg>
#include <mutex>
#ifdef LIVESTREAM_PROCESS_SCAN
#import <Cocoa/Cocoa.h>
#include <mach/mach_vm.h>
#include <malloc/malloc.h>

// Scanner-owned input/patterns/readback live in one explicitly excluded VM
// allocation. Read stdin directly: stdio/iostream would leave fixture copies.
static constexpr size_t scanChunk=1024*1024, scanRangeCap=500000;
struct ScanRange { uintptr_t address; size_t size; const char *owner; };
struct ScanStorage {
    unsigned char raw[512], patterns[4][3072], readback[scanChunk+3072];
    size_t lengths[4], inputSize;
    ScanRange allocations[scanRangeCap], tracked[256];
    size_t allocationCount, trackedCount;
    bool overflow;
};
static ScanStorage *scanStorage;
static live::StreamTestOptions processScanOptions;
static void scanTrack(const void *address,size_t size,const char *owner) {
    if(!address || !size) return;
    auto s=scanStorage;
    if(s->trackedCount==256) { s->overflow=true; return; }
    s->tracked[s->trackedCount++]={(uintptr_t)address,size,owner};
}
static NSString *scanString(NSString *value,const char *owner) {
    const char *bytes=CFStringGetCStringPtr((__bridge CFStringRef)value,kCFStringEncodingUTF8);
    if(bytes) scanTrack(bytes,strlen(bytes),owner);
    const UniChar *wide=CFStringGetCharactersPtr((__bridge CFStringRef)value);
    if(wide) scanTrack(wide,value.length*sizeof(UniChar),owner);
    return value;
}
static kern_return_t scanReader(task_t,vm_address_t address,vm_size_t,void **memory) {
    *memory=(void *)address; return KERN_SUCCESS;
}
static void scanRanges(task_t,void *context,unsigned,vm_range_t *ranges,unsigned count) {
    auto s=scanStorage;
    for(unsigned i=0;i<count;++i) {
        if(s->allocationCount==scanRangeCap) { s->overflow=true; return; }
        s->allocations[s->allocationCount++]={ranges[i].address,ranges[i].size,(const char *)context};
    }
}
static void scanInput() {
    mach_vm_address_t address=0;
    if(mach_vm_allocate(mach_task_self(),&address,sizeof(ScanStorage),VM_FLAGS_ANYWHERE)!=KERN_SUCCESS) abort();
    scanStorage=(ScanStorage *)address;
    auto s=scanStorage;
    while(s->inputSize<sizeof s->raw) {
        ssize_t n=read(STDIN_FILENO,s->raw+s->inputSize,1);
        if(n!=1 || s->raw[s->inputSize]=='\n') break;
        ++s->inputSize;
    }
    if(!s->inputSize || s->inputSize==sizeof s->raw) abort();
    const char hex[]="0123456789ABCDEF";
    for(size_t i=0;i<s->inputSize;++i) {
        unsigned char c=s->raw[i];
        // This scan fixture is ASCII with URL punctuation, not a compiled literal.
        if(c>=128) abort();
        s->patterns[0][s->lengths[0]++]=c;
        if((c>='a' && c<='z') || (c>='A' && c<='Z') || (c>='0' && c<='9') ||
           c=='-' || c=='_' || c=='.' || c=='~') s->patterns[1][s->lengths[1]++]=c;
        else {
            s->patterns[1][s->lengths[1]++]='%';
            s->patterns[1][s->lengths[1]++]=hex[c>>4];
            s->patterns[1][s->lengths[1]++]=hex[c&15];
        }
    }
    for(unsigned p=0;p<2;++p) for(size_t i=0;i<s->lengths[p];++i) {
        s->patterns[p+2][s->lengths[p+2]++]=s->patterns[p][i];
        s->patterns[p+2][s->lengths[p+2]++]=0;
    }
}
static unsigned scanProcess() {
    auto s=scanStorage;
    vm_address_t *zones=nullptr; unsigned zoneCount=0;
    if(malloc_get_all_zones(mach_task_self(),scanReader,&zones,&zoneCount)!=KERN_SUCCESS) abort();
    for(unsigned i=0;i<zoneCount;++i) {
        auto zone=(malloc_zone_t *)zones[i];
        if(zone->introspect->enumerator(mach_task_self(),(void *)zone->zone_name,
            MALLOC_PTR_IN_USE_RANGE_TYPE,zones[i],scanReader,scanRanges)!=KERN_SUCCESS) abort();
    }
    std::sort(s->allocations,s->allocations+s->allocationCount,[](const ScanRange& a,const ScanRange& b) {
        return a.address<b.address;
    });
    unsigned matches=0, port=0, unknown=0, regions=0, readErrors=0;
    uint64_t scanned=0;
    mach_vm_address_t previousEnd=0;
    size_t tailSize=0;
    unsigned tailTag=0;
    mach_vm_address_t address=0; natural_t depth=0;
    while(true) {
        mach_vm_size_t size=0;
        vm_region_submap_info_data_64_t info={};
        mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;
        kern_return_t rc=mach_vm_region_recurse(mach_task_self(),&address,&size,&depth,
            (vm_region_recurse_info_t)&info,&count);
        if(rc==KERN_INVALID_ADDRESS) break;
        if(rc!=KERN_SUCCESS) abort();
        if(info.is_submap) { ++depth; continue; }
        if(info.protection&VM_PROT_READ) {
            ++regions;
            for(mach_vm_size_t offset=0;offset<size;) {
                size_t amount=(size_t)std::min<mach_vm_size_t>(scanChunk,size-offset);
                mach_vm_address_t begin=address+offset;
                // This is the only excluded address range, including patterns.
                if(begin<(uintptr_t)s+sizeof *s && begin+amount>(uintptr_t)s) {
                    if(begin<(uintptr_t)s) amount=(uintptr_t)s-begin;
                    else { offset+=std::min<mach_vm_size_t>(size-offset,(uintptr_t)s+sizeof *s-begin); continue; }
                }
                // Carry the previous bytes across chunks AND adjacent readable
                // VM regions, so a key straddling either boundary is counted.
                if(begin!=previousEnd) tailSize=0;
                mach_vm_size_t got=0;
                rc=mach_vm_read_overwrite(mach_task_self(),begin,amount,(mach_vm_address_t)(s->readback+tailSize),&got);
                if(rc!=KERN_SUCCESS || got!=amount) { ++readErrors; tailSize=0; previousEnd=0; offset+=amount; continue; }
                scanned+=amount;
                size_t available=tailSize+got;
                for(unsigned p=0;p<4;++p) {
                    const unsigned char *cursor=s->readback;
                    const unsigned char *end=s->readback+available;
                    while((size_t)(end-cursor)>=s->lengths[p]) {
                        auto hit=(const unsigned char *)memmem(cursor,end-cursor,s->patterns[p],s->lengths[p]);
                        if(!hit) break;
                        cursor=hit+1;
                        if(hit+s->lengths[p]<=s->readback+tailSize) continue; // already counted
                        uintptr_t location=begin-tailSize+(hit-s->readback);
                        const char *owner="unattributed";
                        for(size_t i=0;i<s->trackedCount;++i) {
                            const auto &r=s->tracked[i];
                            if(location>=r.address && location+s->lengths[p]<=r.address+r.size) owner=r.owner;
                        }
                        auto allocation=std::upper_bound(s->allocations,s->allocations+s->allocationCount,location,
                            [](uintptr_t value,const ScanRange& r) { return value<r.address; });
                        const ScanRange *r=allocation==s->allocations?nullptr:allocation-1;
                        if(r && location>=r->address+r->size) r=nullptr;
                        ++matches;
                        if(!strncmp(owner,"port.",5)) ++port; else ++unknown;
                        printf("memory-match: form=%u address=0x%llx owner=%s zone=%s allocation=0x%llx size=%zu tag=%u\n",
                            p,(unsigned long long)location,owner,r&&r->owner?r->owner:"none/freed",
                            (unsigned long long)(r?r->address:0),r?r->size:0,location<begin?tailTag:info.user_tag);
                    }
                }
                tailSize=std::min<size_t>(3071,available);
                memmove(s->readback,s->readback+available-tailSize,tailSize);
                memset(s->readback+tailSize,0,sizeof s->readback-tailSize);
                previousEnd=begin+amount;
                tailTag=info.user_tag;
                offset+=amount;
            }
        }
        if(!size || address+size<address) break;
        address+=size;
    }
    printf("memory-scan: matches=%u port=%u unattributed=%u regions=%u bytes=%llu read_errors=%u overflow=%d exclusions=1\n",
        matches,port,unknown,regions,(unsigned long long)scanned,readErrors,s->overflow);
    if(s->overflow || readErrors) abort();
    return matches;
}
#endif
static std::mutex sessionNotesLock;
static std::vector<std::string> sessionNotes;
static FILE *sessionNoteFile;
extern "C" void report_note(const char *fmt, ...) {
    char text[512]; va_list ap; va_start(ap,fmt);
    vsnprintf(text,sizeof text,fmt,ap); va_end(ap);
    std::lock_guard<std::mutex> guard(sessionNotesLock);
    sessionNotes.emplace_back(text);
    if(sessionNoteFile) { fprintf(sessionNoteFile,"%s\n",text); fflush(sessionNoteFile); }
    printf("session: %s\n",text);
}

static std::atomic<unsigned> createdSessions{0};
static std::atomic<unsigned> startedRequests{0};
static std::atomic<size_t> receivedResponseBytes{0};
extern "C" unsigned livestream_test_socket_count();
#ifndef LIVESTREAM_SOURCE
#define LIVESTREAM_SOURCE "livestream.mm"
#endif
#include LIVESTREAM_SOURCE

#ifdef LIVESTREAM_PROCESS_SCAN
// Use the production secure controller and poster together; only capture and
// window attachment are doubled. The fixture's external clipboard bytes live
// in the excluded input allocation; its returned NSString is scanned normally.
static int scanCaptureState;
extern "C" int cliprec_live_state(void) { return scanCaptureState; }
extern "C" int cliprec_live_start(void) { scanCaptureState=1; return 1; }
extern "C" void cliprec_live_stop(void) { scanCaptureState=0; }
@interface ScanParent : NSWindow @end
@implementation ScanParent
- (void)beginSheet:(NSWindow *)sheet completionHandler:(void (^)(NSModalResponse))handler { (void)sheet; (void)handler; }
@end
@interface ScanApp : NSObject
@property NSWindow *keyWindow;
- (void)sendEvent:(NSEvent *)event;
@end
@implementation ScanApp
- (void)sendEvent:(NSEvent *)event { (void)event; }
@end
static ScanApp *scanApp;
@interface ScanPasteboard : NSObject
+ (instancetype)generalPasteboard;
- (NSString *)stringForType:(NSPasteboardType)type;
@end
@implementation ScanPasteboard
+ (instancetype)generalPasteboard { return [self new]; }
- (NSString *)stringForType:(NSPasteboardType)type {
    if(![type isEqual:NSPasteboardTypeString]) return nil;
    return [[NSString alloc] initWithBytes:scanStorage->raw length:scanStorage->inputSize encoding:NSASCIIStringEncoding];
}
@end
#define NSPasteboard ScanPasteboard
#undef NSApp
#define NSApp scanApp
#ifndef STREAM_KEY_SOURCE
#define STREAM_KEY_SOURCE "stream_key.m"
#endif
#include STREAM_KEY_SOURCE
#undef NSApp
#undef NSPasteboard
#endif

static void check(bool ok,const char *why) {
    if(!ok) { fprintf(stderr,"FAIL stream: %s\n",why); exit(1); }
}
template<class F> static bool await(F predicate,double seconds=8) {
    auto until=std::chrono::steady_clock::now()+std::chrono::duration<double>(seconds);
    do {
        if(predicate()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    } while(std::chrono::steady_clock::now()<until);
    return predicate();
}
static live::OwnedSegment segment(uint64_t sequence=0,size_t bytes=752,int64_t duration=180000) {
    auto s=std::make_shared<live::Segment>();
    s->sequence=sequence; s->start90k=sequence*180000; s->end90k=s->start90k+duration;
    s->bytes.assign(bytes,(uint8_t)sequence);
    for(size_t i=0;i<bytes;i+=188) s->bytes[i]=0x47;
    return s;
}
static void drained(const std::shared_ptr<live::Poster>& p,double seconds=2) {
    check(await([&]{return p->stats().bytes==0 && p->stats().ticks==0;},seconds),"cancel releases accounted payload");
}
static unsigned noteCount(const char *part) {
    std::lock_guard<std::mutex> guard(sessionNotesLock);
    unsigned n=0; for(const auto &s:sessionNotes) n+=s.find(part)!=std::string::npos;
    return n;
}
static clip::OwnedPacket packet() {
    CMBlockBufferRef block=nullptr; CMSampleBufferRef sample=nullptr;
    size_t bytes=64;
    check(!CMBlockBufferCreateWithMemoryBlock(nullptr,nullptr,bytes,nullptr,nullptr,0,bytes,0,&block),"capture fixture block");
    CMSampleTimingInfo timing={CMTimeMake(1,60),kCMTimeZero,kCMTimeInvalid};
    check(!CMSampleBufferCreateReady(nullptr,block,nullptr,1,1,&timing,1,&bytes,&sample),"capture fixture sample");
    auto p=std::make_shared<clip::Packet>(clip::Track::video,sample);
    CFRelease(sample); CFRelease(block); return p;
}
static void captureHealthy(const std::shared_ptr<live::Poster>& p) {
    std::atomic<unsigned> recorded{0},replayed{0};
    clip::Fanout capture;
    auto file=std::make_shared<clip::Sink>([&](const clip::OwnedPacket&) { ++recorded; return clip::Result::consumed; });
    auto replay=std::make_shared<clip::Sink>([&](const clip::OwnedPacket&) { ++replayed; return clip::Result::consumed; });
    { std::lock_guard<std::mutex> guard(live::streamLock); live::currentStream=p; }
    auto output=live::streamOutput();
    auto stream=std::make_shared<clip::Sink>([output](const clip::OwnedPacket&) {
        return output(segment())?clip::Result::consumed:clip::Result::failed;
    });
    capture.add(file); capture.add(replay); capture.add(stream);
    auto sample=packet();
    capture.publish(sample);
    check(await([&]{return stream->failed();}),"failed live consumer retires independently");
    for(unsigned i=1;i<100;++i) capture.publish(sample);
    capture.close();
    check(await([&]{return capture.drained();}),"capture drains after live failure");
    printf("capture: file=%u replay=%u live_failed=%d healthy=%d\n",recorded.load(),replayed.load(),stream->failed(),capture.healthy());
    check(recorded==100 && replayed==100 && !file->failed() && !replay->failed() && stream->failed() && capture.healthy(),"recording and replay survive live failure");
    live::stopStream();
}
// The scanner visits the complete owned allocation, not just its logical size.
// The fallback exercises the original retained NSString without changing it.
template<class S> static auto keyResidue(const S& state,const std::string& sentinel,int)
    -> decltype(sizeof(state->keyBytes),size_t{}) {
    (void)sentinel;
    unsigned residue=0;
    for(unsigned char byte:state->keyBytes) residue|=byte;
    return residue?1:0; // every byte must be zero, including unused capacity
}
template<class S> static size_t keyResidue(const S& state,const std::string& sentinel,long) {
    NSString *text=[[NSString alloc] initWithBytes:sentinel.data() length:sentinel.size() encoding:NSUTF8StringEncoding];
    return state->key && [state->key containsString:text];
}
static int keyExternalStopProof(const std::string& key,const live::StreamTestOptions& options) {
    auto poster=live::Poster::create(key,options);
    check(!!poster,"external wipe activation");
    auto state=poster->testState();
    dispatch_semaphore_t gate=dispatch_semaphore_create(0), entered=dispatch_semaphore_create(0);
    dispatch_async(state->work,^{
        dispatch_semaphore_signal(entered);
        dispatch_semaphore_wait(gate,DISPATCH_TIME_FOREVER);
    });
    dispatch_semaphore_wait(entered,DISPATCH_TIME_FOREVER);
    std::atomic<bool> returned{false};
    std::thread stopper([&] { poster->stop(); returned=true; });
    // Wait for the stopping thread to retire the producer while work is held.
    check(await([&]{return !poster->stats().running;}),"external stop retires producer");
    bool premature=await([&]{return returned.load();},0.05);
    dispatch_semaphore_signal(gate);
    stopper.join();
    __block size_t residual=0;
    dispatch_sync(state->work,^{ residual=keyResidue(state,key,0); });
    printf("external key wipe: premature_return=%d residual_matches=%zu\n",premature,residual);
    check(!premature && !residual,"Stop waits for cancellation and erasure on another queue");
    return 0;
}
// Hold the poster queue so the first global Stop has retired the producer but
// cannot erase. A second global Stop must join it, including queue reentry.
static int keyConcurrentStopProof(const std::string& key,const live::StreamTestOptions& options,bool reenter) {
    auto poster=live::Poster::create(key,options);
    check(!!poster,"concurrent wipe activation");
    auto state=poster->testState();
    { std::lock_guard<std::mutex> guard(live::streamLock); live::currentStream=poster; }
    dispatch_semaphore_t gate=dispatch_semaphore_create(0), entered=dispatch_semaphore_create(0);
    dispatch_async(state->work,^{
        dispatch_semaphore_signal(entered);
        check(!dispatch_semaphore_wait(gate,dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC)),"release held poster queue");
        if(reenter) livestream_stop(); // must not deadlock on streamLock
    });
    check(!dispatch_semaphore_wait(entered,dispatch_time(DISPATCH_TIME_NOW,2*NSEC_PER_SEC)),"poster queue held");
    std::atomic<bool> firstReturned{false}, secondEntered{false}, secondReturned{false};
    std::thread first([&] { livestream_stop(); firstReturned=true; });
    check(await([&]{return !poster->stats().running;},1),"first stopper retires producer before drain");
    std::thread second([&] { secondEntered=true; livestream_stop(); secondReturned=true; });
    check(await([&]{return secondEntered.load();},1),"second stopper enters");
    bool premature=await([&]{return secondReturned.load();},0.05);
    bool firstPremature=firstReturned.load();
    // The gate still prevents any writes to the key allocation here.
    size_t held=keyResidue(state,key,0);
    dispatch_semaphore_signal(gate);
    check(await([&]{return firstReturned.load() && secondReturned.load();},2),"both stoppers finish bounded queue drain");
    first.join(); second.join();
    __block size_t residual=0;
    dispatch_sync(state->work,^{ residual=keyResidue(state,key,0); });
    printf("concurrent key wipe: first_premature=%d second_premature=%d held_matches=%zu residual_matches=%zu queue_reentry=%d\n",
        firstPremature,premature,held,residual,reenter);
    check(!firstPremature && !premature && held==1 && !residual,"concurrent stoppers wait for poster erasure");
    check(livestream_status()==0,"concurrent stop leaves stream Off");
    return 0;
}
static int keyWipeProof(const std::string& mode,const std::string& key,const live::StreamTestOptions& options) {
    if(mode=="wipe-concurrent" || mode=="wipe-concurrent-queue")
        return keyConcurrentStopProof(key,options,mode=="wipe-concurrent-queue");
    if(mode=="wipe-external") return keyExternalStopProof(key,options);
    auto poster=live::Poster::create(key,options);
    check(!!poster,"wipe poster activation");
    auto state=poster->testState();
    check(keyResidue(state,key,0)>0,"scanner sees active key before cancellation");
    if(mode!="wipe-idle") {
        check(poster->post(segment()),"wipe request admission");
        check(await([&]{return startedRequests.load()>0;}),"wipe request starts");
        if(mode=="wipe-playlist") check(await([&]{return startedRequests.load()>1;}),"playlist starts");
        if(mode=="wipe-retry") check(await([&]{return poster->stats().retries>0;}),"retry scheduled");
    }
    __block size_t residual=0;
    // The original dispatch_async cancellation cannot run before this block
    // returns, making the old readable copy deterministic, without sleeps.
    dispatch_sync(state->work,^{
        if(mode=="wipe-failure") poster->fail(live::StreamError::capture);
        else poster->stop();
        residual=keyResidue(state,key,0);
    });
    printf("key wipe: residual_matches=%zu running=%d\n",residual,poster->stats().running);
    check(!residual && !poster->stats().running,"Stop/terminal failure synchronously clears owned poster bytes");
    drained(poster);
    return 0;
}
// Graceful fixtures release after admission closes; abort fixtures hold the
// response until Stop returns, so it cannot win the race against cancellation.
static int finishProof(const std::string& mode,const std::string& key,const live::StreamTestOptions& options,
                       const std::string& anchor) {
    const char *abortFD=std::getenv("NFS_TEST_ABORT_FD");
    int responseFD=abortFD?std::atoi(abortFD):-1;
    auto p=live::Poster::create(key,options);
    check(!!p,"finish activation");
    auto state=p->testState();
    std::string directory=anchor.substr(0,anchor.find_last_of('/')+1);
    bool idle=mode=="finish-idle", stalled=mode.find("stall")!=std::string::npos;
    unsigned count=(mode=="finish-five" || mode=="finish-queue-stall")?5:mode=="finish-tail"?6:1;
    if(!idle) {
        for(unsigned i=0;i<count;++i) {
            check(p->post(segment(i,752,i%2?180450:180000)),"finish admission");
            if(mode=="finish-tail") check(await([&]{ return !p->stats().bytes; }),"tail acknowledged");
        }
        if(mode=="finish-segment" || mode=="finish-playlist" || mode=="finish-five" ||
           mode=="finish-concurrent" || mode=="finish-retry" || mode=="finish-drain-stall" ||
           mode=="finish-total-stall" || mode=="finish-joined-stall" || mode=="finish-early-stall" || mode=="finish-queue-stall")
        {
            if(abortFD) {
                pollfd ready={responseFD,POLLIN,0}; char held=0;
                check(poll(&ready,1,8000)==1 && read(responseFD,&held,1)==1 && held=='H',"abort response held by fixture");
            } else check(await([&]{return std::ifstream(directory+"finish-held").good();}),"response held by fixture");
        }
        else if(mode=="finish-final-stall")
            check(await([&]{return !p->stats().bytes;}),"normal pair acknowledged before final stall");
    }
    if(mode=="finish-early-stall") std::this_thread::sleep_for(std::chrono::milliseconds(150));
    std::thread release;
    if(!abortFD) release=std::thread([&] {
        check(await([&]{return !p->stats().running;}),"finish closes admission");
        if(mode=="finish-concurrent") check(!live::startStream(key),"Go blocked during shared completion");
        if(mode=="finish-total-stall" || mode=="finish-joined-stall") std::this_thread::sleep_for(std::chrono::milliseconds(2650));
        std::ofstream(directory+"finish-release")<<"release";
    });
    auto began=std::chrono::steady_clock::now();
    if(mode=="finish-concurrent") {
        { std::lock_guard<std::mutex> guard(live::streamLock); live::currentStream=p; }
        std::thread second([]{ livestream_stop(); });
        livestream_stop(); second.join();
    } else if(mode=="finish-joined-stall") {
        std::thread first([&]{ p->finish(); });
        check(await([&]{return !p->stats().running;}),"first finish starts shared deadline");
        std::this_thread::sleep_for(std::chrono::milliseconds(4000));
        p->finish(); first.join();
    } else p->finish();
    double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();
    if(abortFD) {
        // Stop has issued cancellation before the fixture may answer. The
        // graceful-only Go check above belongs to the held completion path;
        // an immediate abort has already completed when this point is reached.
        check(write(responseFD,"R",1)==1,"abort releases held response after Stop");
    } else release.join();
    auto result=p->stats();
    __block size_t residue=0;
    dispatch_sync(state->work,^{ residue=keyResidue(state,key,0); });
    printf("finish proof: mode=%s accepted=%llu posted=%llu playlists=%llu cancelled=%llu failed=%llu finals=%llu finalized=%d bytes=%zu ticks=%lld residue=%zu seconds=%.3f\n",
        mode.c_str(),(unsigned long long)result.accepted,(unsigned long long)result.posted,
        (unsigned long long)result.playlists,(unsigned long long)result.cancelled,(unsigned long long)result.failed,
        (unsigned long long)result.finalPlaylists,result.finalized,result.bytes,(long long)result.ticks,residue,elapsed);
    if(abortFD) {
        check(result.error==live::StreamError::stopped,"abort control must stop without deadline");
        printf("abort control: stopped=1\n");
    }
    check(elapsed<=6.25 && !residue && !result.bytes && !result.ticks,"finish bounded wait and complete key/payload erasure");
    check(!p->post(segment()),"finish rejects stale callback");
    if(mode=="finish-early-stall") check(elapsed<0.55,"finish preserves active pair earlier deadline");
    if(mode=="finish-retry") check(result.retries==2,"final playlist retains bounded retries");
    if(stalled) check(!result.finalized && !result.finalPlaylists && result.error==live::StreamError::deadline,
                      "timeout must not claim acknowledged EOS");
    else if(idle) check(!result.accepted && !createdSessions && !result.playlists,"empty finish creates no session");
    else check(result.accepted==count && result.posted==count && result.playlists==count+1 &&
               result.finalPlaylists==1 && result.finalized && !result.cancelled && !result.failed,
               "graceful held response completes without cancellation and acknowledges one EOS");
    if(mode=="finish-concurrent") {
        std::lock_guard<std::mutex> guard(live::streamLock);
        auto last=live::lastStream;
        printf("last snapshot: posted=%llu playlists=%llu finals=%llu finalized=%d bytes=%zu\n",
            (unsigned long long)last.posted,(unsigned long long)last.playlists,
            (unsigned long long)last.finalPlaylists,last.finalized,last.bytes);
        check(last.posted==result.posted && last.playlists==result.playlists &&
              last.finalPlaylists==result.finalPlaylists && last.finalized==result.finalized && !last.bytes,
              "global Stop snapshots final counters after completion");
    }
    p->finish(); // repeated callers never reset deadlines or send another EOS
    check(p->stats().finalPlaylists==result.finalPlaylists,"repeated finish is idempotent");
    return 0;
}
#ifdef LIVESTREAM_PROCESS_SCAN
static int processScanProof(const char *base,const char *anchor) {
    scanInput();
    processScanOptions.baseURL=base;
    std::ifstream cert(anchor,std::ios::binary);
    processScanOptions.anchorDER.assign(std::istreambuf_iterator<char>(cert),{});
    check(!processScanOptions.anchorDER.empty(),"scan fixture anchor");
    std::shared_ptr<live::StreamState> heldState;
    @autoreleasepool {
        [NSApplication sharedApplication];
        scanApp=[ScanApp new];
        scanApp.keyWindow=[[ScanParent alloc] initWithContentRect:NSMakeRect(0,0,640,480)
            styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
        stream_key_edit(0);
        NSEvent *paste=[NSEvent keyEventWithType:NSEventTypeKeyDown location:NSZeroPoint
            modifierFlags:NSEventModifierFlagCommand timestamp:0 windowNumber:0 context:nil
            characters:@"v" charactersIgnoringModifiers:@"v" isARepeat:NO keyCode:9];
        check(stream_key_route_event(paste),"scan command-paste");
        [controller save:nil];
        check(keySize==scanStorage->inputSize && !panel,"scan Save");
        stream_key_go(0);
        check(!!live::currentStream,"scan Go");
        heldState=live::currentStream->testState();
        scanTrack(keyBytes,sizeof keyBytes,"port.saved");
        scanTrack(heldState->keyBytes,sizeof heldState->keyBytes,"port.poster");
        auto output=live::streamOutput();
        check(output(segment(0)) && output(segment(1)),"scan two admissions");
        check(await([&]{ return live::currentStream->stats().playlists==2; }),"scan two uploads acknowledged");
        stream_key_stop(0);
        check(live::lastStream.finalized && live::lastStream.posted==2,"scan Stop completed");
        stream_key_forget(0);
        scanApp=nil;
    }
    // Drain the serial callback pool and main run loop before the snapshot.
    // Keep the stopped poster allocation alive so a skipped erase is observable.
    dispatch_sync(heldState->work, ^{});
    @autoreleasepool { [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.1]]; }
    unsigned matches=scanProcess();
    check(matches==0,"whole-process scan finds a remaining key copy");
    return 0;
}
#endif
// Upload stall: real segmenter -> poster at the production limits, eight IDR-only 2 s segments fed at twice real
// time; the fixture holds the second segment's response 3.5 s (seven segment intervals). Live must continue.
static int rideStallProof(const std::string& key,live::StreamTestOptions options) {
    options.deadlineSeconds=live::Poster::deadlineSeconds;
    live::SegmentLimits limits;
    auto p=live::Poster::create(key,options);
    check(!!p,"ride activation");
    constexpr unsigned frames=8;
    std::vector<clip::OwnedPacket> packets;
    auto sink=std::make_shared<clip::Sink>([&](const clip::OwnedPacket& packet) {
        packets.push_back(packet); return clip::Result::consumed;
    });
    live::Encoder encoder(live::Profile::youtube(320,180),sink);
    check(!encoder.status(),"ride encoder");
    CVPixelBufferRef pixel=nullptr;
    check(!CVPixelBufferCreate(nullptr,320,180,kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,nullptr,&pixel),"ride pixel");
    CVPixelBufferLockBaseAddress(pixel,0);
    for(size_t plane=0;plane<2;++plane) memset(CVPixelBufferGetBaseAddressOfPlane(pixel,plane),128,
        CVPixelBufferGetBytesPerRowOfPlane(pixel,plane)*CVPixelBufferGetHeightOfPlane(pixel,plane));
    CVPixelBufferUnlockBaseAddress(pixel,0);
    for(unsigned i=0;i<frames;++i) {
        check(encoder.encode(pixel,CMTimeMake(i*180000,90000),CMTimeMake(1500,90000)),"ride encode");
        encoder.complete();
    }
    CVPixelBufferRelease(pixel); sink->close();
    check(await([&]{return sink->drained();}) && packets.size()==frames,"ride video drain");
    clip::AACEncoder audio; check(!audio.status,"ride AAC");
    std::vector<float> pcm(48000*2*2*frames);
    check(audio.encode(pcm.data(),pcm.size()/2,true,[&](clip::OwnedPacket packet) { packets.push_back(packet); }),"ride audio encode");
    auto at=[](const clip::OwnedPacket& packet) { return CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(packet->sample())); };
    std::stable_sort(packets.begin(),packets.end(),[&](const auto& a,const auto& b){ return at(a)<at(b); });
    live::Segmenter mux([&](live::OwnedSegment seg) { return p->post(seg); },limits);
    auto start=std::chrono::steady_clock::now();
    bool fed=true;
    for(auto &packet:packets) {
        std::this_thread::sleep_until(start+std::chrono::duration_cast<std::chrono::steady_clock::duration>(
            std::chrono::duration<double>(std::max(0.0,at(packet)/2))));
        if(mux.consume(packet)!=clip::Result::consumed) { fed=false; break; }
    }
    bool finished=fed && mux.finish();
    auto m=mux.stats();
    await([&]{ auto s=p->stats(); return !s.running || s.playlists>=m.segments; },12);
    auto s=p->stats();
    printf("ride: segments=%llu posted=%llu playlists=%llu dropped=%llu running=%d error=%d segmenter_failed=%d reason=%s held_peak=%zu peak_ticks=%lld seconds=%.3f\n",
        (unsigned long long)m.segments,(unsigned long long)s.posted,(unsigned long long)s.playlists,(unsigned long long)s.dropped,
        s.running,(int)s.error,m.failed,m.reason?m.reason:"none",m.heldPeak,(long long)s.peakTicks,
        std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count());
    check(finished && !m.failed && m.segments==frames,"segmenter rides through a stalled upload");
    check(s.running && s.posted==frames && s.playlists==frames && !s.dropped,"poster rides through a stalled upload");
    p->stop(); drained(p); return 0;
}
int main(int argc,char **argv) {
#ifdef LIVESTREAM_PROCESS_SCAN
    check(argc==4 && !strcmp(argv[1],"process-scan"),"process scan arguments");
    return processScanProof(argv[2],argv[3]);
#endif
    @autoreleasepool {
        check(argc==4,"mode/base/anchor arguments");
        sessionNoteFile=fopen((std::string(argv[3])+".session.txt").c_str(),"w");
        check(sessionNoteFile,"session-note sink");
        std::string mode=argv[1],key;
        std::getline(std::cin,key); // Never put the key in command arguments or environment.
        live::StreamTestOptions options;
        options.baseURL=argv[2];
        std::ifstream cert(argv[3],std::ios::binary);
        options.anchorDER.assign(std::istreambuf_iterator<char>(cert),{});
        check(!options.anchorDER.empty(),"fixture anchor");
        if(mode=="finish-early-stall") options.deadlineSeconds=0.4;
        if(mode.rfind("finish-",0)==0) return finishProof(mode,key,options,argv[3]);
        if(mode=="policy") {
            unsigned rejected=0;
            for(const char *url:{"https://example.invalid/http_upload_hls", "http://a.upload.youtube.com/http_upload_hls",
                "https://a.upload.youtube.com.evil.invalid/http_upload_hls", "https://a.upload.youtube.com:444/http_upload_hls",
                "https://user@a.upload.youtube.com/http_upload_hls", "https://a.upload.youtube.com/http_upload_hls?cid=x",
                "https://a.upload.youtube.com/http_upload_hls#fragment", "https://localhost:443/http_upload_hls",
                "https://127.0.0.2:443/http_upload_hls", "http://127.0.0.1:443/http_upload_hls"}) {
                auto other=options; other.baseURL=url;
                auto p=live::Poster::create(key,other);
                if(!p) ++rejected;
            }
            printf("policy: refused=%u sessions=%u\n",rejected,createdSessions.load());
            check(rejected==10 && !createdSessions,"destination policy rejects before session creation");
            return 0;
        }
        if(mode=="off") {
            auto before=livestream_test_socket_count();
            auto off=live::Poster::create("",options);
            if(off) off->post(segment());
            auto output=live::streamOutput();
            for(unsigned i=0;i<100;++i) check(output(segment(i)),"inactive callback releases segments");
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            printf("off: sessions=%u sockets=%u\n",createdSessions.load(),livestream_test_socket_count()-before);
            check(!off && !createdSessions && livestream_test_socket_count()==before,"Off creates zero sessions and sockets");
            check(!live::startStream(""),"empty start remains inactive");
            return 0;
        }
        if(mode.rfind("wipe-",0)==0) return keyWipeProof(mode,key,options);
        if(mode=="untrusted") options.anchorDER.clear();
        if(mode=="ride-stall") return rideStallProof(key,options);
        if(mode=="deadline" || mode=="playlist-stall") options.deadlineSeconds=0.4;
        auto p=live::Poster::create(key,options);
        check(!!p,"explicit activation");
        if(mode=="count") {
            for(unsigned i=0;i<6;++i) check(p->post(segment(i,188,90000)),"count admission continues");
            auto s=p->stats();
            printf("count: accepted=%llu dropped=%llu peak_bytes=%zu\n",(unsigned long long)s.accepted,(unsigned long long)s.dropped,s.peakBytes);
            check(s.running && s.accepted==5 && s.dropped==1 && s.peakBytes==5*188,"at most five queued plus in-flight segments");
            p->stop(); drained(p); return 0;
        }
        if(mode=="detached-stop") {
            check(p->post(segment()),"detached stop admission");
            check(await([&]{return startedRequests.load()>0;}),"detached task starts");
            p.reset();
            check(await([&]{return noteCount("Stop accepted=1 posted=0 playlists=0 cancelled=1 dropped=0 failed=1")>0;},2),
                "last poster release still logs final cancellation stats");
            printf("detached-stop: final_stop_notes=%u\n",noteCount("Stop accepted="));
            return 0;
        }
        if(mode=="protocol" || mode=="success") {
            unsigned failures=0;
            auto proof=[&](bool ok,const char *name) { if(!ok) { ++failures; fprintf(stderr,"FAIL new: %s\n",name); } };
            unsigned count=mode=="protocol"?6:1;
            for(unsigned i=0;i<count;++i) {
                int64_t duration=i%2?180450:180000;
                check(p->post(segment(i,752,duration)),"protocol admission");
                check(await([&]{return !p->stats().bytes;}),"protocol upload drains");
            }
            auto s=p->stats();
            proof(s.running && s.posted==count && s.playlists==count,"one accepted playlist per segment");
            p->stop(); drained(p);
            await([&]{return noteCount("Stop ")>0;},2);
            proof(noteCount("Go live")==1,"Go session note");
            proof(noteCount("first segment accepted code=")==1,"first segment session note");
            proof(noteCount("first playlist accepted code=")==1,"first playlist session note");
            proof(noteCount("Stop accepted=")==1,"Stop stats session note");
            if(mode=="protocol") {
                auto next=live::Poster::create(key,options);
                check(next && next->post(segment()),"second start");
                check(await([&]{return !next->stats().bytes;}),"second start drains");
                proof(next->stats().playlists==1,"second start playlist");
                next->stop(); drained(next);
                proof(await([&]{return noteCount("Stop accepted=")==2;},2),"second Stop session note");
            }
            printf("new proof: segments=%llu playlists=%llu retries=%llu failures=%u\n",(unsigned long long)s.posted,
                (unsigned long long)s.playlists,(unsigned long long)s.retries,failures);
            return failures?1:0;
        }
        if(mode=="ordered") {
            for(unsigned i=0;i<6;++i) {
                check(p->post(segment(i)),"ordered admission");
                check(await([&]{return p->stats().playlists==i+1 || !p->stats().running;}),"ordered completion");
                check(p->stats().running && p->stats().posted==i+1,"successful ordered POST");
            }
            auto s=p->stats();
            printf("ordered: accepted=%llu posted=%llu peak_bytes=%zu peak_ticks=%lld sessions=%u\n",
                (unsigned long long)s.accepted,(unsigned long long)s.posted,s.peakBytes,(long long)s.peakTicks,createdSessions.load());
            p->stop(); drained(p); return 0;
        }
        if(mode=="jitter") {
            std::vector<clip::OwnedPacket> video;
            auto sink=std::make_shared<clip::Sink>([&](const clip::OwnedPacket& packet) {
                video.push_back(packet); return clip::Result::consumed;
            });
            live::Encoder encoder(live::Profile::youtube(320,180),sink);
            check(!encoder.status(),"jitter encoder");
            CVPixelBufferRef pixel=nullptr;
            check(!CVPixelBufferCreate(nullptr,320,180,kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,nullptr,&pixel),"jitter pixel");
            CVPixelBufferLockBaseAddress(pixel,0);
            for(size_t plane=0;plane<2;++plane) memset(CVPixelBufferGetBaseAddressOfPlane(pixel,plane),128,
                CVPixelBufferGetBytesPerRowOfPlane(pixel,plane)*CVPixelBufferGetHeightOfPlane(pixel,plane));
            CVPixelBufferUnlockBaseAddress(pixel,0);
            for(int64_t t:{int64_t(0),int64_t(180450)}) {
                check(encoder.encode(pixel,CMTimeMake(t,90000),CMTimeMake(1500,90000)),"jitter encode");
                encoder.complete();
            }
            CVPixelBufferRelease(pixel); sink->close();
            check(await([&]{return sink->drained();}) && video.size()==2,"jitter video drain");
            unsigned emitted=0;
            live::Segmenter mux([&](live::OwnedSegment seg) {
                ++emitted;
                check(seg->start90k==0 && seg->end90k==180450,"actual 2.005 second IDR cut");
                bool ok=p->post(seg);
                printf("jitter: ticks=%lld running=%d accepted=%llu\n",(long long)(seg->end90k-seg->start90k),
                    p->stats().running,(unsigned long long)p->stats().accepted);
                return ok;
            });
            check(mux.consume(video[0])==clip::Result::consumed,"first IDR");
            clip::AACEncoder audio; check(!audio.status,"jitter AAC");
            std::vector<float> pcm(48000*3*2);
            check(audio.encode(pcm.data(),pcm.size()/2,true,[&](clip::OwnedPacket packet) {
                check(mux.consume(packet)==clip::Result::consumed,"jitter audio watermark");
            }),"jitter audio encode");
            check(mux.consume(video[1])==clip::Result::consumed && emitted==1,"jitter segment emitted");
            check(await([&]{return p->stats().playlists==1 || !p->stats().running;}),"jitter completion");
            check(p->stats().running && p->stats().posted==1,"jitter segment posts");
            p->stop(); drained(p); return 0;
        }
        if(mode=="oversize") {
            bool continued=p->post(segment(0,live::Poster::maxBytes+188));
            auto s=p->stats();
            printf("oversize: running=%d dropped=%llu accepted=%llu\n",s.running,(unsigned long long)s.dropped,(unsigned long long)s.accepted);
            check(continued && s.running && s.dropped==1 && !s.accepted && !s.bytes,"oversize dropped without stop");
            check(p->post(segment()),"post after oversized segment");
            check(await([&]{return p->stats().playlists==1;}),"posting recovers after drop");
            p->stop(); drained(p); return 0;
        }
        if(mode=="queued" || mode=="bytes") {
            bool queued=mode=="queued";
            size_t bytes=queued?live::Poster::maxBytes/4:live::Poster::maxBytes/16*9;
            unsigned count=queued?4:1;
            for(unsigned i=0;i<count;++i) check(p->post(segment(i,bytes,45000)),"bounded admission");
            check(await([&]{return createdSessions.load()==1;}),"request enters flight");
            check(p->post(segment(count,bytes,45000)),"overflow callback continues");
            auto s=p->stats();
            printf("bound: accepted=%llu dropped=%llu bytes=%zu ticks=%lld running=%d\n",
                (unsigned long long)s.accepted,(unsigned long long)s.dropped,s.peakBytes,(long long)s.peakTicks,s.running);
            check(s.running && s.dropped==1 && s.accepted==count && s.peakBytes==count*bytes && s.peakTicks==count*45000,
                "queue byte bound includes in-flight; overflow only drops");
            p->stop(); drained(p); captureHealthy(p); return 0;
        }
        if(mode=="large-body") {
            check(p->post(segment()),"large response admission");
            check(await([&]{return !p->stats().running || p->stats().posted;}),"large response completion");
            drained(p);
            auto s=p->stats();
            printf("response: received=%zu limit=%zu posted=%llu failed=%llu reason=%d\n",receivedResponseBytes.load(),
                live::Poster::maxResponseBytes,(unsigned long long)s.posted,(unsigned long long)s.failed,(int)s.error);
            check(!s.running && s.error==live::StreamError::responseTooLarge && !s.posted && s.failed==1,
                "large 200 body is a failed post");
            check(receivedResponseBytes<262144,"large body cancelled before fully read");
            captureHealthy(p); return 0;
        }
        check(p->post(segment()),"failure fixture admission");
        auto began=std::chrono::steady_clock::now();
        check(await([&]{return !p->stats().running;}),"failed request stops live");
        double elapsed=std::chrono::duration<double>(std::chrono::steady_clock::now()-began).count();
        auto s=p->stats();
        printf("failure: reason=%d seconds=%.3f peak_bytes=%zu peak_ticks=%lld\n",(int)s.error,elapsed,s.peakBytes,(long long)s.peakTicks);
        if(mode=="deadline") {
            check(s.error==live::StreamError::deadline && elapsed<2,"absolute deadline cancels stalled request");
            drained(p,0.6);
            check(p->stats().cancelled>=1,"deadline cancels task");
        } else if(mode=="playlist-stall") {
            // The segment response consumes 280 ms of the single 400 ms budget.
            // Allow 150 ms for timer delivery/polling; a fresh playlist budget
            // would stop no earlier than 680 ms and must fail this 550 ms limit.
            check(s.posted==1 && s.playlists==0 && startedRequests==2,"deadline reaches stalled playlist");
            check(s.error==live::StreamError::deadline && elapsed<options.deadlineSeconds+0.15,
                "playlist shares absolute deadline");
        }
        else if(mode=="unauthorized" || mode=="playlist401") check(s.error==live::StreamError::keyRejected && s.httpCode==401,"401 is a distinct key rejection");
        else if(mode=="http500") check(s.error==live::StreamError::http && s.httpCode==500 && s.retries==2 && elapsed<3,"500 retries bounded inside deadline");
        else if(mode=="redirect") check(s.error==live::StreamError::redirect && s.httpCode==307,"redirect refused without forwarding key");
        else if(mode=="http") check(s.error==live::StreamError::http && s.httpCode==503,"HTTP status stops live");
        else check(s.error==live::StreamError::transport,"disconnect or untrusted TLS stops live");
        drained(p); captureHealthy(p);
        char text[80]; livestream_error_text(text,sizeof text);
        const char *expected=mode=="http500"?"Error: YouTube 500":mode=="http"?"Error: YouTube 503":
            mode=="redirect"?"Error: YouTube 307":mode=="playlist-stall" || mode=="deadline"?"Error: no response":
            mode=="unauthorized" || mode=="playlist401"?"Error: key rejected":"Error: network";
        check(!strcmp(text,expected),"terminal reason survives capture detach");
        check(noteCount("live: error kind=")>0,"error kind/code session note");
        return 0;
    }
}
