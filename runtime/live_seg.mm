#include "live_seg.h"
#include "livestream.h"
extern "C" {
#include <libavformat/avformat.h>
#include <libavutil/opt.h>
}
#include <algorithm>
#include <cstring>
#include <deque>
#include <limits>
#include <mutex>

namespace live {
namespace {
constexpr int64_t invalid=std::numeric_limits<int64_t>::min();
int64_t ticks(CMTime t) {
    return CMTIME_IS_NUMERIC(t)?CMTimeConvertScale(t,90000,kCMTimeRoundingMethod_RoundHalfAwayFromZero).value:invalid;
}
struct Sample {
    clip::OwnedPacket packet;
    int64_t dts, duration;
    bool key;
    // Assign whole AAC frames by their midpoint. Audio boundaries then differ
    // from the video IDR by at most half an AAC frame, without loss/duplication.
    int64_t position() const { return dts+(packet->track==clip::Track::audio?duration/2:0); }
};
bool append(std::vector<uint8_t>& out,const uint8_t *p,size_t n,size_t cap) {
    if(n>cap-out.size()) return false;
    out.insert(out.end(),p,p+n); return true;
}
bool annexB(const Sample& s,std::vector<uint8_t>& out,size_t cap) {
    auto format=s.packet->format();
    const uint8_t *param=nullptr; size_t length=0,count=0; int prefix=0;
    if(CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format,0,&param,&length,&count,&prefix) ||
       prefix<1 || prefix>4 || count<2) return false;
    static const uint8_t start[]={0,0,0,1};
    if(s.key) for(size_t i=0;i<count;++i) {
        if(CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format,i,&param,&length,nullptr,nullptr) ||
           !append(out,start,4,cap) || !append(out,param,length,cap)) return false;
    }
    auto block=CMSampleBufferGetDataBuffer(s.packet->sample());
    size_t total=block?CMBlockBufferGetDataLength(block):0,offset=0;
    bool idr=false;
    while(offset<total) {
        uint8_t header[4]={};
        if(total-offset<(size_t)prefix || CMBlockBufferCopyDataBytes(block,offset,prefix,header)) return false;
        size_t n=0; for(int i=0;i<prefix;++i) n=(n<<8)|header[i];
        offset+=prefix;
        if(!n || n>total-offset || cap-out.size()<4 || n>cap-out.size()-4) return false;
        if(!append(out,start,4,cap)) return false;
        size_t begin=out.size(); out.resize(begin+n);
        if(CMBlockBufferCopyDataBytes(block,offset,n,out.data()+begin)) return false;
        idr|=(out[begin]&31)==5;
        offset+=n;
    }
    return total && idr==s.key;
}
bool adts(const Sample& s,std::vector<uint8_t>& out,size_t cap) {
    auto asbd=CMAudioFormatDescriptionGetStreamBasicDescription(s.packet->format());
    if(!asbd || asbd->mFormatID!=kAudioFormatMPEG4AAC || asbd->mSampleRate!=48000 ||
       asbd->mChannelsPerFrame!=2 || asbd->mFramesPerPacket!=1024) return false;
    auto block=CMSampleBufferGetDataBuffer(s.packet->sample());
    size_t n=block?CMBlockBufferGetDataLength(block):0,length=n+7;
    if(!n || length>8191 || length>cap) return false;
    // AAC-LC, 48 kHz, stereo, MPEG-4, no CRC; raw AAC payload stays unchanged.
    uint8_t header[]={0xff,0xf1,0x4c,(uint8_t)(0x80|(length>>11)),
        (uint8_t)(length>>3),(uint8_t)(((length&7)<<5)|0x1f),0xfc};
    out.assign(header,header+7); out.resize(length);
    return !CMBlockBufferCopyDataBytes(block,0,n,out.data()+7);
}
struct Mux {
    AVFormatContext *format=nullptr;
    AVIOContext *io=nullptr;
    Segment &segment;
    size_t cap;
    int64_t *lastDTS;
    uint64_t &nonMonotonic;
    Mux(Segment& s,size_t c,int64_t *last,uint64_t &faults):segment(s),cap(c),lastDTS(last),nonMonotonic(faults) {}
    ~Mux() {
        if(format) avformat_free_context(format);
        if(io) { av_freep(&io->buffer); avio_context_free(&io); }
    }
    static int write(void *context,const uint8_t *p,int n) {
        auto &m=*(Mux*)context;
        return n>=0 && append(m.segment.bytes,p,n,m.cap)?n:AVERROR(ENOSPC);
    }
    bool run(std::vector<Sample>& samples) {
        if(avformat_alloc_output_context2(&format,nullptr,"mpegts",nullptr)<0 || !format) return false;
        uint8_t *buffer=(uint8_t*)av_malloc(32768);
        if(!buffer) return false;
        io=avio_alloc_context(buffer,32768,1,this,nullptr,write,nullptr);
        if(!io) { av_free(buffer); return false; }
        format->pb=io; format->flags|=AVFMT_FLAG_CUSTOM_IO;
        format->avoid_negative_ts=AVFMT_AVOID_NEG_TS_DISABLED;
        format->max_delay=0;
        for(int i=0;i<2;++i) {
            auto stream=avformat_new_stream(format,nullptr); if(!stream) return false;
            stream->time_base={1,90000};
            stream->codecpar->codec_type=i?AVMEDIA_TYPE_AUDIO:AVMEDIA_TYPE_VIDEO;
            stream->codecpar->codec_id=i?AV_CODEC_ID_AAC:AV_CODEC_ID_H264;
            if(i) { stream->codecpar->sample_rate=48000; av_channel_layout_default(&stream->codecpar->ch_layout,2); }
            else {
                auto p=std::find_if(samples.begin(),samples.end(),[](const Sample& s){ return s.packet->track==clip::Track::video; });
                if(p==samples.end()) return false;
                auto size=CMVideoFormatDescriptionGetDimensions(p->packet->format());
                stream->codecpar->width=size.width; stream->codecpar->height=size.height;
            }
        }
        AVDictionary *options=nullptr;
        av_dict_set(&options,"mpegts_copyts","1",0);
        av_dict_set(&options,"mpegts_flags","+resend_headers",0);
        int rc=avformat_write_header(format,&options); av_dict_free(&options);
        if(rc<0) return false;
        std::stable_sort(samples.begin(),samples.end(),[](const Sample& a,const Sample& b){ return a.dts<b.dts; });
        for(const auto &s:samples) {
            std::vector<uint8_t> bytes;
            bool audio=s.packet->track==clip::Track::audio;
            if(!(audio?adts(s,bytes,cap):annexB(s,bytes,cap))) return false;
            AVPacket *packet=av_packet_alloc(); if(!packet) return false;
            rc=av_new_packet(packet,(int)bytes.size());
            if(rc>=0) {
                memcpy(packet->data,bytes.data(),bytes.size());
                packet->stream_index=audio?1:0;
                // A fixed transport epoch keeps the initial AAC frame's small
                // negative priming timestamp away from MPEG-TS's 33-bit wrap.
                // Apply the same offset to both tracks in every segment.
                packet->pts=packet->dts=s.dts+90000;
                // Audit the actual transport timestamps across muxer instances.
                // This also makes terminal capture diagnostics cover output DTS.
                int track=audio?1:0;
                if(lastDTS[track]!=invalid && packet->dts<=lastDTS[track]) ++nonMonotonic;
                lastDTS[track]=packet->dts;
                packet->duration=s.duration;
                if(s.key) packet->flags|=AV_PKT_FLAG_KEY;
                rc=av_write_frame(format,packet);
            }
            av_packet_free(&packet);
            if(rc<0) return false;
        }
        if(av_write_trailer(format)<0) return false;
        avio_flush(io);
        // FFmpeg emits SDT, PAT, PMT before the first PES. HLS ingest wants
        // PAT/PMT first; move that optional SDT behind them without copying.
        auto pid=[&](size_t offset) { return ((segment.bytes[offset+1]&31)<<8)|segment.bytes[offset+2]; };
        if(segment.bytes.size()>=3*188 && pid(0)==17 && pid(188)==0 && pid(376)==4096)
            std::rotate(segment.bytes.begin(),segment.bytes.begin()+188,segment.bytes.begin()+3*188);
        return io->error>=0 && !segment.bytes.empty();
    }
};
}
struct Segmenter::State {
    mutable std::mutex lock;
    Output output;
    SegmentLimits limits;
    SegmentStats stats;
    std::deque<Sample> pending;
    size_t bytes=0;
    int64_t last[2]={invalid,invalid},start=invalid,end=invalid;
    int64_t lastMuxDTS[2]={invalid,invalid};
    bool finished=false;
    struct Leases { std::atomic<size_t> held{0}; };
    std::shared_ptr<Leases> leases=std::make_shared<Leases>();
    State(Output o,SegmentLimits l):output(std::move(o)),limits(l) {}
    bool fail(const char *why) {
        if(!stats.failed) stats.reason=why;
        stats.failed=true; pending.clear(); bytes=0; return false;
    }
    bool emit(int64_t cut,bool final=false) {
        // A delayed IDR must never produce an ingest segment above five seconds.
        if(cut-start>450000) return fail("no-idr-5s");
        if(leases->held.load()>=limits.heldSegments) return fail("ingest-backlog");
        std::vector<Sample> samples;
        bool video=false,audio=false;
        for(auto i=pending.begin();i!=pending.end();) {
            if(i->position()<cut) {
                bytes-=i->packet->bytes;
                if(i->position()>=start) {
                    if(i->packet->track==clip::Track::video) {
                        if(!video && !i->key) return fail("no-keyframe");
                        video=true;
                    } else audio=true;
                    samples.push_back(std::move(*i));
                }
                i=pending.erase(i);
            } else ++i;
        }
        // A final partial GOP can be shorter than the gap between AAC midpoints.
        if(!video || (!audio && !final)) return fail(!video?"no-video":"no-audio");
        auto lease=leases; ++lease->held;
        stats.heldPeak=std::max(stats.heldPeak,lease->held.load());
        std::shared_ptr<Segment> segment(new Segment,[lease](Segment *s){ delete s; --lease->held; });
        segment->sequence=stats.segments; segment->start90k=start; segment->end90k=cut;
        if(!Mux(*segment,limits.segmentBytes,lastMuxDTS,stats.nonMonotonic).run(samples)) return fail("mux");
        if(output && !output(segment)) return fail("ingest-closed");
        ++stats.segments; start=cut; return true;
    }
    bool flushReady() {
        if(start==invalid) return true;
        for(;;) {
            int64_t cut=invalid;
            for(auto &s:pending) if(s.packet->track==clip::Track::video && s.key && s.dts-start>=180000) {
                cut=s.dts; break;
            }
            // An audio watermark guarantees no late AAC can enter the old GOP.
            if(cut==invalid || last[1]==invalid || last[1]+960<cut) return true;
            if(!emit(cut)) return false;
        }
    }
};
Segmenter::Segmenter(Output output,SegmentLimits limits):state(new State(std::move(output),limits)) {}
Segmenter::~Segmenter()=default;
clip::Result Segmenter::consume(const clip::OwnedPacket& p) {
    auto &s=*state; std::lock_guard<std::mutex> guard(s.lock);
    if(s.finished || s.stats.failed) return clip::Result::failed;
    if(!p || !p->format()) { s.fail("format"); return clip::Result::failed; }
    int track=p->track==clip::Track::audio?1:0;
    auto sample=p->sample();
    int64_t pts=ticks(CMSampleBufferGetPresentationTimeStamp(sample));
    int64_t dts=ticks(CMSampleBufferGetDecodeTimeStamp(sample));
    if(dts==invalid) dts=pts;
    int64_t duration=ticks(CMSampleBufferGetDuration(sample));
    bool key=false;
    if(!track) {
        if(CMFormatDescriptionGetMediaSubType(p->format())!=kCMVideoCodecType_H264) { s.fail("format"); return clip::Result::failed; }
        auto attrs=CMSampleBufferGetSampleAttachmentsArray(sample,false);
        key=!attrs || !CFArrayGetCount(attrs) || CFDictionaryGetValue((CFDictionaryRef)CFArrayGetValueAtIndex(attrs,0),kCMSampleAttachmentKey_NotSync)!=kCFBooleanTrue;
    }
    if(pts==invalid || dts==invalid || pts!=dts || duration<=0 || duration==invalid ||
       (s.last[track]!=invalid && dts<=s.last[track])) {
        if(s.last[track]!=invalid && dts<=s.last[track]) ++s.stats.nonMonotonic;
        s.fail("timestamps"); return clip::Result::failed;
    }
    if(p->bytes>s.limits.pendingBytes-s.bytes || s.pending.size()>=s.limits.pendingPackets) { s.fail("queue-full"); return clip::Result::failed; }
    s.last[track]=dts;
    if(track) ++s.stats.audio;
    else {
        ++s.stats.video;
        if(s.start==invalid) {
            if(!key) { s.fail("no-keyframe"); return clip::Result::failed; }
            s.start=dts;
        }
        s.end=dts+duration;
    }
    s.pending.push_back({p,dts,duration,key}); s.bytes+=p->bytes;
    s.stats.pendingPeak=std::max(s.stats.pendingPeak,s.bytes);
    return s.flushReady()?clip::Result::consumed:clip::Result::failed;
}
bool Segmenter::finish() {
    auto &s=*state; std::lock_guard<std::mutex> guard(s.lock);
    if(s.finished) return !s.stats.failed;
    s.finished=true;
    if(s.stats.failed) return false;
    if(!s.flushReady()) return false;
    if(s.start!=invalid && s.end>s.start && !s.emit(s.end,true)) return false;
    if(s.stats.nonMonotonic) return s.fail("timestamps");
    s.pending.clear(); s.bytes=0; return true;
}
SegmentStats Segmenter::stats() const { std::lock_guard<std::mutex> guard(state->lock); return state->stats; }
std::unique_ptr<SegmentFeed> SegmentFeed::create(bool active,Segmenter::Output output,std::shared_ptr<clip::Sink> observer) {
    if(!active) return {};
    if(!output) output=streamOutput();
    std::unique_ptr<SegmentFeed> feed(new SegmentFeed);
    feed->segmenter=std::make_shared<Segmenter>(std::move(output));
    auto segmenter=feed->segmenter;
    feed->video=std::make_shared<clip::Sink>([segmenter,observer](const clip::OwnedPacket& p) {
        if(observer && !observer->push(p)) return clip::Result::failed;
        return segmenter->consume(p);
    },512,16*1024*1024);
    feed->audio=std::make_shared<clip::Sink>([segmenter](const clip::OwnedPacket& p) {
        return p->track==clip::Track::audio?segmenter->consume(p):clip::Result::consumed;
    },512,4*1024*1024);
    return feed;
}
bool SegmentFeed::drained() const { return video->drained() && audio->drained(); }
bool SegmentFeed::finish() {
    video->close(); audio->close();
    if(!drained()) return false;
    return !video->failed() && !audio->failed() && segmenter->finish();
}
}
