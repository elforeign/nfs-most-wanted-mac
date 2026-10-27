/* dsound_host.h - host-side DirectSound 8 for the NFS:MW native port.
 *
 * The boot runtime owns the COM plumbing (vtables, AddRef/Release counts, guest <-> host pointers) and calls these
 * functions from its import and vtable thunks. This library owns device and buffer state, the cursors, and the mixer
 * that sums every playing buffer into interleaved 6-channel float (FL FR FC LFE BL BR) and writes it to
 * media::AudioOutput (PCM 5.1 / stereo fold-down / Dolby Digital).
 *
 * Buffer memory is a plain byte pointer supplied by the caller (it lives in guest memory). Lock returns byte offsets
 * into that memory, not pointers, so the runtime turns them into guest addresses itself. The game writes the memory
 * directly from its own threads, exactly as with a software DirectSound buffer: it must stay outside the region
 * between the play and write cursors, and the mixer only reads at or after the write cursor.
 *
 * All structs passed as `void *` (WAVEFORMATEX, DSCAPS, DSBCAPS) are the raw little-endian Win32 layouts, so the
 * runtime can pass MEM(guest_ptr) straight through.
 *
 * Thread safety: every function may be called from any thread. Callbacks (notify, tap) run on the mixer thread
 * (or on the caller's thread in manual mode) without any library lock held.
 */
#ifndef DSOUND_HOST_H
#define DSOUND_HOST_H
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef int32_t DsResult; /* an HRESULT */

#define DS_OK                    ((DsResult)0x00000000)
#define DS_NO_VIRTUALIZATION     ((DsResult)0x0878000A)
#define DSERR_ALLOCATED          ((DsResult)0x8878000A)
#define DSERR_CONTROLUNAVAIL     ((DsResult)0x8878001E)
#define DSERR_INVALIDPARAM       ((DsResult)0x80070057)
#define DSERR_INVALIDCALL        ((DsResult)0x88780032)
#define DSERR_GENERIC            ((DsResult)0x80004005)
#define DSERR_PRIOLEVELNEEDED    ((DsResult)0x88780046)
#define DSERR_OUTOFMEMORY        ((DsResult)0x8007000E)
#define DSERR_BADFORMAT          ((DsResult)0x88780064)
#define DSERR_UNSUPPORTED        ((DsResult)0x80004001)
#define DSERR_NODRIVER           ((DsResult)0x88780078)
#define DSERR_ALREADYINITIALIZED ((DsResult)0x88780082)
#define DSERR_NOAGGREGATION      ((DsResult)0x80040110)
#define DSERR_BUFFERLOST         ((DsResult)0x88780096)
#define DSERR_UNINITIALIZED      ((DsResult)0x887800AA)
#define DSERR_NOINTERFACE        ((DsResult)0x80004002)

/* SetCooperativeLevel */
#define DSSCL_NORMAL       1
#define DSSCL_PRIORITY     2
#define DSSCL_EXCLUSIVE    3
#define DSSCL_WRITEPRIMARY 4

/* DSBUFFERDESC.dwFlags / DSBCAPS.dwFlags */
#define DSBCAPS_PRIMARYBUFFER       0x00000001
#define DSBCAPS_STATIC              0x00000002
#define DSBCAPS_LOCHARDWARE         0x00000004
#define DSBCAPS_LOCSOFTWARE         0x00000008
#define DSBCAPS_CTRL3D              0x00000010
#define DSBCAPS_CTRLFREQUENCY       0x00000020
#define DSBCAPS_CTRLPAN             0x00000040
#define DSBCAPS_CTRLVOLUME          0x00000080
#define DSBCAPS_CTRLPOSITIONNOTIFY  0x00000100
#define DSBCAPS_CTRLFX              0x00000200
#define DSBCAPS_STICKYFOCUS         0x00004000
#define DSBCAPS_GLOBALFOCUS         0x00008000
#define DSBCAPS_GETCURRENTPOSITION2 0x00010000
#define DSBCAPS_LOCDEFER            0x00040000

#define DSBPLAY_LOOPING          0x00000001
#define DSBLOCK_FROMWRITECURSOR  0x00000001
#define DSBLOCK_ENTIREBUFFER     0x00000002

#define DSBSTATUS_PLAYING        0x00000001
#define DSBSTATUS_BUFFERLOST     0x00000002
#define DSBSTATUS_LOOPING        0x00000004
#define DSBSTATUS_LOCHARDWARE    0x00000008
#define DSBSTATUS_LOCSOFTWARE    0x00000010
#define DSBSTATUS_TERMINATED     0x00000020

#define DSBVOLUME_MIN       (-10000)
#define DSBVOLUME_MAX       0
#define DSBPAN_LEFT         (-10000)
#define DSBPAN_RIGHT        10000
#define DSBFREQUENCY_ORIGINAL 0
#define DSBFREQUENCY_MIN    100
#define DSBFREQUENCY_MAX    200000
#define DSBPN_OFFSETSTOP    0xFFFFFFFFu

/* GetSpeakerConfig values (low byte; geometry in the next byte for stereo) */
#define DSSPEAKER_HEADPHONE 1
#define DSSPEAKER_MONO      2
#define DSSPEAKER_QUAD      3
#define DSSPEAKER_STEREO    4
#define DSSPEAKER_SURROUND  5
#define DSSPEAKER_5POINT1   6 /* 5.1 with back speakers: FL FR FC LFE BL BR */
#define DSSPEAKER_7POINT1   7
#define DSSPEAKER_5POINT1_SURROUND 9

typedef struct DsDevice DsDevice;
typedef struct DsBuffer DsBuffer;

typedef enum DsSink {
    DS_SINK_DEVICE = 0, /* media::AudioOutput on the default output device, paced by its queue */
    DS_SINK_NULL = 1,   /* no device: a mixer thread paced by the wall clock (headless real time) */
    DS_SINK_MANUAL = 2  /* no thread: the caller pulls output with ds_device_render (deterministic tests) */
} DsSink;

/* DS_OUT_DOLBY_DIGITAL_PLUS: E-AC-3 passthrough ('cec3', 192 kHz link); DS_OUT_TRUEHD: lossless Dolby TrueHD in MAT
 * ('mtct', 768 kHz link). Neither is picked automatically, only by choice. */
typedef enum DsOutputMode {
    DS_OUT_PCM51 = 0, DS_OUT_STEREO = 1, DS_OUT_DOLBY_DIGITAL = 2, DS_OUT_DOLBY_DIGITAL_PLUS = 3, DS_OUT_TRUEHD = 4
} DsOutputMode;

/* Called when the play cursor reaches a SetNotificationPositions offset, or with DSBPN_OFFSETSTOP's event when a
 * buffer stops (Stop, or a one-shot reaching its end). `event` is the value the runtime registered (a handle). */
typedef void (*DsNotifyFn)(void *user, uint32_t event);
/* Called with every block of mixed 6-ch float (after clamping, before the device's mode conversion). */
typedef void (*DsTapFn)(void *user, const float *pcm51, size_t frames);

typedef struct DsConfig {
    DsSink sink;
    DsOutputMode mode;
    int mix_rate;          /* output rate in Hz; 0 = auto: 48000, then the primary buffer's SetFormat rate (Snd
                              sets 44100, so its 6-ch stream passes through unresampled). Dolby Digital (Plus),
                              TrueHD: always 48000. */
    int latency_frames;    /* target frames queued ahead of the device; 0 = default (1024, 2048+1536 for DD) */
    int block_frames;      /* mixer block size; 0 = default (256) */
    int mute_device;       /* 1: the device gets silence, the tap still gets the real mix (muted tests) */
    uint32_t speaker_config; /* GetSpeakerConfig result; 0 = DSSPEAKER_5POINT1 */
    DsNotifyFn notify;
    void *notify_user;
    DsTapFn tap;
    void *tap_user;
    int auto_mode;         /* 1: the mode suits the device (media::preferred_output_mode), picked again whenever the
                              output is reopened on another device (NFS_AUDIO unset or auto) */
} DsConfig;

/* Defaults: DEVICE sink, PCM 5.1, auto rate. Environment overrides: NFS_AUDIO = pcm51 | stereo | dd | ddp | truehd | null | off
 * (null/off = DS_SINK_NULL), NFS_AUDIO_RATE = Hz (fixed mix rate), NFS_AUDIO_LATENCY = frames. */
void ds_config_default(DsConfig *cfg);
/* The output picked in the app's Audio menu: "auto", "dd", "pcm51", "stereo", "ddp" or "truehd" (NULL = none). Used by ds_config_default
 * when NFS_AUDIO isn't set, and applied at once to running devices: the output reopens in that mode. */
void ds_set_output_choice(const char *choice);
/* Live, process-wide game-mix limiter. Default On; NFS_AUDIO_LIMITER=on|off at startup.
 * Off bypasses without delay or arithmetic. Enabling starts a fresh <=2 ms delay line. */
void ds_set_peak_limiter(int enabled);
/* MoviePlayer lifecycle: bypass limiting/delay while movie PCM shares the game's device. */
void ds_set_movie_playing(int playing);
/* Snd's float-to-int16 boundary: linked limiting BEFORE the guest saturates its six output planes.
 * Once used on a device, mix_block only applies a zero-latency linked bound for resampler overshoot. */
int ds_peak_limiter_enabled(void);
void ds_device_limit_game_mix(DsDevice *dev, float *pcm6, size_t frames, int source_rate);
/* Launch-only one-shot, checked by guest cursor polls with the current pause/loading state. Safe on any thread.
 * Requests the shared Audio-menu reopen after 1.5 s; expires after 5 s. NFS_AUDIO_LAUNCH_REOPEN=0 disables it. */
void ds_device_launch_reopen_tick(DsDevice *dev, int paused);
/* Output changes (device created/released, output opened, kept across a device re-creation, closed, device changes,
 * underruns) are logged to stderr as "dsound: ..." lines and also passed to fn (NULL: none), from any thread. */
void ds_set_log(void (*fn)(const char *line));

/* ------------------------------------------------------------------ IDirectSound8 */
/* DirectSoundCreate / DirectSoundCreate8. cfg may be NULL (ds_config_default). The device sink never fails for want
 * of an output: with no usable output device the mixer runs on the wall clock (as the null sink) and keeps trying,
 * like Windows, where the device doesn't go away under the game. It also follows the default output device: a device
 * that stops taking audio or a change of default reopens the output there, without the buffers noticing. */
DsResult ds_device_create(const DsConfig *cfg, DsDevice **out);
/* Final release: stops the mixer and every buffer. Buffer handles stay valid until each is released (their
 * calls return DSERR_BUFFERLOST); the runtime may release them in any order. */
void ds_device_release(DsDevice *dev);
DsResult ds_device_set_cooperative_level(DsDevice *dev, uint32_t level);
/* dscaps: a DSCAPS (96 bytes) with dwSize set. */
DsResult ds_device_get_caps(DsDevice *dev, void *dscaps);
DsResult ds_device_get_speaker_config(DsDevice *dev, uint32_t *config);
DsResult ds_device_set_speaker_config(DsDevice *dev, uint32_t config);
/* flags/bytes/wfx from DSBUFFERDESC (wfx = MEM(lpwfxFormat), NULL for the primary buffer). mem: `bytes` bytes of
 * caller memory for a secondary buffer (NULL for the primary). The usable size is `bytes` rounded down to the
 * block alignment. On failure the caller frees mem. */
DsResult ds_device_create_buffer(DsDevice *dev, uint32_t flags, uint32_t bytes, const void *wfx, void *mem,
                                 DsBuffer **out);
/* IDirectSound::DuplicateSoundBuffer: shares src's memory and format, own position/volume/pan/frequency, stopped. */
DsResult ds_device_duplicate_buffer(DsDevice *dev, DsBuffer *src, DsBuffer **out);

/* Pull `frames` of mixed 6-ch float (DS_SINK_MANUAL only; advances the clock by exactly that many frames). */
DsResult ds_device_render(DsDevice *dev, float *pcm51, size_t frames);

typedef struct DsStats {
    uint64_t frames_written;  /* mixed output frames handed to the sink */
    uint64_t frames_played;   /* estimate of frames the device has played */
    uint64_t underruns;       /* times the mixer found the sink queue empty (device/null sinks) */
    uint32_t buffers;         /* live secondary buffers */
    uint32_t playing;         /* of which playing */
    float peak[6];            /* per-channel peak |sample| since the last ds_device_get_stats */
    double rms[6];            /* per-channel RMS since the last ds_device_get_stats */
    uint64_t clipped[6];      /* per-channel samples at or over full scale (>= 32767/32768) before the clamp, same window */
    uint64_t window;          /* mixed frames in that window */
    uint64_t limited;         /* samples with look-ahead gain < 1, all 6 channels; Snd counts at its source clock,
                                other streams at the mix clock; excludes the post-resampler safety bound */
    float dip[6];             /* per-channel lowest RMS of a 50 ms stretch (2400 frames) in that window, -1 if none ended */
} DsStats;
DsResult ds_device_get_stats(DsDevice *dev, DsStats *stats);
/* What plays now, without touching the stats window (the runtime's performance overlay): the output mode, whether the
 * output is live (1), the null sink (0) or waiting for a device (-1, mixing on the wall clock), and the underruns. */
DsResult ds_device_output_info(DsDevice *dev, DsOutputMode *mode, int *live, uint64_t *underruns);

/* ------------------------------------------------------------------ IDirectSoundBuffer8 */
void ds_buffer_release(DsBuffer *buf); /* final release; after it returns the mixer no longer reads mem */
/* dsbcaps: a DSBCAPS (20 bytes) with dwSize set. */
DsResult ds_buffer_get_caps(DsBuffer *buf, void *dsbcaps);
/* Writes the buffer's WAVEFORMATEX(-TENSIBLE) to wfx_out (may be NULL); *written = bytes needed/written. */
DsResult ds_buffer_get_format(DsBuffer *buf, void *wfx_out, uint32_t capacity, uint32_t *written);
/* Primary buffer only; needs DSSCL_PRIORITY or higher. */
DsResult ds_buffer_set_format(DsBuffer *buf, const void *wfx);
/* Byte offsets of the current play and write cursors (either pointer may be NULL). */
DsResult ds_buffer_get_current_position(DsBuffer *buf, uint32_t *play, uint32_t *write);
DsResult ds_buffer_set_current_position(DsBuffer *buf, uint32_t offset);
/* Region 1 = [off1, off1+len1), region 2 = [0, len2) when the lock wraps (off2 is always 0 then, len2 0 if not).
 * off2/len2 may be NULL: a wrapping lock is then truncated at the end of the buffer (as on Windows). */
DsResult ds_buffer_lock(DsBuffer *buf, uint32_t offset, uint32_t bytes, uint32_t flags, uint32_t *off1,
                        uint32_t *len1, uint32_t *off2, uint32_t *len2);
DsResult ds_buffer_unlock(DsBuffer *buf, uint32_t off1, uint32_t len1, uint32_t off2, uint32_t len2);
DsResult ds_buffer_play(DsBuffer *buf, uint32_t flags);
DsResult ds_buffer_stop(DsBuffer *buf);
DsResult ds_buffer_get_status(DsBuffer *buf, uint32_t *status);
DsResult ds_buffer_set_volume(DsBuffer *buf, int32_t volume); /* hundredths of dB, -10000..0 */
DsResult ds_buffer_get_volume(DsBuffer *buf, int32_t *volume);
DsResult ds_buffer_set_pan(DsBuffer *buf, int32_t pan); /* -10000 (left) .. 10000 (right) */
DsResult ds_buffer_get_pan(DsBuffer *buf, int32_t *pan);
DsResult ds_buffer_set_frequency(DsBuffer *buf, uint32_t hz); /* 0 = the format's rate */
DsResult ds_buffer_get_frequency(DsBuffer *buf, uint32_t *hz);
DsResult ds_buffer_restore(DsBuffer *buf);

/* IDirectSoundNotify::SetNotificationPositions. offsets[i] is a byte offset or DSBPN_OFFSETSTOP; events[i] is
 * passed back to cfg.notify. Needs DSBCAPS_CTRLPOSITIONNOTIFY and a stopped buffer. count 0 clears them. */
DsResult ds_buffer_set_notification_positions(DsBuffer *buf, uint32_t count, const uint32_t *offsets,
                                              const uint32_t *events);

#ifdef __cplusplus
}
#endif
#endif
