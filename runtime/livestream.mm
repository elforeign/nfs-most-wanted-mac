#include "livestream.h"
#import <Foundation/Foundation.h>
#ifdef LIVESTREAM_TESTING
#import <Security/Security.h>
#endif
#include <deque>
#include <mutex>
#include <condition_variable>
#include <chrono>
#include <algorithm>
#include <cstdio>
extern "C" void report_note(const char *fmt, ...);

@interface NFSSegmentSession : NSObject <NSURLSessionDataDelegate> {
@public
    std::weak_ptr<live::StreamState> state;
}
@end

namespace live {
static void erase(void *bytes,size_t size) {
    volatile unsigned char *p=(volatile unsigned char *)bytes;
    while(size--) *p++=0;
}
// Validate UTF-8 scalars without materializing a key-bearing Foundation string.
static bool validKey(const std::string& key) {
    if(key.empty() || key.size()>512) return false;
    for(size_t i=0;i<key.size();) {
        unsigned char c=key[i++]; uint32_t scalar=c, minimum=0; unsigned more=0;
        if(c>=0xc2 && c<=0xdf) { scalar=c&31; more=1; minimum=0x80; }
        else if(c>=0xe0 && c<=0xef) { scalar=c&15; more=2; minimum=0x800; }
        else if(c>=0xf0 && c<=0xf4) { scalar=c&7; more=3; minimum=0x10000; }
        else if(c>=0x80) return false;
        if(i+more>key.size()) return false;
        while(more--) {
            unsigned char next=key[i++]; if((next&0xc0)!=0x80) return false;
            scalar=(scalar<<6)|(next&63);
        }
        if(scalar<minimum || scalar>0x10ffff || (scalar>=0xd800 && scalar<=0xdfff) ||
           [NSCharacterSet.controlCharacterSet longCharacterIsMember:scalar]) return false;
    }
    return true;
}
static const char *errorKind(StreamError error) {
    switch(error) {
        case StreamError::keyRejected: return "key_rejected";
        case StreamError::http: return "http";
        case StreamError::deadline: return "deadline";
        case StreamError::encoder: return "encoder";
        case StreamError::capture: return "capture";
        case StreamError::redirect: return "redirect";
        case StreamError::responseTooLarge: return "response_too_large";
        case StreamError::overload: return "overload";
        default: return "network";
    }
}
struct StreamState : std::enable_shared_from_this<StreamState> {
    mutable std::mutex lock;
    StreamStats counters;
    std::deque<OwnedSegment> pending;
    OwnedSegment sending;
    struct Entry { uint64_t sequence; int64_t duration; NSString *name; };
    std::deque<Entry> window; // two previously acknowledged entries plus this segment
    NSString *prefix, *sendingName;
    uint64_t nextSequence=0;
    bool playlist=false, stopLogged=false;
    unsigned attempts=0;
    NSInteger responseCode=0;
    NSData *playlistBody=nil, *acknowledgedBody=nil;
    std::condition_variable completion;
    bool finishing=false, finished=false; // guarded by lock; one completion for all callers
    bool finalPlaylist=false; // set on work, cross-queue reads under lock
    dispatch_source_t finishTimer=nullptr;
    std::chrono::steady_clock::time_point drainUntil;
    unsigned char keyBytes[512]={0};
    size_t keySize=0;
    bool taskCancelled=false;
    NSURLComponents *base;
    dispatch_queue_t work;
    dispatch_source_t timer=nullptr;
    NSURLSession *session=nil;
    NSURLSessionDataTask *task=nil;
    std::shared_ptr<StreamState> inFlight; // survive UI detachment through cancellation accounting/logging
    double deadline=Poster::deadlineSeconds;
    size_t responseBytes=0;
#ifdef LIVESTREAM_TESTING
    NSData *anchor=nil;
#endif
    StreamState(const std::string& k,NSURLComponents *b):prefix(NSUUID.UUID.UUIDString),base(b),
        work(dispatch_queue_create("live.https",DISPATCH_QUEUE_SERIAL)) {
        memcpy(keyBytes,k.data(),k.size()); keySize=k.size();
        dispatch_queue_set_specific(work,this,this,nullptr);
        counters.running=true;
    }
    ~StreamState() { erase(keyBytes,sizeof keyBytes); }
    void subtract(const OwnedSegment& s) {
        counters.bytes-=s->bytes.size(); counters.ticks-=s->end90k-s->start90k;
    }
    void stop(StreamError reason,int code=0,const char *detail=nullptr) {
        {
            std::lock_guard<std::mutex> guard(lock);
            if(counters.running || finishing) {
                if(finishing) {
                    report_note("live: finalization incomplete kind=%s",reason==StreamError::stopped?"aborted":errorKind(reason));
                    if(finalPlaylist) ++counters.failed;
                }
                finishing=false;
                counters.running=false; counters.error=reason; counters.httpCode=code; counters.reason=detail;
                if(reason!=StreamError::stopped) {
                    if(detail && code) report_note("live: error kind=%s code=%d reason=%s",errorKind(reason),code,detail);
                    else if(detail) report_note("live: error kind=%s reason=%s",errorKind(reason),detail);
                    else report_note("live: error kind=%s code=%d",errorKind(reason),code);
                }
                for(auto &s:pending) subtract(s);
                pending.clear();
            }
        }
        auto self=shared_from_this();
        // Serialize with request construction, but never dispatch_sync onto self.
        if(dispatch_get_specific(this)==this) cancel();
        else dispatch_sync(work, ^{ self->cancel(); });
    }
    void finish() {
        // Callbacks must be free to run. Queue reentry is an unsafe join: abort.
        if(dispatch_get_specific(this)==this) { stop(StreamError::stopped); return; }
        bool begin=false;
        {
            std::lock_guard<std::mutex> guard(lock);
            if(!finished && !finishing && counters.running) {
                counters.running=false; finishing=true; begin=true;
                drainUntil=std::chrono::steady_clock::now()+std::chrono::seconds(3);
            }
        }
        auto self=shared_from_this();
        if(begin) dispatch_async(work, ^{ self->beginFinish(); });
        else dispatch_sync(work, ^{
            bool active;
            { std::lock_guard<std::mutex> guard(self->lock); active=self->finishing; }
            if(!active) self->cancel();
        });
        std::unique_lock<std::mutex> guard(lock);
        completion.wait(guard,[&]{ return finished; });
    }
    void finishDeadline(double seconds) {
        if(finishTimer) dispatch_source_cancel(finishTimer);
        finishTimer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,work);
        std::weak_ptr<StreamState> weak=shared_from_this();
        dispatch_source_set_timer(finishTimer,dispatch_time(DISPATCH_TIME_NOW,
            (int64_t)(std::max(0.0,seconds)*NSEC_PER_SEC)),DISPATCH_TIME_FOREVER,0);
        dispatch_source_set_event_handler(finishTimer,^{ if(auto s=weak.lock()) s->stop(StreamError::deadline); });
        dispatch_resume(finishTimer);
    }
    void beginFinish() {
        {
            std::lock_guard<std::mutex> guard(lock);
            if(!finishing || finalPlaylist) return;
        }
        finishDeadline(std::chrono::duration<double>(drainUntil-std::chrono::steady_clock::now()).count());
        pump();
    }
    void endPlaylist() {
        {
            std::lock_guard<std::mutex> guard(lock);
            if(!finishing || finalPlaylist) return;
        }
        if(!acknowledgedBody) {
            // Empty streams never create a session or request solely for EOS.
            { std::lock_guard<std::mutex> guard(lock); finishing=false; counters.error=StreamError::stopped; }
            cancel(); return;
        }
        NSMutableData *body=[acknowledgedBody mutableCopy];
        const char end[]="#EXT-X-ENDLIST\n";
        [body appendBytes:end length:sizeof end-1];
        playlistBody=body; playlist=true; attempts=0;
        { std::lock_guard<std::mutex> guard(lock); finalPlaylist=true; }
        finishDeadline(3); // fixed phase deadline; retries and joiners never reset it
        send();
    }
    void logStop() {
        if(stopLogged || task) return;
        StreamStats s;
        { std::lock_guard<std::mutex> guard(lock); s=counters; }
        if(s.running) return;
        stopLogged=true;
        report_note("live: Stop accepted=%llu posted=%llu playlists=%llu cancelled=%llu dropped=%llu failed=%llu retries=%llu bytes=%zu peak_bytes=%zu ticks=%lld peak_ticks=%lld finals=%llu finalized=%d",
            (unsigned long long)s.accepted,(unsigned long long)s.posted,(unsigned long long)s.playlists,
            (unsigned long long)s.cancelled,(unsigned long long)s.dropped,(unsigned long long)s.failed,
            (unsigned long long)s.retries,s.bytes,s.peakBytes,(long long)s.ticks,(long long)s.peakTicks,(unsigned long long)s.finalPlaylists,s.finalized);
    }
    void cancel() {
        if(finishTimer) { dispatch_source_cancel(finishTimer); finishTimer=nullptr; }
        if(timer) { dispatch_source_cancel(timer); timer=nullptr; }
        if(task) {
            if(!taskCancelled) {
                taskCancelled=true;
                { std::lock_guard<std::mutex> guard(lock); ++counters.cancelled; }
                [task cancel];
            }
        }
        {
            std::lock_guard<std::mutex> guard(lock);
            if(sending) { ++counters.failed; subtract(sending); sending.reset(); }
        }
        [session invalidateAndCancel]; session=nil;
        erase(keyBytes,sizeof keyBytes); keySize=0;
        playlistBody=nil; acknowledgedBody=nil; window.clear();
        logStop();
        { std::lock_guard<std::mutex> guard(lock); finishing=false; finished=true; }
        completion.notify_all();
    }
    void pump() {
        {
            std::lock_guard<std::mutex> guard(lock);
            if((!counters.running && !finishing) || sending || finalPlaylist) return;
            if(pending.empty()) {
                if(finishing) { auto self=shared_from_this(); dispatch_async(work, ^{ self->endPlaylist(); }); }
                return;
            }
            sending=std::move(pending.front()); pending.pop_front();
        }
        playlist=false; attempts=0;
        sendingName=[NSString stringWithFormat:@"%@-%llu.ts",prefix,(unsigned long long)nextSequence];
        std::weak_ptr<StreamState> weak=shared_from_this();
        timer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,work);
        // One absolute budget covers the segment, its playlist and all retries.
        dispatch_source_set_timer(timer,dispatch_time(DISPATCH_TIME_NOW,(int64_t)(deadline*NSEC_PER_SEC)),DISPATCH_TIME_FOREVER,0);
        dispatch_source_set_event_handler(timer,^{ if(auto s=weak.lock()) s->stop(StreamError::deadline); });
        dispatch_resume(timer);
        send();
    }
    void send() {
        { std::lock_guard<std::mutex> guard(lock); if(!counters.running && !finishing) return; }
        @autoreleasepool {
            if(!session) {
                auto delegate=[NFSSegmentSession new]; delegate->state=shared_from_this();
                auto configuration=[NSURLSessionConfiguration ephemeralSessionConfiguration];
                configuration.URLCache=nil; configuration.HTTPCookieStorage=nil;
                configuration.URLCredentialStorage=nil; configuration.HTTPShouldSetCookies=NO;
                configuration.requestCachePolicy=NSURLRequestReloadIgnoringLocalCacheData;
                configuration.HTTPMaximumConnectionsPerHost=1;
                configuration.HTTPShouldUsePipelining=NO;
                configuration.timeoutIntervalForRequest=deadline+1;
                configuration.timeoutIntervalForResource=deadline+1;
#ifdef LIVESTREAM_TESTING
                configuration.connectionProxyDictionary=@{};
#endif
                auto callbacks=[NSOperationQueue new]; callbacks.maxConcurrentOperationCount=1;
                callbacks.underlyingQueue=work;
                session=[NSURLSession sessionWithConfiguration:configuration delegate:delegate delegateQueue:callbacks];
            }
            // Only this bounded, erasable buffer holds our encoded cid. NSURL
            // and NSURLRequest copy internally; their storage is not writable.
            char address[2048]={0};
            size_t used=0;
            const char *root=base.URL.absoluteString.UTF8String;
            size_t rootSize=strlen(root);
            const char *name=(playlist?@"live.m3u8":sendingName).UTF8String;
            size_t nameSize=strlen(name);
            if(rootSize+5+3*keySize+13+nameSize>=sizeof address) {
                stop(StreamError::transport); return;
            }
            memcpy(address,root,rootSize); used=rootSize;
            memcpy(address+used,"?cid=",5); used+=5;
            const char hex[]="0123456789ABCDEF";
            for(size_t i=0;i<keySize;++i) {
                unsigned char c=keyBytes[i];
                if((c>='a' && c<='z') || (c>='A' && c<='Z') ||
                   (c>='0' && c<='9') || c=='-' || c=='_' || c=='.' || c=='~') address[used++]=c;
                else { address[used++]='%'; address[used++]=hex[c>>4]; address[used++]=hex[c&15]; }
            }
            const char suffix[]="&copy=0&file=";
            memcpy(address+used,suffix,sizeof suffix-1); used+=sizeof suffix-1;
            memcpy(address+used,name,nameSize); used+=nameSize;
            NSMutableURLRequest *request;
            @autoreleasepool {
                NSString *text=[[NSString alloc] initWithBytes:address length:used encoding:NSASCIIStringEncoding];
                NSURL *url=[[NSURL alloc] initWithString:text];
                request=[NSMutableURLRequest requestWithURL:url];
            }
            erase(address,sizeof address);
            request.HTTPMethod=@"POST"; request.timeoutInterval=deadline+1;
            [request setValue:@"NFS / Mac / 1.0" forHTTPHeaderField:@"User-Agent"];
            [request setValue:playlist?@"application/vnd.apple.mpegurl":@"video/MP2T" forHTTPHeaderField:@"Content-Type"];
            if(playlist) request.HTTPBody=playlistBody;
            else {
                // Foundation shares the immutable payload, including cancellation.
                auto body=sending;
                request.HTTPBody=[[NSData alloc] initWithBytesNoCopy:(void*)body->bytes.data()
                    length:body->bytes.size() deallocator:^(void *,NSUInteger) { (void)body; }];
            }
            responseBytes=0; responseCode=0;
            taskCancelled=false;
            task=[session dataTaskWithRequest:request];
            inFlight=shared_from_this();
            [task resume];
        }
    }
    void makePlaylist() {
        window.push_back({nextSequence,sending->end90k-sending->start90k,sendingName});
        NSMutableString *text=[NSMutableString stringWithFormat:
            @"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:5\n#EXT-X-MEDIA-SEQUENCE:%llu\n",
            (unsigned long long)window.front().sequence];
        for(const auto &e:window) [text appendFormat:@"#EXTINF:%.6f,\n%@\n",e.duration/90000.0,e.name];
        playlistBody=[text dataUsingEncoding:NSUTF8StringEncoding];
    }
    void complete(NSURLSessionTask *finished,NSError *error) {
        if(finished!=task) return;
        task=nil;
        inFlight.reset(); // the delegate holds this state for the completion call
        bool running;
        { std::lock_guard<std::mutex> guard(lock); running=counters.running || finishing; }
        if(error && running) stop(StreamError::transport,(int)responseCode);
        else if(running && responseCode==500) {
            if(attempts<2) {
                unsigned retry=++attempts;
                { std::lock_guard<std::mutex> guard(lock); ++counters.retries; }
                report_note("live: error kind=http code=500 retry=%u",retry);
                auto self=shared_from_this();
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)(0.1*(1u<<(retry-1))*NSEC_PER_SEC)),work,^{ self->send(); });
                return;
            }
            stop(StreamError::http,500);
        } else if(running && responseCode>=200 && responseCode<300) {
            if(finalPlaylist) {
                { std::lock_guard<std::mutex> guard(lock);
                  ++counters.playlists; ++counters.finalPlaylists; counters.finalized=true;
                  counters.error=StreamError::stopped; finishing=false; }
                report_note("live: final playlist accepted code=%d",(int)responseCode);
                cancel(); return;
            }
            if(!playlist) {
                {
                    std::lock_guard<std::mutex> guard(lock);
                    if(++counters.posted==1) report_note("live: first segment accepted code=%d",(int)responseCode);
                }
                makePlaylist(); playlist=true; attempts=0; send(); return;
            }
            {
                std::lock_guard<std::mutex> guard(lock);
                if(++counters.playlists==1) report_note("live: first playlist accepted code=%d",(int)responseCode);
            }
            acknowledgedBody=playlistBody; // snapshot before trimming the acknowledged tail
            while(window.size()>2) window.pop_front();
            ++nextSequence; playlistBody=nil;
        } else if(running) stop(StreamError::transport);
        if(timer) { dispatch_source_cancel(timer); timer=nullptr; }
        {
            std::lock_guard<std::mutex> guard(lock);
            if(sending) {
                if(!counters.running && !finishing) ++counters.failed;
                subtract(sending); sending.reset();
            }
            running=counters.running || finishing;
        }
        if(running) pump(); else cancel();
    }
};

// Armed only for the duration of livestream_start_fake's own start.
static int fakePort=0;
static const char fakeKey[]="fake-ingest-key";
Poster::Poster(std::shared_ptr<StreamState> s):state(std::move(s)) {}
Poster::~Poster() { stop(); }
std::shared_ptr<Poster> Poster::create(const std::string& key
#ifdef LIVESTREAM_TESTING
    , const StreamTestOptions& options
#endif
) {
    if(!validKey(key)) return {};
    @autoreleasepool {
        NSString *address=@"https://a.upload.youtube.com/http_upload_hls";
#ifdef LIVESTREAM_TESTING
        if(!options.baseURL.empty()) address=[NSString stringWithUTF8String:options.baseURL.c_str()];
        NSString *fake=nil;
#else
        // Test only: the fixed key of livestream_start_fake goes to the armed loopback fake ingest; any other key,
        // a real one included, always goes to YouTube.
        NSString *fake=nil;
        if(fakePort>0 && fakePort<=65535 && key==fakeKey) fake=[NSString stringWithFormat:@"http://127.0.0.1:%d/http_upload_hls",fakePort];
        if(fake) address=fake;
#endif
        auto base=[NSURLComponents componentsWithString:address];
        bool production=[base.host isEqualToString:@"a.upload.youtube.com"] && (!base.port || base.port.integerValue==443);
        bool loopback=fake && [base.scheme isEqualToString:@"http"] && [base.host isEqualToString:@"127.0.0.1"] && base.port.integerValue>0;
        bool allowed=production;
        allowed=allowed || loopback;
#ifdef LIVESTREAM_TESTING
        allowed=allowed || ([base.host isEqualToString:@"127.0.0.1"] && base.port.integerValue>0 && base.port.integerValue<=65535);
        if(options.deadlineSeconds<=0 || options.deadlineSeconds>Poster::deadlineSeconds) return {};
#endif
        if((!loopback && ![base.scheme isEqualToString:@"https"]) || !allowed || base.user || base.password || base.query || base.fragment ||
            ![base.path isEqualToString:@"/http_upload_hls"] || !base.URL) return {};
        auto state=std::make_shared<StreamState>(key,base);
#ifdef LIVESTREAM_TESTING
        state->deadline=options.deadlineSeconds;
        if(!production && !options.anchorDER.empty()) state->anchor=[NSData dataWithBytes:options.anchorDER.data() length:options.anchorDER.size()];
#endif
        report_note("live: Go live");
        return std::shared_ptr<Poster>(new Poster(std::move(state)));
    }
}
bool Poster::post(OwnedSegment segment) {
    auto s=state;
    bool admitted=false;
    {
        std::lock_guard<std::mutex> guard(s->lock);
        if(!s->counters.running) return false;
        int64_t duration=segment && segment->start90k>=0 && segment->end90k>segment->start90k ? segment->end90k-segment->start90k : 0;
        if(segment && !segment->bytes.empty() && duration>0 &&
            segment->bytes.size()<=maxBytes-s->counters.bytes && duration<=450000 &&
            s->pending.size()+(s->sending?1:0)<5) {
            s->pending.push_back(std::move(segment));
            s->counters.bytes+=s->pending.back()->bytes.size(); s->counters.ticks+=duration;
            s->counters.peakBytes=std::max(s->counters.peakBytes,s->counters.bytes);
            s->counters.peakTicks=std::max(s->counters.peakTicks,s->counters.ticks);
            ++s->counters.accepted; admitted=true;
        } else { ++s->counters.dropped; }
    }
    if(admitted) dispatch_async(s->work, ^{ s->pump(); });
    // Dropping an unadmitted segment must not retire the segmenter callback.
    return true;
}
void Poster::stop() { state->stop(StreamError::stopped); }
void Poster::finish() { state->finish(); }
void Poster::fail(StreamError reason,int code,const char *detail) { state->stop(reason,code,detail); }
StreamStats Poster::stats() const { std::lock_guard<std::mutex> guard(state->lock); return state->counters; }
namespace {
std::mutex streamLock;
std::shared_ptr<Poster> currentStream, stoppingStream;
StreamStats lastStream;
}
bool startStream(const std::string& key) {
    std::lock_guard<std::mutex> guard(streamLock);
    if(stoppingStream || (currentStream && currentStream->stats().running)) return false;
    auto next=Poster::create(key);
    currentStream=next; lastStream={};
    return !!next;
}
void stopStream() {
    std::shared_ptr<Poster> old;
    {
        std::lock_guard<std::mutex> guard(streamLock);
        if(currentStream) {
            stoppingStream=std::move(currentStream);
        }
        old=stoppingStream;
    }
    // Join one bounded completion outside streamLock so callbacks remain free.
    if(old) old->finish();
    {
        std::lock_guard<std::mutex> guard(streamLock);
        if(old) lastStream=old->stats();
        if(stoppingStream==old) stoppingStream.reset();
    }
}
Segmenter::Output streamOutput() {
    std::shared_ptr<Poster> poster;
    { std::lock_guard<std::mutex> guard(streamLock); poster=currentStream; }
    return [poster](OwnedSegment segment) {
        return !poster || poster->post(std::move(segment));
    };
}
}

int livestream_start_bytes(const void *bytes,size_t size) {
    if(!bytes || !size || size>512) return 0;
    std::string key((const char *)bytes,size);
    bool ok=live::startStream(key);
    volatile char *p=&key[0];
    for(size_t i=0;i<key.size();++i) p[i]=0;
    return ok;
}
int livestream_start_fake(int port) {
    if(port<=0 || port>65535) return 0;
    live::fakePort=port;
    bool ok=live::startStream(live::fakeKey);
    live::fakePort=0;
    return ok;
}
void livestream_stop(void) { live::stopStream(); }
int livestream_status(void) {
    std::lock_guard<std::mutex> guard(live::streamLock);
    if(!live::currentStream) return 0;
    return live::currentStream->stats().running?1:2;
}
void livestream_fail_reason(int kind,int code,const char *reason) {
    std::shared_ptr<live::Poster> poster;
    { std::lock_guard<std::mutex> guard(live::streamLock); poster=live::currentStream?live::currentStream:live::stoppingStream; }
    if(poster) poster->fail(kind==LIVESTREAM_FAILURE_CAPTURE?live::StreamError::capture:live::StreamError::encoder,code,reason);
    live::stopStream();
}
void livestream_fail(int kind) { livestream_fail_reason(kind,0,nullptr); }
void livestream_error_text(char *buf,size_t size) {
    live::StreamStats s;
    { std::lock_guard<std::mutex> guard(live::streamLock);
      s=live::currentStream?live::currentStream->stats():live::lastStream; }
    const char *text="";
    switch(s.error) {
        case live::StreamError::keyRejected: text="Error: key rejected"; break;
        case live::StreamError::http: case live::StreamError::redirect:
            snprintf(buf,size,"Error: YouTube %d",s.httpCode); return;
        case live::StreamError::deadline: text="Error: no response"; break;
        case live::StreamError::encoder: case live::StreamError::capture: {
            const char *kind=s.error==live::StreamError::encoder?"encoder":"capture";
            if(s.httpCode) snprintf(buf,size,"Error: %s %d",kind,s.httpCode);
            else if(s.reason) snprintf(buf,size,"Error: %s (%s)",kind,s.reason);
            else snprintf(buf,size,"Error: %s",kind);
            return;
        }
        case live::StreamError::none: case live::StreamError::stopped: break;
        default: text="Error: network"; break;
    }
    snprintf(buf,size,"%s",text);
}
@implementation NFSSegmentSession
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task
    willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request
    completionHandler:(void (^)(NSURLRequest *))completion {
    (void)session; (void)task; (void)response; (void)request;
    if(auto s=state.lock()) s->stop(live::StreamError::redirect,(int)response.statusCode);
    completion(nil);
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task
    didReceiveResponse:(NSURLResponse *)response completionHandler:(void (^)(NSURLSessionResponseDisposition))completion {
    (void)session; (void)task;
    NSInteger code=[(NSHTTPURLResponse*)response statusCode];
    if(auto s=state.lock()) {
        if(task!=s->task) { completion(NSURLSessionResponseCancel); return; }
        s->responseCode=code;
        if((code<200 || code>=300) && code!=500) {
            s->stop(code==401?live::StreamError::keyRejected:live::StreamError::http,(int)code);
            completion(NSURLSessionResponseCancel); return;
        }
    }
    completion(NSURLSessionResponseAllow);
}
- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {
    (void)session;
    if(auto s=state.lock()) {
        if(task!=s->task) return;
        // Delivery is serial on work. Never buffer or stringify response bytes.
        if(data.length>live::Poster::maxResponseBytes-s->responseBytes) {
            s->stop(live::StreamError::responseTooLarge,(int)s->responseCode);
            [task cancel];
        } else s->responseBytes+=data.length;
    }
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error {
    (void)session;
    if(auto s=state.lock()) s->complete(task,error); // Never stringify an NSError containing a URL.
}
#ifdef LIVESTREAM_TESTING
- (void)URLSession:(NSURLSession *)session didReceiveChallenge:(NSURLAuthenticationChallenge *)challenge
    completionHandler:(void (^)(NSURLSessionAuthChallengeDisposition,NSURLCredential *))completion {
    (void)session;
    auto s=state.lock();
    if(s && s->anchor && [challenge.protectionSpace.host isEqualToString:@"127.0.0.1"] &&
        [challenge.protectionSpace.authenticationMethod isEqualToString:NSURLAuthenticationMethodServerTrust]) {
        auto trust=challenge.protectionSpace.serverTrust;
        auto cert=SecCertificateCreateWithData(nullptr,(__bridge CFDataRef)s->anchor);
        bool valid=false;
        if(trust && cert) {
            NSArray *anchors=@[(__bridge id)cert];
            valid=SecTrustSetAnchorCertificates(trust,(__bridge CFArrayRef)anchors)==errSecSuccess &&
                SecTrustSetAnchorCertificatesOnly(trust,true)==errSecSuccess && SecTrustEvaluateWithError(trust,nullptr);
        }
        if(cert) CFRelease(cert);
        completion(valid?NSURLSessionAuthChallengeUseCredential:NSURLSessionAuthChallengeCancelAuthenticationChallenge,
            valid?[NSURLCredential credentialForTrust:trust]:nil);
    } else completion(NSURLSessionAuthChallengePerformDefaultHandling,nil);
}
#endif
@end
