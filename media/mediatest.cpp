// mediatest - headless checks for port/media.
//   mediatest decode <movie> <outdir> [frames]   frames as .ppm (first, middle), all audio as 6-ch WAV, channel RMS
//   mediatest ac3 <movie> <out.spdif>            movie audio -> AC-3 5.1 -> IEC 61937 stream (check with ffprobe -f spdif)
//   mediatest eac3 <movie> <out.spdif>           the same as E-AC-3 (Dolby Digital Plus, 1024 kbit/s, 24,576-byte bursts)
//   mediatest truehd <movie> <out.spdif>         the same as Dolby TrueHD in MAT (lossless, 61,440-byte bursts)
//   mediatest play <movie> <pcm51|stereo|dd|ddp|truehd> [seconds] [mute]   CoreAudio playback smoke test ("mute" sends silence)
//   mediatest speakers <dir> [1-6] [dd|ddp|truehd] [--out <file>] [--lead <s>]   spoken speaker names one channel at a time (see below)
//   mediatest tones <dd|ddp|truehd> <seconds> --out <file>   a steady tone per channel (FL 440 FR 550 C 660 LFE 45 BL 770 BR 880 Hz)
//   mediatest spdifcheck <file.spdif> [<out.f32>]   checks every IEC 61937 burst header, decodes the stream back with
//                                                   FFmpeg's spdif demuxer (TrueHD: MAT unwrapped here, FFmpeg's
//                                                   demuxer doesn't read it) and writes 6-ch float (FL FR FC LFE BL BR)
//   mediatest tone <out.wav> <hz> <seconds> [amplitude]   mono 16-bit 48 kHz test tone (the LFE file for speakers)
// --out never opens a device: the output's offline sink writes exactly the bytes its IOProc would hand the device
// (AudioOutput::open_offline / render_offline), pulled in 512-frame device buffers.
#include <atomic>
#include <chrono>
#include <new>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <algorithm>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include "media.h"

extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
}

using namespace media;

static void write_ppm(const std::string &path, const VideoFrame &f) {
    FILE *o = fopen(path.c_str(), "wb");
    fprintf(o, "P6\n%d %d\n255\n", f.width, f.height);
    for (size_t i = 0; i < (size_t)f.width * f.height; ++i) {
        const uint8_t *p = &f.bgra[i * 4];
        uint8_t rgb[3] = {p[2], p[1], p[0]};
        fwrite(rgb, 1, 3, o);
    }
    fclose(o);
}

static void write_wav6(const std::string &path, const std::vector<float> &pcm, int rate) {
    FILE *o = fopen(path.c_str(), "wb");
    uint32_t frames = (uint32_t)(pcm.size() / 6), data = frames * 6 * 2;
    auto u32 = [o](uint32_t v) { fwrite(&v, 4, 1, o); };
    auto u16 = [o](uint16_t v) { fwrite(&v, 2, 1, o); };
    fwrite("RIFF", 1, 4, o); u32(4 + 8 + 40 + 8 + data); fwrite("WAVE", 1, 4, o);
    fwrite("fmt ", 1, 4, o); u32(40); u16(0xFFFE); u16(6); u32(rate); u32(rate * 12); u16(12); u16(16);
    u16(22); u16(16); u32(0x3F);   // WAVE_FORMAT_EXTENSIBLE, mask FL FR FC LFE BL BR
    const uint8_t pcm_guid[16] = {1, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71};
    fwrite(pcm_guid, 1, 16, o);
    fwrite("data", 1, 4, o); u32(data);
    for (float v : pcm) {
        int s = (int)lrintf(std::fmax(-1.f, std::fmin(1.f, v)) * 32767.f);
        u16((uint16_t)(int16_t)s);
    }
    fclose(o);
}

// mediatest speakers <dir> [1-6] [dd|ddp|truehd] [--out <file>]: plays <dir>/0.wav .. 5.wav (mono 16-bit 48 kHz, e.g. spoken
// speaker names from `say`, a 45 Hz tone for the LFE: media/speaker_wavs.sh) on 5.1 channels L R C LFE Ls Rs in turn,
// through AudioOutput like the game, to check the routing to real speakers. With a channel number, only that channel,
// three times. With "dd" / "ddp" / "truehd", through Dolby Digital / Dolby Digital Plus / Dolby TrueHD (passthrough
// when the device offers AC-3 / E-AC-3 / MAT). With --out, nothing is played: the device's byte stream goes to the file.
static std::vector<float> load_wav(const std::string &path) {
    std::vector<float> out;
    FILE *f = fopen(path.c_str(), "rb");
    if (!f) return out;
    std::vector<uint8_t> b;
    uint8_t buf[65536];
    for (size_t n; (n = fread(buf, 1, sizeof buf, f)) > 0;) b.insert(b.end(), buf, buf + n);
    fclose(f);
    for (size_t p = 12; p + 8 <= b.size();) {                // RIFF chunks: find "data"
        uint32_t len = b[p + 4] | b[p + 5] << 8 | b[p + 6] << 16 | (uint32_t)b[p + 7] << 24;
        if (!memcmp(&b[p], "data", 4)) {
            for (size_t i = p + 8; i + 1 < b.size() && i < p + 8 + len; i += 2) out.push_back((int16_t)(b[i] | b[i + 1] << 8) / 32768.0f);
            break;
        }
        p += 8 + len + (len & 1);
    }
    return out;
}
// Plays (or with an offline output, renders to `file`) until at most `down_to` frames are queued.
struct Sink {
    AudioOutput out;
    FILE *file = nullptr;
    std::vector<uint8_t> chunk;
    bool open(OutputMode mode, const char *path) {
        if (!path) return out.open(mode, 48000);
        if (!out.open_offline(mode, 48000) || !(file = fopen(path, "wb"))) return false;
        chunk.resize(512 * out.device_frame_bytes());        // one device buffer of 512 frames
        return true;
    }
    void drain(size_t down_to) {
        while (out.queued_frames() > down_to) {
            if (!file) { std::this_thread::sleep_for(std::chrono::milliseconds(20)); continue; }
            out.render_offline(chunk.data(), chunk.size());
            fwrite(chunk.data(), 1, chunk.size(), file);
        }
    }
    void close() {
        out.close();
        if (file) fclose(file);
        file = nullptr;
    }
};

static OutputMode parse_mode(const std::string &m) {
    return m == "stereo" ? OutputMode::Stereo : m == "dd" ? OutputMode::DolbyDigital
         : m == "ddp" ? OutputMode::DolbyDigitalPlus : m == "truehd" ? OutputMode::TrueHd : OutputMode::Pcm51;
}

static int speakers(const std::string &dir, int only, OutputMode mode, const char *file, double lead_s) {
    static const char *names[6] = {"front left", "front right", "centre", "subwoofer (LFE)", "surround left", "surround right"};
    Sink sink;
    if (!sink.open(mode, file)) { fprintf(stderr, "cannot open the audio output\n"); return 1; }
    printf("output: %s%s%s\n", sink.out.describe().c_str(), file ? " -> " : "", file ? file : "");
    // Silence first: the TV / receiver has to notice the new format and lock onto it (Dolby Digital: under 1 s; TrueHD's
    // MAT switches the HDMI link to its high-bit-rate mode and took an HDMI receiver several seconds).
    std::vector<float> lead((size_t)(lead_s * 48000) * 6, 0.0f);
    sink.out.write(lead.data(), lead.size() / 6);
    for (int i = 0; i < (only ? 3 : 6); ++i) {
        int ch = only ? only - 1 : i;
        std::vector<float> mono = load_wav(dir + "/" + std::to_string(ch) + ".wav");
        if (mono.empty()) { fprintf(stderr, "no %s/%d.wav\n", dir.c_str(), ch); return 1; }
        mono.resize(mono.size() + 48000 * 3 / 2, 0.0f);       // a pause after each name
        std::vector<float> frames(mono.size() * 6, 0.0f);
        for (size_t i = 0; i < mono.size(); ++i) frames[i * 6 + (size_t)ch] = mono[i];
        printf("channel %d: %s\n", ch + 1, names[ch]);
        fflush(stdout);
        sink.out.write(frames.data(), mono.size());
        // The next name goes in while 0.25 s of this one's pause is still queued: the bitstream stays gapless (a
        // gap of zeros between bursts is legal, but a decoder may mute while it finds the burst period again).
        sink.drain(48000 / 4);
    }
    sink.drain(0);
    sink.close();
    return 0;
}

// mediatest tones <dd|ddp|truehd> <seconds> --out <file>: one steady sine per channel, so a decoded round trip shows
// each channel's level, frequency, crosstalk and any dropped or repeated frame (a phase jump).
static const double kToneHz[6] = {440, 550, 660, 45, 770, 880};
static const double kToneAmp[6] = {0.25, 0.25, 0.25, 0.5, 0.25, 0.25};
static int tones(OutputMode mode, double seconds, const char *file) {
    Sink sink;
    if (!sink.open(mode, file)) { fprintf(stderr, "cannot open the audio output\n"); return 1; }
    printf("output: %s%s%s\n", sink.out.describe().c_str(), file ? " -> " : "", file ? file : "");
    const size_t total = (size_t)(seconds * 48000), block = 1000;   // odd-sized writes, as the game's mixer makes them
    std::vector<float> buf(block * 6);
    for (size_t t = 0; t < total; t += block) {
        size_t n = std::min(block, total - t);
        for (size_t i = 0; i < n; ++i)
            for (int ch = 0; ch < 6; ++ch)
                buf[i * 6 + ch] = (float)(kToneAmp[ch] * std::sin(2 * M_PI * kToneHz[ch] * (double)(t + i) / 48000.0));
        sink.out.write(buf.data(), n);
        sink.drain(48000 / 8);
    }
    sink.drain(0);
    sink.close();
    printf("%zu frames (%.2f s) of tones\n", total, total / 48000.0);
    return 0;
}

static int write_tone(const char *path, double hz, double seconds, double amp) {
    FILE *o = fopen(path, "wb");
    if (!o) return 1;
    uint32_t frames = (uint32_t)(seconds * 48000), data = frames * 2;
    auto u32 = [o](uint32_t v) { fwrite(&v, 4, 1, o); };
    auto u16 = [o](uint16_t v) { fwrite(&v, 2, 1, o); };
    fwrite("RIFF", 1, 4, o); u32(36 + data); fwrite("WAVE", 1, 4, o);
    fwrite("fmt ", 1, 4, o); u32(16); u16(1); u16(1); u32(48000); u32(96000); u16(2); u16(16);
    fwrite("data", 1, 4, o); u32(data);
    const uint32_t ramp = 2400;                               // 50 ms fades: no clicks
    for (uint32_t i = 0; i < frames; ++i) {
        double g = std::min(1.0, std::min(i, frames - 1 - i) / (double)ramp);
        u16((uint16_t)(int16_t)lrint(32767.0 * amp * g * std::sin(2 * M_PI * hz * i / 48000.0)));
    }
    fclose(o);
    return 0;
}

// mediatest spdifcheck <file.spdif> [<out.f32>]: walks the IEC 61937 stream burst by burst (Pa/Pb sync, Pc data type,
// Pd length, the payload's AC-3 / E-AC-3 sync word and frame size, zero stuffing up to the next burst, the burst
// period), then decodes it with FFmpeg's spdif demuxer and ac3/eac3 decoder; out.f32 gets the decoded interleaved
// float in FL FR FC LFE BL BR order, for media/spdif_verify.py.
// The TrueHD bytes from the MAT frames: access units (a 16-bit word: 4-bit check nibble, 12-bit length in words; then
// the 16-bit input timing) with zero padding between them. Each is decoded with FFmpeg's truehd decoder; the input
// timing must go up by 40 samples from one to the next (none lost or repeated).
static int decode_truehd(const std::vector<uint8_t> &t, const char *out_path, size_t errors) {
    const AVCodec *codec = avcodec_find_decoder(AV_CODEC_ID_TRUEHD);
    AVCodecContext *dec = codec ? avcodec_alloc_context3(codec) : nullptr;
    if (!dec || avcodec_open2(dec, codec, nullptr) < 0) { fprintf(stderr, "no TrueHD decoder\n"); return 1; }
    AVPacket *pkt = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    std::vector<float> pcm;
    size_t units = 0, majors = 0, decode_errors = 0, timing_errors = 0, max_unit = 0, unit_bytes = 0;
    int last_timing = -1;
    char layout[128] = "?";
    int bits = 0;
    auto drain = [&] {
        while (avcodec_receive_frame(dec, frame) == 0) {
            av_channel_layout_describe(&frame->ch_layout, layout, sizeof layout);
            int nch = frame->ch_layout.nb_channels;
            bits = dec->bits_per_raw_sample;
            for (int i = 0; i < frame->nb_samples; ++i)
                for (int ch = 0; ch < 6; ++ch) {
                    float v = 0;
                    if (ch < nch) {
                        if (frame->format == AV_SAMPLE_FMT_S32) v = ((const int32_t *)frame->data[0])[i * nch + ch] / 2147483648.0f;
                        else if (frame->format == AV_SAMPLE_FMT_S32P) v = ((const int32_t *)frame->extended_data[ch])[i] / 2147483648.0f;
                        else if (frame->format == AV_SAMPLE_FMT_S16) v = ((const int16_t *)frame->data[0])[i * nch + ch] / 32768.0f;
                        else if (frame->format == AV_SAMPLE_FMT_S16P) v = ((const int16_t *)frame->extended_data[ch])[i] / 32768.0f;
                    }
                    pcm.push_back(v);
                }
        }
    };
    for (size_t p = 0; p + 4 <= t.size();) {
        unsigned w = t[p] << 8 | t[p + 1];
        if (!w) { p += 2; continue; }                         // padding between access units
        size_t len = (w & 0xFFF) * 2;
        if (len < 4 || p + len > t.size()) { ++decode_errors; break; }
        int timing = t[p + 2] << 8 | t[p + 3];
        if (last_timing >= 0 && ((timing - last_timing) & 0xFFFF) != 40) ++timing_errors;
        last_timing = timing;
        if (len >= 8 && t[p + 4] == 0xF8 && t[p + 5] == 0x72 && t[p + 6] == 0x6F && t[p + 7] == 0xBA) ++majors;
        max_unit = std::max(max_unit, len);
        unit_bytes += len;
        av_new_packet(pkt, (int)len);
        memcpy(pkt->data, &t[p], len);
        if (avcodec_send_packet(dec, pkt) < 0) ++decode_errors;
        av_packet_unref(pkt);
        drain();
        ++units;
        p += len;
    }
    avcodec_send_packet(dec, nullptr);
    drain();
    printf("TrueHD: %zu access units (%zu with a major sync), largest %zu bytes (the MAT slot is 2,560), %.0f kbit/s "
           "on average, %zu timing steps other than 40\n", units, majors, max_unit,
           units ? unit_bytes * 8.0 / (units * 40 / 48000.0) / 1000 : 0.0, timing_errors);
    printf("decoded: truehd, %d Hz, %s, %d-bit, %zu frames (%.3f s), %zu decode errors\n", dec->sample_rate, layout, bits,
           pcm.size() / 6, pcm.size() / 6 / 48000.0, decode_errors);
    if (out_path) {
        FILE *o = fopen(out_path, "wb");
        if (o) { fwrite(pcm.data(), sizeof(float), pcm.size(), o); fclose(o); }
    }
    av_frame_free(&frame);
    av_packet_free(&pkt);
    avcodec_free_context(&dec);
    return errors || decode_errors || timing_errors ? 1 : 0;
}

static int spdifcheck(const char *path, const char *out_path) {
    std::vector<uint8_t> b;
    {
        FILE *f = fopen(path, "rb");
        if (!f) { fprintf(stderr, "cannot read %s\n", path); return 1; }
        uint8_t buf[65536];
        for (size_t n; (n = fread(buf, 1, sizeof buf, f)) > 0;) b.insert(b.end(), buf, buf + n);
        fclose(f);
    }
    auto le16 = [&b](size_t at) { return (unsigned)(b[at] | b[at + 1] << 8); };
    size_t bursts = 0, errors = 0, gaps = 0, last = SIZE_MAX, period_min = SIZE_MAX, period_max = 0, nonzero_stuffing = 0;
    unsigned pc_seen = 0xFFFF, pd_min = 0xFFFF, pd_max = 0, frames_hdr_mismatch = 0;
    std::vector<uint8_t> thd;                                 // TrueHD bytes out of the MAT frames
    int bad_shown = 0;
    auto bad = [&](size_t at, const char *what) {
        ++errors;
        if (bad_shown++ < 10) printf("  burst at byte %zu: %s\n", at, what);
    };
    for (size_t p = 0; p + 8 <= b.size();) {
        if (le16(p) != 0xF872 || le16(p + 2) != 0x4E1F) {
            if (b[p] || b[p + 1]) ++nonzero_stuffing;
            p += 2;
            continue;
        }
        unsigned pc = le16(p + 4), pd = le16(p + 6), type = pc & 0x1F;
        bool eac3 = type == 0x15, mat = type == 0x16;
        size_t period = mat ? TrueHdMat::kBurstBytes : eac3 ? Ac3Spdif::kEac3BurstBytes : Ac3Spdif::kBurstBytes;
        size_t len = eac3 || mat ? pd : (pd + 7) / 8;         // E-AC-3 and MAT: Pd in bytes, AC-3: in bits
        if (type != 0x01 && type != 0x15 && type != 0x16) bad(p, "data type is not AC-3 (1), E-AC-3 (0x15) or MAT (0x16)");
        if (mat) {                                            // MAT: Pd is the MAT frame, the codes sit at fixed places
            if (pd != TrueHdMat::kMatFrameBytes) bad(p, "MAT Pd isn't 61,424");
            if (pc & 0xFFE0) bad(p, "MAT Pc has bits above the data type set");
            if (p + period > b.size()) { bad(p, "MAT burst cut short"); break; }
            if (!TrueHdMat::unwrap(&b[p], period, thd)) bad(p, "MAT start / middle / end codes missing");
            for (size_t i = p + 8 + len; i < p + period; ++i)
                if (b[i]) { bad(p, "non-zero bytes after the MAT frame"); break; }
            if (last != SIZE_MAX) {
                size_t dp = p - last;
                period_min = std::min(period_min, dp);
                period_max = std::max(period_max, dp);
                if (dp != period) ++gaps;
            }
            pc_seen = pc;
            pd_min = std::min(pd_min, pd);
            pd_max = std::max(pd_max, pd);
            last = p;
            ++bursts;
            p += period;
            continue;
        }
        if (pc_seen != 0xFFFF && pc != pc_seen) bad(p, "Pc changed");
        if (eac3 && (pc & 0xFFE0)) bad(p, "E-AC-3 Pc has bits above the data type set");
        pc_seen = pc;
        pd_min = std::min(pd_min, pd);
        pd_max = std::max(pd_max, pd);
        if (len + 8 > period || p + 8 + len > b.size()) { bad(p, "Pd longer than the burst"); p += 2; continue; }
        // payload: the frame's 16-bit words byte-swapped (LE samples), so the sync word 0B77 reads 77 0B
        const uint8_t *q = &b[p + 8];
        if (q[0] != 0x77 || q[1] != 0x0B) bad(p, "payload doesn't start with the AC-3 sync word");
        else {
            uint8_t h[6] = {q[1], q[0], q[3], q[2], q[5], q[4]};   // header bytes in stream order
            size_t frame_bytes = 0;
            if (eac3) {
                frame_bytes = (((h[2] & 7u) << 8 | h[3]) + 1) * 2;
                unsigned bsid = h[5] >> 3, strmtyp = h[2] >> 6, fscod = h[4] >> 6, nblk = fscod == 3 ? 3 : (h[4] >> 4) & 3;
                if (bsid != 16 || strmtyp != 0 || nblk != 3) bad(p, "not an independent 6-block E-AC-3 frame");
            } else {
                static const uint16_t words48[38] = {64, 64, 80, 80, 96, 96, 112, 112, 128, 128, 160, 160, 192, 192,
                    224, 224, 256, 256, 320, 320, 384, 384, 448, 448, 512, 512, 640, 640, 768, 768, 896, 896, 1024, 1024,
                    1152, 1152, 1280, 1280};
                unsigned fscod = h[4] >> 6, code = h[4] & 0x3F;
                if (fscod == 0 && code < 38) frame_bytes = words48[code] * 2;
                if ((pc >> 8 & 7) != (h[5] & 7u)) bad(p, "Pc bsmod differs from the frame's");
            }
            if (frame_bytes != len) ++frames_hdr_mismatch;
        }
        for (size_t i = p + 8 + ((len + 1) & ~(size_t)1); i < std::min(b.size(), p + period); ++i)
            if (b[i]) { bad(p, "non-zero bytes after the payload"); break; }
        if (last != SIZE_MAX) {
            size_t dp = p - last;
            period_min = std::min(period_min, dp);
            period_max = std::max(period_max, dp);
            if (dp != period) ++gaps;
            if (dp < period) bad(p, "burst closer than one period to the previous one");
        }
        last = p;
        ++bursts;
        p += period;
    }
    printf("%s: %zu bytes, %zu bursts, Pc 0x%04X, Pd %u..%u, period %zu..%zu bytes, %zu periods not exactly one burst "
           "(gaps), %u payloads whose frame header disagrees with Pd, %zu non-zero 16-bit words outside bursts, "
           "%zu errors\n", path, b.size(), bursts, pc_seen, pd_min, pd_max, period_min == SIZE_MAX ? 0 : period_min,
           period_max, gaps, frames_hdr_mismatch, nonzero_stuffing, errors);

    if (pc_seen == 0x16) return decode_truehd(thd, out_path, errors);

    // Decode back through FFmpeg: spdif demuxer -> ac3/eac3 decoder.
    AVFormatContext *fmt = nullptr;
    const AVInputFormat *spdif = av_find_input_format("spdif");
    if (avformat_open_input(&fmt, path, spdif, nullptr) != 0 || avformat_find_stream_info(fmt, nullptr) < 0) {
        fprintf(stderr, "FFmpeg can't open %s as IEC 61937\n", path);
        return 1;
    }
    AVCodecParameters *par = fmt->streams[0]->codecpar;
    const AVCodec *codec = avcodec_find_decoder(par->codec_id);
    AVCodecContext *dec = codec ? avcodec_alloc_context3(codec) : nullptr;
    if (!dec || avcodec_parameters_to_context(dec, par) < 0 || avcodec_open2(dec, codec, nullptr) < 0) {
        fprintf(stderr, "no decoder for %s\n", avcodec_get_name(par->codec_id));
        return 1;
    }
    AVPacket *pkt = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    std::vector<float> pcm;
    size_t packets = 0, decode_errors = 0;
    long long bitrate = 0;
    char layout[128] = "?";
    auto drain = [&] {
        while (avcodec_receive_frame(dec, frame) == 0) {
            av_channel_layout_describe(&frame->ch_layout, layout, sizeof layout);
            int nch = frame->ch_layout.nb_channels;
            for (int i = 0; i < frame->nb_samples; ++i)
                for (int ch = 0; ch < 6; ++ch) {
                    float v = 0;
                    if (ch < nch) {
                        if (frame->format == AV_SAMPLE_FMT_FLTP) v = ((const float *)frame->extended_data[ch])[i];
                        else if (frame->format == AV_SAMPLE_FMT_FLT) v = ((const float *)frame->data[0])[i * nch + ch];
                    }
                    pcm.push_back(v);
                }
        }
    };
    while (av_read_frame(fmt, pkt) >= 0) {
        ++packets;
        if (avcodec_send_packet(dec, pkt) < 0) ++decode_errors;
        drain();
        av_packet_unref(pkt);
    }
    avcodec_send_packet(dec, nullptr);
    drain();
    bitrate = dec->bit_rate;
    printf("decoded: %s, %d Hz, %s, %lld bit/s, %zu packets, %zu frames (%.3f s), %zu decode errors\n", codec->name,
           dec->sample_rate, layout, bitrate, packets, pcm.size() / 6, pcm.size() / 6 / 48000.0, decode_errors);
    if (out_path) {
        FILE *o = fopen(out_path, "wb");
        if (o) { fwrite(pcm.data(), sizeof(float), pcm.size(), o); fclose(o); }
    }
    av_frame_free(&frame);
    av_packet_free(&pkt);
    avcodec_free_context(&dec);
    avformat_close_input(&fmt);
    return errors || decode_errors ? 1 : 0;
}

// passthrough-fallback (audit2 #4): a forced Dolby Digital whose passthrough is refused must fail the open (the caller
// retries, then plays PCM) and never fall through to a PCM AudioUnit that would play the IEC 61937 bursts as samples.
// Offline: no device is opened (test_refuse_passthrough). Control: PCM 5.1 does reach the unit path (counter 1).
// Result: PASS (dd open=0 units=0, pcm51 units=1). Old code (DD falls through): FAIL, dd open=0 units=1 (the unit path was reached).
static int passthrough_fallback() {
    media::test_refuse_passthrough(true);
    AudioOutput dd;
    bool dd_open = dd.open(OutputMode::DolbyDigital, 48000);
    int dd_units = media::test_unit_attempts();
    dd.close();
    media::test_refuse_passthrough(true);
    AudioOutput pcm;
    bool pcm_open = pcm.open(OutputMode::Pcm51, 48000);
    int pcm_units = media::test_unit_attempts();
    media::test_refuse_passthrough(false);
    printf("dd open=%d units=%d, pcm51 open=%d units=%d\n", dd_open, dd_units, pcm_open, pcm_units);
    bool ok = !dd_open && dd_units == 0 && !pcm_open && pcm_units == 1;
    printf(ok ? "PASS\n" : "FAIL\n");
    return ok ? 0 : 1;
}

// callback-alloc (audit2 #5): the device callback (render_offline shares its take path) must neither allocate nor free.
// The old std::deque queue freed a 512-byte block every 128 floats consumed. Counts global new/delete during 400
// render_offline calls (64 frames of 5.1 each) after 4096 frames were written (Pcm51 and Stereo).
// Result: PASS (allocs=0 frees=0 in both modes). Not covered: a writer holding the lock (its hold is now a memcpy; stereo downmix moved out). Old code (deque): FAIL, Pcm51 frees=23, Stereo frees=7.
static std::atomic<long> g_news{0}, g_deletes{0};
static std::atomic<bool> g_count{false};
void *operator new(size_t n) { if (g_count) ++g_news; void *p = malloc(n ? n : 1); if (!p) throw std::bad_alloc(); return p; }
void operator delete(void *p) noexcept { if (g_count && p) ++g_deletes; free(p); }
void operator delete(void *p, size_t) noexcept { if (g_count && p) ++g_deletes; free(p); }
static int callback_alloc() {
    long frees[2] = {0, 0}, allocs[2] = {0, 0};
    const OutputMode modes[2] = {OutputMode::Pcm51, OutputMode::Stereo};
    for (int m = 0; m < 2; ++m) {
        AudioOutput out;
        if (!out.open_offline(modes[m], 48000)) { printf("open_offline failed\n"); return 1; }
        std::vector<float> in(4096 * 6, 0.25f), dst(64 * 6);
        out.write(in.data(), 4096);
        g_news = 0; g_deletes = 0; g_count = true;
        for (int i = 0; i < 400; ++i) out.render_offline(dst.data(), 64 * out.device_frame_bytes());
        g_count = false;
        frees[m] = g_deletes; allocs[m] = g_news;
        out.close();
    }
    printf("Pcm51 allocs=%ld frees=%ld, Stereo allocs=%ld frees=%ld\n", allocs[0], frees[0], allocs[1], frees[1]);
    bool ok = !frees[0] && !allocs[0] && !frees[1] && !allocs[1];
    printf(ok ? "PASS\n" : "FAIL\n");
    return ok ? 0 : 1;
}

int main(int argc, char **argv) {
    if (argc == 2 && std::string(argv[1]) == "callback-alloc") return callback_alloc();
    if (argc == 2 && std::string(argv[1]) == "passthrough-fallback") return passthrough_fallback();
    double lead_s = -1;                                     // --lead <seconds>: silence before the first name
    for (int i = 1; i + 1 < argc; ++i)
        if (!strcmp(argv[i], "--lead")) {
            lead_s = atof(argv[i + 1]);
            for (int k = i; k + 2 <= argc; ++k) argv[k] = argv[k + 2];
            argc -= 2;
            break;
        }
    const char *out_file = nullptr;                         // --out <file>: offline sink, no device
    for (int i = 1; i + 1 < argc; ++i)
        if (!strcmp(argv[i], "--out")) {
            out_file = argv[i + 1];
            for (int k = i; k + 2 <= argc; ++k) argv[k] = argv[k + 2];
            argc -= 2;
            break;
        }
    if (argc >= 3 && std::string(argv[1]) == "speakers") {      // speakers <dir> [1-6] [dd|ddp]
        std::string last = argv[argc - 1];
        bool coded = last == "dd" || last == "ddp" || last == "truehd";
        int only = argc - coded >= 4 ? atoi(argv[3]) : 0;
        OutputMode mode = coded ? parse_mode(last) : OutputMode::Pcm51;
        if (lead_s < 0) lead_s = mode == OutputMode::TrueHd ? 6 : 1;   // TrueHD: the link takes seconds to lock
        return speakers(argv[2], only, mode, out_file, lead_s);
    }
    if (argc >= 4 && std::string(argv[1]) == "tones") {
        if (!out_file) { fprintf(stderr, "tones writes to a file only: --out <file>\n"); return 2; }
        return tones(parse_mode(argv[2]), atof(argv[3]), out_file);
    }
    if (argc >= 3 && std::string(argv[1]) == "spdifcheck") return spdifcheck(argv[2], argc > 3 ? argv[3] : nullptr);
    if (argc >= 5 && std::string(argv[1]) == "tone")
        return write_tone(argv[2], atof(argv[3]), atof(argv[4]), argc > 5 ? atof(argv[5]) : 0.5);
    if (argc < 4) {
        fprintf(stderr, "usage: mediatest decode|ac3|eac3|play ... | speakers <dir> [1-6] [dd|ddp|truehd] [--out f] | tones | "
                        "spdifcheck | tone (see mediatest.cpp)\n");
        return 2;
    }
    std::string cmd = argv[1], movie = argv[2];
    MovieDecoder dec;
    if (!dec.open(movie)) {
        fprintf(stderr, "cannot open %s\n", movie.c_str());
        return 1;
    }
    printf("%s: %s, %.1f s\n", movie.c_str(), dec.describe().c_str(), dec.duration());

    if (cmd == "decode") {
        std::string out = argv[3];
        int limit = argc > 4 ? atoi(argv[4]) : 1 << 30;
        VideoFrame f;
        int n = 0;
        std::vector<float> audio, chunk(4096 * 6);
        double last_pts = 0;
        while (n < limit && dec.next_frame(f)) {
            if (n == 0) write_ppm(out + "/frame_first.ppm", f);
            if (n == 300) write_ppm(out + "/frame_300.ppm", f);
            last_pts = f.pts;
            ++n;
            size_t got;
            while ((got = dec.take_audio(chunk.data(), 4096))) audio.insert(audio.end(), chunk.begin(), chunk.begin() + got * 6);
        }
        size_t got;
        while ((got = dec.take_audio(chunk.data(), 4096))) audio.insert(audio.end(), chunk.begin(), chunk.begin() + got * 6);
        printf("video: %d frames, last pts %.2f s; audio: %zu frames (%.2f s at 48 kHz)\n", n, last_pts,
               audio.size() / 6, audio.size() / 6 / 48000.0);
        const char *names[6] = {"FL", "FR", "FC", "LFE", "BL", "BR"};
        printf("channel RMS:");
        for (int ch = 0; ch < 6; ++ch) {
            double s = 0;
            for (size_t i = ch; i < audio.size(); i += 6) s += audio[i] * audio[i];
            printf(" %s=%.4f", names[ch], audio.empty() ? 0 : std::sqrt(s / (audio.size() / 6)));
        }
        printf("\n");
        write_wav6(out + "/audio_51.wav", audio, 48000);
        return 0;
    }
    if (cmd == "ac3" || cmd == "eac3" || cmd == "truehd") {
        Ac3Spdif enc;
        TrueHdMat thd;
        bool mat = cmd == "truehd";
        if (!(mat ? thd.open() : cmd == "eac3" ? enc.open_eac3() : enc.open())) {
            fprintf(stderr, "%s encoder unavailable\n", cmd.c_str());
            return 1;
        }
        std::vector<uint8_t> bursts;
        std::vector<float> chunk(4096 * 6);
        VideoFrame f;
        size_t frames = 0;
        bool more = true;
        while (more) {
            more = dec.next_frame(f);   // after the last frame, drain the audio still queued
            size_t got;
            while ((got = dec.take_audio(chunk.data(), 4096))) {
                if (mat) thd.push(chunk.data(), got, bursts);
                else enc.push(chunk.data(), got, bursts);
                frames += got;
            }
        }
        FILE *o = fopen(argv[3], "wb");
        fwrite(bursts.data(), 1, bursts.size(), o);
        fclose(o);
        printf("encoded %zu audio frames -> %zu IEC 61937 bursts (%zu bytes)\n", frames,
               bursts.size() / (mat ? TrueHdMat::kBurstBytes : enc.burst_bytes()), bursts.size());
        return 0;
    }
    if (cmd == "play") {
        std::string m = argv[3];
        OutputMode mode = parse_mode(m);
        double seconds = argc > 4 ? atof(argv[4]) : 5;
        bool mute = argc > 5 && std::string(argv[5]) == "mute";
        AudioOutput outp;
        if (!outp.open(mode)) {
            fprintf(stderr, "cannot open CoreAudio output\n");
            return 1;
        }
        printf("output: %s\n", outp.describe().c_str());
        auto t0 = std::chrono::steady_clock::now();
        std::vector<float> chunk(4096 * 6);
        VideoFrame f;
        while (dec.next_frame(f)) {
            size_t got;
            while ((got = dec.take_audio(chunk.data(), 4096))) {
                if (mute) std::fill(chunk.begin(), chunk.begin() + got * 6, 0.0f);
                outp.write(chunk.data(), got);
            }
            while (outp.queued_frames() > 48000 / 2) std::this_thread::sleep_for(std::chrono::milliseconds(10));
            if (std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() > seconds) break;
        }
        printf("played %.1f s\n", std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count());
        return 0;
    }
    return 2;
}
