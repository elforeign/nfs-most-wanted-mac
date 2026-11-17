#!/usr/bin/env python3
"""Real NSURLSession to HTTPS on 127.0.0.1 only, without a game or external URL.
Abort controls under four yes processes: 14/20 trials failed before, all in
finish-concurrent (segment/playlist/five/retry: 0/20 each); 0/40 after in all
five cases, with their graceful counterparts also 0/40. Held-response socket
handshake spans Stop's return. Restoring graceful finish in the abort control
is rejected: deadline is not an immediate Stop (1/1 deliberately wrong run).
Whole-process key scan baseline/acceptance: 0/0 matches; skipped poster erase:
1 match in port.poster, DefaultMallocZone allocation size 1280. Scans raw and
percent-encoded UTF-8/UTF-16 across readable VM, with one scanner allocation
excluded. This snapshot does not prove swap/crash-dump/AppKit cleanup.
Early global snapshot control: final ACKs=0 in saved stats vs fixed 1; bytes 752/0.
Graceful completion: reviewed abort cancelled=1/finals=0; fixed held segment,
playlist and five queued segments cancelled=failed=0, exactly one final ACK.
Combined Stop timeout 5.669 s; final retry 2 retries/1 ACK; key residue 0/512 bytes.
Join-deadline reset: 6.673 s FAIL vs 5.669 s PASS; pair reset: 1.244 s FAIL vs 0.233 s PASS.
False ACK on stalled final is rejected (finals=0, finalized=1); healthy finalized=1/finals=1.
Shared drain: five queued items, fixed deadline/cancelled=1/finals=0;
disabled common timer posts five and a final (cancelled=0/finals=1), FAIL.
Concurrent global Stop original/fixed: second premature return 1/0 in both
plain and poster-queue reentry cases (FAIL/PASS); first premature 0/0,
held key scans 1/1, final residue 0/0. Fixed stoppers finish within 2 s of
queue release, including Stop reentry from the poster queue.
Hardening original/fixed: 5/5 owned-buffer scans find 1/0 sentinel matches each;
external Stop returns before cancellation 1/0 times (eventual residue 0/0).
Livefix original/fixed: 7 segments/0 playlists vs 7/7 over two starts;
unique names 6/7, playlist/window/session assertions 7/0 failures. Original
202/500/401, playlist deadline and detached Stop checks fail; fixed all pass.
Count bound original/fixed: 6/5 admitted, 0/1 dropped. Last-poster Stop note 0/1.
Shared 400 ms deadline, segment delayed 280 ms: fixed 411 ms PASS with a
150 ms scheduling margin (550 ms limit). Playlist timer-reset mutation:
696 ms FAIL new check; 701 ms PASS old two-second check on the same fixture.
Original PAT/PMT and >5 s violations are covered by live_seg_test.
Measured: 13 legacy cases + 10 protocol cases; 6/6 ordered pairs on one connection,
0 key/URL/session matches, Off 0 sockets.
Pinned original callbacks: actual 180450-tick segment stops before posting;
fixed accepts/posts 1/1. Oversize original stops; fixed drops 1 then posts 1/1.
Large 200 body: legacy unbounded callback reads 524288 bytes over the segment
and playlist responses (262144 each); fixed cancels at 5120 bytes, posted=0,
failed=1 (4096 budget plus one delivered chunk).
Four queued/in-flight 16 MiB segments fit exactly; fifth drops without stopping.
Uncapped/unaccounted controls admit 2 x 36 MiB; fixed stays <= 64 MiB (byte bound five 4K segments).
Raw/URL logging controls leak 1/12 sentinel matches; fixed emits 0, and full
URLs are redacted before any test log/report. The original leak check passes:
there was no production logger to fix; intentional logging controls fail it.
Recording/replay receive 100/100 after live failure; coupled control gets 1/100.
Session anchors and test URL overrides do not exist in the shipping build.
Legacy compiled faults plus the lost-completion lifetime control fail the same assertions.
"""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import io
import re
import hashlib
import secrets
import time
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import importlib.util

FINAL_ABORT_FRAGMENTS = {'    void stop(StreamError reason': '    void stop(StreamError reason,int code=0,const char *detail=nullptr) {\n        {\n            std::lock_guard<std::mutex> guard(lock);\n            if(counters.running) {\n                counters.running=false; counters.error=reason; counters.httpCode=code; counters.reason=detail;\n                if(reason!=StreamError::stopped) {\n                    if(detail && code) report_note("live: error kind=%s code=%d reason=%s",errorKind(reason),code,detail);\n                    else if(detail) report_note("live: error kind=%s reason=%s",errorKind(reason),detail);\n                    else report_note("live: error kind=%s code=%d",errorKind(reason),code);\n                }\n                for(auto &s:pending) subtract(s);\n                pending.clear();\n            }\n        }\n        auto self=shared_from_this();\n        // Serialize with request construction, but never dispatch_sync onto self.\n        if(dispatch_get_specific(this)==this) cancel();\n        else dispatch_sync(work, ^{ self->cancel(); });\n    }', '    void cancel()': '    void cancel() {\n        if(timer) { dispatch_source_cancel(timer); timer=nullptr; }\n        if(task) {\n            if(!taskCancelled) {\n                taskCancelled=true;\n                { std::lock_guard<std::mutex> guard(lock); ++counters.cancelled; }\n                [task cancel];\n            }\n        } else {\n            std::lock_guard<std::mutex> guard(lock);\n            if(sending) { ++counters.failed; subtract(sending); sending.reset(); }\n        }\n        [session invalidateAndCancel]; session=nil;\n        erase(keyBytes,sizeof keyBytes); keySize=0;\n        playlistBody=nil; window.clear();\n        logStop();\n    }', 'void Poster::stop()': 'void Poster::stop() { state->stop(StreamError::stopped); }'}
FINAL_ABORT_DIGESTS = {'    void stop(StreamError reason': 'da2e7a81edbf4d49f769f72ecfd1d04ba1489f9d96b4ee89a3afa2a1958d25fd', '    void cancel()': 'd70c7e9215d2f2aa8153b7eeada5849fc6b18b1d4e400eb1ac849bab2baba782', 'void Poster::stop()': 'f521e83cea09b6ca8624404611378f4c9222012d77698465f49272b734ed1e9e'}

# Original global Stop detached the poster before its synchronous erasure.
BASE_STOP_STREAM = 'void stopStream() {\n    std::shared_ptr<Poster> old;\n    { std::lock_guard<std::mutex> guard(streamLock); old=std::move(currentStream);\n      if(old) lastStream=old->stats(); }\n    if(old) old->stop();\n}'
BASE_STOP_STREAM_SHA256 = 'd909bfba87d09069289705f6ec0960fd65b42e2b204871681ce13f83b3f79735'

# Original admission/data callbacks, pinned to the reviewed revision.
BASE_POST = 'bool Poster::post(OwnedSegment segment) {\n    auto s=state;\n    bool admitted=false;\n    {\n        std::lock_guard<std::mutex> guard(s->lock);\n        if(!s->counters.running) return false;\n        int64_t duration=segment && segment->start90k>=0 && segment->end90k>segment->start90k ? segment->end90k-segment->start90k : 0;\n        if(segment && !segment->bytes.empty() && duration>0 &&\n            segment->bytes.size()<=maxBytes-s->counters.bytes && duration<=maxTicks-s->counters.ticks) {\n            s->pending.push_back(std::move(segment));\n            s->counters.bytes+=s->pending.back()->bytes.size(); s->counters.ticks+=duration;\n            s->counters.peakBytes=std::max(s->counters.peakBytes,s->counters.bytes);\n            s->counters.peakTicks=std::max(s->counters.peakTicks,s->counters.ticks);\n            ++s->counters.accepted; admitted=true;\n        } else { s->counters.running=false; s->counters.error=StreamError::overload; }\n    }\n    if(admitted) dispatch_async(s->work, ^{ s->pump(); });\n    else s->stop(StreamError::overload);\n    return admitted;\n}\n'
BASE_POST_SHA256 = '7a1823fbe589d9618a605826087d9e3d42ba16756022b3ae7119795ab808eadf'
BASE_DATA = '- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {\n    (void)session; (void)task; (void)data; // Response bodies are never buffered or reported.\n}\n'
BASE_DATA_SHA256 = '107f8d60834f7fe642a86a042f593311545c517b146c85dc77995d5f7f2920b5'

# Original segment-only request/completion and response handling, not repository history.
LIVEFIX_ORIGINAL = {'    void stop(StreamError reason)': '    void stop(StreamError reason) {\n        {\n            std::lock_guard<std::mutex> guard(lock);\n            if(counters.running) { counters.running=false; counters.error=reason; }\n            for(auto &s:pending) subtract(s);\n            pending.clear();\n        }\n        auto self=shared_from_this();\n        dispatch_async(work, ^{ self->cancel(); });\n    }', '    void cancel()': '    void cancel() {\n        if(timer) { dispatch_source_cancel(timer); timer=nullptr; }\n        if(task) {\n            { std::lock_guard<std::mutex> guard(lock); ++counters.cancelled; }\n            [task cancel];\n        }\n        [session invalidateAndCancel]; session=nil;\n        key=nil;\n    }', '    void pump()': '    void pump() {\n        {\n            std::lock_guard<std::mutex> guard(lock);\n            if(!counters.running || sending || pending.empty()) return;\n            sending=std::move(pending.front()); pending.pop_front();\n        }\n        @autoreleasepool {\n            if(!session) {\n                auto delegate=[NFSSegmentSession new]; delegate->state=shared_from_this();\n                auto configuration=[NSURLSessionConfiguration ephemeralSessionConfiguration];\n                configuration.URLCache=nil; configuration.HTTPCookieStorage=nil;\n                configuration.URLCredentialStorage=nil; configuration.HTTPShouldSetCookies=NO;\n                configuration.requestCachePolicy=NSURLRequestReloadIgnoringLocalCacheData;\n                // The dispatch timer is the absolute deadline. Foundation\'s\n                // backup timeout leaves its cancellation callback time to run.\n                configuration.timeoutIntervalForRequest=deadline+1;\n                configuration.timeoutIntervalForResource=deadline+1;\n#ifdef LIVESTREAM_TESTING\n                configuration.connectionProxyDictionary=@{};\n#endif\n                auto callbacks=[NSOperationQueue new]; callbacks.maxConcurrentOperationCount=1;\n                callbacks.underlyingQueue=work;\n                session=[NSURLSession sessionWithConfiguration:configuration delegate:delegate delegateQueue:callbacks];\n            }\n            NSURLComponents *url=[base copy];\n            url.queryItems=@[[NSURLQueryItem queryItemWithName:@"cid" value:key],\n                [NSURLQueryItem queryItemWithName:@"copy" value:@"0"],\n                [NSURLQueryItem queryItemWithName:@"file" value:[NSString stringWithFormat:@"%llu.ts",(unsigned long long)sending->sequence]]];\n            url.percentEncodedQuery=[url.percentEncodedQuery stringByReplacingOccurrencesOfString:@"+" withString:@"%2B"];\n            auto request=[NSMutableURLRequest requestWithURL:url.URL];\n            request.HTTPMethod=@"POST"; request.timeoutInterval=deadline+1;\n            [request setValue:@"video/MP2T" forHTTPHeaderField:@"Content-Type"];\n            // The immutable body shares the segment allocation, including while\n            // Foundation retains it during cancellation. No second payload copy.\n            auto body=sending;\n            request.HTTPBody=[[NSData alloc] initWithBytesNoCopy:(void*)body->bytes.data()\n                length:body->bytes.size() deallocator:^(void *,NSUInteger) { (void)body; }];\n            responseBytes=0;\n            task=[session dataTaskWithRequest:request];\n            std::weak_ptr<StreamState> weak=shared_from_this();\n            timer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,work);\n            dispatch_source_set_timer(timer,dispatch_time(DISPATCH_TIME_NOW,(int64_t)(deadline*NSEC_PER_SEC)),DISPATCH_TIME_FOREVER,0);\n            dispatch_source_set_event_handler(timer,^{ if(auto s=weak.lock()) s->stop(StreamError::deadline); });\n            dispatch_resume(timer);\n            [task resume];\n        }\n    }', '    void complete(NSURLSessionTask *finished,NSError *error)': '    void complete(NSURLSessionTask *finished,NSError *error) {\n        if(finished!=task) return;\n        if(timer) { dispatch_source_cancel(timer); timer=nullptr; }\n        {\n            std::lock_guard<std::mutex> guard(lock);\n            if(error || !counters.running) ++counters.failed;\n            if(error && counters.running) { counters.running=false; counters.error=StreamError::transport; }\n            if(!error && counters.running) ++counters.posted;\n            subtract(sending); sending.reset();\n        }\n        task=nil;\n        bool running;\n        { std::lock_guard<std::mutex> guard(lock); running=counters.running; }\n        if(running) pump(); else stop(StreamError::transport);\n    }', 'response': '- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task\n    didReceiveResponse:(NSURLResponse *)response completionHandler:(void (^)(NSURLSessionResponseDisposition))completion {\n    (void)session; (void)task;\n    NSInteger code=[(NSHTTPURLResponse*)response statusCode];\n    if(code<200 || code>=300) {\n        if(auto s=state.lock()) s->stop(live::StreamError::http);\n        completion(NSURLSessionResponseCancel);\n    } else completion(NSURLSessionResponseAllow);\n}', 'post': 'bool Poster::post(OwnedSegment segment) {\n    auto s=state;\n    bool admitted=false;\n    {\n        std::lock_guard<std::mutex> guard(s->lock);\n        if(!s->counters.running) return false;\n        int64_t duration=segment && segment->start90k>=0 && segment->end90k>segment->start90k ? segment->end90k-segment->start90k : 0;\n        if(segment && !segment->bytes.empty() && duration>0 &&\n            segment->bytes.size()<=maxBytes-s->counters.bytes) {\n            s->pending.push_back(std::move(segment));\n            s->counters.bytes+=s->pending.back()->bytes.size(); s->counters.ticks+=duration;\n            s->counters.peakBytes=std::max(s->counters.peakBytes,s->counters.bytes);\n            s->counters.peakTicks=std::max(s->counters.peakTicks,s->counters.ticks);\n            ++s->counters.accepted; admitted=true;\n        } else { ++s->counters.dropped; }\n    }\n    if(admitted) dispatch_async(s->work, ^{ s->pump(); });\n    // Dropping an unadmitted segment must not retire the segmenter callback.\n    return true;\n}', 'redirect': '- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task\n    willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request\n    completionHandler:(void (^)(NSURLRequest *))completion {\n    (void)session; (void)task; (void)response; (void)request;\n    if(auto s=state.lock()) s->stop(live::StreamError::redirect);\n    completion(nil);\n}', 'data': '- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:(NSData *)data {\n    (void)session;\n    if(auto s=state.lock()) {\n        if(task!=s->task) return;\n        // Delivery is serial on work. Never buffer or stringify response bytes.\n        if(data.length>live::Poster::maxResponseBytes-s->responseBytes) {\n            s->stop(live::StreamError::responseTooLarge);\n            [task cancel];\n        } else s->responseBytes+=data.length;\n    }\n}'}
LIVEFIX_ORIGINAL_SHA256 = {'    void stop(StreamError reason)': 'f1e0bf47713e22bc05cc07905f408ae6aa1080fbb038b7993efadfd21e8fa7a1', '    void cancel()': '9ff0550cdd63355aa5698454a78782f0f9d75f6dd3915af3568e5e32382cc058', '    void pump()': 'a0666407f49ab541914e5d2179feac638049c3bcf8d1a5777173c4efb83e553a', '    void complete(NSURLSessionTask *finished,NSError *error)': '504a1fc1465bf68cd186f63c64d15934f0bb745f7a3e26c5acfa5c4f148cc646', 'response': '9b5d789af5fa0946705274f9d6e003292ab4778c64b7e2bf40a911ef199d1fe1', 'post': '5e6fd1e18fd1f4aee658eccf830b397c6dc5812b427ebb065ed12241bdb70c3a', 'redirect': '4a9288279ea4a31fe8e1a819c1f2a8136f1f4f9b5266663f5820e9caa69f912b', 'data': 'fb6fda1dcd66364fb4b11048cf2cf8119112fe65001afa21d9eac66f65015340'}

def original_ingest(source):
    for name, text in LIVEFIX_ORIGINAL.items():
        assert hashlib.sha256(text.encode()).hexdigest() == LIVEFIX_ORIGINAL_SHA256[name]
    for name in ('stop', 'cancel', 'pump', 'complete', 'send', 'makePlaylist', 'endPlaylist'):
        begin = source.index('    void '+name+'(')
        end = source.index('\n    }', begin)+len('\n    }')
        old = next((v for k,v in LIVEFIX_ORIGINAL.items() if k.startswith('    void '+name+'(')), '    void endPlaylist() {}' if name=='endPlaylist' else '')
        source = source[:begin]+old+source[end:]
    for kind in ('response','redirect','data'):
        signature=LIVEFIX_ORIGINAL[kind].split('{',1)[0]
        begin=source.index(signature)
        end=source.index('\n}',begin)+2
        source=source[:begin]+LIVEFIX_ORIGINAL[kind]+source[end:]
    begin = source.index('bool Poster::post(')
    end = source.index('\nvoid Poster::stop()', begin)
    source = source[:begin]+LIVEFIX_ORIGINAL['post']+source[end:]
    source = source.replace('        report_note("live: Go live");\n', '')
    fail = 'void Poster::fail(StreamError reason,int code,const char *detail) { state->stop(reason,code,detail); }'
    assert source.count(fail) == 1
    source = source.replace(fail, 'void Poster::fail(StreamError reason,int,const char *) { state->stop(reason); }')
    source=source.replace('        inFlight.reset(); // the delegate holds this state for the completion call\n','')
    source=source.replace('key=nil;', 'erase(keyBytes,sizeof keyBytes); keySize=0;')
    source=source.replace('value:key]', 'value:[[NSString alloc] initWithBytes:keyBytes length:keySize encoding:NSUTF8StringEncoding]]')
    source=source.replace('void Poster::finish() { state->finish(); }','void Poster::finish() { stop(); }')
    return source.replace('static const char *errorKind', '__attribute__((unused)) static const char *errorKind')


# Key lifetime baseline: only the key-owning functions/members from the reviewed code.
KEY_ORIGINAL = {'stop': '    void stop(StreamError reason,int code=0) {\n        {\n            std::lock_guard<std::mutex> guard(lock);\n            if(!counters.running) return;\n            counters.running=false; counters.error=reason; counters.httpCode=code;\n            if(reason!=StreamError::stopped) {\n                report_note("live: error kind=%s code=%d",errorKind(reason),code);\n            }\n            for(auto &s:pending) subtract(s);\n            pending.clear();\n        }\n        auto self=shared_from_this();\n        dispatch_async(work, ^{ self->cancel(); });\n    }', 'cancel': '    void cancel() {\n        if(timer) { dispatch_source_cancel(timer); timer=nullptr; }\n        if(task) {\n            { std::lock_guard<std::mutex> guard(lock); ++counters.cancelled; }\n            [task cancel];\n        } else {\n            std::lock_guard<std::mutex> guard(lock);\n            if(sending) { subtract(sending); sending.reset(); }\n        }\n        [session invalidateAndCancel]; session=nil;\n        key=nil; playlistBody=nil; window.clear();\n        logStop();\n    }', 'send': '    void send() {\n        { std::lock_guard<std::mutex> guard(lock); if(!counters.running) return; }\n        @autoreleasepool {\n            if(!session) {\n                auto delegate=[NFSSegmentSession new]; delegate->state=shared_from_this();\n                auto configuration=[NSURLSessionConfiguration ephemeralSessionConfiguration];\n                configuration.URLCache=nil; configuration.HTTPCookieStorage=nil;\n                configuration.URLCredentialStorage=nil; configuration.HTTPShouldSetCookies=NO;\n                configuration.requestCachePolicy=NSURLRequestReloadIgnoringLocalCacheData;\n                configuration.HTTPMaximumConnectionsPerHost=1;\n                configuration.HTTPShouldUsePipelining=NO;\n                configuration.timeoutIntervalForRequest=deadline+1;\n                configuration.timeoutIntervalForResource=deadline+1;\n#ifdef LIVESTREAM_TESTING\n                configuration.connectionProxyDictionary=@{};\n#endif\n                auto callbacks=[NSOperationQueue new]; callbacks.maxConcurrentOperationCount=1;\n                callbacks.underlyingQueue=work;\n                session=[NSURLSession sessionWithConfiguration:configuration delegate:delegate delegateQueue:callbacks];\n            }\n            NSURLComponents *url=[base copy];\n            url.queryItems=@[[NSURLQueryItem queryItemWithName:@"cid" value:key],\n                [NSURLQueryItem queryItemWithName:@"copy" value:@"0"],\n                [NSURLQueryItem queryItemWithName:@"file" value:playlist?@"live.m3u8":sendingName]];\n            url.percentEncodedQuery=[url.percentEncodedQuery stringByReplacingOccurrencesOfString:@"+" withString:@"%2B"];\n            auto request=[NSMutableURLRequest requestWithURL:url.URL];\n            request.HTTPMethod=@"POST"; request.timeoutInterval=deadline+1;\n            [request setValue:@"NFS / Mac / 1.0" forHTTPHeaderField:@"User-Agent"];\n            [request setValue:playlist?@"application/vnd.apple.mpegurl":@"video/MP2T" forHTTPHeaderField:@"Content-Type"];\n            if(playlist) request.HTTPBody=playlistBody;\n            else {\n                // Foundation shares the immutable payload, including cancellation.\n                auto body=sending;\n                request.HTTPBody=[[NSData alloc] initWithBytesNoCopy:(void*)body->bytes.data()\n                    length:body->bytes.size() deallocator:^(void *,NSUInteger) { (void)body; }];\n            }\n            responseBytes=0; responseCode=0;\n            task=[session dataTaskWithRequest:request];\n            inFlight=shared_from_this();\n            [task resume];\n        }\n    }', 'create': 'std::shared_ptr<Poster> Poster::create(const std::string& key\n#ifdef LIVESTREAM_TESTING\n    , const StreamTestOptions& options\n#endif\n) {\n    if(key.empty()) return {};\n    if(key.size()>512) return {};\n    @autoreleasepool {\n        NSString *text=[[NSString alloc] initWithBytes:key.data() length:key.size() encoding:NSUTF8StringEncoding];\n        if(!text || !text.length || [text rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location!=NSNotFound) return {};\n        NSString *address=@"https://a.upload.youtube.com/http_upload_hls";\n#ifdef LIVESTREAM_TESTING\n        if(!options.baseURL.empty()) address=[NSString stringWithUTF8String:options.baseURL.c_str()];\n#endif\n        auto base=[NSURLComponents componentsWithString:address];\n        bool production=[base.host isEqualToString:@"a.upload.youtube.com"] && (!base.port || base.port.integerValue==443);\n        bool allowed=production;\n#ifdef LIVESTREAM_TESTING\n        allowed=allowed || ([base.host isEqualToString:@"127.0.0.1"] && base.port.integerValue>0 && base.port.integerValue<=65535);\n        if(options.deadlineSeconds<=0 || options.deadlineSeconds>Poster::deadlineSeconds) return {};\n#endif\n        if(![base.scheme isEqualToString:@"https"] || !allowed || base.user || base.password || base.query || base.fragment ||\n            ![base.path isEqualToString:@"/http_upload_hls"] || !base.URL) return {};\n        auto state=std::make_shared<StreamState>(text,base);\n#ifdef LIVESTREAM_TESTING\n        state->deadline=options.deadlineSeconds;\n        if(!production && !options.anchorDER.empty()) state->anchor=[NSData dataWithBytes:options.anchorDER.data() length:options.anchorDER.size()];\n#endif\n        report_note("live: Go live");\n        return std::shared_ptr<Poster>(new Poster(std::move(state)));\n    }\n}', 'constructor': '    StreamState(NSString *k,NSURLComponents *b):prefix(NSUUID.UUID.UUIDString),key(k),base(b),\n        work(dispatch_queue_create("live.https",DISPATCH_QUEUE_SERIAL)) { counters.running=true; }'}
KEY_ORIGINAL_SHA256 = {'stop': '707fd602de1237822d96c6b229d67a4b8dddf8c045b33a4ea887e6dface99770', 'cancel': '7f36835b4917172b9387530adbce3c4ca71cebdde2911af66220cf1fa56236b5', 'send': '01de40f5ed0c430abdee23bf35133b3d02e12c0d169f0fc89be888ee99d4e820', 'create': '19278d8c17eeef5e5e2e240d92083deb312cc27899274c8f0e2507aaf578860c', 'constructor': 'a1578e01819481e8ab9bf52bf1fd395612a818eac5d9569289f99b039b9ed934'}

def function(text,signature):
    begin=text.index(signature); brace=text.index('{',begin); depth=1; end=brace+1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}'); end+=1
    return text[begin:end]


def original_key_lifetime(source):
    for name,text in KEY_ORIGINAL.items():
        assert hashlib.sha256(text.encode()).hexdigest()==KEY_ORIGINAL_SHA256[name]
    for name,signature in [('stop','    void stop('),('cancel','    void cancel('),('send','    void send('),
                           ('create','std::shared_ptr<Poster> Poster::create(')]:
        source=source.replace(function(source,signature),KEY_ORIGINAL[name])
    begin=source.index('    StreamState('); end=source.index('\n    void subtract(',begin)
    source=source[:begin]+KEY_ORIGINAL['constructor']+source[end:]
    source=source.replace('    unsigned char keyBytes[512]={0};\n    size_t keySize=0;\n    bool taskCancelled=false;',
                          '    NSString *key;')
    # The pinned stop() predates named failure reasons; the reason detail is irrelevant to key lifetime.
    before='{ state->stop(reason,code,detail); }'
    assert source.count(before)==1
    source=source.replace(before,'{ (void)detail; state->stop(reason,code); }')
    source=source.replace('void Poster::finish() { state->finish(); }','void Poster::finish() { stop(); }')
    return source.replace('static void erase(', '__attribute__((unused)) static void erase(').replace(
                          'static bool validKey(', '__attribute__((unused)) static bool validKey(')

RT = Path(__file__).resolve().parent
ROOT = RT.parent
CACHE = ROOT / '.metal-cache/livestream'


class Fixture:
    def __init__(self, cert, private, key, mode, abort_control=False):
        self.key, self.mode = key, mode
        self.received, self.escaped, self.invalid = [], 0, 0
        self.accepted = 0
        self.names, self.playlists, self.events = [], [], []
        self.attempts = {}
        self.generations, self.all_names = [], set()
        self.prefix = None
        self.awaiting_playlist = False
        self.playlist_sequences, self.durations = [], []
        self.stop = threading.Event()
        self.directory = Path(cert).parent
        self.finals = 0
        self.last_playlist = None
        # Abort controls keep the selected response in flight through Stop's
        # return. The child receives the hold notification before Stop, and
        # releases it afterwards; scheduling cannot turn this into an ACK.
        self.abort_channel, self.abort_child = socket.socketpair() if abort_control else (None, None)
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def do_POST(self):
                parts = urlsplit(self.path)
                if parts.path != '/http_upload_hls':
                    fixture.escaped += 1
                query = parse_qs(parts.query, keep_blank_values=True)
                length = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(length)
                name = query.get('file', [''])[0]
                playlist = name.endswith('.m3u8')
                final = playlist and body.endswith(b'#EXT-X-ENDLIST\n')
                kind = 'playlist' if playlist else 'segment'
                attempt = fixture.attempts.get(name, 0)
                fixture.attempts[name] = attempt + 1
                code = 200
                if fixture.mode == 'http': code = 503
                if fixture.mode in ('unauthorized', 'playlist401') and (not playlist or fixture.mode == 'playlist401'):
                    code = 401 if not (fixture.mode == 'playlist401' and not playlist) else 200
                if fixture.mode == 'http500': code = 500
                if fixture.mode == 'accepted202': code = 202
                if fixture.mode in ('retry-segment', 'retry-playlist') and playlist == (fixture.mode == 'retry-playlist') and attempt < 2:
                    code = 500
                if fixture.mode == 'redirect': code = 307
                if fixture.mode == 'finish-retry' and final:
                    code = 500 if fixture.finals < 2 else 200
                    fixture.finals += 1
                valid = (parts.path == '/http_upload_hls' and
                         set(query) == {'cid', 'copy', 'file'} and
                         query['cid'] == [fixture.key] and query['copy'] == ['0'] and
                         re.fullmatch(r'[^/]+ / [^/]+ / [^/]+', self.headers.get('User-Agent', '')) is not None)
                if playlist:
                    valid = valid and self.headers.get('Content-Type') == 'application/vnd.apple.mpegurl'
                    parsed = fixture.parse_playlist(body)
                    if final:
                        parsed = parsed and body == fixture.last_playlist+b'#EXT-X-ENDLIST\n'
                    elif code in (200,202):
                        fixture.last_playlist = body
                    fixture.playlists.append(bool(valid and parsed))
                    valid = valid and parsed
                    if code in (200, 202): fixture.awaiting_playlist = False
                else:
                    match = re.fullmatch(r'([A-Fa-f0-9-]{36})-(\d+)\.ts', name)
                    sequence = int(match[2]) if match else int(name[:-3]) if name[:-3].isdigit() else 0
                    if match and match[1] != fixture.prefix:
                        if fixture.awaiting_playlist: valid = False
                        fixture.prefix = match[1]
                        fixture.names = []
                        fixture.generations.append(fixture.names)
                    valid = valid and match is not None and self.headers.get('Content-Type') == 'video/MP2T'
                    valid = valid and not fixture.awaiting_playlist
                    valid = valid and (name in fixture.names or sequence == len(fixture.names))
                    if fixture.mode == 'ordered':
                        expected = bytes(0x47 if i % 188 == 0 else sequence for i in range(752))
                        valid = valid and body == expected
                    if code in (200, 202) and name not in fixture.names:
                        valid = valid and name not in fixture.all_names
                        fixture.all_names.add(name)
                        fixture.names.append(name)
                        fixture.awaiting_playlist = True
                    fixture.received.append(bool(valid))
                fixture.events.append((kind, name, code, time.monotonic()))
                fixture.invalid += not valid
                hold = ((fixture.mode in ('finish-segment','finish-five','finish-concurrent','finish-retry','finish-total-stall','finish-joined-stall','finish-early-stall','finish-queue-stall','finish-drain-stall') and not playlist and len(fixture.received)==1)
                        or (fixture.mode=='finish-playlist' and playlist and not final and len(fixture.playlists)==1))
                if hold:
                    if fixture.abort_channel is not None:
                        fixture.abort_channel.sendall(b'H')
                        if fixture.abort_channel.recv(1) != b'R': return
                    else:
                        (fixture.directory/'finish-held').write_text('held')
                        until=time.monotonic()+10
                        while not fixture.stop.is_set() and time.monotonic()<until:
                            if fixture.mode not in ('finish-drain-stall','finish-early-stall') and (fixture.directory/'finish-release').exists(): break
                            fixture.stop.wait(0.002)
                        if fixture.mode in ('finish-drain-stall','finish-early-stall'): return
                if fixture.mode=='finish-queue-stall' and not playlist: fixture.stop.wait(0.65)
                if fixture.mode=='ride-stall' and not playlist and len(fixture.received)==2: fixture.stop.wait(3.5)
                if final and fixture.mode in ('finish-final-stall','finish-total-stall','finish-joined-stall'):
                    fixture.stop.wait(10); return
                if fixture.mode == 'playlist-stall' and not playlist:
                    # Spend most of the 400 ms budget before starting the playlist.
                    fixture.stop.wait(0.28)
                if fixture.mode == 'stall' or (fixture.mode == 'playlist-stall' and playlist):
                    # No response completes this exchange. After cancellation,
                    # do not read another request from the retired connection.
                    self.close_connection = True
                    fixture.stop.wait(12)
                    return
                if fixture.mode == 'disconnect':
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    self.close_connection = True
                    return
                try:
                    self.send_response(code)
                    if code == 307:
                        self.send_header('Location', fixture.base.replace('/http_upload_hls', '/escape'))
                    self.send_header('Content-Length', '262144' if fixture.mode == 'large-body' else '0')
                    self.end_headers()
                    if fixture.mode == 'large-body':
                        # One cancellation window, rather than 256 coalesced tiny
                        # sleeps that can consume the request's absolute budget.
                        self.wfile.write(b'x'*5120)
                        self.wfile.flush()
                        fixture.stop.wait(0.2)
                        self.wfile.write(b'x'*(262144-5120))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                    pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True

            def get_request(self):
                connection, address = super().get_request()
                assert address[0] == '127.0.0.1'
                fixture.accepted += 1
                return connection, address

            def handle_error(self, *_):
                # Exceptions can contain the raw request URL; keep diagnostics numeric.
                fixture.invalid += 1

        self.server = Server(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, private)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.base = f'https://127.0.0.1:{self.server.server_port}/http_upload_hls'
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02})
        self.thread.start()

    def parse_playlist(self, body):
        try:
            text = body.decode('utf-8')
            lines = text.splitlines()
            assert lines[0] == '#EXTM3U' and len(body) <= 2048
            tags = {line.split(':', 1)[0]: line.split(':', 1)[1] for line in lines if ':' in line}
            sequence = int(tags['#EXT-X-MEDIA-SEQUENCE'])
            target = int(tags['#EXT-X-TARGETDURATION'])
            assert int(tags['#EXT-X-VERSION']) >= 3 and 1 <= target <= 5
            assert '#EXT-X-STREAM-INF' not in tags
            names = [line for line in lines if line and not line.startswith('#')]
            durations = [float(line.split(':', 1)[1].rstrip(',')) for line in lines if line.startswith('#EXTINF:')]
            assert names == self.names[-3:] and 1 <= len(names) <= 3
            assert sequence == max(0, len(self.names)-3)
            assert len(durations) == len(names) and all(0 < d <= 5 and d <= target for d in durations)
            for name in names:
                i = lines.index(name)
                assert lines[i-1].startswith('#EXTINF:')
            self.playlist_sequences.append(sequence)
            self.durations.append(durations)
            return True
        except (ValueError, KeyError, IndexError, AssertionError, UnicodeError):
            return False

    def close(self):
        self.stop.set()
        if self.abort_channel is not None:
            self.abort_channel.shutdown(socket.SHUT_RDWR)
            self.abort_channel.close()
            self.abort_child.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        assert not self.thread.is_alive(), 'fixture stopped'


def cancellation_fixture_proof(directory):
    """A reset after a held playlist must not become a second request error.

    Original: valid playlist, invalid=1, extra_reads=1. Retired connection:
    invalid=0, extra_reads=0; malformed playlist still increments invalid to 1.
    """
    class ResetAfterRequest(io.BytesIO):
        extra_reads = 0

        def readline(self, *args):
            if self.tell() == len(self.getvalue()):
                self.extra_reads += 1
                raise ConnectionResetError('cancelled fixture peer')
            return super().readline(*args)

    for valid in (True, False):
        fixture = Fixture(directory/'cert.pem', directory/'key.pem', 'fixture', 'playlist-stall')
        fixture.stop.set()  # Release the held response as close() does after Stop.
        fixture.names = ['12345678-1234-1234-1234-123456789abc-0.ts']
        body = ('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n'
                '#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:2.0,\n'+fixture.names[0]+'\n').encode() if valid else b'invalid\n'
        request = (f'POST /http_upload_hls?cid=fixture&copy=0&file=live.m3u8 HTTP/1.1\r\n'
                   f'User-Agent: Test / Test / Test\r\nContent-Type: application/vnd.apple.mpegurl\r\n'
                   f'Content-Length: {len(body)}\r\n\r\n').encode()+body
        handler = object.__new__(fixture.server.RequestHandlerClass)
        handler.rfile, handler.wfile = ResetAfterRequest(request), io.BytesIO()
        try:
            try:
                handler.handle()
            except ConnectionResetError:
                fixture.server.handle_error(None, None)
        finally:
            fixture.close()
        print(f'cancelled playlist fixture: valid={int(valid)} invalid={fixture.invalid} '
              f'extra_reads={handler.rfile.extra_reads}', flush=True)
        assert fixture.playlists == [valid] and fixture.invalid == int(not valid)
        assert handler.rfile.extra_reads == 0, 'cancelled exchange must retire the connection'


def compile_binary(directory, name, source, packets=None, driver=None, process_scan=False):
    marker = 'session=[NSURLSession sessionWithConfiguration:configuration delegate:delegate delegateQueue:callbacks];'
    assert source.count(marker) == 1
    path = directory / (name + '.mm')
    source = source.replace(marker, '++createdSessions; ' + marker)
    source = source.replace('[task resume];','[task resume]; ++startedRequests;')
    source = source.replace('if(finished!=task) return;', 'if(finished!=task) return; if(error) fprintf(stderr,"request_error_code=%lld\\n",(long long)error.code);')
    data_marker = '(NSData *)data {\n'
    assert source.count(data_marker) == 1
    source = source.replace(data_marker, data_marker+'    receivedResponseBytes+=data.length;\n')
    if process_scan:
        # Observe only existing string values, without retaining or copying them.
        assert source.count('auto next=Poster::create(key);')==1, 'scan must select the loopback options'
        assert source.count('NSURL *url=[[NSURL alloc] initWithString:text];')==1
        source = source.replace('auto next=Poster::create(key);', 'auto next=Poster::create(key,processScanOptions);')
        source = source.replace('NSURL *url=[[NSURL alloc] initWithString:text];',
            'scanString(text,"port.url-text"); NSURL *url=[[NSURL alloc] initWithString:text]; '
            'scanString((__bridge NSString *)CFURLGetString((__bridge CFURLRef)url),"port.url");')
        key_source = (RT/'stream_key.m').read_text()
        assert key_source.count('replaceKey(field.stringValue)')==1
        assert key_source.count('NSString *text=[NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];')==1
        key_source = key_source.replace('replaceKey(field.stringValue)',
            'replaceKey(scanString(field.stringValue,"port.save"))')
        key_source = key_source.replace('volatile unsigned char *p=bytes;', 'volatile unsigned char *p=(unsigned char *)bytes;')
        key_source = key_source.replace('NSString *text=[NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];',
            'NSString *text=scanString([NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString],"port.paste");')
        (directory/'scan-key.m').write_text(key_source)
    path.write_text(source)
    exe = directory / name
    command = ['clang++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
               '-Wno-deprecated-declarations', '-fobjc-arc', '-I'+str(RT),
               '-DLIVESTREAM_SOURCE="'+str(path)+'"', str(driver or RT/'livestream_test.mm'),
               str(packets or RT/'clip_packets.mm'), str(RT/'live_seg.mm'), str(RT/'live_encode.mm'),
               '-I'+str(ROOT/'third_party/ffmpeg/include'),
               *[str(ROOT/'third_party/ffmpeg/lib'/('lib'+name+'.a')) for name in ('avformat','avcodec','avutil')],
               '-lz', str(directory/'socket-probe.dylib')]
    if process_scan:
        command += ['-DLIVESTREAM_PROCESS_SCAN=1', '-DSTREAM_KEY_SOURCE="'+str(directory/'scan-key.m')+'"',
                    '-framework', 'Cocoa']
    for framework in ('Foundation', 'CoreMedia', 'CoreVideo', 'VideoToolbox', 'AudioToolbox', 'Security'):
        command += ['-framework', framework]
    built = subprocess.run(command+['-o', str(exe)], capture_output=True, text=True, timeout=90)
    (CACHE/(name+'-build.log')).write_text(built.stdout+built.stderr)
    assert built.returncode == 0, built.stderr[-3500:]
    return exe


def run(exe, mode, fixture_mode, directory, key, broken=False, leak=False, abort_control=False):
    for name in ('finish-release','finish-held'):
        (directory/name).unlink(missing_ok=True)
    fixture = Fixture(directory/'cert.pem', directory/'key.pem', key, fixture_mode, abort_control)
    abort_env = {'NFS_TEST_ABORT_FD': str(fixture.abort_child.fileno())} if abort_control else {}
    libraries = ':'.join(filter(None, (str(directory/'socket-probe.dylib'),
                                      os.environ.get('DYLD_INSERT_LIBRARIES'))))
    try:
        result = subprocess.run([str(exe), mode, fixture.base, str(directory/'cert.der')],
                                input=key+'\n', capture_output=True, text=True, timeout=180 if mode=='process-scan' else 18,
                                pass_fds=(fixture.abort_child.fileno(),) if abort_control else (),
                                env=dict(os.environ, DYLD_INSERT_LIBRARIES=libraries, **abort_env))
    finally:
        fixture.close()
    session_path = directory/'cert.der.session.txt'
    session_text = session_path.read_text() if session_path.exists() else ''
    output = result.stdout+result.stderr
    if mode=='process-scan':
        assert 'memory-scan:' in output, f'process scan did not complete: rc={result.returncode}'
        summary=re.search(r'memory-scan: matches=(\d+) port=(\d+) unattributed=(\d+).*read_errors=0 overflow=0 exclusions=1',output)
        assert summary, 'process scan coverage/metadata must complete without read errors'
        if broken:
            assert int(summary[1])>=1 and int(summary[2])>=1 and 'owner=port.poster' in output, 'skipped poster erase must be detected and attributed'
            assert 'FAIL stream: whole-process scan finds a remaining key copy' in output
        else:
            assert int(summary[1])==0, 'no raw/encoded key copies may remain'
    assert key not in session_text and key.split('+',1)[0] not in session_text, 'private session sink'
    (CACHE/(exe.name+'-'+mode+'-session.txt')).write_text(session_text)
    # The ASCII prefix survives URL escaping, so raw and encoded URL leaks fail.
    sentinel = key.split('+', 1)[0]
    matches = output.count(sentinel)
    urls = re.findall(r'https://[^\s]*http_upload_hls[^\s]*', output)
    # A deliberate leak is detected in memory, then redacted before any report/log.
    safe = re.sub(r'https://[^\s]*', '[redacted URL]', output)
    safe = safe.replace(key, '[redacted]').replace(sentinel, '[redacted]')
    (CACHE/(exe.name+'-'+mode+'.log')).write_text(safe)
    assert not (matches or urls) or leak, 'key or upload URL present in process output'
    valid = (result.returncode == 0 and not matches and not urls and not fixture.escaped and not fixture.invalid)
    if mode == 'process-scan':
        valid = valid and fixture.received==[True]*2 and fixture.playlists==[True]*3 and 'finals=1 finalized=1' in output
    if mode == 'ordered':
        valid = valid and fixture.received == [True]*6 and fixture.playlists == [True]*6 and fixture.accepted == 1
    if mode == 'ride-stall':
        valid = valid and fixture.received == [True]*8 and fixture.playlists == [True]*8
    if mode in ('jitter', 'oversize'):
        valid = valid and fixture.received == [True] and fixture.playlists == [True]
    if mode in ('protocol','success') and not broken:
        assert 'live: Go live' in session_text and 'live: first segment accepted code=' in session_text
        assert 'live: first playlist accepted code=' in session_text and 'live: Stop accepted=' in session_text
    if mode == 'protocol':
        valid = valid and len(fixture.generations) == 2 and [len(g) for g in fixture.generations] == [6, 1]
        valid = valid and fixture.playlists == [True]*7 and fixture.playlist_sequences == [0,0,0,1,2,3,0]
        expected = [[2.005 if j%2 else 2.0 for j in range(max(0,i-2),i+1)] for i in range(6)]+[[2.0]]
        valid = valid and fixture.durations == expected and fixture.accepted == 2
    if fixture_mode in ('retry-segment', 'retry-playlist') and not broken and not mode.startswith('wipe-'):
        times = [t for _,_,code,t in fixture.events if code == 500]
        assert len(times) == 2 and times[1]-times[0] >= 0.08, 'bounded exponential retry starts at 100 ms'
        last_retry = next(t for _,_,code,t in fixture.events if code in (200,202)) if fixture_mode == 'retry-segment' else fixture.events[-1][3]
        assert last_retry-times[1] >= 0.16, 'second retry doubles to 200 ms'
    if mode == 'off':
        valid = valid and fixture.accepted == 0
    if mode == 'untrusted':
        valid = valid and not fixture.received
    print(f'{exe.name}/{mode}: rc={result.returncode} posts={len(fixture.received)} '
          f'playlists={len(fixture.playlists)} unique_names={len({name for kind,name,_,_ in fixture.events if kind == "segment"})} connections={fixture.accepted} invalid={fixture.invalid} escaped={fixture.escaped} key_matches={matches} url_matches={len(urls)}; '
          + safe.strip().replace('\n', '; '), flush=True)
    assert valid != broken, f'{exe.name}/{mode}: '+('broken control escaped' if broken else 'fixed check failed')
    if abort_control:
        assert 'abort control: stopped=1' in output, 'abort control must stop without deadline'
    return safe


def certificate(directory):
    config = directory/'certificate.cnf'
    config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=extensions\nprompt=no\n'
                      '[dn]\nCN=127.0.0.1\n[extensions]\nsubjectAltName=IP:127.0.0.1\n'
                      'basicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n'
                      'extendedKeyUsage=serverAuth\n')
    commands = [['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                 '-config', str(config), '-keyout', str(directory/'key.pem'), '-out', str(directory/'cert.pem')],
                ['openssl', 'x509', '-in', str(directory/'cert.pem'), '-outform', 'DER', '-out', str(directory/'cert.der')]]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, 'loopback certificate generation failed'


def socket_probe(directory):
    # dyld intentionally excludes calls originating in the interposer image.
    # A separate image covers both raw calls in the harness and framework calls.
    source = directory/'socket-probe.cpp'
    source.write_text(r'''
#include <atomic>
#include <sys/socket.h>
static std::atomic<unsigned> sockets{0};
extern "C" unsigned livestream_test_socket_count() { return sockets.load(); }
static int counted(int domain,int type,int protocol) {
    ++sockets;
    return socket(domain,type,protocol);
}
__attribute__((used)) static const struct { const void *replacement,*original; } probe
    __attribute__((section("__DATA,__interpose")))={(const void*)counted,(const void*)socket};
''')
    library = directory/'socket-probe.dylib'
    result = subprocess.run(['clang++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
                             '-dynamiclib', str(source), '-install_name', str(library), '-o', str(library)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-2500:]


def fake_ingest_proof(directory):
    sys.path.insert(0,str(RT))
    import live_fake_ingest
    state=live_fake_ingest.IngestState()
    generation='12345678-1234-1234-1234-123456789abc'
    names=[]; rows=[]
    body=bytes([0x47,0,0]+[0]*185)
    tail=b''
    for index in range(12):
        name=f'{generation}-{index}.ts'; names.append(name)
        ok,extinf,eos,gen,refs=state.upload(name,body)
        assert ok and not eos and gen==generation
        rows.append(f'{index*2}\t{name}\t188\t1\t0\t0\t{gen}\t')
        tail=('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:5\n'
              f'#EXT-X-MEDIA-SEQUENCE:{max(0,index-2)}\n'+
              ''.join(f'#EXTINF:2.000000,\n{n}\n' for n in names[-3:])).encode()
        ok,extinf,eos,gen,refs=state.upload('live.m3u8',tail)
        assert ok and extinf==2 and not eos and refs==names[-3:]
        rows.append(f'{index*2+0.1}\tlive.m3u8\t{len(tail)}\t1\t2\t0\t{gen}\t{",".join(refs)}')
    final=tail+b'#EXT-X-ENDLIST\n'
    assert not state.upload('live.m3u8',final.replace(b'2.000000',b'2.500000'))[0], 'EOS preserves exact snapshot'
    assert not state.upload('live.m3u8',final+b'#EXT-X-ENDLIST\n')[0], 'duplicate ENDLIST rejected'
    ok,extinf,eos,gen,refs=state.upload('live.m3u8',final)
    assert ok and eos and gen==generation and refs==names[-3:]
    rows.append(f'24\tlive.m3u8\t{len(final)}\t1\t2\t1\t{gen}\t{",".join(refs)}')
    assert not state.upload('live.m3u8',final)[0], 'duplicate final rejected'
    assert not state.upload(f'{generation}-12.ts',body)[0], 'post-final media rejected'
    root=directory/'counts'; (root/'ingest').mkdir(parents=True); (root/'game').mkdir()
    (root/'game.user').mkdir()
    events='\n'.join('script: '+s for s in ('unfocus:15','hide:15','unfocus:30','livestop','livequit'))
    (root/'game/events.log').write_text(events)
    session=('live: first segment accepted code=202\n'
             'live: Stop accepted=12 posted=12 playlists=13 cancelled=0 dropped=0 failed=0 retries=0 '
             'bytes=0 peak_bytes=188 ticks=0 peak_ticks=180000 finals=1 finalized=1\n')
    (root/'game.user/session.txt').write_text(session)
    def judge(values,valid):
        (root/'ingest/ingest.tsv').write_text('\n'.join(values)+'\n')
        result=subprocess.run(['/bin/sh',str(RT/'live_fake.sh'),'--counts',str(root)],capture_output=True,text=True,timeout=15)
        assert (result.returncode==0)==valid, result.stdout+result.stderr
    judge(rows,True)
    for broken in (rows[:-1],rows+[rows[-1]],rows+[rows[0]],
                   [r.replace('\t1\t'+generation+'\t','\t0\t'+generation+'\t') for r in rows]):
        judge(broken,False)
    (root/'game.user/session.txt').write_text(session.replace('cancelled=0','cancelled=1'))
    judge(rows,False)
    print('fake ingest/counts: valid=1 rejected=5; exact tail, duplicate ENDLIST/final, post-final media rejected',flush=True)


def main():
    if sys.platform != 'darwin':
        print('SKIP: requires macOS NSURLSession and media frameworks')
        return 3
    CACHE.mkdir(parents=True, exist_ok=True)
    source = (RT/'livestream.mm').read_text()
    hook = 'if(!output) output=streamOutput();'
    segment_source = (RT/'live_seg.mm').read_text()
    # Audit all local output paths. Errors cross the API only as StreamError enums;
    # report_note receives fixed strings, enum names and scalars; NSError/URLs never become text.
    for text in (source, segment_source):
        assert not re.search(r'\b(?:NSLog|fprintf|printf|os_log|diag_\w+)\s*\(', text)
        assert not re.search(r'\.(?:localizedDescription|debugDescription|userInfo)\b', text)
    report=(RT/'report.m').read_text()
    assert 'session_note(notes[i],' in report and r'fprintf(sess_f, "%s\n", buf);' in report
    assert 'fflush(sess_f);' in report
    def attached(text):
        assert text.count(hook) == 1, 'segment callback is attached'
    attached(segment_source)
    try:
        attached(segment_source.replace(hook, ''))
    except AssertionError:
        pass
    else:
        raise AssertionError('removed hook control escaped')
    with tempfile.TemporaryDirectory(prefix='https-', dir=CACHE) as temporary:
        directory = Path(temporary)
        certificate(directory)
        fake_ingest_proof(directory)
        socket_probe(directory)
        key = 'stream-sentinel-'+secrets.token_hex(16)+'+/?&=#%é😀'
        cancellation_fixture_proof(directory)
        fixed = compile_binary(directory, 'fixed', source)
        scan = compile_binary(directory, 'process-scan', source, process_scan=True)
        scan_key = 'scan-'+secrets.token_hex(24)+'+/?&=#%'
        run(scan,'process-scan','ordered',directory,scan_key)
        erase = 'erase(keyBytes,sizeof keyBytes); keySize=0;'
        assert function(source,'    void cancel()').count(erase)==1
        broken_scan_source=source.replace(function(source,'    void cancel()'),
            function(source,'    void cancel()').replace(erase,'keySize=0;'))
        broken_scan=compile_binary(directory,'process-scan-no-erase',broken_scan_source,process_scan=True)
        run(broken_scan,'process-scan','ordered',directory,scan_key,broken=True)
        for name,fragment in FINAL_ABORT_FRAGMENTS.items():
            assert hashlib.sha256(fragment.encode()).hexdigest()==FINAL_ABORT_DIGESTS[name]
        # Delegate graceful entry back to the reviewed immediate abort, retaining
        # today's exact stop/cancel fragments for the measured baseline.
        immediate=source.replace(function(source,'void Poster::finish()'), 'void Poster::finish() { stop(); }')
        for name,fragment in FINAL_ABORT_FRAGMENTS.items():
            immediate=immediate.replace(function(immediate,name),fragment)
        abort=compile_binary(directory,'finish-abort-original',immediate)
        healthy=('finish-segment','finish-playlist','finish-five','finish-concurrent','finish-retry','finish-tail')
        for mode in healthy:
            run(fixed,mode,mode,directory,key)
            if mode!='finish-tail':
                failure=run(abort,mode,mode,directory,key,broken=True,abort_control=True)
                assert re.search(r'cancelled=[1-9]\d*.*finals=0',failure), 'abort control must cancel and send no EOS'
        lie=source.replace('        send();\n    }\n    void logStop()',
            '        { std::lock_guard<std::mutex> guard(lock); counters.finalized=true; }\n        send();\n    }\n    void logStop()')
        assert lie!=source
        false_ack=compile_binary(directory,'finish-false-ack',lie)
        run(false_ack,'finish-final-stall','finish-final-stall',directory,key,broken=True)
        run(fixed,'finish-idle','segments',directory,key)
        for mode in ('finish-drain-stall','finish-final-stall','finish-total-stall','finish-joined-stall','finish-early-stall','finish-queue-stall'):
            run(fixed,mode,mode,directory,key)
        reset_join=source.replace('            if(!active) self->cancel();',
            '            if(active) self->finishDeadline(3); else self->cancel();')
        assert reset_join!=source
        mutant=compile_binary(directory,'finish-reset-join',reset_join)
        failure=run(mutant,'finish-joined-stall','finish-joined-stall',directory,key,broken=True)
        assert 'FAIL stream: finish bounded wait' in failure
        reset_pair=source.replace('        finishDeadline(std::chrono::duration<double>(drainUntil-',
            '        if(timer) { dispatch_source_cancel(timer); timer=nullptr; }\n        finishDeadline(std::chrono::duration<double>(drainUntil-')
        assert reset_pair!=source
        mutant=compile_binary(directory,'finish-reset-pair',reset_pair)
        failure=run(mutant,'finish-early-stall','finish-early-stall',directory,key,broken=True)
        assert 'FAIL stream: finish preserves active pair earlier deadline' in failure
        no_shared_drain=source.replace('        finishDeadline(std::chrono::duration<double>(drainUntil-',
            '        if(sending) return;\n        finishDeadline(std::chrono::duration<double>(drainUntil-')
        assert no_shared_drain!=source
        mutant=compile_binary(directory,'finish-no-shared-drain',no_shared_drain)
        failure=run(mutant,'finish-queue-stall','finish-queue-stall',directory,key,broken=True)
        assert 'finals=1 finalized=1' in failure and 'FAIL stream: timeout must not claim' in failure
        early_snapshot=source.replace('            stoppingStream=std::move(currentStream);',
            '            stoppingStream=std::move(currentStream);\n            lastStream=stoppingStream->stats();')
        early_snapshot=early_snapshot.replace('        if(old) lastStream=old->stats();\n','')
        assert early_snapshot!=source
        mutant=compile_binary(directory,'finish-early-snapshot',early_snapshot)
        failure=run(mutant,'finish-concurrent','finish-concurrent',directory,key,broken=True)
        assert 'last snapshot: posted=0 playlists=0 finals=0' in failure
        assert 'FAIL stream: global Stop snapshots final counters' in failure
        assert hashlib.sha256(BASE_STOP_STREAM.encode()).hexdigest() == BASE_STOP_STREAM_SHA256
        stop_original = source.replace(function(source, 'void stopStream()'), BASE_STOP_STREAM)
        detached = compile_binary(directory, 'detached-stop-original', stop_original)
        for mode in ('wipe-concurrent', 'wipe-concurrent-queue'):
            failure = run(detached, mode, 'stall', directory, key, broken=True)
            assert 'second_premature=1 held_matches=1' in failure
            assert 'FAIL stream: concurrent stoppers wait for poster erasure' in failure
            run(fixed, mode, 'stall', directory, key)
        key_original = compile_binary(directory, 'key-original', original_key_lifetime(source))
        for mode in ('wipe-idle','wipe-flight','wipe-failure','wipe-playlist','wipe-retry','wipe-external'):
            fixture='retry-segment' if mode=='wipe-retry' else 'playlist-stall' if mode=='wipe-playlist' else 'stall'
            run(key_original,mode,fixture,directory,key,broken=True)
            run(fixed,mode,fixture,directory,key)
        # Restarting the timer for the playlist escaped the old two-second check.
        transition = 'makePlaylist(); playlist=true; attempts=0; send(); return;'
        assert source.count(transition) == 1
        reset = source.replace(transition, 'makePlaylist(); playlist=true; attempts=0; '
            'dispatch_source_set_timer(timer,dispatch_time(DISPATCH_TIME_NOW,'
            '(int64_t)(deadline*NSEC_PER_SEC)),DISPATCH_TIME_FOREVER,0); send(); return;')
        mutant = compile_binary(directory, 'playlist-deadline-reset', reset)
        failure = run(mutant, 'playlist-stall', 'playlist-stall', directory, key, broken=True)
        assert 'FAIL stream: playlist shares absolute deadline' in failure
        driver = (RT/'livestream_test.mm').read_text()
        tightened = 's.error==live::StreamError::deadline && elapsed<options.deadlineSeconds+0.15'
        assert driver.count(tightened) == 1
        old_driver = directory/'old-deadline-check.mm'
        old_driver.write_text(driver.replace(tightened, 's.error==live::StreamError::deadline && elapsed<2'))
        old_check = compile_binary(directory, 'playlist-deadline-reset-old-check', reset, driver=old_driver)
        run(old_check, 'playlist-stall', 'playlist-stall', directory, key)
        original_livefix = compile_binary(directory, 'segment-only-original', original_ingest(source))
        for mode, fixture in [('protocol','protocol'), ('success','accepted202'), ('success','retry-segment'),
                              ('success','retry-playlist'), ('unauthorized','unauthorized'), ('playlist401','playlist401'),
                              ('http500','http500'), ('playlist-stall','playlist-stall'), ('detached-stop','stall'), ('count','stall')]:
            run(original_livefix, mode, fixture, directory, key, broken=True)
            run(fixed, mode, fixture, directory, key)
        # Execute the original bug using checksum-pinned callbacks, without history reads.
        for fragment, digest in ((BASE_POST, BASE_POST_SHA256), (BASE_DATA, BASE_DATA_SHA256)):
            assert hashlib.sha256(fragment.encode()).hexdigest() == digest
        begin = source.index('bool Poster::post(')
        end = source.index('void Poster::stop()', begin)
        baseline = source[:begin]+BASE_POST+source[end:]
        begin = baseline.index('- (void)URLSession:(NSURLSession *)session dataTask:(NSURLSessionDataTask *)task didReceiveData:')
        end = baseline.index('- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:', begin)
        baseline = baseline[:begin]+BASE_DATA+baseline[end:]
        baseline = 'namespace live { constexpr long long maxTicks=180000; }\n'+baseline
        original = compile_binary(directory, 'reviewed-base', baseline)
        for mode, fixture in [('jitter','jitter'), ('oversize','ordered'), ('large-body','large-body')]:
            run(original, mode, fixture, directory, key, broken=True)
        for mode, fixture in [('jitter','jitter'), ('oversize','ordered'), ('large-body','large-body'), ('ordered', 'ordered'), ('off', 'ordered'), ('policy', 'ordered'),
                              ('bytes', 'stall'), ('queued', 'stall'), ('deadline', 'stall'),
                              ('disconnect', 'disconnect'), ('redirect', 'redirect'), ('http', 'http'),
                              ('untrusted', 'ordered')]:
            run(fixed, mode, fixture, directory, key)
        # A short upload stall: the fixed limits ride through it; the same driver at the old limits
        # (two held segments, a 3 s per-segment deadline) ends live as the reported stream did.
        run(fixed, 'ride-stall', 'ride-stall', directory, key)
        riding = 'options.deadlineSeconds=live::Poster::deadlineSeconds;\n    live::SegmentLimits limits;'
        assert driver.count(riding) == 1
        old_limits = directory/'old-ride-limits.mm'
        old_limits.write_text(driver.replace(riding, 'options.deadlineSeconds=3;\n    live::SegmentLimits limits; limits.heldSegments=2;'))
        failure = run(compile_binary(directory, 'ride-old-limits', source, driver=old_limits), 'ride-stall', 'ride-stall', directory, key, broken=True)
        assert 'FAIL stream: segmenter rides through a stalled upload' in failure and 'reason=ingest-backlog' in failure
        lost_lifetime=compile_binary(directory,'lost-completion-lifetime',source.replace('inFlight=shared_from_this();',''))
        run(lost_lifetime,'detached-stop','stall',directory,key,broken=True)
        limits = 'segment->bytes.size()<=maxBytes-s->counters.bytes'
        mutants = [
            ('uncapped', limits, 'true', 'bytes', 'stall'),
            ('stop-on-drop', '} else { ++s->counters.dropped; }',
             '} else { ++s->counters.dropped; s->counters.running=false; }', 'oversize', 'ordered'),
            ('unaccount-flight', 'sending=std::move(pending.front()); pending.pop_front();',
             'sending=std::move(pending.front()); pending.pop_front(); subtract(sending);', 'bytes', 'stall'),
            ('key-log', 'if(!validKey(key)) return {};', 'fprintf(stderr,"%s\\n",key.c_str()); if(!validKey(key)) return {};', 'ordered', 'ordered'),
            ('key-url-log', 'request=[NSMutableURLRequest requestWithURL:url];',
             'fprintf(stderr,"%s\\n",url.absoluteString.UTF8String); request=[NSMutableURLRequest requestWithURL:url];', 'ordered', 'ordered'),
            ('default-on', 'if(!validKey(key)) return {};', 'if(key.empty()) return create("default-on",options); if(!validKey(key)) return {};', 'off', 'ordered'),
            ('off-socket', 'if(!validKey(key)) return {};',
             'if(key.empty()) { int fd=socket(AF_INET,SOCK_STREAM,0); if(fd>=0) close(fd); return {}; } if(!validKey(key)) return {};', 'off', 'ordered'),
            ('wrong-order', '(unsigned long long)nextSequence];', '(unsigned long long)0];', 'ordered', 'ordered'),
            ('host-policy', 'bool allowed=production;', 'bool allowed=true;', 'policy', 'ordered'),
            ('no-deadline', 'dispatch_source_set_event_handler(timer,^{ if(auto s=weak.lock()) s->stop(StreamError::deadline); });',
             'dispatch_source_set_event_handler(timer,^{ if(auto s=weak.lock()) (void)s; });', 'deadline', 'stall'),
            ('no-cancel', '++counters.cancelled; }\n                [task cancel];',
             '++counters.cancelled; }\n                return;', 'deadline', 'stall'),
            ('follow-redirect', 'if(auto s=state.lock()) s->stop(live::StreamError::redirect,(int)response.statusCode);\n    completion(nil);',
             'completion(request);', 'redirect', 'redirect'),
            ('ignore-http', 'if((code<200 || code>=300) && code!=500)', 'if(code<0)', 'http', 'http'),
            ('ignore-error', 'if(error && running) stop(StreamError::transport,(int)responseCode);', 'if(error && running) { counters.running=false; counters.error=StreamError::none; }', 'disconnect', 'disconnect'),
            ('trust-all', 'else completion(NSURLSessionAuthChallengePerformDefaultHandling,nil);',
             'else completion(NSURLSessionAuthChallengeUseCredential,[NSURLCredential credentialForTrust:challenge.protectionSpace.serverTrust]);',
             'untrusted', 'ordered'),
        ]
        for name, before, after, mode, fixture in mutants:
            assert source.count(before) == 1, (name, source.count(before))
            mutant = compile_binary(directory, name, source.replace(before, after))
            run(mutant, mode, fixture, directory, key, broken=True, leak=name in ('key-log', 'key-url-log'))
        packets = (RT/'clip_packets.mm').read_text()
        before = 'void Fanout::publish(const OwnedPacket& p) { for(auto &s:sinks) s->push(p); }'
        after = 'void Fanout::publish(const OwnedPacket& p) { for(auto &s:sinks) if(s->failed()) return; for(auto &s:sinks) s->push(p); }'
        assert packets.count(before) == 1
        path = directory/'coupled-packets.mm'
        path.write_text(packets.replace(before, after))
        mutant = compile_binary(directory, 'coupled-failure', source, path)
        run(mutant, 'bytes', 'stall', directory, key, broken=True)
        print(f'livestream PASS: 13 legacy + 10 protocol cases; original ingest and {len(mutants)+3} compiled mutants + removed hook rejected; '
              'playlist timer reset passes old check; key output redacted', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
