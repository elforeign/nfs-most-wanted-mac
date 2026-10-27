// media.cpp - see media.h. FFmpeg 7.1 (static, port/third_party/ffmpeg) + CoreAudio.
#include "media.h"
#include "movie_api.h"

#include <AudioToolbox/AudioToolbox.h>
#include <CoreAudio/CoreAudio.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <deque>
#include <mutex>
#include <cstdio>
#include <cstring>
#include <thread>
#include <unistd.h>

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/channel_layout.h>
#include <libavutil/imgutils.h>
#include <libavutil/opt.h>
#include <libswresample/swresample.h>
#include <libswscale/swscale.h>
}

namespace media {

static AVChannelLayout surround_layout() {
    AVChannelLayout l;
    av_channel_layout_from_mask(&l, AV_CH_LAYOUT_5POINT1_BACK);   // FL FR FC LFE BL BR
    return l;
}

void fold_to_stereo(const float *in, float *out, size_t frames) {
    const float k = 0.70710678f;
    for (size_t i = 0; i < frames; ++i) {
        const float *s = in + i * 6;
        float l = s[0] + k * s[2] + k * s[4], r = s[1] + k * s[2] + k * s[5];
        out[i * 2] = l / (1.0f + 2 * k);
        out[i * 2 + 1] = r / (1.0f + 2 * k);
    }
}

// ------------------------------------------------------------------ MovieDecoder
struct MovieDecoder::Impl {
    AVFormatContext *fmt = nullptr;
    AVCodecContext *vdec = nullptr, *adec = nullptr;
    int vstream = -1, astream = -1;
    SwsContext *sws = nullptr;
    SwrContext *swr = nullptr;
    AVPacket *pkt = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    int out_channels = 6, out_rate = 48000;
    std::deque<float> audio;
    bool eof = false;

    ~Impl() {
        sws_freeContext(sws);
        swr_free(&swr);
        avcodec_free_context(&vdec);
        avcodec_free_context(&adec);
        avformat_close_input(&fmt);
        av_packet_free(&pkt);
        av_frame_free(&frame);
    }

    bool open_codec(int idx, AVCodecContext **ctx) {
        const AVCodec *c = avcodec_find_decoder(fmt->streams[idx]->codecpar->codec_id);
        if (!c) return false;
        *ctx = avcodec_alloc_context3(c);
        avcodec_parameters_to_context(*ctx, fmt->streams[idx]->codecpar);
        return avcodec_open2(*ctx, c, nullptr) == 0;
    }

    void drain_audio() {
        while (avcodec_receive_frame(adec, frame) == 0) {
            if (!swr) {
                AVChannelLayout out_l = out_channels == 6 ? surround_layout() : AVChannelLayout(AV_CHANNEL_LAYOUT_STEREO);
                swr_alloc_set_opts2(&swr, &out_l, AV_SAMPLE_FMT_FLT, out_rate, &frame->ch_layout,
                                    (AVSampleFormat)frame->format, frame->sample_rate, 0, nullptr);
                swr_init(swr);
            }
            int max_out = swr_get_out_samples(swr, frame->nb_samples);
            std::vector<float> buf((size_t)std::max(max_out, 0) * out_channels);
            uint8_t *outp[1] = {(uint8_t *)buf.data()};
            int n = swr_convert(swr, outp, max_out, (const uint8_t **)frame->extended_data, frame->nb_samples);
            if (n > 0) audio.insert(audio.end(), buf.begin(), buf.begin() + (size_t)n * out_channels);
        }
    }

    bool drain_video(VideoFrame &out) {
        if (avcodec_receive_frame(vdec, frame) != 0) return false;
        int w = frame->width, h = frame->height;
        sws = sws_getCachedContext(sws, w, h, (AVPixelFormat)frame->format, w, h, AV_PIX_FMT_BGRA, SWS_BILINEAR,
                                   nullptr, nullptr, nullptr);
        out.width = w;
        out.height = h;
        out.bgra.resize((size_t)w * h * 4);
        uint8_t *dst[1] = {out.bgra.data()};
        int stride[1] = {w * 4};
        sws_scale(sws, frame->data, frame->linesize, 0, h, dst, stride);
        AVRational tb = fmt->streams[vstream]->time_base;
        out.pts = frame->best_effort_timestamp == AV_NOPTS_VALUE ? 0 : frame->best_effort_timestamp * av_q2d(tb);
        return true;
    }
};

MovieDecoder::MovieDecoder() : d(new Impl) {}
MovieDecoder::~MovieDecoder() = default;

bool MovieDecoder::open(const std::string &path, int out_channels, int out_rate, bool video, bool audio) {
    d->out_channels = out_channels;
    d->out_rate = out_rate;
    if (avformat_open_input(&d->fmt, path.c_str(), nullptr, nullptr) != 0) return false;
    if (avformat_find_stream_info(d->fmt, nullptr) < 0) return false;
    d->vstream = video ? av_find_best_stream(d->fmt, AVMEDIA_TYPE_VIDEO, -1, -1, nullptr, 0) : -1;
    d->astream = audio ? av_find_best_stream(d->fmt, AVMEDIA_TYPE_AUDIO, -1, -1, nullptr, 0) : -1;
    if (video && (d->vstream < 0 || !d->open_codec(d->vstream, &d->vdec))) return false;
    if (d->astream >= 0 && !d->open_codec(d->astream, &d->adec)) d->astream = -1;
    return video || d->adec;
}

bool MovieDecoder::pump_audio(size_t min_frames) {
    while (d->audio.size() / (size_t)d->out_channels < min_frames) {
        if (d->eof || !d->adec) return false;
        if (av_read_frame(d->fmt, d->pkt) < 0) {
            d->eof = true;
            avcodec_send_packet(d->adec, nullptr);
            d->drain_audio();
            return false;
        }
        if (d->pkt->stream_index == d->astream) {
            avcodec_send_packet(d->adec, d->pkt);
            d->drain_audio();
        }
        av_packet_unref(d->pkt);
    }
    return true;
}

bool MovieDecoder::next_frame(VideoFrame &out) {
    if (!d->vdec) return false;
    for (;;) {
        if (d->drain_video(out)) return true;
        if (d->eof) return false;
        if (av_read_frame(d->fmt, d->pkt) < 0) {
            d->eof = true;
            avcodec_send_packet(d->vdec, nullptr);
            if (d->adec) {
                avcodec_send_packet(d->adec, nullptr);
                d->drain_audio();
            }
            continue;
        }
        if (d->pkt->stream_index == d->vstream) {
            avcodec_send_packet(d->vdec, d->pkt);
        } else if (d->pkt->stream_index == d->astream) {
            avcodec_send_packet(d->adec, d->pkt);
            d->drain_audio();
        }
        av_packet_unref(d->pkt);
    }
}

size_t MovieDecoder::take_audio(float *dst, size_t max_frames) {
    size_t n = std::min(max_frames, d->audio.size() / (size_t)d->out_channels);
    std::copy(d->audio.begin(), d->audio.begin() + n * d->out_channels, dst);
    d->audio.erase(d->audio.begin(), d->audio.begin() + n * d->out_channels);
    return n;
}

int MovieDecoder::width() const { return d->vdec ? d->vdec->width : 0; }
int MovieDecoder::height() const { return d->vdec ? d->vdec->height : 0; }
double MovieDecoder::duration() const { return d->fmt ? d->fmt->duration / (double)AV_TIME_BASE : 0; }
int MovieDecoder::source_channels() const { return d->adec ? d->adec->ch_layout.nb_channels : 0; }
std::string MovieDecoder::describe() const {
    char buf[256];
    snprintf(buf, sizeof buf, "%s %dx%d, audio %s %d ch %d Hz", d->vdec ? d->vdec->codec->name : "-", width(), height(),
             d->adec ? d->adec->codec->name : "-", source_channels(), d->adec ? d->adec->sample_rate : 0);
    return buf;
}

// ------------------------------------------------------------------ Ac3Spdif
struct Ac3Spdif::Impl {
    AVCodecContext *enc = nullptr;
    AVFrame *frame = av_frame_alloc();
    AVPacket *pkt = av_packet_alloc();
    std::vector<float> pending;   // interleaved 6-ch
    bool eac3 = false;
    ~Impl() {
        avcodec_free_context(&enc);
        av_frame_free(&frame);
        av_packet_free(&pkt);
    }
    bool open(AVCodecID id, int bitrate) {
        avcodec_free_context(&enc);
        pending.clear();
        eac3 = id == AV_CODEC_ID_EAC3;
        const AVCodec *c = avcodec_find_encoder(id);
        if (!c) return false;
        enc = avcodec_alloc_context3(c);
        enc->sample_rate = 48000;
        enc->sample_fmt = AV_SAMPLE_FMT_FLTP;
        enc->bit_rate = bitrate;
        enc->ch_layout = surround_layout();
        // One frame per burst: AC-3 frames are always 1536 samples; E-AC-3 ones are only at <= 1,024 kbit/s (6 blocks).
        if (avcodec_open2(enc, c, nullptr) < 0 || enc->frame_size != kFramesPerBurst) return false;
        av_frame_unref(frame);
        frame->nb_samples = kFramesPerBurst;
        frame->format = AV_SAMPLE_FMT_FLTP;
        av_channel_layout_copy(&frame->ch_layout, &enc->ch_layout);
        return av_frame_get_buffer(frame, 0) == 0;
    }
};

Ac3Spdif::Ac3Spdif() : d(new Impl) {}
Ac3Spdif::~Ac3Spdif() = default;

bool Ac3Spdif::open(int bitrate) { return d->open(AV_CODEC_ID_AC3, bitrate); }
bool Ac3Spdif::open_eac3(int bitrate) { return d->open(AV_CODEC_ID_EAC3, bitrate); }
bool Ac3Spdif::eac3() const { return d->eac3; }
size_t Ac3Spdif::burst_bytes() const { return d->eac3 ? kEac3BurstBytes : kBurstBytes; }
size_t Ac3Spdif::pending_frames() const { return d->pending.size() / 6; }

// Pa Pb Pc Pd, then the frame's big-endian 16-bit words as little-endian samples, then zeros to the burst size.
static size_t put_burst(uint16_t pc, uint16_t pd, const uint8_t *frame, size_t bytes, uint8_t *burst, size_t burst_bytes) {
    auto put = [burst](size_t at, uint16_t v) { burst[at] = uint8_t(v); burst[at + 1] = uint8_t(v >> 8); };
    put(0, 0xF872);
    put(2, 0x4E1F);
    put(4, pc);
    put(6, pd);
    size_t i = 0;
    for (; i + 1 < bytes; i += 2) {          // AC-3 is big-endian 16-bit words; IEC 61937 samples are LE
        burst[8 + i] = frame[i + 1];
        burst[9 + i] = frame[i];
    }
    if (i < bytes) {
        burst[8 + i] = 0;
        burst[9 + i] = frame[i];
        i += 2;
    }
    memset(burst + 8 + i, 0, burst_bytes - 8 - i);
    return burst_bytes;
}

size_t Ac3Spdif::wrap(const uint8_t *frame, size_t bytes, uint8_t *burst) {
    if (!frame || bytes < 6 || bytes > kBurstBytes - 8 || frame[0] != 0x0B || frame[1] != 0x77) return 0;
    const unsigned bsmod = frame[5] & 7u;
    return put_burst(uint16_t(0x0001u | (bsmod << 8)), uint16_t(bytes * 8), frame, bytes, burst, kBurstBytes);
}

size_t Ac3Spdif::wrap_eac3(const uint8_t *frame, size_t bytes, uint8_t *burst) {
    if (!frame || bytes < 6 || bytes > kEac3BurstBytes - 8 || frame[0] != 0x0B || frame[1] != 0x77) return 0;
    // An independent substream (strmtyp 0) with bsid 16 and 6 blocks (fscod 3 means 6 blocks at a reduced rate), whose
    // frmsiz matches: anything else would need several frames per burst.
    const unsigned strmtyp = frame[2] >> 6, frmsiz = ((frame[2] & 7u) << 8 | frame[3]) + 1, fscod = frame[4] >> 6,
                   numblkscod = fscod == 3 ? 3 : (frame[4] >> 4) & 3, bsid = frame[5] >> 3;
    if (bsid != 16 || strmtyp != 0 || numblkscod != 3 || frmsiz * 2 != bytes) return 0;
    return put_burst(0x0015, uint16_t(bytes), frame, bytes, burst, kEac3BurstBytes);
}

void Ac3Spdif::push(const float *pcm, size_t frames, std::vector<uint8_t> &out) {
    if (!d->enc) return;
    const size_t burst = burst_bytes();
    d->pending.insert(d->pending.end(), pcm, pcm + frames * 6);
    while (d->pending.size() >= (size_t)kFramesPerBurst * 6) {
        av_frame_make_writable(d->frame);
        // AC-3's 5.1 channel order in FFmpeg follows the layout mask: FL FR FC LFE BL BR
        for (int ch = 0; ch < 6; ++ch) {
            float *plane = (float *)d->frame->data[ch];
            for (int i = 0; i < kFramesPerBurst; ++i) plane[i] = d->pending[(size_t)i * 6 + ch];
        }
        d->pending.erase(d->pending.begin(), d->pending.begin() + (size_t)kFramesPerBurst * 6);
        if (avcodec_send_frame(d->enc, d->frame) < 0) continue;
        while (avcodec_receive_packet(d->enc, d->pkt) == 0) {
            size_t at = out.size();
            out.resize(at + burst);
            size_t ok = d->eac3 ? wrap_eac3(d->pkt->data, d->pkt->size, out.data() + at)
                                : wrap(d->pkt->data, d->pkt->size, out.data() + at);
            if (!ok) out.resize(at);
            av_packet_unref(d->pkt);
        }
    }
}

// ------------------------------------------------------------------ TrueHdMat
struct TrueHdMat::Impl {
    AVCodecContext *enc = nullptr;
    AVFormatContext *mux = nullptr;   // FFmpeg's spdif muxer, writing into `sink`
    AVFrame *frame = av_frame_alloc();
    AVPacket *pkt = av_packet_alloc();
    std::vector<float> pending;       // interleaved 6-ch, less than one access unit
    std::vector<uint8_t> *sink = nullptr;
    uint64_t pushed = 0, bursts = 0;  // input frames taken; MAT bursts written
    int64_t next_pts = 0;
    ~Impl() { close(); av_frame_free(&frame); av_packet_free(&pkt); }
    void close() {
        if (mux) {
            if (mux->pb) { av_freep(&mux->pb->buffer); avio_context_free(&mux->pb); }
            avformat_free_context(mux);
            mux = nullptr;
        }
        avcodec_free_context(&enc);
        pending.clear();
        pushed = bursts = 0;
        next_pts = 0;
    }
    static int write(void *self, const uint8_t *buf, int size) {
        auto *s = (Impl *)self;
        if (s->sink) s->sink->insert(s->sink->end(), buf, buf + size);
        return size;
    }
};

TrueHdMat::TrueHdMat() : d(new Impl) {}
TrueHdMat::~TrueHdMat() = default;

bool TrueHdMat::open() {
    d->close();
    const AVCodec *c = avcodec_find_encoder(AV_CODEC_ID_TRUEHD);
    if (!c) return false;
    d->enc = avcodec_alloc_context3(c);
    d->enc->sample_rate = 48000;
    d->enc->sample_fmt = AV_SAMPLE_FMT_S32P;                   // 24-bit TrueHD (the samples' top 24 bits)
    d->enc->strict_std_compliance = FF_COMPLIANCE_EXPERIMENTAL; // FFmpeg marks its TrueHD encoder experimental
    av_channel_layout_from_mask(&d->enc->ch_layout, AV_CH_LAYOUT_5POINT1);   // FL FR FC LFE SL SR: TrueHD's 5.1
    if (avcodec_open2(d->enc, c, nullptr) < 0 || d->enc->frame_size != 40) return false;
    av_frame_unref(d->frame);
    d->frame->nb_samples = 40;
    d->frame->format = AV_SAMPLE_FMT_S32P;
    av_channel_layout_copy(&d->frame->ch_layout, &d->enc->ch_layout);
    if (av_frame_get_buffer(d->frame, 0) < 0) return false;
    if (avformat_alloc_output_context2(&d->mux, nullptr, "spdif", nullptr) < 0) return false;
    AVStream *st = avformat_new_stream(d->mux, nullptr);
    if (!st || avcodec_parameters_from_context(st->codecpar, d->enc) < 0) return false;
    const int iosize = 1 << 16;
    uint8_t *iobuf = (uint8_t *)av_malloc(iosize);
    d->mux->pb = avio_alloc_context(iobuf, iosize, 1, d.get(), nullptr, Impl::write, nullptr);
    std::vector<uint8_t> none;
    d->sink = &none;
    bool ok = d->mux->pb && avformat_write_header(d->mux, nullptr) >= 0;
    if (ok) avio_flush(d->mux->pb);
    d->sink = nullptr;
    return ok && none.empty();
}

void TrueHdMat::push(const float *pcm, size_t frames, std::vector<uint8_t> &out) {
    if (!d->mux) return;
    d->pending.insert(d->pending.end(), pcm, pcm + frames * 6);
    d->pushed += frames;
    d->sink = &out;
    const size_t start = out.size();
    while (d->pending.size() >= 40 * 6) {
        av_frame_make_writable(d->frame);
        for (int ch = 0; ch < 6; ++ch) {                       // FL FR FC LFE BL BR -> the same slots (BL/BR as SL/SR)
            int32_t *plane = (int32_t *)d->frame->data[ch];
            for (int i = 0; i < 40; ++i) {
                double v = std::lrint((double)d->pending[(size_t)i * 6 + ch] * 8388608.0);
                plane[i] = (int32_t)std::min(8388607.0, std::max(-8388608.0, v)) * 256;
            }
        }
        d->pending.erase(d->pending.begin(), d->pending.begin() + 40 * 6);
        d->frame->pts = d->next_pts;                           // the muxer wants timestamps, even though spdif ignores them
        d->next_pts += 40;
        if (avcodec_send_frame(d->enc, d->frame) < 0) continue;
        while (avcodec_receive_packet(d->enc, d->pkt) == 0) {
            d->pkt->stream_index = 0;
            av_write_frame(d->mux, d->pkt);                    // a burst comes out when a MAT frame is full
            av_packet_unref(d->pkt);
        }
    }
    avio_flush(d->mux->pb);
    d->sink = nullptr;
    d->bursts += (out.size() - start) / kBurstBytes;
}

size_t TrueHdMat::pending_frames() const {
    uint64_t done = d->bursts * kFramesPerBurst;
    return d->pushed > done ? (size_t)(d->pushed - done) : 0;
}

bool TrueHdMat::unwrap(const uint8_t *b, size_t bytes, std::vector<uint8_t> &truehd) {
    if (bytes < 8 + kMatFrameBytes || (b[0] | b[1] << 8) != 0xF872 || (b[2] | b[3] << 8) != 0x4E1F ||
        ((b[4] | b[5] << 8) & 0x1F) != 0x16)
        return false;
    std::vector<uint8_t> mat(kMatFrameBytes);
    for (size_t i = 0; i < kMatFrameBytes; i += 2) { mat[i] = b[8 + i + 1]; mat[i + 1] = b[8 + i]; }
    // the codes spdifenc.c puts at 0 (20 bytes), 30,708 (12) and 61,408 (16)
    static const struct { size_t pos, len; uint8_t first; } codes[] = {{0, 20, 0x07}, {30708, 12, 0xC3}, {61408, 16, 0xC3}};
    size_t at = 0;
    for (auto &c : codes) {
        if (mat[c.pos] != c.first) return false;
        truehd.insert(truehd.end(), mat.begin() + at, mat.begin() + c.pos);
        at = c.pos + c.len;
    }
    return true;
}

// ------------------------------------------------------------------ default device's speaker layout
const char *output_mode_name(OutputMode m) {
    switch (m) {
    case OutputMode::Pcm51: return "pcm51";
    case OutputMode::Stereo: return "stereo";
    case OutputMode::DolbyDigital: return "dd";
    case OutputMode::DolbyDigitalPlus: return "ddp";
    default: return "truehd";
    }
}

// ------------------------------------------------------------------ fake device (NFS_AUDIO_FAKE_DEVICE)
// Unattended tests of the output-device logic without real audio: every AudioOutput opens on a fake HDMI TV that takes
// MAT, AC-3 and E-AC-3 (or on fake speakers), drained by a thread at the device rate; nothing reaches CoreAudio. As on
// macOS, while a passthrough output holds the TV (hog mode) the default output moves to the speakers, and it comes back
// 0.3 s after the passthrough is closed. Each open and close is logged ("media: fake device: ...").
static constexpr uint32_t kFakeTv = 0x7FA0, kFakeSpeakers = 0x7FA1;
static bool fake_device() {
    static const bool on = getenv("NFS_AUDIO_FAKE_DEVICE") != nullptr;
    return on;
}
static std::atomic<uint64_t> g_devices_gen{0};
static std::atomic<int> g_fake_hogs{0};
static std::atomic<int64_t> g_fake_unhog_ns{-1000000000000LL};
static int64_t mono_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
static uint32_t fake_default() {
    return g_fake_hogs > 0 || mono_ns() - g_fake_unhog_ns.load() < 300000000 ? kFakeSpeakers : kFakeTv;
}

// The fake TV's receiver: what a TV would get from each fake output, decoded and checked, so a guarded run (runtime/gate.sh's
// smoke launch) sees the stream the user hears without a device. Bitstreams: every IEC 61937 burst (Pa/Pb sync, Pc data
// type for the mode, nothing but zero stuffing between bursts), its payload decoded with FFmpeg's truehd / ac3 / eac3
// decoder (TrueHD: the MAT frame unwrapped, each access unit's input timing 40 samples on from the last); TrueHD is
// lossless, so every decoded sample is compared with what the mixer wrote (quantised to 24 bits as the encoder does):
// a channel order, scaling or framing fault shows as mismatched frames. PCM: the floats as they leave. Per channel RMS
// and peak (as the decoder hands them out: FL FR FC LFE SL SR, the mixer's FL FR C LFE BL BR), samples at full scale
// (clipped) and not finite. One line a second per output and a total at close in $BOOT_DIAG/fake_tv.log (stderr's
// "media: fake TV:" total without BOOT_DIAG).
struct FakeRx {
    OutputMode mode;
    int rate, channels;
    unsigned id;
    int64_t t0, sec0;
    std::vector<uint8_t> buf;            // bitstream bytes not yet parsed
    AVCodecContext *dec = nullptr;
    AVPacket *pkt = av_packet_alloc();
    AVFrame *frm = av_frame_alloc();
    std::mutex in_m;
    std::deque<int32_t> in_q;            // TrueHD: what the mixer wrote, 24-bit, 6 per frame, not yet decoded
    int last_timing = -1;
    struct Stats {
        uint64_t bursts = 0, frames = 0, junk = 0, badtype = 0, decode_errors = 0, timing_errors = 0, exact = 0, mismatched = 0,
                 clipped = 0, nonfinite = 0;
        double sum2[6] = {0}, peak[6] = {0};
    } sec, all;
    static FILE *log() {
        static FILE *f = [] {
            const char *d = getenv("BOOT_DIAG");
            if (!d) return (FILE *)nullptr;
            FILE *o = fopen((std::string(d) + "/fake_tv.log").c_str(), "a");
            if (o) setvbuf(o, nullptr, _IOLBF, 0);
            return o;
        }();
        return f;
    }
    FakeRx(OutputMode m, int r, int ch, const std::string &dev) : mode(m), rate(r), channels(ch) {
        static std::atomic<unsigned> next{0};
        id = ++next;
        t0 = sec0 = mono_ns();
        AVCodecID cid = m == OutputMode::TrueHd ? AV_CODEC_ID_TRUEHD : m == OutputMode::DolbyDigitalPlus ? AV_CODEC_ID_EAC3
                      : m == OutputMode::DolbyDigital ? AV_CODEC_ID_AC3 : AV_CODEC_ID_NONE;
        if (cid != AV_CODEC_ID_NONE) {
            const AVCodec *c = avcodec_find_decoder(cid);
            dec = c ? avcodec_alloc_context3(c) : nullptr;
            if (dec && avcodec_open2(dec, c, nullptr) < 0) avcodec_free_context(&dec);
        }
        if (FILE *f = log())
            fprintf(f, "open %u %s %d Hz %s on \"%s\"%s\n", id, output_mode_name(m), r,
                    bitstream() ? "IEC 61937" : ch == 6 ? "6 ch" : "2 ch", dev.c_str(), cid != AV_CODEC_ID_NONE && !dec ? " (no decoder)" : "");
    }
    ~FakeRx() {
        line("close", all, (mono_ns() - t0) / 1e9);
        avcodec_free_context(&dec);
        av_packet_free(&pkt);
        av_frame_free(&frm);
    }
    void line(const char *what, const Stats &s, double secs) {
        char b[900];
        int n = snprintf(b, sizeof b, "%s %u %s t %.1f s bursts %llu frames %llu junk %llu badtype %llu decode_errors %llu "
                         "timing_errors %llu exact %llu mismatched %llu clipped %llu nonfinite %llu rms", what, id,
                         output_mode_name(mode), secs, (unsigned long long)s.bursts, (unsigned long long)s.frames,
                         (unsigned long long)s.junk, (unsigned long long)s.badtype, (unsigned long long)s.decode_errors,
                         (unsigned long long)s.timing_errors, (unsigned long long)s.exact, (unsigned long long)s.mismatched,
                         (unsigned long long)s.clipped, (unsigned long long)s.nonfinite);
        int nch = bitstream() ? 6 : channels;
        for (int k = 0; k < nch; ++k) n += snprintf(b + n, sizeof b - n, " %.4f", s.frames ? std::sqrt(s.sum2[k] / s.frames) : 0.0);
        n += snprintf(b + n, sizeof b - n, " peak");
        for (int k = 0; k < nch; ++k) n += snprintf(b + n, sizeof b - n, " %.4f", s.peak[k]);
        if (FILE *f = log()) fprintf(f, "%s\n", b);
        else if (!strcmp(what, "close")) fprintf(stderr, "media: fake TV: %s\n", b);
    }
    bool bitstream() const { return mode == OutputMode::DolbyDigital || mode == OutputMode::DolbyDigitalPlus || mode == OutputMode::TrueHd; }
    void count(Stats &s, int ch, double v) {
        if (!std::isfinite(v)) { ++s.nonfinite; return; }
        s.sum2[ch] += v * v;
        s.peak[ch] = std::max(s.peak[ch], std::fabs(v));
        if (std::fabs(v) >= 0.99999) ++s.clipped;
    }
    void sample(int ch, double v) { count(sec, ch, v); count(all, ch, v); }
    void frames(uint64_t n) { sec.frames += n; all.frames += n; }
    template <class F> void both(F f) { f(sec); f(all); }
    // The mixer's 6-channel input as the TrueHD encoder quantises it (TrueHdMat::push)
    void input(const float *pcm, size_t n) {
        if (mode != OutputMode::TrueHd) return;
        std::lock_guard<std::mutex> g(in_m);
        for (size_t i = 0; i < n * 6; ++i) {
            double v = std::lrint((double)pcm[i] * 8388608.0);
            in_q.push_back((int32_t)std::min(8388607.0, std::max(-8388608.0, v)));
        }
    }
    void decoded(AVFrame *f) {
        int nch = f->ch_layout.nb_channels;
        for (int i = 0; i < f->nb_samples; ++i) {
            int32_t want[6] = {0};
            bool have = false;
            if (mode == OutputMode::TrueHd) {
                std::lock_guard<std::mutex> g(in_m);
                if (in_q.size() >= 6) { for (int k = 0; k < 6; ++k) want[k] = in_q[(size_t)k]; in_q.erase(in_q.begin(), in_q.begin() + 6); have = true; }
            }
            bool same = have;
            for (int ch = 0; ch < 6; ++ch) {
                double v = 0;
                int32_t q = 0;
                if (ch < nch) {
                    if (f->format == AV_SAMPLE_FMT_S32) q = ((const int32_t *)f->data[0])[i * nch + ch], v = q / 2147483648.0;
                    else if (f->format == AV_SAMPLE_FMT_S32P) q = ((const int32_t *)f->extended_data[ch])[i], v = q / 2147483648.0;
                    else if (f->format == AV_SAMPLE_FMT_S16) q = ((const int16_t *)f->data[0])[i * nch + ch] * 65536, v = q / 2147483648.0;
                    else if (f->format == AV_SAMPLE_FMT_S16P) q = ((const int16_t *)f->extended_data[ch])[i] * 65536, v = q / 2147483648.0;
                    else if (f->format == AV_SAMPLE_FMT_FLTP) v = ((const float *)f->extended_data[ch])[i];
                    else if (f->format == AV_SAMPLE_FMT_FLT) v = ((const float *)f->data[0])[i * nch + ch];
                }
                if (have && (q >> 8) != want[ch]) same = false;
                sample(ch, v);
            }
            if (mode == OutputMode::TrueHd) both([&](Stats &s) { same ? ++s.exact : ++s.mismatched; });
        }
        frames((uint64_t)f->nb_samples);
    }
    void decode(const uint8_t *p, size_t n) {
        if (!dec) return;
        if (av_new_packet(pkt, (int)n) < 0) return;
        memcpy(pkt->data, p, n);
        if (avcodec_send_packet(dec, pkt) < 0) both([](Stats &s) { ++s.decode_errors; });
        av_packet_unref(pkt);
        while (avcodec_receive_frame(dec, frm) == 0) decoded(frm);
    }
    void burst(const uint8_t *b, size_t bytes, int type) {
        both([](Stats &s) { ++s.bursts; });
        int want = mode == OutputMode::TrueHd ? 0x16 : mode == OutputMode::DolbyDigitalPlus ? 0x15 : 0x01;
        if (type != want) { both([](Stats &s) { ++s.badtype; }); return; }
        std::vector<uint8_t> payload;
        if (mode == OutputMode::TrueHd) {
            if (!TrueHdMat::unwrap(b, bytes, payload)) { both([](Stats &s) { ++s.decode_errors; }); return; }
            for (size_t p = 0; p + 4 <= payload.size();) {   // access units with zero padding between them
                unsigned w = payload[p] << 8 | payload[p + 1];
                if (!w) { p += 2; continue; }
                size_t len = (w & 0xFFF) * 2;
                if (len < 4 || p + len > payload.size()) { both([](Stats &s) { ++s.decode_errors; }); break; }
                int timing = payload[p + 2] << 8 | payload[p + 3];
                if (last_timing >= 0 && ((timing - last_timing) & 0xFFFF) != 40) both([](Stats &s) { ++s.timing_errors; });
                last_timing = timing;
                decode(&payload[p], len);
                p += len;
            }
        } else {
            for (size_t i = 8; i + 1 < bytes; i += 2) { payload.push_back(b[i + 1]); payload.push_back(b[i]); }   // s16le words
            decode(payload.data(), payload.size());
        }
    }
    // What the fake TV takes off the output each drain.
    void receive(const uint8_t *p, size_t n) {
        if (!bitstream()) {
            const float *s = (const float *)p;
            size_t fr = n / sizeof(float) / (size_t)channels;
            for (size_t i = 0; i < fr; ++i)
                for (int ch = 0; ch < channels; ++ch) sample(ch, s[i * (size_t)channels + (size_t)ch]);
            frames(fr);
        } else {
            buf.insert(buf.end(), p, p + n);
            size_t at = 0;
            for (;;) {
                size_t q = at;
                while (q + 4 <= buf.size() && !(buf[q] == 0x72 && buf[q + 1] == 0xF8 && buf[q + 2] == 0x1F && buf[q + 3] == 0x4E)) q += 2;
                for (size_t k = at; k < q && k < buf.size(); ++k) if (buf[k]) both([](Stats &s) { ++s.junk; });
                if (q + 8 > buf.size()) { at = q; break; }
                int type = (buf[q + 4] | buf[q + 5] << 8) & 0x1F;
                size_t pd = (size_t)(buf[q + 6] | buf[q + 7] << 8), len = type == 0x01 ? pd / 8 : pd;
                len = (len + 1) & ~(size_t)1;
                if (q + 8 + len > buf.size()) { at = q; break; }
                burst(&buf[q], 8 + len, type);
                at = q + 8 + len;
            }
            buf.erase(buf.begin(), buf.begin() + (ptrdiff_t)std::min(at, buf.size()));
        }
        int64_t t = mono_ns();
        if (t - sec0 >= 1000000000) {
            line("sec", sec, (t - t0) / 1e9);
            sec = Stats{};
            sec0 = t;
        }
    }
};

std::string device_name(uint32_t dev) {
    if (fake_device()) return dev == kFakeTv ? "fake HDMI TV" : dev == kFakeSpeakers ? "fake speakers" : "?";
    std::string name = "?";
    CFStringRef cf = nullptr;
    UInt32 n = sizeof cf;
    AudioObjectPropertyAddress a = {kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    if (dev && AudioObjectGetPropertyData(dev, &a, 0, nullptr, &n, &cf) == noErr && cf) {
        char buf[256];
        if (CFStringGetCString(cf, buf, sizeof buf, kCFStringEncodingUTF8)) name = buf;
        CFRelease(cf);
    }
    return name;
}

// ------------------------------------------------------------------ physical formats
static std::string format_text(const AudioStreamBasicDescription &f) {
    char id[5] = {(char)(f.mFormatID >> 24), (char)(f.mFormatID >> 16), (char)(f.mFormatID >> 8), (char)f.mFormatID, 0};
    char buf[96];
    snprintf(buf, sizeof buf, "'%s' %.0f Hz %u ch %u-bit", id, f.mSampleRate, (unsigned)f.mChannelsPerFrame,
             (unsigned)f.mBitsPerChannel);
    return buf;
}
static bool read_physical_format(AudioStreamID s, AudioStreamBasicDescription *f) {
    AudioObjectPropertyAddress pf = {kAudioStreamPropertyPhysicalFormat, kAudioObjectPropertyScopeGlobal,
                                     kAudioObjectPropertyElementMain};
    UInt32 n = sizeof *f;
    *f = {};
    return AudioObjectGetPropertyData(s, &pf, 0, nullptr, &n, f) == noErr;
}
// Physical format changes are asynchronous: waits (<= ms) until the stream reports `want`'s format and rate.
static bool wait_physical_format(AudioStreamID s, const AudioStreamBasicDescription &want, int ms) {
    for (int t = 0;; t += 10) {
        AudioStreamBasicDescription now;
        if (read_physical_format(s, &now) && now.mFormatID == want.mFormatID && now.mSampleRate == want.mSampleRate) return true;
        if (t >= ms) return false;
        usleep(10000);
    }
}
// A PCM physical format for a stream: its best 2-channel 48 kHz linear PCM (most bits), else any linear PCM.
static bool pcm_physical_format(AudioStreamID s, AudioStreamBasicDescription *out) {
    AudioObjectPropertyAddress f = {kAudioStreamPropertyAvailablePhysicalFormats, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    UInt32 fn = 0;
    if (AudioObjectGetPropertyDataSize(s, &f, 0, nullptr, &fn) != noErr || !fn) return false;
    std::vector<AudioStreamRangedDescription> fmts(fn / sizeof(AudioStreamRangedDescription));
    if (AudioObjectGetPropertyData(s, &f, 0, nullptr, &fn, fmts.data()) != noErr) return false;
    int best = -1;
    for (auto &r : fmts) {
        if (r.mFormat.mFormatID != kAudioFormatLinearPCM) continue;
        AudioStreamBasicDescription c = r.mFormat;
        if (c.mSampleRate == 0 && r.mSampleRateRange.mMinimum <= 48000 && r.mSampleRateRange.mMaximum >= 48000) c.mSampleRate = 48000;
        int score = (c.mSampleRate == 48000 ? 1000 : 0) + (c.mChannelsPerFrame == 2 ? 500 : 0) + (int)c.mBitsPerChannel;
        if (score > best) { best = score; *out = c; }
    }
    return best >= 0;
}
static pid_t hog_owner(AudioObjectID dev) {
    pid_t pid = -1;
    UInt32 n = sizeof pid;
    AudioObjectPropertyAddress a = {kAudioDevicePropertyHogMode, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    AudioObjectGetPropertyData(dev, &a, 0, nullptr, &n, &pid);
    return pid;
}
// A device whose output stream was left in an encoded format (a passthrough that exited without restoring it, or whose
// restore went wrong) plays PCM from the system mixer as noise: for MAT, at a quarter of the rate (the 768 kHz format on
// a 192 kHz link). When nobody holds the device, put its streams back to PCM before playing PCM on it.
static void repair_encoded_streams(AudioObjectID dev) {
    if (fake_device() || !dev || hog_owner(dev) != -1) return;
    AudioObjectPropertyAddress a = {kAudioDevicePropertyStreams, kAudioObjectPropertyScopeOutput, kAudioObjectPropertyElementMain};
    UInt32 n = 0;
    if (AudioObjectGetPropertyDataSize(dev, &a, 0, nullptr, &n) != noErr || !n) return;
    std::vector<AudioStreamID> streams(n / sizeof(AudioStreamID));
    if (AudioObjectGetPropertyData(dev, &a, 0, nullptr, &n, streams.data()) != noErr) return;
    for (AudioStreamID s : streams) {
        AudioStreamBasicDescription now, pcm;
        if (!read_physical_format(s, &now) || now.mFormatID == kAudioFormatLinearPCM || !pcm_physical_format(s, &pcm)) continue;
        AudioObjectPropertyAddress pf = {kAudioStreamPropertyPhysicalFormat, kAudioObjectPropertyScopeGlobal,
                                         kAudioObjectPropertyElementMain};
        bool ok = AudioObjectSetPropertyData(s, &pf, 0, nullptr, sizeof pcm, &pcm) == noErr && wait_physical_format(s, pcm, 1000);
        fprintf(stderr, "media: \"%s\" was left in an encoded format (%s): %s %s\n", device_name(dev).c_str(),
                format_text(now).c_str(), ok ? "set back to" : "could not set it back to", format_text(pcm).c_str());
    }
}

// The device's output stream that takes an encoded format: AC-3 (kAudioFormat60958AC3 'cac3', or 'IAC3', at 48 kHz:
// the link runs at the audio rate) or E-AC-3 ('cec3', not in the public headers, at 192 kHz: IEC 61937-3 sends E-AC-3
// at 4x the audio rate, 6,144 link frames per 1,536-sample frame; an HDMI device that supports it lists it with
// mFramesPerPacket 6144). 0 if none; fmt/index get the format and the stream's buffer index.
// TrueHD: MAT ('mtct', 768 kHz = 4 x 192 kHz, the HDMI high-bit-rate rate; 15,360 frames per 61,440-byte MAT frame).
static AudioStreamID find_encoded_stream(AudioObjectID dev, OutputMode mode, AudioStreamBasicDescription *fmt = nullptr,
                                         UInt32 *index = nullptr) {
    AudioObjectPropertyAddress a = {kAudioDevicePropertyStreams, kAudioObjectPropertyScopeOutput, kAudioObjectPropertyElementMain};
    UInt32 n = 0;
    if (!dev || AudioObjectGetPropertyDataSize(dev, &a, 0, nullptr, &n) != noErr || !n) return 0;
    std::vector<AudioStreamID> streams(n / sizeof(AudioStreamID));
    if (AudioObjectGetPropertyData(dev, &a, 0, nullptr, &n, streams.data()) != noErr) return 0;
    for (UInt32 i = 0; i < streams.size(); ++i) {
        AudioObjectPropertyAddress f = {kAudioStreamPropertyAvailablePhysicalFormats, kAudioObjectPropertyScopeGlobal,
                                        kAudioObjectPropertyElementMain};
        UInt32 fn = 0;
        if (AudioObjectGetPropertyDataSize(streams[i], &f, 0, nullptr, &fn) != noErr) continue;
        std::vector<AudioStreamRangedDescription> fmts(fn / sizeof(AudioStreamRangedDescription));
        if (AudioObjectGetPropertyData(streams[i], &f, 0, nullptr, &fn, fmts.data()) != noErr) continue;
        for (auto &r : fmts) {
            const AudioFormatID id = r.mFormat.mFormatID;
            bool ok = mode == OutputMode::DolbyDigitalPlus
                          ? id == 'cec3' && r.mFormat.mSampleRate == 192000 && r.mFormat.mChannelsPerFrame == 2
                      : mode == OutputMode::TrueHd
                          ? id == 'mtct' && r.mFormat.mSampleRate == 768000 && r.mFormat.mChannelsPerFrame == 2
                          : (id == kAudioFormat60958AC3 || id == 'IAC3') && r.mFormat.mSampleRate == 48000;
            if (!ok) continue;
            if (fmt) *fmt = r.mFormat;
            if (index) *index = i;
            return streams[i];
        }
    }
    return 0;
}

bool default_device_takes_eac3() { if (fake_device()) return fake_default() == kFakeTv; return find_encoded_stream(default_output_device(), OutputMode::DolbyDigitalPlus) != 0; }
bool default_device_takes_mat() { if (fake_device()) return fake_default() == kFakeTv; return find_encoded_stream(default_output_device(), OutputMode::TrueHd) != 0; }

OutputMode preferred_output_mode(std::string *why, bool truehd) {
    if (fake_device()) {
        bool tv = fake_default() == kFakeTv;
        if (why) *why = tv ? (truehd ? "\"fake HDMI TV\" takes Dolby TrueHD (MAT)" : "\"fake HDMI TV\" takes Dolby Digital")
                           : "\"fake speakers\", 2 speakers";
        return tv ? (truehd ? OutputMode::TrueHd : OutputMode::DolbyDigital) : OutputMode::Stereo;
    }
    AudioObjectID dev = kAudioObjectUnknown;
    UInt32 n = sizeof dev;
    AudioObjectPropertyAddress a = {kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, nullptr, &n, &dev) != noErr || dev == kAudioObjectUnknown) {
        if (why) *why = "no default output device";
        return OutputMode::Stereo;
    }
    std::string name = "?";
    CFStringRef cf = nullptr;
    n = sizeof cf;
    a = {kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    if (AudioObjectGetPropertyData(dev, &a, 0, nullptr, &n, &cf) == noErr && cf) {
        char buf[256];
        if (CFStringGetCString(cf, buf, sizeof buf, kCFStringEncodingUTF8)) name = buf;
        CFRelease(cf);
    }
    // The preferred layout, expanded to one label per channel.
    a = {kAudioDevicePropertyPreferredChannelLayout, kAudioObjectPropertyScopeOutput, kAudioObjectPropertyElementMain};
    std::vector<uint8_t> raw;
    if (AudioObjectGetPropertyDataSize(dev, &a, 0, nullptr, &n) == noErr && n >= sizeof(AudioChannelLayout)) {
        raw.resize(n);
        if (AudioObjectGetPropertyData(dev, &a, 0, nullptr, &n, raw.data()) != noErr) raw.clear();
    }
    std::vector<AudioChannelLabel> labels;
    if (!raw.empty()) {
        auto *l = (const AudioChannelLayout *)raw.data();
        std::vector<uint8_t> full;
        if (l->mChannelLayoutTag != kAudioChannelLayoutTag_UseChannelDescriptions) {
            AudioFormatPropertyID p = l->mChannelLayoutTag == kAudioChannelLayoutTag_UseChannelBitmap
                                          ? kAudioFormatProperty_ChannelLayoutForBitmap : kAudioFormatProperty_ChannelLayoutForTag;
            const void *spec = p == kAudioFormatProperty_ChannelLayoutForBitmap ? (const void *)&l->mChannelBitmap
                                                                                : (const void *)&l->mChannelLayoutTag;
            UInt32 sz = 0;
            if (AudioFormatGetPropertyInfo(p, 4, spec, &sz) == noErr && sz) {
                full.resize(sz);
                if (AudioFormatGetProperty(p, 4, spec, &sz, full.data()) == noErr) l = (const AudioChannelLayout *)full.data();
            }
        }
        for (UInt32 i = 0; i < l->mNumberChannelDescriptions; ++i) labels.push_back(l->mChannelDescriptions[i].mChannelLabel);
    }
    // A device that takes encoded AC-3 (HDMI or optical to a TV, soundbar or receiver) gets Dolby Digital passthrough:
    // its channel order travels inside the stream, which multichannel PCM through a TV's (e)ARC doesn't reliably keep.
    // Dolby TrueHD first (lossless); Dolby Digital Plus is never picked here (the user chooses it in the Audio menu).
    if (truehd && find_encoded_stream(dev, OutputMode::TrueHd)) {
        if (why) *why = "\"" + name + "\" takes Dolby TrueHD (MAT)";
        return OutputMode::TrueHd;
    }
    if (find_encoded_stream(dev, OutputMode::DolbyDigital)) {
        if (why) *why = "\"" + name + "\" takes Dolby Digital" + (find_encoded_stream(dev, OutputMode::DolbyDigitalPlus) ? ", Dolby Digital Plus" : "") +
                        (find_encoded_stream(dev, OutputMode::TrueHd) ? ", Dolby TrueHD (MAT)" : "");
        return OutputMode::DolbyDigital;
    }
    int surround = 0;
    for (AudioChannelLabel c : labels)
        surround += c == kAudioChannelLabel_LeftSurround || c == kAudioChannelLabel_RightSurround ||
                    c == kAudioChannelLabel_RearSurroundLeft || c == kAudioChannelLabel_RearSurroundRight ||
                    c == kAudioChannelLabel_LeftSurroundDirect || c == kAudioChannelLabel_RightSurroundDirect;
    OutputMode m = surround >= 2 ? OutputMode::Pcm51 : OutputMode::Stereo;
    if (why) *why = "\"" + name + "\", " + std::to_string(labels.size()) + " speakers" + (surround >= 2 ? " with surrounds" : "");
    return m;
}

// ------------------------------------------------------------------ device changes
static OSStatus devices_changed(AudioObjectID, UInt32, const AudioObjectPropertyAddress *, void *) {
    g_devices_gen.fetch_add(1, std::memory_order_relaxed);
    return noErr;
}
static std::atomic<int> g_fail_opens{0};
void fail_next_opens(int n) { g_fail_opens = n; }
// Tests: passthrough is refused as with a TV that is asleep, and no PCM AudioUnit is ever created (counted instead), so a
// run never opens a real device.
static std::atomic<bool> g_refuse_passthrough{false};
static std::atomic<int> g_unit_attempts{0};
void test_refuse_passthrough(bool on) { g_refuse_passthrough = on; g_unit_attempts = 0; }
int test_unit_attempts() { return g_unit_attempts; }

bool device_alive(uint32_t dev) {
    if (fake_device()) return dev == kFakeTv || dev == kFakeSpeakers;
    UInt32 alive = 0, n = sizeof alive;
    AudioObjectPropertyAddress a = {kAudioDevicePropertyDeviceIsAlive, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    return dev && AudioObjectGetPropertyData(dev, &a, 0, nullptr, &n, &alive) == noErr && alive;
}
uint32_t default_output_device() {
    if (fake_device()) return fake_default();
    AudioObjectID dev = 0;
    UInt32 n = sizeof dev;
    AudioObjectPropertyAddress a = {kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    return AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, nullptr, &n, &dev) == noErr ? dev : 0;
}
uint64_t output_devices_generation() {
    if (fake_device()) {   // the fake default changes on its own clock: count its changes here
        static std::atomic<uint32_t> last{kFakeTv};
        uint32_t now = fake_default();
        if (last.exchange(now) != now) g_devices_gen.fetch_add(1, std::memory_order_relaxed);
        return g_devices_gen.load(std::memory_order_relaxed);
    }
    static std::once_flag once;
    std::call_once(once, [] {
        AudioObjectPropertyAddress a = {kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal,
                                        kAudioObjectPropertyElementMain};
        AudioObjectAddPropertyListener(kAudioObjectSystemObject, &a, devices_changed, nullptr);
        a.mSelector = kAudioHardwarePropertyDevices;
        AudioObjectAddPropertyListener(kAudioObjectSystemObject, &a, devices_changed, nullptr);
    });
    return g_devices_gen.load(std::memory_order_relaxed);
}

// ------------------------------------------------------------------ AudioOutput (CoreAudio default output unit)
static int64_t now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}


// FIFO for the device callback: take() only copies and advances an index, so the callback never frees memory; only the
// writer's put() can allocate (growth, rare: the initial capacity is about a second of 5.1).
template <class T> class Ring {
    std::vector<T> buf;
    size_t head = 0, n = 0;
public:
    explicit Ring(size_t cap) : buf(cap) {}
    size_t size() const { return n; }
    bool empty() const { return n == 0; }
    void put(const T *src, size_t k) {
        if (n + k > buf.size()) {
            size_t cap = buf.size() ? buf.size() : 1;
            while (cap < n + k) cap *= 2;
            std::vector<T> g(cap);
            for (size_t i = 0; i < n; ++i) g[i] = buf[(head + i) % buf.size()];
            buf.swap(g);
            head = 0;
        }
        size_t tail = (head + n) % buf.size(), first = std::min(k, buf.size() - tail);
        std::copy(src, src + first, buf.begin() + tail);
        std::copy(src + first, src + k, buf.begin());
        n += k;
    }
    size_t take(T *dst, size_t want) {
        size_t have = std::min(want, n), first = std::min(have, buf.size() - head);
        std::copy(buf.begin() + head, buf.begin() + head + first, dst);
        std::copy(buf.begin(), buf.begin() + (have - first), dst + first);
        head = buf.size() ? (head + have) % buf.size() : 0;
        n -= have;
        return have;
    }
};
struct AudioOutput::Impl {
    AudioComponentInstance unit = nullptr;
    std::atomic<int64_t> last_callback{0};   // last consumption / valid idle callback, or the start (open grace period)
    const bool diag = getenv("BOOT_DIAG") && *getenv("BOOT_DIAG");
    AudioCallbackStats stats;              // under lock; zero_callbacks is collected even without diagnostics
    int64_t callback_at = 0;               // callback arrival, distinct from output health
    AudioDeviceID opened_on = 0;
    // Dolby Digital passthrough: the device itself in an encoded AC-3 format (hog mode, mixing off, an IOProc)
    AudioDeviceID dev = 0;
    AudioDeviceIOProcID proc = nullptr;
    UInt32 stream_index = 0;
    AudioStreamID stream = 0;
    AudioStreamBasicDescription saved_format = {};
    UInt32 saved_mixing = 1;
    bool passthrough = false;
    bool offline = false;           // open_offline: no device, render_offline() pulls
    bool fake = false;              // NFS_AUDIO_FAKE_DEVICE: drained by fake_thread at the device rate
    std::atomic<bool> fake_run{false};
    std::thread fake_thread;
    std::unique_ptr<FakeRx> rx;     // the fake TV's receiver (fake_tv.log)
    OutputMode mode = OutputMode::Pcm51;
    int rate = 48000, channels = 6;
    mutable std::mutex lock;
    Ring<float> fq{1u << 18};       // Pcm51 / Stereo samples
    Ring<uint8_t> bq{1u << 18};     // DolbyDigital / DolbyDigitalPlus bytes (IEC 61937 as s16le stereo)
    Ac3Spdif ac3;
    TrueHdMat thd;
    std::vector<uint8_t> bursts;

    bool bitstream() const {
        return mode == OutputMode::DolbyDigital || mode == OutputMode::DolbyDigitalPlus || mode == OutputMode::TrueHd;
    }
    int burst_frames() const { return mode == OutputMode::TrueHd ? TrueHdMat::kFramesPerBurst : Ac3Spdif::kFramesPerBurst; }
    size_t burst_bytes() const { return mode == OutputMode::TrueHd ? TrueHdMat::kBurstBytes : ac3.burst_bytes(); }
    // What every callback hands the device: queued bytes / samples, zero filled when the queue runs dry (between
    // IEC 61937 bursts zeros are the standard stuffing). The IOProc, the render callback and render_offline share it.
    void callback_begin() {
        if (!diag) return;
        int64_t t = now_ns();
        std::lock_guard<std::mutex> g(lock);
        ++stats.callbacks;
        if (callback_at) stats.gap_max_ns = std::max(stats.gap_max_ns, (uint64_t)(t - callback_at));
        callback_at = t;
    }
    // With lock held. Count shortages from bytes actually supplied, not encoder-pending input frames. A valid
    // callback can still starve; conversely, zero padding already present inside an encoded burst isn't starvation.
    void consumed(size_t have, size_t want) {
        if (have < want) ++stats.zero_callbacks;
        // A parked/idle/priming output can legitimately have no queued data. Valid callbacks supplying zeros
        // keep it alive; with queued data, only consumption is progress. Unusable buffers never reach here.
        if (have || (bq.empty() && fq.empty())) last_callback.store(now_ns(), std::memory_order_relaxed);
        if (diag) {
            if (want) ++stats.valid;
            stats.taken_bytes += have;
            stats.zero_bytes += want - have;
        }
    }
    void take_bytes(uint8_t *dst, size_t want) {
        if (!dst || !want) return;
        std::lock_guard<std::mutex> g(lock);
        size_t have = bq.take(dst, want);
        memset(dst + have, 0, want - have);
        consumed(have, want);
    }
    void take_floats(float *dst, size_t want) {
        if (!dst || !want) return;
        std::lock_guard<std::mutex> g(lock);
        size_t have = fq.take(dst, want);
        std::fill(dst + have, dst + want, 0.0f);
        consumed(have * sizeof(float), want * sizeof(float));
    }
    bool open_encoder() {
        return rate == 48000 && (mode == OutputMode::TrueHd ? thd.open()
                                 : mode == OutputMode::DolbyDigitalPlus ? ac3.open_eac3() : ac3.open());
    }

    static OSStatus io(AudioObjectID, const AudioTimeStamp *, const AudioBufferList *, const AudioTimeStamp *,
                       AudioBufferList *out, const AudioTimeStamp *, void *self) {   // passthrough: raw IEC 61937 bytes
        auto *s = (Impl *)self;
        s->callback_begin();
        if (!out) return noErr;
        size_t unfed = 0;
        // CoreAudio can supply several streams. Initialize every writable buffer we don't feed, including all
        // of them when the selected stream disappeared. Neither an invalid index nor a null/empty buffer is health.
        for (UInt32 i = 0; i < out->mNumberBuffers; ++i) {
            AudioBuffer &b = out->mBuffers[i];
            if (i != s->stream_index && b.mData && b.mDataByteSize) {
                memset(b.mData, 0, b.mDataByteSize);
                unfed += b.mDataByteSize;
            }
        }
        if (s->stream_index >= out->mNumberBuffers) {
            std::lock_guard<std::mutex> g(s->lock);
            if (unfed) ++s->stats.zero_callbacks;
            if (s->diag) s->stats.zero_bytes += unfed;
            return noErr;
        }
        AudioBuffer &b = out->mBuffers[s->stream_index];
        s->take_bytes((uint8_t *)b.mData, b.mDataByteSize);
        return noErr;
    }
    bool open_passthrough();
    void close_passthrough();
    bool open_fake();
    void stop_fake() {
        fake_run = false;
        if (fake_thread.joinable()) fake_thread.join();
    }
    static OSStatus render(void *self, AudioUnitRenderActionFlags *, const AudioTimeStamp *, UInt32, UInt32 n,
                           AudioBufferList *io) {
        auto *s = (Impl *)self;
        s->callback_begin();
        if (s->bitstream()) s->take_bytes((uint8_t *)io->mBuffers[0].mData, (size_t)n * 4);
        else s->take_floats((float *)io->mBuffers[0].mData, (size_t)n * s->channels);
        return noErr;
    }
};

// Devices number their speakers their own way: HDMI sinks often report L R LFE C Ls Rs, and the output unit doesn't
// reorder our L R C LFE Ls Rs to match. Read the device's speaker labels and route each of our channels to the device
// channel with the same label (kAudioOutputUnitProperty_ChannelMap: per device channel, the source channel or -1).
// Rear surrounds stand in for missing side surrounds. Devices with fewer than 6 labelled channels keep the default
// mapping (the unit folds 5.1 down from the declared input layout).
static void map_to_device_speakers(AudioComponentInstance unit) {
    AudioDeviceID dev = 0;
    UInt32 sz = sizeof dev;
    if (AudioUnitGetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &dev, &sz) != noErr || !dev)
        return;
    AudioObjectPropertyAddress a = {kAudioDevicePropertyPreferredChannelLayout, kAudioDevicePropertyScopeOutput,
                                    kAudioObjectPropertyElementMain};
    if (AudioObjectGetPropertyDataSize(dev, &a, 0, nullptr, &sz) != noErr) return;
    std::vector<uint8_t> mem(sz);
    auto *l = (AudioChannelLayout *)mem.data();
    if (AudioObjectGetPropertyData(dev, &a, 0, nullptr, &sz, l) != noErr || l->mNumberChannelDescriptions < 6) return;
    static const AudioChannelLabel ours[6] = {kAudioChannelLabel_Left, kAudioChannelLabel_Right, kAudioChannelLabel_Center,
                                              kAudioChannelLabel_LFEScreen, kAudioChannelLabel_LeftSurround,
                                              kAudioChannelLabel_RightSurround};
    std::vector<SInt32> map(l->mNumberChannelDescriptions, -1);
    int found = 0;
    for (UInt32 i = 0; i < l->mNumberChannelDescriptions; ++i) {
        AudioChannelLabel lab = l->mChannelDescriptions[i].mChannelLabel;
        if (lab == kAudioChannelLabel_RearSurroundLeft) lab = kAudioChannelLabel_LeftSurround;
        if (lab == kAudioChannelLabel_RearSurroundRight) lab = kAudioChannelLabel_RightSurround;
        for (int k = 0; k < 6; ++k)
            if (lab == ours[k] && std::find(map.begin(), map.end(), k) == map.end()) { map[i] = k; ++found; break; }
    }
    if (found != 6) return;
    UInt32 bytes = (UInt32)(map.size() * sizeof(SInt32));
    if (AudioUnitSetProperty(unit, kAudioOutputUnitProperty_ChannelMap, kAudioUnitScope_Output, 0, map.data(), bytes) != noErr)
        AudioUnitSetProperty(unit, kAudioOutputUnitProperty_ChannelMap, kAudioUnitScope_Input, 0, map.data(), bytes);
}

// Dolby Digital (Plus) over HDMI / optical has to reach the device bit-exact, which the default output unit (a mixer with
// volume and format conversion) doesn't do. If the default device offers an encoded AC-3 format (kAudioFormat60958AC3,
// 48 kHz, e.g. HDMI to a TV or receiver that decodes Dolby Digital), take it in hog mode with mixing off, switch that
// stream to the AC-3 format and feed the IEC 61937 bursts straight from an IOProc, the usual S/PDIF passthrough. The
// channels are then labelled inside the AC-3 stream, so nothing between the Mac and the decoder can reorder them.
// close() restores the format, mixing and hog mode. Dolby Digital Plus works the same way with the E-AC-3 format
// ('cec3', 192 kHz link rate; find_encoded_stream).
bool AudioOutput::Impl::open_passthrough() {
    AudioObjectPropertyAddress a = {kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    UInt32 n = sizeof dev;
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &a, 0, nullptr, &n, &dev) != noErr || !dev) return false;
    AudioStreamBasicDescription ac3 = {};
    stream = find_encoded_stream(dev, mode, &ac3, &stream_index);
    if (!stream) return false;
    pid_t pid = getpid();
    a = {kAudioDevicePropertyHogMode, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    if (AudioObjectSetPropertyData(dev, &a, 0, nullptr, sizeof pid, &pid) != noErr) { stream = 0; return false; }
    a = {kAudioDevicePropertySupportsMixing, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    n = sizeof saved_mixing;
    if (AudioObjectGetPropertyData(dev, &a, 0, nullptr, &n, &saved_mixing) == noErr) {
        UInt32 off = 0;
        AudioObjectSetPropertyData(dev, &a, 0, nullptr, sizeof off, &off);
    }
    AudioObjectPropertyAddress pf = {kAudioStreamPropertyPhysicalFormat, kAudioObjectPropertyScopeGlobal,
                                     kAudioObjectPropertyElementMain};
    n = sizeof saved_format;
    AudioObjectGetPropertyData(stream, &pf, 0, nullptr, &n, &saved_format);
    // close() gives this format back, so it must be PCM. A stream that is still encoded here was left so by an earlier
    // passthrough (or is still switching back from our own last close): restoring that encoded format would leave the
                                                                                                                
    if (saved_format.mFormatID != kAudioFormatLinearPCM) {
        AudioStreamBasicDescription pcm;
        std::string was = format_text(saved_format);
        if (pcm_physical_format(stream, &pcm)) saved_format = pcm;
        else saved_format.mFormatID = 0;
        fprintf(stderr, "media: \"%s\" was in an encoded format (%s) before passthrough: will restore %s\n",
                device_name(dev).c_str(), was.c_str(), saved_format.mFormatID ? format_text(saved_format).c_str() : "nothing");
    }
    passthrough = true;                                          // from here on close_passthrough() undoes it
    if (AudioObjectSetPropertyData(stream, &pf, 0, nullptr, sizeof ac3, &ac3) != noErr) { close_passthrough(); return false; }
    for (int i = 0; i < 50; ++i) {                               // the switch is asynchronous: wait for it (<= 1 s)
        AudioStreamBasicDescription now = {};
        n = sizeof now;
        AudioObjectGetPropertyData(stream, &pf, 0, nullptr, &n, &now);
        if (now.mFormatID == ac3.mFormatID) break;
        usleep(20000);
    }
    if (AudioDeviceCreateIOProcID(dev, io, this, &proc) != noErr || AudioDeviceStart(dev, proc) != noErr) {
        close_passthrough();
        return false;
    }
    return true;
}
void AudioOutput::Impl::close_passthrough() {
    if (!passthrough) return;
    if (proc) { AudioDeviceStop(dev, proc); AudioDeviceDestroyIOProcID(dev, proc); proc = nullptr; }
    AudioObjectPropertyAddress pf = {kAudioStreamPropertyPhysicalFormat, kAudioObjectPropertyScopeGlobal,
                                     kAudioObjectPropertyElementMain};
    // The switch back is asynchronous: wait for it while still holding the device, so the next open (Snd re-creates its
    // device within milliseconds) reads the restored PCM format, and nothing plays PCM into the encoded one meanwhile.
    if (saved_format.mFormatID && AudioObjectSetPropertyData(stream, &pf, 0, nullptr, sizeof saved_format, &saved_format) == noErr &&
        !wait_physical_format(stream, saved_format, 1000))
        fprintf(stderr, "media: \"%s\": the format did not switch back to %s within 1 s\n", device_name(dev).c_str(),
                format_text(saved_format).c_str());
    AudioObjectPropertyAddress a = {kAudioDevicePropertySupportsMixing, kAudioObjectPropertyScopeGlobal,
                                    kAudioObjectPropertyElementMain};
    AudioObjectSetPropertyData(dev, &a, 0, nullptr, sizeof saved_mixing, &saved_mixing);
    pid_t none = -1;
    a = {kAudioDevicePropertyHogMode, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    AudioObjectSetPropertyData(dev, &a, 0, nullptr, sizeof none, &none);
    passthrough = false;
    stream = 0;
}

bool AudioOutput::Impl::open_fake() {
    uint32_t on = default_output_device();
    if (bitstream() && on != kFakeTv) return false;   // passthrough needs the TV's encoded formats
    fake = true;
    passthrough = bitstream();                         // exclusive(), like the real passthrough
    opened_on = on;
    if (passthrough) { ++g_fake_hogs; g_devices_gen.fetch_add(1, std::memory_order_relaxed); }
    last_callback = now_ns();
    rx.reset(new FakeRx(mode, rate, channels, device_name(on)));
    fake_run = true;
    fake_thread = std::thread([this] {
        std::vector<uint8_t> sink;
        const double dev_rate = mode == OutputMode::DolbyDigitalPlus ? 192000 : mode == OutputMode::TrueHd ? 768000 : rate;
        const size_t frame_bytes = bitstream() ? 4 : sizeof(float) * (size_t)channels;
        int64_t t0 = now_ns();
        uint64_t done = 0;
        while (fake_run) {
            usleep(5000);
            int64_t t = now_ns();
            uint64_t due = (uint64_t)((double)(t - t0) * dev_rate / 1e9);
            size_t frames = (size_t)(due - done);
            done = due;
            sink.resize(frames * frame_bytes);
            callback_begin();
            if (bitstream()) take_bytes(sink.data(), sink.size());
            else take_floats((float *)sink.data(), frames * (size_t)channels);
            rx->receive(sink.data(), sink.size());
        }
    });
    fprintf(stderr, "media: fake device: open %s %d Hz on \"%s\"%s\n", output_mode_name(mode), rate,
            device_name(on).c_str(), passthrough ? " (hog mode)" : "");
    return true;
}

AudioOutput::AudioOutput() : d(new Impl) {}
AudioOutput::~AudioOutput() { close(); }

bool AudioOutput::open(OutputMode mode, int rate) {
    if (g_fail_opens > 0 && g_fail_opens-- > 0) return false;
    d->mode = mode;
    d->rate = rate;
    d->channels = mode == OutputMode::Pcm51 ? 6 : 2;
    if (d->bitstream() && !d->open_encoder()) return false;
    if (fake_device()) return d->open_fake();
    if (d->bitstream() && !g_refuse_passthrough && d->open_passthrough()) {
        d->last_callback = now_ns();
        d->opened_on = d->dev;
        return true;
    }
    // Encoded bursts through the mixer come out as noise (E-AC-3 and MAT need their 192 / 768 kHz links; AC-3's 16-bit
    // stereo burst stream is no PCM either, e.g. after the output flipped from the TV to the laptop's speakers). A refused
    // passthrough fails the open instead: the caller retries, then opens a decoded PCM output (open_in_background).
    if (d->bitstream()) return false;
    if (g_refuse_passthrough) { ++g_unit_attempts; return false; }
    repair_encoded_streams(default_output_device());
    AudioComponentDescription desc = {kAudioUnitType_Output, kAudioUnitSubType_DefaultOutput,
                                      kAudioUnitManufacturer_Apple, 0, 0};
    AudioComponent comp = AudioComponentFindNext(nullptr, &desc);
    if (!comp || AudioComponentInstanceNew(comp, &d->unit) != noErr) return false;
    AudioStreamBasicDescription f = {};
    f.mSampleRate = rate;
    f.mFormatID = kAudioFormatLinearPCM;
    f.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked;
    f.mBitsPerChannel = 32;
    f.mChannelsPerFrame = d->channels;
    f.mFramesPerPacket = 1;
    f.mBytesPerFrame = f.mBytesPerPacket = d->channels * f.mBitsPerChannel / 8;
    if (AudioUnitSetProperty(d->unit, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &f, sizeof f) != noErr)
        return false;
    if (mode == OutputMode::Pcm51) {
        AudioChannelLayout layout = {};
        layout.mChannelLayoutTag = kAudioChannelLayoutTag_MPEG_5_1_A;   // L R C LFE Ls Rs
        AudioUnitSetProperty(d->unit, kAudioUnitProperty_AudioChannelLayout, kAudioUnitScope_Input, 0, &layout,
                             sizeof layout);
    }
    AURenderCallbackStruct cb = {Impl::render, d.get()};
    AudioUnitSetProperty(d->unit, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &cb, sizeof cb);
    if (mode == OutputMode::Pcm51) map_to_device_speakers(d->unit);
    if (AudioUnitInitialize(d->unit) != noErr) return false;
    d->last_callback = now_ns();                 // stalled() counts from here (passthrough: from its IOProc's start)
    UInt32 sz = sizeof d->opened_on;
    AudioUnitGetProperty(d->unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &d->opened_on, &sz);
    return AudioOutputUnitStart(d->unit) == noErr;
}

bool AudioOutput::open_offline(OutputMode mode, int rate) {
    d->mode = mode;
    d->rate = rate;
    d->channels = mode == OutputMode::Pcm51 ? 6 : 2;
    if (d->bitstream() && !d->open_encoder()) return false;
    d->offline = true;
    return true;
}
void AudioOutput::render_offline(void *dst, size_t bytes) {
    d->callback_begin();
    if (d->bitstream()) d->take_bytes((uint8_t *)dst, bytes);
    else d->take_floats((float *)dst, bytes / sizeof(float));
}
size_t AudioOutput::device_frame_bytes() const { return d->bitstream() ? 4 : sizeof(float) * (size_t)d->channels; }
int AudioOutput::device_rate() const {
    return d->mode == OutputMode::DolbyDigitalPlus ? 192000 : d->mode == OutputMode::TrueHd ? 768000 : d->rate;
}
size_t AudioOutput::pending_frames() const {   // the encoders belong to the writer's thread: no lock
    return !d->bitstream() ? 0 : d->mode == OutputMode::TrueHd ? d->thd.pending_frames() : d->ac3.pending_frames();
}

void AudioOutput::close() {
    d->offline = false;
    if (d->fake) {
        d->stop_fake();
        d->rx.reset();
        if (d->passthrough) { --g_fake_hogs; g_fake_unhog_ns = mono_ns(); g_devices_gen.fetch_add(1, std::memory_order_relaxed); }
        fprintf(stderr, "media: fake device: close %s on \"%s\"\n", output_mode_name(d->mode), device_name(d->opened_on).c_str());
        d->fake = d->passthrough = false;
        return;
    }
    d->close_passthrough();
    if (d->unit) {
        AudioOutputUnitStop(d->unit);
        AudioUnitUninitialize(d->unit);
        AudioComponentInstanceDispose(d->unit);
        d->unit = nullptr;
    }
}

void AudioOutput::write(const float *pcm51, size_t frames) {
    if (d->rx) d->rx->input(pcm51, frames);   // the fake TV compares what it decodes with this
    if (d->bitstream()) {           // Dolby Digital (Plus), TrueHD: encode outside the lock the device callback takes
        d->bursts.clear();
        int64_t started = d->diag ? now_ns() : 0;
        if (d->mode == OutputMode::TrueHd) d->thd.push(pcm51, frames, d->bursts);
        else d->ac3.push(pcm51, frames, d->bursts);
        uint64_t elapsed = d->diag ? (uint64_t)(now_ns() - started) : 0;
        std::lock_guard<std::mutex> g(d->lock);
        if (d->diag) d->stats.encode_max_ns = std::max(d->stats.encode_max_ns, elapsed);
        d->bq.put(d->bursts.data(), d->bursts.size());
        return;
    }
    std::vector<float> st;          // the downmix is built outside the lock the device callback takes
    if (d->mode == OutputMode::Stereo) {
        st.resize(frames * 2);
        fold_to_stereo(pcm51, st.data(), frames);
    }
    std::lock_guard<std::mutex> g(d->lock);
    if (d->mode == OutputMode::Pcm51) d->fq.put(pcm51, frames * 6);
    else if (d->mode == OutputMode::Stereo) d->fq.put(st.data(), st.size());
}

uint32_t AudioOutput::device() const { return d->opened_on; }
void AudioOutput::stop_for_test() {
    if (d->fake) { d->fake_run = false; if (d->fake_thread.joinable()) d->fake_thread.join(); return; }
    if (d->passthrough && d->proc) AudioDeviceStop(d->dev, d->proc);
    if (d->unit) AudioOutputUnitStop(d->unit);
}
bool AudioOutput::exclusive() const { return d->passthrough; }

bool AudioOutput::stalled(double seconds) const {
    return (d->unit || d->passthrough || d->fake) && now_ns() - d->last_callback.load(std::memory_order_relaxed) > (int64_t)(seconds * 1e9);
}

size_t AudioOutput::queued_frames() const {
    std::lock_guard<std::mutex> g(d->lock);
    // A burst stands for 1536 frames at the open rate (6,144 bytes for AC-3, 24,576 for E-AC-3), or 960 (61,440 for MAT).
    return d->bitstream() ? d->bq.size() * d->burst_frames() / d->burst_bytes() : d->fq.size() / d->channels;
}

AudioCallbackStats AudioOutput::take_callback_stats(bool reset_max) {
    std::lock_guard<std::mutex> g(d->lock);
    AudioCallbackStats s = d->stats;
    s.queue_bytes = d->bitstream() ? d->bq.size() : d->fq.size() * sizeof(float);
    s.queued_frames = d->bitstream() ? d->bq.size() * d->burst_frames() / d->burst_bytes() : d->fq.size() / d->channels;
    d->stats.zero_callbacks = 0;
    if (reset_max) d->stats.gap_max_ns = d->stats.encode_max_ns = 0;
    return s;
}

std::string AudioOutput::describe() const {
    switch (d->mode) {
    case OutputMode::Pcm51: return "PCM 5.1 (6 ch float, MPEG 5.1 A layout)";
    case OutputMode::Stereo: return "Stereo (ITU fold-down)";
    case OutputMode::TrueHd:
        return d->offline ? "Dolby TrueHD 5.1 (lossless, 24-bit, MAT in IEC 61937 at 768 kHz, offline sink)"
                          : "Dolby TrueHD 5.1 (lossless, 24-bit, MAT in IEC 61937 passthrough to the device at 768 kHz)";
    case OutputMode::DolbyDigitalPlus:
        return d->offline ? "Dolby Digital Plus 5.1 (E-AC-3 1024 kbit/s, IEC 61937 at 192 kHz, offline sink)"
                          : "Dolby Digital Plus 5.1 (E-AC-3 1024 kbit/s, IEC 61937 passthrough to the device at 192 kHz)";
    default: return d->offline ? "Dolby Digital 5.1 (AC-3 640 kbit/s, IEC 61937, offline sink)"
                  : d->passthrough ? "Dolby Digital 5.1 (AC-3 640 kbit/s, IEC 61937 passthrough to the device)"
                                   : "Dolby Digital 5.1 (AC-3 640 kbit/s in IEC 61937, through the mixer)";
    }
}

}  // namespace media

// ------------------------------------------------------------------ C API (movie_api.h)
struct MediaMovie {
    media::MovieDecoder dec;
    media::VideoFrame frame;
    std::string desc;
};

extern "C" {
MediaMovie *media_movie_open(const char *path, int channels, int rate) { return media_movie_open_streams(path, channels, rate, 1, 1); }
MediaMovie *media_movie_open_streams(const char *path, int channels, int rate, int video, int audio) {
    MediaMovie *m = new MediaMovie;
    if (!m->dec.open(path, channels, rate, video != 0, audio != 0)) {
        delete m;
        return nullptr;
    }
    m->desc = m->dec.describe();
    return m;
}
int media_movie_pump_audio(MediaMovie *m, size_t min_frames) { return m->dec.pump_audio(min_frames); }
void media_movie_close(MediaMovie *m) { delete m; }
const uint8_t *media_movie_next_frame(MediaMovie *m, int *width, int *height, double *pts_seconds) {
    if (!m->dec.next_frame(m->frame)) return nullptr;
    if (width) *width = m->frame.width;
    if (height) *height = m->frame.height;
    if (pts_seconds) *pts_seconds = m->frame.pts;
    return m->frame.bgra.data();
}
size_t media_movie_take_audio(MediaMovie *m, float *dst, size_t max_frames) { return m->dec.take_audio(dst, max_frames); }
int media_movie_width(const MediaMovie *m) { return m->dec.width(); }
int media_movie_height(const MediaMovie *m) { return m->dec.height(); }
int media_movie_source_channels(const MediaMovie *m) { return m->dec.source_channels(); }
double media_movie_duration(const MediaMovie *m) { return m->dec.duration(); }
const char *media_movie_describe(const MediaMovie *m) { return m->desc.c_str(); }
}
