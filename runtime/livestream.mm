#include "livestream.h"
#import <Foundation/Foundation.h>
#ifdef LIVESTREAM_TESTING
#import <Security/Security.h>
#endif
#include <deque>
#include <mutex>
#include <algorithm>
#include <cstdio>
extern "C" void report_note(const char *fmt, ...);

@interface NFSSegmentSession : NSObject <NSURLSessionDataDelegate> {
@public
    std::weak_ptr<live::StreamState> state;
}
@end

namespace live {
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
    NSData *playlistBody=nil;
    NSString *key;
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
    StreamState(NSString *k,NSURLComponents *b):prefix(NSUUID.UUID.UUIDString),key(k),base(b),
        work(dispatch_queue_create("live.https",DISPATCH_QUEUE_SERIAL)) { counters.running=true; }
    void subtract(const OwnedSegment& s) {
        counters.bytes-=s->bytes.size(); counters.ticks-=s->end90k-s->start90k;
    }
    void stop(StreamError reason,int code=0,const char *detail=nullptr) {
        {
            std::lock_guard<std::mutex> guard(lock);
            if(!counters.running) return;
            counters.running=false; counters.error=reason; counters.httpCode=code; counters.reason=detail;
            if(reason!=StreamError::stopped) {
                if(detail && code) report_note("live: error kind=%s code=%d reason=%s",errorKind(reason),code,detail);
                else if(detail) report_note("live: error kind=%s reason=%s",errorKind(reason),detail);
                else report_note("live: error kind=%s code=%d",errorKind(reason),code);
            }
            for(auto &s:pending) subtract(s);
            pending.clear();
        }
        auto self=shared_from_this();
        dispatch_async(work, ^{ self->cancel(); });
    }
    void logStop() {
        if(stopLogged || task) return;
        StreamStats s;
        { std::lock_guard<std::mutex> guard(lock); s=counters; }
        if(s.running) return;
        stopLogged=true;
        report_note("live: Stop accepted=%llu posted=%llu playlists=%llu cancelled=%llu dropped=%llu failed=%llu retries=%llu bytes=%zu peak_bytes=%zu ticks=%lld peak_ticks=%lld",
            (unsigned long long)s.accepted,(unsigned long long)s.posted,(unsigned long long)s.playlists,
            (unsigned long long)s.cancelled,(unsigned long long)s.dropped,(unsigned long long)s.failed,
            (unsigned long long)s.retries,s.bytes,s.peakBytes,(long long)s.ticks,(long long)s.peakTicks);
    }
    void cancel() {
        if(timer) { dispatch_source_cancel(timer); timer=nullptr; }
        if(task) {
            { std::lock_guard<std::mutex> guard(lock); ++counters.cancelled; }
            [task cancel];
        } else {
            std::lock_guard<std::mutex> guard(lock);
            if(sending) { subtract(sending); sending.reset(); }
        }
        [session invalidateAndCancel]; session=nil;
        key=nil; playlistBody=nil; window.clear();
        logStop();
    }
    void pump() {
        {
            std::lock_guard<std::mutex> guard(lock);
            if(!counters.running || sending || pending.empty()) return;
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
        { std::lock_guard<std::mutex> guard(lock); if(!counters.running) return; }
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
            NSURLComponents *url=[base copy];
            url.queryItems=@[[NSURLQueryItem queryItemWithName:@"cid" value:key],
                [NSURLQueryItem queryItemWithName:@"copy" value:@"0"],
                [NSURLQueryItem queryItemWithName:@"file" value:playlist?@"live.m3u8":sendingName]];
            url.percentEncodedQuery=[url.percentEncodedQuery stringByReplacingOccurrencesOfString:@"+" withString:@"%2B"];
            auto request=[NSMutableURLRequest requestWithURL:url.URL];
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
        { std::lock_guard<std::mutex> guard(lock); running=counters.running; }
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
            while(window.size()>2) window.pop_front();
            ++nextSequence; playlistBody=nil;
        } else if(running) stop(StreamError::transport);
        if(timer) { dispatch_source_cancel(timer); timer=nullptr; }
        {
            std::lock_guard<std::mutex> guard(lock);
            if(!counters.running) ++counters.failed;
            subtract(sending); sending.reset();
            running=counters.running;
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
    if(key.empty()) return {};
    if(key.size()>512) return {};
    @autoreleasepool {
        NSString *text=[[NSString alloc] initWithBytes:key.data() length:key.size() encoding:NSUTF8StringEncoding];
        if(!text || !text.length || [text rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location!=NSNotFound) return {};
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
        auto state=std::make_shared<StreamState>(text,base);
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
void Poster::fail(StreamError reason,int code,const char *detail) { state->stop(reason,code,detail); }
StreamStats Poster::stats() const { std::lock_guard<std::mutex> guard(state->lock); return state->counters; }
namespace {
std::mutex streamLock;
std::shared_ptr<Poster> currentStream;
StreamStats lastStream;
}
bool startStream(const std::string& key) {
    std::lock_guard<std::mutex> guard(streamLock);
    if(currentStream && currentStream->stats().running) return false;
    auto next=Poster::create(key);
    currentStream=next; lastStream={};
    return !!next;
}
void stopStream() {
    std::shared_ptr<Poster> old;
    { std::lock_guard<std::mutex> guard(streamLock); old=std::move(currentStream);
      if(old) lastStream=old->stats(); }
    if(old) old->stop();
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
    { std::lock_guard<std::mutex> guard(live::streamLock); poster=live::currentStream; }
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
