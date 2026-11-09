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
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <thread>
#include <sys/socket.h>
#include <unistd.h>
#include <cstdarg>
#include <mutex>
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
int main(int argc,char **argv) {
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
        if(mode=="untrusted") options.anchorDER.clear();
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
            size_t bytes=queued?4*1024*1024:9*1024*1024;
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
