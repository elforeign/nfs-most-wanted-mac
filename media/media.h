// media.h - movies and multichannel audio for the NFS:MW native port (FFmpeg + CoreAudio).
//
//  MovieDecoder   X360 WMV3/WMA Pro or PC EA VP6/ADPCM -> BGRA frames + interleaved float audio (5.1 or stereo)
//  Ac3Spdif       6-channel float PCM -> Dolby Digital (AC-3) or Dolby Digital Plus (E-AC-3) frames wrapped as
//                 IEC 61937 bursts
//  AudioOutput    CoreAudio output: 6-channel PCM (lossless), or the AC-3 / E-AC-3 bitstream as 2-ch 16-bit "PCM"
#pragma once
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace media {

// Channel order used everywhere here (and by the game's Snd engine in mode 3): FL FR FC LFE BL BR.
constexpr int kSurroundChannels = 6;

struct VideoFrame {
    int width = 0, height = 0;
    double pts = 0;                  // seconds
    std::vector<uint8_t> bgra;       // width * height * 4
};

class MovieDecoder {
  public:
    MovieDecoder();
    ~MovieDecoder();
    // out_channels: 6 (5.1, sources with fewer channels are up-mapped FL/FR) or 2 (ITU fold-down).
    // video/audio: which streams to decode (at least one). Players open a file twice, one decoder per stream,
    // because the X360 WMVs store audio ~1.4 s later in the file than the video it plays with.
    bool open(const std::string &path, int out_channels = kSurroundChannels, int out_rate = 48000, bool video = true,
              bool audio = true);
    // Decodes until one video frame is ready (audio decoded on the way is queued). false at end of file.
    bool next_frame(VideoFrame &out);
    // Decodes audio packets (skipping the others) until min_frames are queued. false at end of file.
    bool pump_audio(size_t min_frames);
    // Moves up to max_frames of queued interleaved float audio into dst; returns frames moved.
    size_t take_audio(float *dst, size_t max_frames);
    int width() const;
    int height() const;
    double duration() const;
    int source_channels() const;
    std::string describe() const;

  private:
    struct Impl;
    std::unique_ptr<Impl> d;
};

// Dolby Digital (AC-3) or Dolby Digital Plus (E-AC-3) in IEC 61937 bursts, the byte stream an S/PDIF / HDMI passthrough
// output sends as 2-ch 16-bit little-endian samples. One burst per 1536-sample encoded frame (32 ms at 48 kHz):
//   AC-3   (IEC 61937-3): burst every 6,144 bytes = 1,536 frames of 2 x 16-bit at 48 kHz (the link runs at the audio
//          rate). Pc = 0x0001 | bsmod << 8, Pd = payload length in bits.
//   E-AC-3 (IEC 61937-3): burst every 24,576 bytes = 6,144 frames of 2 x 16-bit at 192 kHz (the link runs at 4x the
//          audio rate, macOS's 'cec3' format). Pc = 0x0015, Pd = payload length in bytes. A burst carries 6 audio
//          blocks; our encoder always writes 6-block frames, so one frame per burst (FFmpeg's spdifenc.c
//          spdif_header_eac3 packs 6 / 3 / 2 / 1 frames of 1 / 2 / 3 / 6 blocks the same way).
class Ac3Spdif {
  public:
    static constexpr int kFramesPerBurst = 1536;                 // one AC-3 / 6-block E-AC-3 frame
    static constexpr size_t kBurstBytes = kFramesPerBurst * 4;   // AC-3: as 2 ch x 16-bit at 48 kHz
    static constexpr size_t kEac3BurstBytes = kBurstBytes * 4;   // E-AC-3: as 2 ch x 16-bit at 192 kHz
    // 1,024 kbit/s is E-AC-3's ceiling for 6-block frames at 48 kHz (frmsiz is 11 bits: at most 2,048 words = 4,096
    // bytes per 32 ms frame; FFmpeg's encoder switches to shorter frames above it). 60 % more than AC-3's 640 kbit/s
    // maximum, the same frame timing, and well inside the 24,568 bytes an E-AC-3 burst can carry.
    static constexpr int kEac3BitRate = 1024000;
    Ac3Spdif();
    ~Ac3Spdif();
    bool open(int bitrate = 640000);                 // AC-3
    bool open_eac3(int bitrate = kEac3BitRate);      // E-AC-3
    bool eac3() const;
    size_t burst_bytes() const;                      // kBurstBytes or kEac3BurstBytes
    size_t pending_frames() const;                   // pushed but not yet in a burst (< 1536)
    // Feed interleaved 6-channel float (FL FR FC LFE BL BR) at 48 kHz; complete bursts are appended to out.
    void push(const float *pcm, size_t frames, std::vector<uint8_t> &out);
    // IEC 61937-3 burst for one AC-3 frame (Pa F872, Pb 4E1F, Pc type 1 | bsmod<<8, Pd bits), zero padded.
    static size_t wrap(const uint8_t *frame, size_t bytes, uint8_t *burst);
    // IEC 61937-3 burst for one 6-block E-AC-3 frame (Pa F872, Pb 4E1F, Pc 0x0015, Pd bytes), zero padded to 24,576.
    static size_t wrap_eac3(const uint8_t *frame, size_t bytes, uint8_t *burst);

  private:
    struct Impl;
    std::unique_ptr<Impl> d;
};

// Dolby TrueHD 5.1, lossless, in IEC 61937 MAT frames: FFmpeg's (experimental) truehd encoder at 24 bits, access units
// of 40 samples, packed by FFmpeg's spdif muxer (spdifenc.c spdif_header_truehd) into 61,424-byte MAT frames (start,
// middle and end codes at fixed offsets; each access unit placed at 2,560 bytes per 40 samples of its input timing,
// zeros between), one 61,440-byte burst (Pc 0x0016, Pd 61,424 bytes) per 960 samples, sent at 768 kHz (4x 192 kHz:
// the HDMI high-bit-rate link; macOS's 'mtct' format, 15,360 frames per packet). The 6-channel mix reaches the decoder
// bit for bit (24-bit; the game's 16-bit audio exactly), with its channels labelled in the stream like Dolby Digital.
class TrueHdMat {
  public:
    static constexpr int kFramesPerBurst = 960;                  // one MAT frame: 24 access units of 40 samples
    static constexpr size_t kBurstBytes = 61440;                 // 15,360 frames of 2 ch x 16-bit at 768 kHz
    static constexpr size_t kMatFrameBytes = 61424;
    TrueHdMat();
    ~TrueHdMat();
    bool open();
    // Feed interleaved 6-channel float (FL FR FC LFE BL BR) at 48 kHz (quantised to 24 bits); bursts are appended.
    void push(const float *pcm, size_t frames, std::vector<uint8_t> &out);
    size_t pending_frames() const;   // pushed but not yet in a burst (the encoder's restart interval + the open MAT frame)
    // Tests: the TrueHD bytes of one MAT burst (byte-swapped back, the three MAT codes removed; zeros between access
    // units stay). false if it isn't a MAT burst.
    static bool unwrap(const uint8_t *burst, size_t bytes, std::vector<uint8_t> &truehd);

  private:
    struct Impl;
    std::unique_ptr<Impl> d;
};

enum class OutputMode { Pcm51, Stereo, DolbyDigital, DolbyDigitalPlus, TrueHd };
const char *output_mode_name(OutputMode m);   // "pcm51", "stereo", "dd", "ddp", "truehd"

struct AudioCallbackStats {
    uint64_t callbacks = 0, valid = 0, taken_bytes = 0, zero_bytes = 0;
    uint64_t zero_callbacks = 0;  // callbacks with a shortage since the previous take_callback_stats(), even without BOOT_DIAG
    size_t queue_bytes = 0, queued_frames = 0;
    uint64_t gap_max_ns = 0, encode_max_ns = 0;  // BOOT_DIAG interval maxima
};

class AudioOutput {
  public:
    AudioOutput();
    ~AudioOutput();
    // Opens the default output device. Pcm51: 6-ch float with the 5.1 channel layout (CoreAudio maps or folds
    // it for the device). Stereo: folds to 2 ch here. DolbyDigital: 48 kHz AC-3 bitstream as 2-ch 16-bit; this
    // only sounds right on a device/receiver that decodes IEC 61937 (HDMI, optical). DolbyDigitalPlus: E-AC-3 in
    // IEC 61937 at 192 kHz, passthrough only (the device must offer 'cec3'; there is no mixer fallback, a 192 kHz
    // bitstream through the system mixer would be noise). TrueHd: Dolby TrueHD in MAT at 768 kHz ('mtct'),
    // passthrough only likewise. The bitstream modes take 48 kHz input.
    bool open(OutputMode mode, int rate = 48000);
    // Offline sink (tests, mediatest --out): the same queue, encoder and burst wrapping, but no device is opened.
    // render_offline() then hands out exactly the bytes the IOProc / render callback would give the device for a
    // buffer of `bytes` (bitstream modes: IEC 61937 as s16le stereo, zero filled when the queue runs dry; PCM modes:
    // interleaved float).
    bool open_offline(OutputMode mode, int rate = 48000);
    void render_offline(void *dst, size_t bytes);
    size_t device_frame_bytes() const;   // bytes per device frame: 4 for the bitstreams, 4 x channels for PCM
    int device_rate() const;             // the device's frame rate: 192000 for E-AC-3, 768000 for MAT, else the open rate
    size_t pending_frames() const;       // bitstream modes: input frames held before the next burst (encoder, MAT frame)
    void close();
    // Interleaved 6-channel float input at the open rate (mode conversion happens here).
    void write(const float *pcm51, size_t frames);
    size_t queued_frames() const;
    int minimum_queue_frames() const;   // PCM: current two device periods plus safety offset; atomic input-rate frames
    // Cumulative BOOT_DIAG byte/callback totals, queue depth now, and shortage callbacks since the previous read.
    // Maxima reset only when requested (the mixer emits them once per second). No callback-thread logging.
    AudioCallbackStats take_callback_stats(bool reset_max = false);
    std::string describe() const;
    // No consumption or valid empty-queue callback for `seconds`. Queued data requires consumption progress;
    // an unusable selected stream/buffer never refreshes health, even when the queue is empty.
    // Starts with a grace period at open. The output has to be reopened.
    bool stalled(double seconds) const;
    uint32_t device() const;     // the CoreAudio device it was opened on (0 if unknown)
    // Dolby Digital passthrough holds its device in hog mode, so macOS moves the system default output elsewhere
    // meanwhile: such an output only has to be reopened when its device goes away (device_alive).
    bool exclusive() const;
    // Tests: the device stops asking for audio, as if it went away (the output stays open until close()).
    void stop_for_test();

  private:
#ifdef MEDIA_AUDIO_CALLBACK_TEST
    friend struct AudioOutputTest;  // standalone dstest callback fixture; no device or production test controls
#endif
    struct Impl;
    std::unique_ptr<Impl> d;
};

// Whether the default output device offers E-AC-3 passthrough ('cec3' at 192 kHz). Dolby Digital Plus is never picked
// automatically (the user chooses it in the Audio menu or with NFS_AUDIO=ddp); this is for the log.
bool default_device_takes_eac3();
bool default_device_takes_mat();   // Dolby TrueHD in MAT ('mtct' at 768 kHz)

// The mode for the default output device: TrueHd when it takes MAT (lossless, and like any passthrough it keeps the
// channel order, which multichannel PCM through a TV's eARC may not; confirmed by listening on an eARC system), else DolbyDigital when it takes encoded AC-3 (HDMI or optical to a decoder), else Pcm51 when its speaker
// layout (Audio MIDI Setup > Configure Speakers) has surround channels, Stereo otherwise (built-in speakers,
// headphones, AirPods: fold_to_stereo here rather than whatever CoreAudio would do with 6 channels on 2). `why` gets
// the device name and the reason, for the log. truehd = false skips TrueHD (the fallback when its passthrough is refused).
OutputMode preferred_output_mode(std::string *why = nullptr, bool truehd = true);

// Changes whenever the default output device changes or a device appears or goes away (a CoreAudio listener, installed
// by the first call). An open AudioOutput stays on its device; a caller that follows the default reopens.
uint64_t output_devices_generation();
uint32_t default_output_device();   // the default output's AudioDeviceID, 0 if none
bool device_alive(uint32_t dev);    // still connected and running (kAudioDevicePropertyDeviceIsAlive)
void test_refuse_passthrough(bool on);   // tests: passthrough refused, PCM AudioUnit creation counted and refused
int test_unit_attempts();                // tests: PCM AudioUnit creations attempted since test_refuse_passthrough
void fail_next_opens(int n);        // tests: the next n AudioOutput::open calls fail, as with no device
std::string device_name(uint32_t dev);   // a CoreAudio device's name, for the log ("?" if unknown)
// NFS_AUDIO_FAKE_DEVICE=1 (tests): AudioOutput opens on a fake HDMI TV (takes MAT, AC-3, E-AC-3) or fake speakers and
// never touches CoreAudio; while a passthrough holds the TV the default output is the speakers, as on macOS.

// ITU-R BS.775 fold-down 5.1 -> stereo (LFE dropped), as the X360 does for stereo outputs.
void fold_to_stereo(const float *in51, float *out2, size_t frames);

}  // namespace media
