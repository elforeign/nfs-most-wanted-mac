// audio.h - the host mixer: the arithmetic, the test seams and the engine.
//
// DirectSound and QMixer both describe a channel with numbers the host has to
// convert before the mixer can use them, and every one of those conversions
// is testable without opening an audio device.
#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// DirectSound volume is attenuation in hundredths of a decibel, 0 for full
// scale down to -10000 for silence. Returns a linear gain in 0..1.
float host_audio_gain_from_millibels(int32_t millibels);

// DirectSound pan is not a position: it is an attenuation applied to one
// channel and only one. A positive pan attenuates the left channel by that
// many hundredths of a decibel and leaves the right alone; a negative pan does
// the reverse. So a centred sound is two unattenuated channels, and panning
// hard right does not make the right channel louder, it silences the left.
//
// That is why this returns two gains rather than a position: an
// equal-power pan law would quietly change the level of every sound the game
// pans, and the volume it set separately would no longer mean what it said.
void host_audio_pan_gains(int32_t millibels, float *left, float *right);

// The rate a Play should use. A channel that has never been retuned by
// SetFrequency plays at whatever rate its buffer was created with; only an
// explicit override displaces that. Inheriting a default rate is how a first
// Play of a 44100 Hz buffer ends up running at half speed.
uint32_t host_audio_play_rate(uint32_t buffer_rate, uint32_t override_rate, int overridden);

// Bytes per sample frame for a format: 8- or 16-bit, mono or stereo.
uint32_t host_audio_frame_bytes(int bits, int channels);

// Six optional planar destinations in FL, FR, FC, LFE, BL, BR order.
// Accepts mono, stereo and standard 5.1 PCM. Does not downmix or upmix.
uint32_t host_audio_decode_planes(const void *pcm, uint32_t bytes, int bits, int channels,
                                  float *const *out, uint32_t max_frames);

// Decodes raw PCM into float samples in -1..1, deinterleaved. `right` may be
// null for a mono destination, in which case a stereo source is downmixed.
// 8-bit PCM is unsigned with 0x80 at silence, 16-bit is signed little-endian:
// both are what a WAV file holds and what the guest hands the shim.
// Returns the number of frames written.
uint32_t host_audio_decode_pcm(const void *pcm, uint32_t bytes, int bits, int channels, float *left,
                               float *right, uint32_t max_frames);

// Where playback has reached after `elapsed` seconds of wall clock, for a
// channel whose node is not reporting progress. A DirectSound play cursor moves
// at the buffer's byte rate from the moment it starts, whether or not anybody
// can hear it: silence is not a stalled device, and a guest that polls
// GetCurrentPosition and sleeps until it moves will wait for ever if it does
// not. The result is aligned down to a sample frame, because real hardware
// reports positions at frame boundaries.
uint32_t host_audio_wall_clock_bytes(double elapsed, uint32_t rate, int bits, int channels,
                                     uint32_t start_offset, uint32_t total_bytes, int loop);

// Where playback has reached, in bytes from the start of the buffer, given the
// frames rendered since it started at `start_offset`. A looping channel wraps
// over the whole buffer, because a loop that began part-way through returns to
// the beginning and not to where it started.
uint32_t host_audio_position_bytes(uint64_t frames_rendered, uint32_t start_offset,
                                   uint32_t total_bytes, int bits, int channels, int loop);

// ---------------------------------------------------------------------------
// The test seam, and the lock-order rule it exists to check.
//
// The mixer keeps the lock discipline the AVFoundation version needed, where
// a completion handler that took the mutex the stopper was holding deadlocked
// the first live run: player calls and channel data stay on separate locks.
//
// Two rules follow, and both are checkable:
//   * No player call is ever made while the channel-data lock is held.
//     host_audio_lock_violations() counts breaches of that.
//   * host_audio_completed takes no lock at all. It stores a generation into
//     an atomic and the guest side reconciles when it next asks.
// ---------------------------------------------------------------------------
struct HostAudioNodeOps {
    void (*stop)(int32_t channel, uint64_t generation);
    void (*play)(int32_t channel, uint64_t generation);
    void (*schedule)(int32_t channel, uint64_t generation, uint32_t from, uint32_t to, int loop);
    // A gapless append. A test watches this to check that continuing a sound
    // never stops or restarts the node, which is the whole point of it.
    void (*queue)(int32_t channel, uint32_t bytes);
    // Optional signed player timeline, including a device's pre-start samples.
    // Returns nonzero when the sample time is valid.
    int (*sample_time)(int32_t channel, int64_t *frames);
};
// Installs stand-ins for node calls, so a test can model a
// completion arriving from inside stop without opening an audio device. Null
// restores the real ones. Installing any also lets channels exist with no
// engine behind them.
void host_audio_set_node_ops(const struct HostAudioNodeOps *ops);

// What a scheduled buffer's completion handler does. Public so a test can fire
// it at the moment the real framework would.
void host_audio_completed(int32_t channel, uint64_t generation);
// The same for a queued buffer, which reports the bytes that finished rather
// than a generation: a stream has no end to name.
void host_audio_queue_completed(int32_t channel, uint32_t bytes);

// How many times a node call was attempted while the channel-data lock was
// held. Zero, always; anything else is the deadlock waiting to happen.
uint32_t host_audio_lock_violations(void);

// The clipper between the mixer and the output saturates anything above full
// scale and leaves everything below it exactly as it was. These say how much
// it had to take off since the process started: the count of samples and the
// worst magnitude it saw, as a multiple of full scale. A build whose count is
// zero over a whole session never needed the node at all; a worst much above
// one means something upstream is summing louder than the original does.
uint64_t host_audio_clipped_samples(void);
float host_audio_worst_overshoot(void);
// Three rates a test compares. All three must agree: where they do not, a
// sample-rate conversion sits somewhere between the mixer and the output, and
// one after the clipper rings past the corner the clipper just made. The
// clipper rate is 0 when there is no clipper node at all.
double host_audio_clipper_output_rate(void);
double host_audio_mixer_output_rate(void);
double host_audio_output_bus_rate(void);

// ---------------------------------------------------------------------------
// The engine itself, for the other thing that makes sound.
//
// The music is MIDI through a SoundFont, rendered by a synth the mixer sums
// in after the voices and before the clipper: one output, so the music and
// the effects never compete for the device. host_audio_engine returns an
// opaque non-null token when there is audio at all, and null when there is
// none; host_audio_engine_run starts the output device if it is not running.
void *host_audio_engine(void);
void host_audio_engine_run(void);
// The music source. `render` ADDS `frames` of stereo audio at the mixer's
// rate into `left`/`right`; it runs on the render thread with the mixer's
// render lock held, so it must not call back into the mixer. Null clears it.
typedef void (*HostAudioMusicRender)(void *context, float *left, float *right, uint32_t frames);
void host_audio_set_music_source(HostAudioMusicRender render, void *context);
// The rate the mixer renders at, which is what a music source must produce.
double host_audio_render_rate(void);

// Offline rendering, which is the only way a test can hear anything. Manual
// rendering mode runs the same graph into a buffer instead of into a device,
// so a test measures what would have been played without opening the hardware
// or making a sound in the room. Begin before anything is attached, render in
// frame counts, end to give the engine back.
// Writes the mixer's own output to a WAV for the whole run: a tap on the main
// mixer when there is a device, the offline render itself when there is not.
// `path` may be null to measure without writing. See audio_capture.h for what
// is measured and why. Non-zero if the capture is on.
int host_audio_capture_begin(const char *path);
void host_audio_capture_end(void);
// Pause or resume the output device without stopping the engine (app lifecycle).
void host_audio_pause(bool paused);
// NFSMW 3.0.4: output silence (the mix and the device keep running, so streams keep time); the focus events set it
// when the core mod's mute_in_background is on.
void host_audio_set_background_mute(bool muted);

// Channels with something playing on them, which is what separates a silence
// that is correct from one that is a fault.
uint32_t host_audio_playing_channels(void);

// How long each channel spent telling its caller it had a queue, against how
// much audio it was ever handed. A channel that claims depth for far longer
// than it holds is starving its caller while every number says it is fine,
// which is what a completion-driven count did before the clock replaced it.
void host_audio_queue_report(void *file);

int host_audio_offline_begin(double sample_rate, uint32_t max_frames);
uint32_t host_audio_offline_render(uint32_t frames, float *peak);
// Renders the production mix to caller-owned six planar buffers. Each must
// hold `frames` floats. For channel-isolation/streaming tests, without a device.
uint32_t host_audio_offline_render_planes(uint32_t frames, float *const *out);
// Time the host is not going to render, counted towards the play cursors
// anyway. Without it a host that falls behind stops every cursor with it.
void host_audio_offline_skip(uint32_t frames);
void host_audio_offline_end(void);
// Closes the output device now. A host calls it before it tears down the
// platform audio it runs on (SDL_Quit): left to the process's exit handlers,
// the device's stream is destroyed after SDL is gone, through a freed mutex.
void host_audio_shutdown(void);
// Test86: reopen the output (stop and start the sink, Dolby link refresh included), e.g. after the display -
// and with it the HDMI link - changed mode. Waits for a playing movie like a transport change.
void host_audio_restart_output(const char *why);

#ifdef __cplusplus
}
#endif
