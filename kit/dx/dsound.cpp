// dsound.cpp - DirectSound: the device, secondary buffers, the 3D buffer and
// listener interfaces, and notification positions.
//
// A secondary buffer owns guest memory the game locks and fills with PCM. Play
// forwards that memory, the format and the current volume and pan to
// host_audio_play; Task 7 mixes it. Positional parameters are stored and
// reported back faithfully but are not applied to the sample here, which is
// what the brief specifies for this task.
//
// Static cross-referencing of the EXE shows IID_IDirectSound3DBuffer,
// IID_IDirectSound3DListener and IID_IDirectSoundNotify are all referenced
// from code, so all three are implemented rather than refused.
#include "com.h"
#include "dx.h"
#include "host_api.h"
#include "../runtime/memory.h"
#include "../runtime/win32.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <iterator>
#include "../platform/os.h"

#define IID_BYTES(a, b, c, d0, d1, d2, d3, d4, d5, d6, d7)                                         \
    {(uint8_t)((a) & 0xff),                                                                        \
     (uint8_t)(((a) >> 8) & 0xff),                                                                 \
     (uint8_t)(((a) >> 16) & 0xff),                                                                \
     (uint8_t)(((a) >> 24) & 0xff),                                                                \
     (uint8_t)((b) & 0xff),                                                                        \
     (uint8_t)(((b) >> 8) & 0xff),                                                                 \
     (uint8_t)((c) & 0xff),                                                                        \
     (uint8_t)(((c) >> 8) & 0xff),                                                                 \
     d0,                                                                                           \
     d1,                                                                                           \
     d2,                                                                                           \
     d3,                                                                                           \
     d4,                                                                                           \
     d5,                                                                                           \
     d6,                                                                                           \
     d7}

static const uint8_t IID_IDirectSound_[16] =
    IID_BYTES(0x279AFA83, 0x4981, 0x11CE, 0xA5, 0x21, 0x00, 0x20, 0xAF, 0x0B, 0xE5, 0x60);
static const uint8_t IID_IDirectSoundBuffer_[16] =
    IID_BYTES(0x279AFA85, 0x4981, 0x11CE, 0xA5, 0x21, 0x00, 0x20, 0xAF, 0x0B, 0xE5, 0x60);
static const uint8_t IID_IDirectSound3DListener_[16] =
    IID_BYTES(0x279AFA84, 0x4981, 0x11CE, 0xA5, 0x21, 0x00, 0x20, 0xAF, 0x0B, 0xE5, 0x60);
static const uint8_t IID_IDirectSound3DBuffer_[16] =
    IID_BYTES(0x279AFA86, 0x4981, 0x11CE, 0xA5, 0x21, 0x00, 0x20, 0xAF, 0x0B, 0xE5, 0x60);
static const uint8_t IID_IDirectSoundNotify_[16] =
    IID_BYTES(0xB0210783, 0x89CD, 0x11D0, 0xAF, 0x08, 0x00, 0xA0, 0xC9, 0x25, 0xCD, 0x16);

namespace {

// DuplicateSoundBuffer shares the original's sample data, so the memory
// outlives whichever buffer is released first. A reference count keyed by the
// guest address is the smallest thing that gets that right; releasing the
// original while a duplicate still plays must not free the samples.
std::vector<std::pair<uint32_t, uint32_t>> &pcm_refs() {
    static auto *v = new std::vector<std::pair<uint32_t, uint32_t>>();
    return *v;
}
void pcm_retain(uint32_t addr) {
    if (!addr)
        return;
    for (auto &e : pcm_refs())
        if (e.first == addr) {
            ++e.second;
            return;
        }
    pcm_refs().push_back({addr, 1});
}
void pcm_release(uint32_t addr) {
    if (!addr)
        return;
    for (size_t i = 0; i < pcm_refs().size(); ++i) {
        if (pcm_refs()[i].first != addr)
            continue;
        if (--pcm_refs()[i].second == 0) {
            heap_free_guarded(addr);
            pcm_refs().erase(pcm_refs().begin() + (ptrdiff_t)i);
        }
        return;
    }
    // Not tracked: nothing allocated it here, so nothing frees it.
}

ComObj *this_dsound(X86 *c) {
    ComObj *o = com_this_arg(c);
    return (o && o->kind == K_DSOUND) ? o : nullptr;
}
ComObj *this_buffer(X86 *c) {
    ComObj *o = com_this_arg(c);
    return (o && o->kind == K_DSBUFFER) ? o : nullptr;
}

// ---------------------------------------------------------------------------
// Notification positions.
//
// The game streams music into a looping secondary buffer and does it the way
// DirectSound intends: it asks IDirectSoundNotify for an event at offset 0 and
// one at the half-way point, plays the buffer looping, and parks a worker
// thread in WaitForMultipleObjects on those two events. Every time one fires
// it refills the half the play cursor has just left. (0056ec50 creates the
// thread and the events, 0056efe0 registers the two positions and calls Play,
// 0056eb70 is the wait loop, 0056f090/00576390 are the refill and the
// Lock/copy/Unlock it ends in.)
//
// So a shim that records the positions and never signals them does not merely
// lose a notification: the worker never wakes, the second half of the buffer
// is never written, and the host loops the one half that was filled before
// Play for the rest of the run. That is what the first live run heard.
//
// Nothing here runs on a host thread. The positions are serviced whenever a
// guest thread enters this module or QSWaveMixPump, both of which happen far
// more often than the 174 ms a half-buffer lasts, and the signal itself goes
// through guest_event_signal_from_host, which queues it for the scheduler.
// ---------------------------------------------------------------------------
const uint32_t DSBPN_OFFSETSTOP = 0xffffffffu;
// The gap on each side of a sample ring (heap_alloc_guarded): wider than the
// largest block a mixer writes in one go (a 10 ms tick of 7.1 at 48 kHz is 7.5 KB).
const uint32_t DSOUND_RING_GUARD = 0x00004000u;

struct NotifyPos {
    uint32_t offset = 0;
    uint32_t event = 0;
};

struct NotifyRecord {
    uint32_t obj_id = 0;
    std::vector<NotifyPos> pos;
    uint32_t last = 0;     // where the play cursor was last seen
    bool tracking = false; // false until the first service after a Play
};

std::vector<NotifyRecord> &notifies() {
    static auto *v = new std::vector<NotifyRecord>();
    return *v;
}

NotifyRecord *notify_for(uint32_t id, bool create = false) {
    for (auto &n : notifies())
        if (n.obj_id == id)
            return &n;
    if (!create)
        return nullptr;
    notifies().push_back(NotifyRecord{});
    notifies().back().obj_id = id;
    return &notifies().back();
}

// ---------------------------------------------------------------------------
// Streaming into a looping buffer.
//
// This is how the game actually plays its music and speech, and the Wine trace
// of the original is unambiguous about it. Buffer 0120FDF8: 32768 bytes,
// 22050 Hz stereo 16-bit, DSBCAPS_GETCURRENTPOSITION2 | CTRLVOLUME | CTRLPAN |
// CTRLFREQUENCY, `Play(0, 0, DSBPLAY_LOOPING)` while it is still empty. Then a
// dedicated thread polls GetCurrentPosition every ten milliseconds and, every
// forty, locks three or four kilobytes at its own running write offset - 0,
// 3584, 7056, 10640, ... wrapping at 32768 - and unlocks it. It stays about a
// lap ahead of the play cursor. No notification positions are ever registered
// on this path; the poll is the whole clock.
//
// So the first lap really is silence, in the original as much as here, and the
// zero-peak first buffer of the first live run was the ring as Play found it.
// What was wrong was afterwards: re-submitting the whole ring on each Unlock
// restarts the voice twenty-five times a second, which is what "garbled"
// sounds like.
//
// The fix is to stop pretending the ring is a loop once the guest starts
// writing into it. Ring order from the play cursor *is* play order, so the
// rest of the current lap is re-issued as a one-shot and every region the
// guest unlocks afterwards is appended behind it with host_audio_queue. The
// join is sample-accurate and the voice never restarts.
//
// A looping buffer nobody writes into is left alone: it is a loop, and the
// host keeps looping it.
// ---------------------------------------------------------------------------
struct StreamRecord {
    uint32_t obj_id = 0;
    bool active = false;   // converted, and being fed by Unlock
    bool refused = false;  // this host cannot stream; stay on re-submission
    uint32_t ring_end = 0; // ring offset just past the last byte fed
};

std::vector<StreamRecord> &streams() {
    static auto *v = new std::vector<StreamRecord>();
    return *v;
}

StreamRecord *stream_for(uint32_t id, bool create = false) {
    for (auto &r : streams())
        if (r.obj_id == id)
            return &r;
    if (!create)
        return nullptr;
    streams().push_back(StreamRecord{});
    streams().back().obj_id = id;
    return &streams().back();
}

void stream_drop(uint32_t id) {
    for (size_t i = 0; i < streams().size(); ++i) {
        if (streams()[i].obj_id != id)
            continue;
        streams().erase(streams().begin() + (ptrdiff_t)i);
        return;
    }
}

// A converted stream ends whenever the sound it belongs to does. The record is
// kept - the buffer may be played again - but it starts from nothing.
void stream_reset(uint32_t id) {
    if (StreamRecord *r = stream_for(id)) {
        r->active = false;
        r->ring_end = 0;
    }
}

// Where a converted stream's play cursor is.
//
// The host owns this now. host_audio_stream resumes the ring at the offset the
// loop had reached and reports it, and host_audio_played_bytes counts on from
// there at the sample rate, never going backwards, across a re-schedule,
// across a refill and across an underrun. Reducing that modulo the ring is the
// whole of it.
//
// It has to be a count of what has been played and not of what is in flight.
// The guest keeps about a lap of audio in flight on purpose, so a cursor
// derived from the outstanding bytes sits a fixed distance ahead of the
// guest's own write offset for ever - and that distance is exactly what the
// video player's refill gate measures (0057df10 returns the bytes from its
// write offset forward to the cursor, 0057dc80 compares that to the next
// chunk's length). Freeze it and the guest stops writing, which stops the
// stream, which freezes it further: the movie waits on a number that will not
// move, with nothing to show that anything is wrong.
uint32_t stream_position(const ComObj *b) {
    return b->buf_bytes ? host_audio_played_bytes(b->channel) % b->buf_bytes : 0;
}

// Where the buffer's play cursor is now, in bytes from the start of the
// buffer. For a buffer that is not a converted stream the host models it and
// it moves whether or not anything asks; a host that does not track playback
// reports 0, which reads as "still at the start" and simply never crosses a
// position.
uint32_t live_position(const ComObj *b) {
    if (!b->playing || b->channel < 0 || !b->buf_bytes)
        return b->play_cursor;
    for (const auto &e : streams())
        if (e.obj_id == b->id && e.active)
            return stream_position(b);
    return host_audio_position(b->channel) % b->buf_bytes;
}

// Did the cursor pass `o` on its way from `last` to `pos`? The interval is
// half-open at the near end and closed at the far end, because DirectSound
// signals when the cursor *reaches* an offset. A looping buffer wraps, and
// offset 0 is reached only by wrapping, which is why the wrapped case is
// written out rather than folded into the other.
bool cursor_crossed(uint32_t last, uint32_t pos, uint32_t o) {
    if (pos == last)
        return false;
    if (pos > last)
        return o > last && o <= pos;
    return o > last || o <= pos;
}

void signal_stop_positions(const ComObj *b) {
    NotifyRecord *n = notify_for(b->id);
    if (!n)
        return;
    for (const NotifyPos &p : n->pos)
        if (p.offset == DSBPN_OFFSETSTOP && p.event)
            guest_event_signal_from_host(p.event);
    n->tracking = false;
}

// Walks every buffer that registered positions and signals the ones the play
// cursor has reached since the last look. Cheap: the list holds only buffers
// that asked, which for this game is one per streaming voice.
void service_notifications() {
    for (NotifyRecord &n : notifies()) {
        if (n.pos.empty())
            continue;
        ComObj *b = com_get(n.obj_id);
        if (!b || b->kind != K_DSBUFFER)
            continue;

        // A one-shot that has run out stops at the end of its data; that is a
        // stop, and DSBPN_OFFSETSTOP is what reports it.
        if (b->playing && !b->looping && b->channel >= 0 && !host_audio_is_playing(b->channel)) {
            b->playing = false;
            b->play_cursor = b->buf_bytes;
            signal_stop_positions(b);
            continue;
        }
        if (!b->playing || b->channel < 0 || !b->buf_bytes)
            continue;

        uint32_t pos = live_position(b);
        if (!n.tracking) {
            n.last = pos;
            n.tracking = true;
            continue;
        }
        if (pos == n.last)
            continue;
        for (const NotifyPos &p : n.pos) {
            if (p.offset == DSBPN_OFFSETSTOP || !p.event)
                continue;
            if (p.offset >= b->buf_bytes)
                continue;
            if (cursor_crossed(n.last, pos, p.offset))
                guest_event_signal_from_host(p.event);
        }
        n.last = pos;
        b->play_cursor = pos;
    }
}

// The listener is a view on the DirectSound object, so its state lives there.
float g_listener[12] = {0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0};
float g_distance_factor = 1.0f, g_doppler_factor = 1.0f, g_rolloff_factor = 1.0f;

void read_wave_format(ComObj *b, uint32_t wfx) {
    if (!wfx || !gm_valid(wfx, 16))
        return;
    uint16_t tag = rd16(wfx + WFX_OFF_wFormatTag);
    // WAVEFORMATEXTENSIBLE (0xFFFE) names its real format in a SubFormat
    // GUID whose first four bytes are the old tag: 1 for PCM.
    if (tag == 0xfffe && gm_valid(wfx, 40) && rd16(wfx + 16) >= 22 &&
        rd32(wfx + 24) == WAVE_FORMAT_PCM && rd16(wfx + 28) == 0 && rd16(wfx + 30) == 0x10)
        tag = WAVE_FORMAT_PCM;
    if (tag != WAVE_FORMAT_PCM) {
        log_once("dsound.fmt",
                 "dsound: wave format tag %u is not PCM; the buffer is treated "
                 "as PCM and will sound wrong if it is compressed",
                 tag);
    }
    b->nchannels = rd16(wfx + WFX_OFF_nChannels);
    b->rate = rd32(wfx + WFX_OFF_nSamplesPerSec);
    b->bits = rd16(wfx + WFX_OFF_wBitsPerSample);
    b->block_align = rd16(wfx + WFX_OFF_nBlockAlign);
    if (!b->nchannels)
        b->nchannels = 1;
    if (!b->rate)
        b->rate = 22050;
    if (!b->bits)
        b->bits = 8;
    if (!b->block_align)
        b->block_align = b->nchannels * (b->bits / 8);
}

void write_wave_format(const ComObj *b, uint32_t wfx) {
    wr16(wfx + WFX_OFF_wFormatTag, WAVE_FORMAT_PCM);
    wr16(wfx + WFX_OFF_nChannels, (uint16_t)b->nchannels);
    wr32(wfx + WFX_OFF_nSamplesPerSec, b->rate);
    wr32(wfx + WFX_OFF_nAvgBytesPerSec, b->rate * b->block_align);
    wr16(wfx + WFX_OFF_nBlockAlign, (uint16_t)b->block_align);
    wr16(wfx + WFX_OFF_wBitsPerSample, (uint16_t)b->bits);
    wr16(wfx + WFX_OFF_cbSize, 0);
}

// ---------------------------------------------------------------------------
// RECOMP_AUDIO_TRACE=N prints the first N audio events - every Lock, Unlock,
// GetCurrentPosition and submission on a playing buffer, with the offsets, the
// lengths and the peak of what was written. A stream that goes quiet is
// always one of a small number of things, and they are told apart by which of
// these lines stops appearing: the guest not asking, the guest asking and
// writing nothing, or the write not reaching the host. Nothing here is on by
// default and nothing here costs anything when it is off.
// ---------------------------------------------------------------------------
// RECOMP_AUDIO_DUMP=<path> writes every run the guest puts into a streaming ring
// to a raw file, in the order it wrote them. Concatenated that way the file is
// the decoded stream itself, which is what makes it comparable against a
// reference decode of the same source: a chunk dropped, repeated or joined at
// the wrong offset shows up as the alignment drifting, and nothing else does.
FILE *audio_dump_file() {
    static FILE *f = nullptr;
    static bool tried = false;
    if (!tried) {
        tried = true;
        if (const char *path = recomp_env("AUDIO_DUMP"))
            f = fopen(path, "wb");
    }
    return f;
}

int audio_trace_budget() {
    static int budget = -1;
    if (budget < 0) {
        const char *v = recomp_env("AUDIO_TRACE");
        budget = v ? (int)strtol(v, nullptr, 0) : 0;
    }
    return budget;
}

bool audio_trace_take() {
    static int left = -1;
    if (left < 0)
        left = audio_trace_budget();
    if (left <= 0)
        return false;
    --left;
    return true;
}

#define ATRACE(...)                                                                                \
    do {                                                                                           \
        if (audio_trace_budget() && audio_trace_take())                                            \
            LOGW(__VA_ARGS__);                                                                     \
    } while (0)

// The loudest sample in a block of guest PCM, as a fraction of full scale.
// It is the one number that separates "the guest handed us silence" from "the
// pipeline swallowed it", and those two have entirely different causes. The
// host measures the same thing on its own side; the point of measuring here as
// well is that the two numbers together say which side lost the sound.
float pcm_peak(uint32_t addr, uint32_t bytes, uint32_t bits) {
    if (!addr || !bytes || !gm_fits(addr, (uint64_t)bytes))
        return 0.0f;
    const uint8_t *p = (const uint8_t *)gm_ptr(addr);
    float peak = 0.0f;
    if (bits == 8) {
        for (uint32_t i = 0; i < bytes; ++i) {
            float v = ((float)p[i] - 128.0f) / 128.0f;
            if (v < 0)
                v = -v;
            if (v > peak)
                peak = v;
        }
    } else {
        for (uint32_t i = 0; i + 1 < bytes; i += 2) {
            int16_t s = (int16_t)(uint16_t)(p[i] | (p[i + 1] << 8));
            float v = (float)s / 32768.0f;
            if (v < 0)
                v = -v;
            if (v > peak)
                peak = v;
        }
    }
    return peak;
}

// Buffers that have already announced themselves. One line per buffer, on its
// first Play, so a live run says what it handed the host without a per-refill
// flood.
std::vector<uint32_t> &announced() {
    static auto *v = new std::vector<uint32_t>();
    return *v;
}

void announce_once(const ComObj *b, uint32_t from) {
    for (uint32_t id : announced())
        if (id == b->id)
            return;
    announced().push_back(b->id);
    LOGW("dsound: buffer %u first play on channel %d: %u Hz, %u ch, %u bit, "
         "%u bytes from %u, %s, peak %.3f",
         b->id, b->channel, b->frequency ? b->frequency : b->rate, b->nchannels, b->bits,
         b->buf_bytes, from, b->looping ? "looping" : "one-shot",
         (double)pcm_peak(b->buf_pixels, b->buf_bytes, b->bits));
}

void start_playback_loop(ComObj *b, uint32_t from, bool loop) {
    if (b->is_primary_buffer || b->channel < 0 || !b->buf_pixels)
        return;
    HostAudioPlay p;
    memset(&p, 0, sizeof p);
    p.channel = b->channel;
    p.pcm = gm_ptr(b->buf_pixels);
    p.bytes = b->buf_bytes;
    p.sample_rate = (int32_t)(b->frequency ? b->frequency : b->rate);
    p.channels = (int32_t)b->nchannels;
    p.bits = (int32_t)b->bits;
    p.loop = loop ? 1 : 0;
    p.volume = b->volume;
    p.pan = b->pan;
    p.start_offset = from;
    announce_once(b, from);
    ATRACE("dsound: play buffer %u channel %d from %u, %u bytes, loop %d, peak %.3f", b->id,
           b->channel, from, b->buf_bytes, p.loop,
           (double)pcm_peak(b->buf_pixels, b->buf_bytes, b->bits));
    host_audio_play(&p);
}

void start_playback(ComObj *b, uint32_t from) {
    start_playback_loop(b, from, b->looping);
}

// Hands one run of the ring to the host as a continuation of what is already
// sounding. Returns false when the host refused, which is its way of saying it
// cannot continue a sound and the caller must start one instead.
bool stream_queue_run(ComObj *b, uint32_t off, uint32_t len) {
    if (!len)
        return true;
    // Written where it lies, not scheduled behind what is playing. A ring is
    // memory the cursor runs through, and the offset is known here, so the
    // host can put the bytes exactly where the guest put them instead of
    // treating each run as a buffer that follows the last one. The difference
    // is a lap boundary that costs nothing against one that costs 25 ms of
    // silence, because a full-ring write can only ever arrive just after the
    // previous run has finished playing.
    int32_t taken = host_audio_write(b->channel, gm_ptr(b->buf_pixels + off), off, len);
    // A host with no in-place path says 0, and the run is scheduled behind
    // instead, which is what this did before and still works.
    if (taken <= 0)
        taken = host_audio_queue(b->channel, gm_ptr(b->buf_pixels + off), len);
    ATRACE("dsound: queue buffer %u channel %d off %u len %u -> %d, peak %.3f", b->id, b->channel,
           off, len, taken, (double)pcm_peak(b->buf_pixels + off, len, b->bits));
    return taken > 0;
}

// The guest has just written [off, off+len) of a ring it is playing. Feed it,
// splitting at the wrap because a run that crosses the end of the ring is two
// runs in play order.
bool stream_feed(ComObj *b, StreamRecord *r, uint32_t off, uint32_t len) {
    if (!b->buf_bytes || !len)
        return true;
    if (off != r->ring_end) {
        // The traced game writes strictly in ring order. Anything else is a
        // guest doing something this model does not describe, so say so once
        // and follow it rather than silently playing the wrong bytes.
        log_once("dsound.streamjump",
                 "dsound: a streamed buffer was written at %u with %u fed; "
                 "following the guest rather than the ring",
                 off, r->ring_end);
    }
    uint32_t first = len;
    uint32_t second = 0;
    if (off + len > b->buf_bytes) {
        first = b->buf_bytes - off;
        second = len - first;
    }
    if (!stream_queue_run(b, off, first))
        return false;
    if (second && !stream_queue_run(b, 0, second))
        return false;
    r->ring_end = (off + len) % b->buf_bytes;
    return true;
}

// ===========================================================================
// IDirectSoundBuffer
// ===========================================================================
void Buffer_GetCaps(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t out = arg(c, 1);
    if (!b || !out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if (rd32(out) != DSBCAPS_SIZE || !gm_valid(out, DSBCAPS_SIZE)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    wr32(out + 4, b->buf_flags); // dwFlags
    wr32(out + 8, b->buf_bytes); // dwBufferBytes
    wr32(out + 12, 0);           // dwUnlockTransferRate
    wr32(out + 16, 0);           // dwPlayCpuOverhead
    com_ret(c, DS_OK);
}

void Buffer_GetCurrentPosition(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t play = arg(c, 1), write = arg(c, 2);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    service_notifications();
    uint32_t pos = live_position(b);
    b->play_cursor = pos;
    ATRACE("dsound: GetCurrentPosition buffer %u -> play %u (playing %d, "
           "looping %d, channel %d, %u bytes)",
           b->id, pos, (int)b->playing, (int)b->looping, b->channel, b->buf_bytes);
    if (play && gm_valid(play, 4))
        wr32(play, pos);
    // The write cursor leads the play cursor by the mixer's own look-ahead;
    // the region between them is the part DirectSound will not promise. Wine
    // reports ten milliseconds of it, which for the game's 22050 Hz stereo
    // 16-bit music buffer is the 880 bytes its trace shows on every one of
    // three and a half thousand polls.
    if (write && gm_valid(write, 4)) {
        uint32_t align = b->block_align ? b->block_align : 1;
        uint32_t lead = (b->rate ? b->rate : 22050) * align / 100u;
        lead -= lead % align;
        wr32(write, b->buf_bytes ? (pos + lead) % b->buf_bytes : 0u);
    }
    com_ret(c, DS_OK);
}

void Buffer_GetFormat(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t out = arg(c, 1), cap = arg(c, 2), written = arg(c, 3);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if (!out) {
        if (written && gm_valid(written, 4))
            wr32(written, WFX_SIZE);
        com_ret(c, DS_OK);
        return;
    }
    if (cap < WFX_SIZE || !gm_fits(out, WFX_SIZE)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    write_wave_format(b, out);
    if (written && gm_valid(written, 4))
        wr32(written, WFX_SIZE);
    com_ret(c, DS_OK);
}

void Buffer_GetVolume(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t out = arg(c, 1);
    if (!b || !out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    wr32(out, (uint32_t)b->volume);
    com_ret(c, DS_OK);
}

void Buffer_GetPan(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t out = arg(c, 1);
    if (!b || !out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    wr32(out, (uint32_t)b->pan);
    com_ret(c, DS_OK);
}

void Buffer_GetFrequency(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t out = arg(c, 1);
    if (!b || !out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    wr32(out, b->frequency ? b->frequency : b->rate);
    com_ret(c, DS_OK);
}

void Buffer_GetStatus(X86 *c) {
    heap_readonly_report(); // NFSMW issue 8: the sound thread asks this every 10 ms
    ComObj *b = this_buffer(c);
    uint32_t out = arg(c, 1);
    if (!b || !out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    // A buffer the host has finished playing is no longer playing, so ask.
    service_notifications();
    if (b->playing && !b->looping && b->channel >= 0 && !host_audio_is_playing(b->channel))
        b->playing = false;
    uint32_t st = 0;
    if (b->playing)
        st |= DSBSTATUS_PLAYING;
    if (b->playing && b->looping)
        st |= DSBSTATUS_LOOPING;
    wr32(out, st);
    com_ret(c, DS_OK);
}

DX_STUB(Buffer_Initialize, DSERR_INVALIDCALL) // already initialised

// Expose one or two guest memory spans for a circular sound-buffer lock.
// Validate offsets and lengths before returning pointers into the guest allocation.
void Buffer_Lock(X86 *c) {
    ComObj *b = this_buffer(c);
    service_notifications();
    uint32_t off = arg(c, 1), bytes = arg(c, 2);
    uint32_t p1 = arg(c, 3), n1 = arg(c, 4);
    uint32_t p2 = arg(c, 5), n2 = arg(c, 6);
    uint32_t flags = arg(c, 7);
    if (!b || !p1 || !n1) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    // Both required out-parameters, and the optional pair when supplied, must
    // be writable before anything is stored through them.
    if (!gm_valid(p1, 4) || !gm_valid(n1, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if ((p2 && !gm_valid(p2, 4)) || (n2 && !gm_valid(n2, 4))) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if (!b->buf_pixels || !b->buf_bytes) {
        com_ret(c, DSERR_INVALIDCALL);
        return;
    }

    // DSBLOCK_ENTIREBUFFER = 2 ignores dwBytes.
    if (flags & 2u) {
        off = 0;
        bytes = b->buf_bytes;
    }
    // DSBLOCK_FROMWRITECURSOR = 1 starts at the write cursor.
    if (flags & 1u)
        off = b->write_cursor;
    if (off >= b->buf_bytes) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if (bytes > b->buf_bytes)
        bytes = b->buf_bytes;

    // A lock that runs off the end wraps into a second region, which is the
    // whole reason Lock has two output pointers.
    uint32_t first = bytes;
    uint32_t second = 0;
    if (off + bytes > b->buf_bytes) {
        first = b->buf_bytes - off;
        second = bytes - first;
    }
    // The regions handed out are inside the buffer by construction, but the
    // buffer's own span is re-checked so a corrupted record cannot hand the
    // guest a pointer past the arena.
    if (!gm_fits(b->buf_pixels, (uint64_t)b->buf_bytes)) {
        com_ret(c, DSERR_INVALIDCALL);
        return;
    }
    wr32(p1, b->buf_pixels + off);
    wr32(n1, first);
    if (p2)
        wr32(p2, second ? b->buf_pixels : 0);
    if (n2)
        wr32(n2, second);
    b->lock_off = off;
    b->lock_len = bytes;
    ATRACE("dsound: Lock buffer %u off %u bytes %u flags %x -> %u + %u "
           "(playing %d, looping %d, channel %d)",
           b->id, off, bytes, flags, first, second, (int)b->playing, (int)b->looping, b->channel);
    com_ret(c, DS_OK);
}

void Buffer_Play(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t flags = arg(c, 3);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    b->looping = (flags & DSBPLAY_LOOPING) != 0;
    if (b->is_primary_buffer) {
        // Playing the primary buffer only keeps the mixer running; there is no
        // sample to hear. Recording the state is the whole contract.
        b->playing = true;
        com_ret(c, DS_OK);
        return;
    }
    if (b->channel < 0)
        b->channel = dx_alloc_audio_channel();
    if (b->channel < 0) {
        com_ret(c, DSERR_ALLOCATED);
        return;
    }
    b->playing = true;
    // A fresh Play is a fresh sound: whatever the last one had been converted
    // into is over, and the ring is a loop again until the guest writes.
    stream_reset(b->id);
    start_playback(b, b->play_cursor);
    if (NotifyRecord *n = notify_for(b->id)) {
        n->last = b->play_cursor;
        n->tracking = true;
    }
    LOGV("dsound: play channel %d, %u bytes, %u Hz, %u ch, %u bit%s", b->channel, b->buf_bytes,
         b->frequency ? b->frequency : b->rate, b->nchannels, b->bits,
         b->looping ? ", looping" : "");
    com_ret(c, DS_OK);
}

void Buffer_SetCurrentPosition(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t pos = arg(c, 1);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if (b->buf_bytes && pos >= b->buf_bytes) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    b->play_cursor = pos;
    // Moving the cursor is a discontinuity by definition, so a stream that had
    // been converted starts again from here.
    stream_reset(b->id);
    if (b->playing)
        start_playback(b, pos);
    com_ret(c, DS_OK);
}

void Buffer_SetFormat(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t wfx = arg(c, 1);
    if (!b || !wfx) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    // Only the primary buffer's format may be set, exactly as DirectSound
    // documents; a secondary buffer's format is fixed at creation.
    if (!b->is_primary_buffer) {
        com_ret(c, DSERR_INVALIDCALL);
        return;
    }
    read_wave_format(b, wfx);
    LOGV("dsound: primary format %u Hz, %u ch, %u bit", b->rate, b->nchannels, b->bits);
    com_ret(c, DS_OK);
}

void Buffer_SetVolume(X86 *c) {
    ComObj *b = this_buffer(c);
    int32_t v = (int32_t)arg(c, 1);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if (v > DSBVOLUME_MAX || v < DSBVOLUME_MIN) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    b->volume = v;
    if (b->channel >= 0)
        host_audio_set_volume(b->channel, v);
    com_ret(c, DS_OK);
}

void Buffer_SetPan(X86 *c) {
    ComObj *b = this_buffer(c);
    int32_t v = (int32_t)arg(c, 1);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if (v > DSBPAN_RIGHT || v < DSBPAN_LEFT) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    b->pan = v;
    if (b->channel >= 0)
        host_audio_set_pan(b->channel, v);
    com_ret(c, DS_OK);
}

void Buffer_SetFrequency(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t hz = arg(c, 1);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    // DSBFREQUENCY_ORIGINAL is 0 and restores the format's own rate.
    if (hz && (hz < 100 || hz > 100000)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    b->frequency = hz;
    if (b->channel >= 0)
        host_audio_set_frequency(b->channel, hz ? hz : b->rate);
    com_ret(c, DS_OK);
}

void Buffer_Stop(X86 *c) {
    ComObj *b = this_buffer(c);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    // Stop keeps the position it stopped at, so a later Play resumes there.
    if (b->playing && b->channel >= 0) {
        b->play_cursor = live_position(b);
        host_audio_stop(b->channel);
    }
    b->playing = false;
    stream_reset(b->id);
    signal_stop_positions(b);
    com_ret(c, DS_OK);
}

// Commit a sound-buffer write and forward the affected PCM spans to native playback.
// Handle circular writes and streaming progress without restarting unchanged audio.
void Buffer_Unlock(X86 *c) {
    ComObj *b = this_buffer(c);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    // The guest has just rewritten part of the buffer, and a buffer that is
    // already playing has to hear it. How depends on what the guest is doing:
    // writing into a ring it is looping is a stream and is fed by appending,
    // anything else is re-submitted.
    uint32_t off = b->lock_off, len = b->lock_len;
    b->lock_off = b->lock_len = 0;
    // The guest has just written: its own ring's gaps now, every guarded
    // block about once a second (thread stacks, the other rings).
    heap_guard_check(b->buf_pixels);
    heap_readonly_report(); // NFSMW issue 8: any write to a sound buffer object that was stopped
    {
        static uint32_t unlocks;
        if (++unlocks % 128u == 0)
            heap_guard_check_all();
    }
    // The step across the seam between this run and the one before it, in the
    // ring rather than in the call sequence: the sample immediately before
    // `off` is the last one the previous run wrote, because the guest writes
    // in ring order. Measured here so a discontinuity can be placed on the
    // guest's side of host_audio_write rather than argued about across it.
    if (audio_trace_budget() && len && b->buf_bytes && b->bits == 16 &&
        gm_fits(b->buf_pixels, (uint64_t)b->buf_bytes)) {
        uint32_t align = b->block_align ? b->block_align : 4;
        uint32_t prev = (off + b->buf_bytes - align) % b->buf_bytes;
        const uint8_t *ring = (const uint8_t *)gm_ptr(b->buf_pixels);
        float worst = 0.0f;
        for (uint32_t ch = 0; ch < b->nchannels && ch * 2 + 1 < align; ++ch) {
            const uint8_t *a = ring + prev + ch * 2;
            const uint8_t *d = ring + off + ch * 2;
            int16_t last = (int16_t)(uint16_t)(a[0] | (a[1] << 8));
            int16_t first = (int16_t)(uint16_t)(d[0] | (d[1] << 8));
            float step = (float)(first - last) / 32768.0f;
            if (step < 0)
                step = -step;
            if (step > worst)
                worst = step;
        }
        // The two samples either side, not only the distance between them.
        // A decoder that restarts its predictor at every chunk begins each one
        // near zero whatever the signal was doing, so "large, then small" is a
        // different fault from "large, then large but wrong".
        const uint8_t *a0 = ring + prev;
        const uint8_t *d0 = ring + off;
        int16_t last0 = (int16_t)(uint16_t)(a0[0] | (a0[1] << 8));
        int16_t first0 = (int16_t)(uint16_t)(d0[0] | (d0[1] << 8));
        // And the same measurement inside the run, which is the control. If
        // the boundaries are no worse than the interior then the audio simply
        // has transients and the seams are innocent; if they are far worse,
        // the boundary is where something is going wrong.
        uint32_t big = 0, pairs = 0, worst_interior = 0;
        for (uint32_t k = align; k < len; k += align) {
            uint32_t at = (off + k) % b->buf_bytes;
            uint32_t be = (off + k - align) % b->buf_bytes;
            const uint8_t *q0 = ring + be;
            const uint8_t *q1 = ring + at;
            int16_t v0 = (int16_t)(uint16_t)(q0[0] | (q0[1] << 8));
            int16_t v1 = (int16_t)(uint16_t)(q1[0] | (q1[1] << 8));
            int step = v1 - v0;
            if (step < 0)
                step = -step;
            if (step > 8192)
                ++big; // a quarter of full scale
            if (step > (int)worst_interior)
                worst_interior = (uint32_t)step;
            ++pairs;
        }
        // The interior's worst step, not only its rate. A boundary step is
        // only remarkable next to the largest step the signal makes anyway.
        ATRACE("dsound: seam at %u: step %.3f, last %d first %d, "
               "interior %u big of %u, interior worst %.3f",
               off, (double)worst, (int)last0, (int)first0, big, pairs,
               (double)worst_interior / 32768.0);
    }

    // Both halves of a run that wraps. Measuring only the first and reporting
    // zero for the rest made every wrapping run look like silence, which is a
    // trace that invents the fault it is there to find.
    if (audio_trace_budget()) {
        uint32_t first = len, second = 0;
        if (b->buf_bytes && off + len > b->buf_bytes) {
            first = b->buf_bytes - off;
            second = len - first;
        }
        float peak = pcm_peak(b->buf_pixels + off, first, b->bits);
        if (second) {
            float p2 = pcm_peak(b->buf_pixels, second, b->bits);
            if (p2 > peak)
                peak = p2;
        }
        ATRACE("dsound: Unlock buffer %u off %u len %u%s (playing %d, looping %d, "
               "channel %d, peak %.3f)",
               b->id, off, len, second ? " wrapping" : "", (int)b->playing, (int)b->looping,
               b->channel, (double)peak);
    }
    if (!b->playing || b->channel < 0 || !b->buf_bytes) {
        com_ret(c, DS_OK);
        return;
    }

    if (FILE *dump = audio_dump_file()) {
        if (len && b->buf_bytes && gm_fits(b->buf_pixels, (uint64_t)b->buf_bytes)) {
            const uint8_t *ring = (const uint8_t *)gm_ptr(b->buf_pixels);
            uint32_t first = len, second = 0;
            if (off + len > b->buf_bytes) {
                first = b->buf_bytes - off;
                second = len - first;
            }
            fwrite(ring + off, 1, first, dump);
            if (second)
                fwrite(ring, 1, second, dump);
        }
    }

    StreamRecord *r = stream_for(b->id, true);
    if (b->looping && len && !r->refused) {
        // The guest is writing into a ring it is playing, which is a stream
        // however DirectSound spells it. Ring order from the play cursor is
        // play order, so the rest of the current lap becomes a one-shot and
        // everything written afterwards is appended behind it. Nothing
        // restarts, which is the whole point.
        // The guest is writing into a ring it is playing, which is a stream
        // however DirectSound spells it. host_audio_stream converts it at the
        // cursor: one join, nothing repeated and nothing skipped, though the
        // host does stop and reschedule the voice to do it, so there is a
        // seam of a millisecond or two. It happens once per sound and on a
        // ring that holds silence at that moment. Every region unlocked
        // afterwards is appended behind it, and none of those restarts
        // anything, which is the part that was audible.
        bool was_active = r->active;
        if (!r->active) {
            int32_t at = host_audio_stream(b->channel);
            if (at < 0) {
                r->refused = true;
                log_once("dsound.nostream",
                         "dsound: this host cannot continue a sound, so a buffer "
                         "the guest streams into is re-submitted at every refill; "
                         "the join is audible");
            } else {
                r->active = true;
                r->ring_end = (uint32_t)at % (b->buf_bytes ? b->buf_bytes : 1u);
                ATRACE("dsound: buffer %u is a stream now, from %d", b->id, at);
            }
        }
        if (r->active) {
            (void)was_active;
            if (stream_feed(b, r, off, len)) {
                b->play_cursor = live_position(b);
                if (NotifyRecord *n = notify_for(b->id)) {
                    n->last = b->play_cursor;
                    n->tracking = true;
                }
                com_ret(c, DS_OK);
                return;
            }
            // Refused with a stream established. The host accepts a late chunk
            // after an underrun, so this is a voice that has gone rather than
            // one that is behind: convert again from wherever it has reached.
            int32_t again = host_audio_stream(b->channel);
            if (again >= 0) {
                r->ring_end = (uint32_t)again % (b->buf_bytes ? b->buf_bytes : 1u);
                log_once("dsound.restream",
                         "dsound: a streamed buffer's voice was replaced between "
                         "refills and has been converted again");
                if (stream_feed(b, r, off, len)) {
                    b->play_cursor = live_position(b);
                    com_ret(c, DS_OK);
                    return;
                }
            }
            r->active = false;
            r->refused = true;
            r->ring_end = 0;
            log_once("dsound.nostream", "dsound: this host cannot continue a sound, so a buffer "
                                        "the guest streams into is re-submitted at every refill; "
                                        "the join is audible");
        }
        uint32_t pos = live_position(b);
        start_playback_loop(b, pos, true);
        b->play_cursor = pos;
        com_ret(c, DS_OK);
        return;
    }

    // Not a stream: a buffer that is playing has to hear what was just written
    // into it, and the only way to say so is to submit it again. From the live
    // cursor, not from the last one anybody asked about - the writer never
    // calls GetCurrentPosition, so a stale cursor restarts the sound at the
    // top of the buffer every time.
    b->play_cursor = live_position(b);
    start_playback(b, b->play_cursor);
    if (NotifyRecord *n = notify_for(b->id)) {
        n->last = b->play_cursor;
        n->tracking = true;
    }
    com_ret(c, DS_OK);
}

void Buffer_Restore(X86 *c) {
    com_ret(c, DS_OK);
} // buffers are never lost

const ComMethod g_dsbuffer[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"GetCaps", 2, Buffer_GetCaps},
    {"GetCurrentPosition", 3, Buffer_GetCurrentPosition},
    {"GetFormat", 4, Buffer_GetFormat},
    {"GetVolume", 2, Buffer_GetVolume},
    {"GetPan", 2, Buffer_GetPan},
    {"GetFrequency", 2, Buffer_GetFrequency},
    {"GetStatus", 2, Buffer_GetStatus},
    {"Initialize", 3, Buffer_Initialize},
    {"Lock", 8, Buffer_Lock},
    {"Play", 4, Buffer_Play},
    {"SetCurrentPosition", 2, Buffer_SetCurrentPosition},
    {"SetFormat", 2, Buffer_SetFormat},
    {"SetVolume", 2, Buffer_SetVolume},
    {"SetPan", 2, Buffer_SetPan},
    {"SetFrequency", 2, Buffer_SetFrequency},
    {"Stop", 1, Buffer_Stop},
    {"Unlock", 5, Buffer_Unlock},
    {"Restore", 1, Buffer_Restore},
};

// ===========================================================================
// IDirectSound3DBuffer - a view on the same buffer object.
// ===========================================================================
// DS3DBUFFER is 76 bytes: dwSize, vPosition, vVelocity, dwInsideConeAngle,
// dwOutsideConeAngle, vConeOrientation, lConeOutsideVolume, flMinDistance,
// flMaxDistance, dwMode.
void B3D_GetAllParameters(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t out = arg(c, 1);
    if (!b || !out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    uint32_t size = rd32(out);
    if (size < 76 || !gm_valid(out, size)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    gm_zero(out + 4, size - 4);
    for (int i = 0; i < 3; ++i)
        wrf32(out + 4 + 4u * (uint32_t)i, b->pos3d[i]);
    for (int i = 0; i < 3; ++i)
        wrf32(out + 16 + 4u * (uint32_t)i, b->vel3d[i]);
    wr32(out + 28, 360);   // dwInsideConeAngle
    wr32(out + 32, 360);   // dwOutsideConeAngle
    wrf32(out + 48, 1.0f); // flMinDistance
    wrf32(out + 52, 1000000000.0f);
    com_ret(c, DS_OK);
}

void B3D_GetPosition(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t out = arg(c, 1);
    if (!b || !out || !gm_valid(out, 12)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    for (int i = 0; i < 3; ++i)
        wrf32(out + 4u * (uint32_t)i, b->pos3d[i]);
    com_ret(c, DS_OK);
}

void B3D_GetVelocity(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t out = arg(c, 1);
    if (!b || !out || !gm_valid(out, 12)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    for (int i = 0; i < 3; ++i)
        wrf32(out + 4u * (uint32_t)i, b->vel3d[i]);
    com_ret(c, DS_OK);
}

void B3D_SetPosition(X86 *c) {
    ComObj *b = this_buffer(c);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    // The three coordinates are floats passed by value on the stack.
    uint32_t x = arg(c, 1), y = arg(c, 2), z = arg(c, 3);
    memcpy(&b->pos3d[0], &x, 4);
    memcpy(&b->pos3d[1], &y, 4);
    memcpy(&b->pos3d[2], &z, 4);
    com_ret(c, DS_OK);
}

void B3D_SetVelocity(X86 *c) {
    ComObj *b = this_buffer(c);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    uint32_t x = arg(c, 1), y = arg(c, 2), z = arg(c, 3);
    memcpy(&b->vel3d[0], &x, 4);
    memcpy(&b->vel3d[1], &y, 4);
    memcpy(&b->vel3d[2], &z, 4);
    com_ret(c, DS_OK);
}

void B3D_SetAllParameters(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t in = arg(c, 1);
    if (!b || !in || !gm_valid(in, 76)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    for (int i = 0; i < 3; ++i)
        b->pos3d[i] = rdf32(in + 4 + 4u * (uint32_t)i);
    for (int i = 0; i < 3; ++i)
        b->vel3d[i] = rdf32(in + 16 + 4u * (uint32_t)i);
    com_ret(c, DS_OK);
}

// The remaining 3D parameters are accepted and reported as their defaults.
// Positional audio is stored, not applied, in this task.
DX_STUB(B3D_GetConeAngles, DS_OK)
DX_STUB(B3D_GetConeOrientation, DS_OK)
DX_STUB(B3D_GetConeOutsideVolume, DS_OK)
DX_STUB(B3D_GetMaxDistance, DS_OK)
DX_STUB(B3D_GetMinDistance, DS_OK)
DX_STUB(B3D_GetMode, DS_OK)
DX_STUB(B3D_SetConeAngles, DS_OK)
DX_STUB(B3D_SetConeOrientation, DS_OK)
DX_STUB(B3D_SetConeOutsideVolume, DS_OK)
DX_STUB(B3D_SetMaxDistance, DS_OK)
DX_STUB(B3D_SetMinDistance, DS_OK)
DX_STUB(B3D_SetMode, DS_OK)

const ComMethod g_ds3dbuffer[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"GetAllParameters", 2, B3D_GetAllParameters},
    {"GetConeAngles", 3, B3D_GetConeAngles},
    {"GetConeOrientation", 2, B3D_GetConeOrientation},
    {"GetConeOutsideVolume", 2, B3D_GetConeOutsideVolume},
    {"GetMaxDistance", 2, B3D_GetMaxDistance},
    {"GetMinDistance", 2, B3D_GetMinDistance},
    {"GetMode", 2, B3D_GetMode},
    {"GetPosition", 2, B3D_GetPosition},
    {"GetVelocity", 2, B3D_GetVelocity},
    {"SetAllParameters", 3, B3D_SetAllParameters},
    {"SetConeAngles", 4, B3D_SetConeAngles},
    {"SetConeOrientation", 5, B3D_SetConeOrientation},
    {"SetConeOutsideVolume", 3, B3D_SetConeOutsideVolume},
    {"SetMaxDistance", 3, B3D_SetMaxDistance},
    {"SetMinDistance", 3, B3D_SetMinDistance},
    {"SetMode", 3, B3D_SetMode},
    {"SetPosition", 5, B3D_SetPosition},
    {"SetVelocity", 5, B3D_SetVelocity},
};

// ===========================================================================
// IDirectSound3DListener - a view on the DirectSound object.
// ===========================================================================
void L3D_GetPosition(X86 *c) {
    uint32_t out = arg(c, 1);
    if (!out || !gm_valid(out, 12)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    for (int i = 0; i < 3; ++i)
        wrf32(out + 4u * (uint32_t)i, g_listener[i]);
    com_ret(c, DS_OK);
}

void L3D_SetPosition(X86 *c) {
    uint32_t x = arg(c, 1), y = arg(c, 2), z = arg(c, 3);
    memcpy(&g_listener[0], &x, 4);
    memcpy(&g_listener[1], &y, 4);
    memcpy(&g_listener[2], &z, 4);
    com_ret(c, DS_OK);
}

void L3D_GetOrientation(X86 *c) {
    uint32_t front = arg(c, 1), top = arg(c, 2);
    if (front && gm_valid(front, 12))
        for (int i = 0; i < 3; ++i)
            wrf32(front + 4u * (uint32_t)i, g_listener[3 + i]);
    if (top && gm_valid(top, 12))
        for (int i = 0; i < 3; ++i)
            wrf32(top + 4u * (uint32_t)i, g_listener[6 + i]);
    com_ret(c, DS_OK);
}

void L3D_SetOrientation(X86 *c) {
    for (int i = 0; i < 6; ++i) {
        uint32_t v = arg(c, 1 + i);
        memcpy(&g_listener[3 + i], &v, 4);
    }
    com_ret(c, DS_OK);
}

void L3D_GetVelocity(X86 *c) {
    uint32_t out = arg(c, 1);
    if (!out || !gm_valid(out, 12)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    for (int i = 0; i < 3; ++i)
        wrf32(out + 4u * (uint32_t)i, g_listener[9 + i]);
    com_ret(c, DS_OK);
}

void L3D_SetVelocity(X86 *c) {
    uint32_t x = arg(c, 1), y = arg(c, 2), z = arg(c, 3);
    memcpy(&g_listener[9], &x, 4);
    memcpy(&g_listener[10], &y, 4);
    memcpy(&g_listener[11], &z, 4);
    com_ret(c, DS_OK);
}

void L3D_GetAllParameters(X86 *c) {
    uint32_t out = arg(c, 1);
    // DS3DLISTENER: dwSize, vPosition, vVelocity, vOrientFront, vOrientTop,
    // flDistanceFactor, flRolloffFactor, flDopplerFactor = 68 bytes.
    if (!out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    uint32_t size = rd32(out);
    if (size < 68 || !gm_valid(out, size)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    gm_zero(out + 4, size - 4);
    for (int i = 0; i < 3; ++i)
        wrf32(out + 4 + 4u * (uint32_t)i, g_listener[i]);
    for (int i = 0; i < 3; ++i)
        wrf32(out + 16 + 4u * (uint32_t)i, g_listener[9 + i]);
    for (int i = 0; i < 3; ++i)
        wrf32(out + 28 + 4u * (uint32_t)i, g_listener[3 + i]);
    for (int i = 0; i < 3; ++i)
        wrf32(out + 40 + 4u * (uint32_t)i, g_listener[6 + i]);
    wrf32(out + 52, g_distance_factor);
    wrf32(out + 56, g_rolloff_factor);
    wrf32(out + 60, g_doppler_factor);
    com_ret(c, DS_OK);
}

void L3D_SetAllParameters(X86 *c) {
    uint32_t in = arg(c, 1);
    if (!in || !gm_valid(in, 68)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    for (int i = 0; i < 3; ++i)
        g_listener[i] = rdf32(in + 4 + 4u * (uint32_t)i);
    for (int i = 0; i < 3; ++i)
        g_listener[9 + i] = rdf32(in + 16 + 4u * (uint32_t)i);
    for (int i = 0; i < 3; ++i)
        g_listener[3 + i] = rdf32(in + 28 + 4u * (uint32_t)i);
    for (int i = 0; i < 3; ++i)
        g_listener[6 + i] = rdf32(in + 40 + 4u * (uint32_t)i);
    g_distance_factor = rdf32(in + 52);
    g_rolloff_factor = rdf32(in + 56);
    g_doppler_factor = rdf32(in + 60);
    com_ret(c, DS_OK);
}

void L3D_GetDistanceFactor(X86 *c) {
    uint32_t out = arg(c, 1);
    if (out && gm_valid(out, 4))
        wrf32(out, g_distance_factor);
    com_ret(c, DS_OK);
}
void L3D_GetDopplerFactor(X86 *c) {
    uint32_t out = arg(c, 1);
    if (out && gm_valid(out, 4))
        wrf32(out, g_doppler_factor);
    com_ret(c, DS_OK);
}
void L3D_GetRolloffFactor(X86 *c) {
    uint32_t out = arg(c, 1);
    if (out && gm_valid(out, 4))
        wrf32(out, g_rolloff_factor);
    com_ret(c, DS_OK);
}
void L3D_SetDistanceFactor(X86 *c) {
    uint32_t v = arg(c, 1);
    memcpy(&g_distance_factor, &v, 4);
    com_ret(c, DS_OK);
}
void L3D_SetDopplerFactor(X86 *c) {
    uint32_t v = arg(c, 1);
    memcpy(&g_doppler_factor, &v, 4);
    com_ret(c, DS_OK);
}
void L3D_SetRolloffFactor(X86 *c) {
    uint32_t v = arg(c, 1);
    memcpy(&g_rolloff_factor, &v, 4);
    com_ret(c, DS_OK);
}
void L3D_CommitDeferredSettings(X86 *c) {
    com_ret(c, DS_OK);
}

const ComMethod g_ds3dlistener[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"GetAllParameters", 2, L3D_GetAllParameters},
    {"GetDistanceFactor", 2, L3D_GetDistanceFactor},
    {"GetDopplerFactor", 2, L3D_GetDopplerFactor},
    {"GetOrientation", 3, L3D_GetOrientation},
    {"GetPosition", 2, L3D_GetPosition},
    {"GetRolloffFactor", 2, L3D_GetRolloffFactor},
    {"GetVelocity", 2, L3D_GetVelocity},
    {"SetAllParameters", 3, L3D_SetAllParameters},
    {"SetDistanceFactor", 3, L3D_SetDistanceFactor},
    {"SetDopplerFactor", 3, L3D_SetDopplerFactor},
    {"SetOrientation", 8, L3D_SetOrientation},
    {"SetPosition", 5, L3D_SetPosition},
    {"SetRolloffFactor", 3, L3D_SetRolloffFactor},
    {"SetVelocity", 5, L3D_SetVelocity},
    {"CommitDeferredSettings", 1, L3D_CommitDeferredSettings},
};

// ===========================================================================
// IDirectSoundNotify - a view on the buffer object.
// ===========================================================================
void Notify_SetNotificationPositions(X86 *c) {
    ComObj *b = this_buffer(c);
    uint32_t n = arg(c, 1), list = arg(c, 2);
    if (!b) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if (n && (!list || !gm_valid(list, n * 8))) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    // DirectSound refuses a change while the buffer is playing, because the
    // positions it is being asked to forget may be the ones it is between.
    if (n && b->playing) {
        com_ret(c, DSERR_INVALIDCALL);
        return;
    }
    b->notify_count = n;

    NotifyRecord *rec = notify_for(b->id, true);
    rec->pos.clear();
    rec->tracking = false;
    // DSBPOSITIONNOTIFY is {dwOffset, hEventNotify}, eight bytes each.
    for (uint32_t i = 0; i < n; ++i) {
        NotifyPos p;
        p.offset = rd32(list + 8u * i);
        p.event = rd32(list + 8u * i + 4);
        if (p.offset != DSBPN_OFFSETSTOP && b->buf_bytes && p.offset >= b->buf_bytes) {
            LOGW("dsound: notification position %u is past the end of a "
                 "%u-byte buffer; ignoring it",
                 p.offset, b->buf_bytes);
            continue;
        }
        rec->pos.push_back(p);
    }
    LOGV("dsound: buffer %u takes %u notification positions", b->id, n);
    com_ret(c, DS_OK);
}

const ComMethod g_dsnotify[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"SetNotificationPositions", 3, Notify_SetNotificationPositions},
};

// ===========================================================================
// IDirectSound
// ===========================================================================
void DS_CreateSoundBuffer(X86 *c) {
    ComObj *ds = this_dsound(c);
    uint32_t desc = arg(c, 1), out = arg(c, 2), outer = arg(c, 3);
    if (!ds || !out) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    com_out_ptr(out, 0);
    if (outer) {
        com_ret(c, CLASS_E_NOAGGREGATION);
        return;
    }
    if (!desc || !gm_valid(desc, DSBUFFERDESC_SIZE)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }

    uint32_t flags = rd32(desc + DSBD_OFF_dwFlags);
    uint32_t bytes = rd32(desc + DSBD_OFF_dwBufferBytes);
    uint32_t wfx = rd32(desc + DSBD_OFF_lpwfxFormat);
    bool primary = (flags & DSBCAPS_PRIMARYBUFFER) != 0;

    // DirectSound requires a format for a secondary buffer and forbids one for
    // the primary, and a secondary buffer must have a non-zero size.
    if (primary) {
        if (bytes || wfx) {
            com_ret(c, DSERR_INVALIDPARAM);
            return;
        }
    } else {
        if (!bytes || !wfx) {
            com_ret(c, DSERR_INVALIDPARAM);
            return;
        }
        if (bytes > 64u * 1024 * 1024) {
            com_ret(c, DSERR_INVALIDPARAM);
            return;
        }
    }

    ComObj *b = com_new(K_DSBUFFER);
    b->buf_flags = flags;
    b->is_primary_buffer = primary;
    if (primary) {
        b->rate = 22050;
        b->nchannels = 2;
        b->bits = 16;
        b->block_align = 4;
    } else {
        read_wave_format(b, wfx);
        b->buf_bytes = bytes;
        // A gap on each side of the samples (NFSMW issue 8): the buffer's own
        // COM view is allocated next, and a write off the end of the ring
        // must not reach it.
        b->buf_pixels = heap_alloc_guarded(bytes, true, DSOUND_RING_GUARD, "dsound buffer", b->id);
        pcm_retain(b->buf_pixels);
        if (!b->buf_pixels) {
            com_release(b);
            LOGW("dsound: out of guest memory for a %u-byte sound buffer", bytes);
            com_ret(c, E_OUTOFMEMORY);
            return;
        }
    }
    uint32_t view = com_view(b, IF_DSBUFFER);
    if (!view) {
        com_release(b);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view);
    LOGV("dsound: created a %s buffer of %u bytes (flags %08x)", primary ? "primary" : "secondary",
         bytes, flags);
    com_ret(c, DS_OK);
}

void DS_GetCaps(X86 *c) {
    uint32_t out = arg(c, 1);
    if (!out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    uint32_t size = rd32(out);
    if (size < 8 || size > DSCAPS_SIZE || !gm_valid(out, size)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    gm_zero(out + 4, size - 4);
    wr32(out + DSCAPS_OFF_dwFlags, DSCAPS_PRIMARY16BIT | DSCAPS_PRIMARYSTEREO |
                                       DSCAPS_SECONDARY16BIT | DSCAPS_SECONDARYSTEREO |
                                       DSCAPS_CONTINUOUSRATE | DSCAPS_CERTIFIED);
    com_ret(c, DS_OK);
}

void DS_DuplicateSoundBuffer(X86 *c) {
    ComObj *ds = this_dsound(c);
    uint32_t src_ptr = arg(c, 1), out = arg(c, 2);
    ComObj *src = src_ptr ? com_this(src_ptr) : nullptr;
    if (!ds || !src || src->kind != K_DSBUFFER || !out) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    if (src->is_primary_buffer) {
        com_ret(c, DSERR_INVALIDCALL);
        return;
    }
    ComObj *b = com_new(K_DSBUFFER);
    b->buf_flags = src->buf_flags;
    b->buf_bytes = src->buf_bytes;
    b->rate = src->rate;
    b->nchannels = src->nchannels;
    b->bits = src->bits;
    b->block_align = src->block_align;
    b->volume = src->volume;
    b->pan = src->pan;
    // A duplicate shares the original's sample data in DirectSound. It takes
    // its own reference, so releasing either buffer in either order leaves the
    // samples valid for whichever one is still alive.
    b->buf_pixels = src->buf_pixels;
    b->buf_bytes = src->buf_bytes;
    pcm_retain(b->buf_pixels);
    uint32_t view = com_view(b, IF_DSBUFFER);
    if (!view) {
        com_release(b);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    com_out_ptr(out, view);
    com_ret(c, DS_OK);
}

void DS_SetCooperativeLevel(X86 *c) {
    ComObj *ds = this_dsound(c);
    if (!ds) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    ds->ds_hwnd = arg(c, 1);
    ds->ds_coop = arg(c, 2);
    com_ret(c, DS_OK);
}

void DS_Compact(X86 *c) {
    com_ret(c, DS_OK);
}

void DS_GetSpeakerConfig(X86 *c) {
    uint32_t out = arg(c, 1);
    if (!out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    // The native mixer/sink currently produces stereo PCM. In the DirectSound
    // ABI 2 means MONO; STEREO is 4 (independent of the channel count).
    wr32(out, 4); // DSSPEAKER_STEREO
    com_ret(c, DS_OK);
}

void DS_SetSpeakerConfig(X86 *c) {
    com_ret(c, DS_OK);
}
// Initialize(lpGuid): an object from DirectSoundCreate is already initialised
// and one from CoCreateInstance is not; both are the same host object here,
// and the default device is the only device, so it succeeds either way.
void DS_Initialize(X86 *c) {
    com_ret(c, DS_OK);
}

const ComMethod g_dsound[] = {
    {"QueryInterface", 3, com_QueryInterface},
    {"AddRef", 1, com_AddRef},
    {"Release", 1, com_Release},
    {"CreateSoundBuffer", 4, DS_CreateSoundBuffer},
    {"GetCaps", 2, DS_GetCaps},
    {"DuplicateSoundBuffer", 3, DS_DuplicateSoundBuffer},
    {"SetCooperativeLevel", 3, DS_SetCooperativeLevel},
    {"Compact", 1, DS_Compact},
    {"GetSpeakerConfig", 2, DS_GetSpeakerConfig},
    {"SetSpeakerConfig", 2, DS_SetSpeakerConfig},
    {"Initialize", 2, DS_Initialize},
};

// ===========================================================================
// DSOUND.dll exports
// ===========================================================================
// DirectSoundCreate(lpGuid, ppDS, pUnkOuter). The EXE imports it by ordinal 1,
// which the loader names "ord1"; both names are registered so the IAT entry
// and GetProcAddress agree.
void DirectSoundCreate(X86 *c) {
    uint32_t out = arg(c, 1), outer = arg(c, 2);
    if (!out || !gm_valid(out, 4)) {
        com_ret(c, DSERR_INVALIDPARAM);
        return;
    }
    wr32(out, 0);
    if (outer) {
        com_ret(c, CLASS_E_NOAGGREGATION);
        return;
    }
    ComObj *ds = com_new(K_DSOUND);
    uint32_t view = com_view(ds, IF_DSOUND);
    if (!view) {
        com_release(ds);
        com_ret(c, E_OUTOFMEMORY);
        return;
    }
    wr32(out, view);
    LOGV("dsound: DirectSoundCreate -> %08x", view);
    com_ret(c, DS_OK);
}

// CLSID_DirectSound, for a game built against the DirectX 7 era SDK that
// creates its DirectSound through CoCreateInstance rather than
// DirectSoundCreate. Both make the same host object.
static const uint8_t CLSID_DirectSound_[16] =
    IID_BYTES(0x47D4D946, 0x62E8, 0x11CF, 0x93, 0xBC, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00);

static ComObj *dsound_create() {
    return com_new(K_DSOUND);
}

const ImportShim g_dsound_exports[] = {
    {"DSOUND.dll", "ord1", 3, DirectSoundCreate},
    {"DSOUND.dll", "DirectSoundCreate", 3, DirectSoundCreate},
};

// A DirectSound object also answers to IDirectSound3DListener, and a buffer to
// IDirectSound3DBuffer and IDirectSoundNotify. Both are the same host object
// seen through another interface, which the view table already handles.
ComObj *dsound_qi(ComObj *self, ComIface want) {
    if (want == IF_DS3DLISTENER)
        return self;
    return nullptr;
}
ComObj *dsbuffer_qi(ComObj *self, ComIface want) {
    if (want == IF_DS3DBUFFER || want == IF_DSNOTIFY)
        return self;
    return nullptr;
}

void dsbuffer_destroy(ComObj *b) {
    for (size_t i = 0; i < notifies().size(); ++i) {
        if (notifies()[i].obj_id != b->id)
            continue;
        notifies().erase(notifies().begin() + (ptrdiff_t)i);
        break;
    }
    stream_drop(b->id);
    if (b->playing && b->channel >= 0)
        host_audio_stop(b->channel);
    if (b->channel >= 0) {
        dx_free_audio_channel(b->channel);
        b->channel = -1;
    }
    // Every holder of the sample memory, original and duplicates alike, drops
    // one reference; the last one out frees it.
    pcm_release(b->buf_pixels);
    b->buf_pixels = 0;
}

} // namespace

void dsound_reset() {
    // The sample blocks lived in the arena mem_init discarded, so the
    // reference table must not outlive it. So did the guest event handles the
    // notification records name.
    pcm_refs().clear();
    notifies().clear();
    streams().clear();
    announced().clear();
    for (int i = 0; i < 3; ++i) {
        g_listener[i] = 0.0f;
        g_listener[9 + i] = 0.0f;
    }
    g_listener[3] = 0.0f;
    g_listener[4] = 0.0f;
    g_listener[5] = 1.0f;
    g_listener[6] = 0.0f;
    g_listener[7] = 1.0f;
    g_listener[8] = 0.0f;
    g_distance_factor = g_doppler_factor = g_rolloff_factor = 1.0f;
}

// Called from QSWaveMixPump, which the game runs every frame. The streaming
// worker is parked in WaitForMultipleObjects and cannot service itself, and
// the main thread has no reason to touch DirectSound between refills, so this
// is the tick that keeps a streamed buffer moving.
void dsound_pump() {
    service_notifications();
}

void dsound_register() {
    static bool done = false;
    if (done)
        return;
    done = true;

    com_define(IF_DSOUND, "DSOUND.dll", "IDirectSound", g_dsound, std::size(g_dsound));
    com_define(IF_DSBUFFER, "DSOUND.dll", "IDirectSoundBuffer", g_dsbuffer, std::size(g_dsbuffer));
    com_define(IF_DS3DBUFFER, "DSOUND.dll", "IDirectSound3DBuffer", g_ds3dbuffer,
               std::size(g_ds3dbuffer));
    com_define(IF_DS3DLISTENER, "DSOUND.dll", "IDirectSound3DListener", g_ds3dlistener,
               std::size(g_ds3dlistener));
    com_define(IF_DSNOTIFY, "DSOUND.dll", "IDirectSoundNotify", g_dsnotify, std::size(g_dsnotify));

    com_bind(IF_DSOUND, K_DSOUND);
    com_bind(IF_DS3DLISTENER, K_DSOUND);
    com_bind(IF_DSBUFFER, K_DSBUFFER);
    com_bind(IF_DS3DBUFFER, K_DSBUFFER);
    com_bind(IF_DSNOTIFY, K_DSBUFFER);

    com_register_iid(IF_DSOUND, IID_IDirectSound_);
    com_register_iid(IF_DSBUFFER, IID_IDirectSoundBuffer_);
    com_register_iid(IF_DS3DBUFFER, IID_IDirectSound3DBuffer_);
    com_register_iid(IF_DS3DLISTENER, IID_IDirectSound3DListener_);
    com_register_iid(IF_DSNOTIFY, IID_IDirectSoundNotify_);

    com_set_destructor(K_DSBUFFER, dsbuffer_destroy);
    com_set_qi_hook(K_DSOUND, dsound_qi);
    com_set_qi_hook(K_DSBUFFER, dsbuffer_qi);

    com_register_class(CLSID_DirectSound_, "DirectSound", IF_DSOUND, dsound_create);
    imports_register(g_dsound_exports, std::size(g_dsound_exports));
}
