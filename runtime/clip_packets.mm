#include "clip_packets.h"
#include <algorithm>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <mutex>
#include <thread>
#include <chrono>
namespace clip {
Packet::Packet(Track t,CMSampleBufferRef s):sample_(nullptr),format_(nullptr),track(t),bytes(CMSampleBufferGetTotalSampleSize(s)) {
    // Own the sample header and retain its backing data independently of the
    // encoder callback. Published samples and attachments are read-only.
    CMSampleBufferCreateCopy(kCFAllocatorDefault,s,&sample_);
    format_=CMSampleBufferGetFormatDescription(s); if(format_) CFRetain(format_);
}
Packet::~Packet() { if(sample_) CFRelease(sample_); if(format_) CFRelease(format_); }
struct Sink::State {
    mutable std::mutex lock;
    std::condition_variable changed;
    std::deque<OwnedPacket> queue;
    const size_t maxPackets,maxBytes;
    size_t bytes=0,peak=0;
    bool closed=false,bad=false,busy=false;
    uint64_t count=0;
    Consumer consume;
    State(Consumer c,size_t p,size_t b):maxPackets(p),maxBytes(b),consume(std::move(c)) {}
    void run() {
            std::unique_lock<std::mutex> guard(lock);
            size_t next=0;
            for(;;) {
                changed.wait(guard,[&]{ return bad || closed || !queue.empty(); });
                if(bad || (closed && queue.empty())) return;
                if(next>=queue.size()) next=0;
                auto p=queue[next]; busy=true;
                guard.unlock();
                Result result;
                @autoreleasepool { result=consume(p); }
                guard.lock(); busy=false;
                if(bad) { changed.notify_all(); return; }
                if(result==Result::failed) { bad=true; queue.clear(); bytes=0; }
                else if(result==Result::consumed) { bytes-=p->bytes; queue.erase(queue.begin()+next); next=0; ++count; }
                else {
                    // Writer readiness is per track. Do not hold audio behind
                    // a full video input, or video behind a full audio input.
                    next=0;
                    for(size_t i=0;i<queue.size();++i) if(queue[i]->track!=p->track) { next=i; break; }
                    changed.wait_for(guard,std::chrono::milliseconds(5),[&]{ return bad; });
                }
                changed.notify_all();
            }
    }
};
Sink::Sink(Consumer c,size_t p,size_t b):state(std::make_shared<State>(std::move(c),p,b)),
    consumerThread([s=state] { s->run(); }) {}
Sink::~Sink() {
    cancel();
    // A consumer may be stuck inside external I/O. Its shared state (including
    // the in-flight packet and callback) outlives this handle, then retires on
    // that thread without touching the recorder or retrying another packet.
    consumerThread.detach();
}
bool Sink::push(const OwnedPacket& p) {
    std::lock_guard<std::mutex> guard(state->lock);
    if(state->bad || state->closed) return false;
    if(state->queue.size()>=state->maxPackets || p->bytes>state->maxBytes-state->bytes) {
        state->bad=true; state->queue.clear(); state->bytes=0; state->changed.notify_all(); return false;
    }
    state->queue.push_back(p); state->bytes+=p->bytes;
    state->peak=std::max(state->peak,state->bytes); state->changed.notify_one(); return true;
}
void Sink::close() { std::lock_guard<std::mutex> guard(state->lock); state->closed=true; state->changed.notify_all(); }
void Sink::cancel() { std::lock_guard<std::mutex> guard(state->lock); state->bad=true; state->queue.clear(); state->bytes=0; state->changed.notify_all(); }
bool Sink::failed() const { std::lock_guard<std::mutex> guard(state->lock); return state->bad; }
bool Sink::drained() const { std::lock_guard<std::mutex> guard(state->lock); return state->queue.empty() && !state->busy; }
size_t Sink::queued() const { std::lock_guard<std::mutex> guard(state->lock); return state->queue.size(); }
size_t Sink::peakBytes() const { std::lock_guard<std::mutex> guard(state->lock); return state->peak; }
uint64_t Sink::delivered() const { std::lock_guard<std::mutex> guard(state->lock); return state->count; }
void Fanout::publish(const OwnedPacket& p) { for(auto &s:sinks) s->push(p); }
void Fanout::close() { for(auto &s:sinks) s->close(); }
void Fanout::cancel() { for(auto &s:sinks) s->cancel(); sinks.clear(); }
bool Fanout::drained() const { for(auto &s:sinks) if(!s->drained()) return false; return true; }
bool Fanout::healthy() const { for(auto &s:sinks) if(!s->failed()) return true; return false; }
static constexpr OSStatus needPCM=0x70636d3f;
AACEncoder::AACEncoder(int64_t first):packetFrame(first),firstFrame(first) {
    AudioStreamBasicDescription in={},out={};
    in.mSampleRate=48000; in.mFormatID=kAudioFormatLinearPCM;
    in.mFormatFlags=kAudioFormatFlagIsFloat|kAudioFormatFlagIsPacked;
    in.mBytesPerPacket=in.mBytesPerFrame=8; in.mFramesPerPacket=1;
    in.mChannelsPerFrame=2; in.mBitsPerChannel=32;
#ifdef CLIPREC_PCM_CONTROL
    out=in;
#else
    out.mSampleRate=48000; out.mFormatID=kAudioFormatMPEG4AAC;
    out.mChannelsPerFrame=2; out.mFramesPerPacket=1024; out.mFormatFlags=kMPEG4Object_AAC_LC;
    UInt32 size=sizeof(out);
    status=AudioFormatGetProperty(kAudioFormatProperty_FormatInfo,0,nullptr,&size,&out);
    if(!status) status=AudioConverterNew(&in,&out,&converter);
    UInt32 bitrate=192000;
    if(!status) status=AudioConverterSetProperty(converter,kAudioConverterEncodeBitRate,sizeof(bitrate),&bitrate);
    UInt32 actualBitrate=0; size=sizeof(actualBitrate);
    if(!status) status=AudioConverterGetProperty(converter,kAudioConverterEncodeBitRate,&size,&actualBitrate);
    if(!status && actualBitrate!=bitrate) status=kAudioConverterErr_FormatNotSupported;
    size=sizeof(out);
    if(!status) status=AudioConverterGetProperty(converter,kAudioConverterCurrentOutputStreamDescription,&size,&out);
    if(!status && (out.mFormatID!=kAudioFormatMPEG4AAC || out.mSampleRate!=48000 || out.mChannelsPerFrame!=2 || out.mFramesPerPacket!=1024)) status=kAudioConverterErr_FormatNotSupported;
    AudioConverterPrimeInfo prime={}; size=sizeof(prime);
    if(!status) status=AudioConverterGetProperty(converter,kAudioConverterPrimeInfo,&size,&prime);
    leading=prime.leadingFrames;
    size=sizeof(maxPacket);
    if(!status) status=AudioConverterGetProperty(converter,kAudioConverterPropertyMaximumOutputPacketSize,&size,&maxPacket);
    if(!status && !maxPacket) status=kAudioConverterErr_FormatNotSupported;
    if(status) return;
#endif
    std::vector<uint8_t> cookie;
#ifndef CLIPREC_PCM_CONTROL
    UInt32 cookieSize=0;
    status=AudioConverterGetPropertyInfo(converter,kAudioConverterCompressionMagicCookie,&cookieSize,nullptr);
    if(!status && !cookieSize) status=kAudioConverterErr_FormatNotSupported;
    if(status) return;
    cookie.resize(cookieSize);
    status=AudioConverterGetProperty(converter,kAudioConverterCompressionMagicCookie,&cookieSize,cookie.data());
    if(status) return;
#endif
    status=CMAudioFormatDescriptionCreate(nullptr,&out,0,nullptr,cookie.size(),cookie.data(),nullptr,&format_);
    pending.reserve(16384);
}
AACEncoder::~AACEncoder() { if(converter) AudioConverterDispose(converter); if(format_) CFRelease(format_); }
OSStatus AACEncoder::supply(AudioConverterRef,UInt32 *n,AudioBufferList *data,AudioStreamPacketDescription **,void *context) {
    auto &a=*(AACEncoder*)context;
    *n=(UInt32)std::min<size_t>(*n,(a.pending.size()-a.cursor)/2);
    data->mNumberBuffers=1; data->mBuffers[0].mNumberChannels=2;
    a.supplied.assign(a.pending.begin()+a.cursor,a.pending.begin()+a.cursor+*n*2);
    data->mBuffers[0].mData=*n?a.supplied.data():nullptr;
    data->mBuffers[0].mDataByteSize=*n*8; a.cursor+=*n*2;
    return *n || a.finalInput ? noErr : needPCM;
}
bool AACEncoder::encode(const float *pcm,size_t frames,bool final,const std::function<void(OwnedPacket)> &emit) {
    if(status || finished) return !status;
    if(frames) pending.insert(pending.end(),pcm,pcm+frames*2);
    finalInput=final;
    for(;;) {
#ifdef CLIPREC_PCM_CONTROL
        size_t n=std::min<size_t>(1024,(pending.size()-cursor)/2);
        if(!n) break;
        const void *payload=pending.data()+cursor; size_t bytes=n*8;
        cursor+=n*2;
#else
        if(!final && pending.size()-cursor<8192) break;
        std::vector<uint8_t> encoded(maxPacket);
        AudioBufferList buffers={}; buffers.mNumberBuffers=1;
        buffers.mBuffers[0]={2,maxPacket,encoded.data()};
        UInt32 packets=1; AudioStreamPacketDescription desc={};
        OSStatus rc=AudioConverterFillComplexBuffer(converter,supply,this,&packets,&buffers,&desc);
        if(rc && rc!=needPCM) { status=rc; return false; }
        if(!packets) break;
        size_t n=1024; const void *payload=encoded.data()+desc.mStartOffset; size_t bytes=desc.mDataByteSize;
#endif
        CMBlockBufferRef block=nullptr; CMSampleBufferRef sample=nullptr;
        status=CMBlockBufferCreateWithMemoryBlock(nullptr,nullptr,bytes,nullptr,nullptr,0,bytes,0,&block);
        if(!status) status=CMBlockBufferReplaceDataBytes(payload,block,0,bytes);
        CMSampleTimingInfo timing={CMTimeMake((int64_t)n,48000),CMTimeMake(packetFrame-leading,48000),kCMTimeInvalid};
#ifdef CLIPREC_PCM_CONTROL
        size_t sampleBytes=8;
        timing.duration=CMTimeMake(1,48000);
        if(!status) status=CMSampleBufferCreateReady(nullptr,block,format_,n,1,&timing,1,&sampleBytes,&sample);
#else
        if(!status) status=CMSampleBufferCreateReady(nullptr,block,format_,1,1,&timing,1,&bytes,&sample);
#endif
        if(block) CFRelease(block);
        if(status) return false;
        if(packetFrame-firstFrame<leading) {
            CFDictionaryRef trim=CMTimeCopyAsDictionary(CMTimeMake(std::min<int64_t>(n,leading-(packetFrame-firstFrame)),48000),nullptr);
            CMSetAttachment(sample,kCMSampleBufferAttachmentKey_TrimDurationAtStart,trim,kCMAttachmentMode_ShouldPropagate); CFRelease(trim);
        }
        emit(std::make_shared<Packet>(Track::audio,sample)); CFRelease(sample);
        packetFrame+=n;
    }
    if(cursor) { pending.erase(pending.begin(),pending.begin()+cursor); cursor=0; }
    if(final) finished=true;
    return true;
}
}
