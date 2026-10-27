// movies.c - the X360 movies (720p WMV3, WMA Pro 5.1) in the PC movie player.
//
// The game plays movies with EA's RCMP AV_PLAYER (VP6 video, EA ADPCM stereo), driven by MoviePlayer:
//   Play 00542A40            opens the AV_PLAYER on MOVIES\<name>.vp6, gets the first frame, sizes the movie texture
//                            (PlatSetFirstMovieFrame), sets state 5 (playing)
//   UpdateFunction 00525F70  each frame: decode when it's time, convert into the movie texture (FillInTextureInfo);
//                            at the end of video and audio sets the next state to 0, and Update 00542C50 then tears
//                            down the player and tells the front end the movie is done
//   Stop 00517610, ~MoviePlayer 00525E60  skip / screen exit
// These four are replaced here (recomp/hooks.txt). When the X360 disc has the same movie as a WMV, Play opens it
// with FFmpeg (media/movie_api.h) instead of creating the AV_PLAYER; the MoviePlayer's AV_PLAYER pointer stays 0,
// which every remaining game path (Update, Pause, the destructor) already checks. Otherwise the original runs (VP6).
//
// Video: a decoder thread fills a short frame queue; the game thread shows the newest frame whose time has come
// (wall clock since Play) by writing it into the game's own movie texture. The PC movies are 4:3 pictures (16:9
// letterboxed) squeezed into 1024x512 and drawn on a 4:3 quad, so the 1280x720 frame goes into a 1280x960 texture
// with black bars; on a wide screen it goes in without bars and widescreen.c widens the quad back to 16:9. The texture
// is created by the game's PlatSetFirstMovieFrame, sized from a stand-in frame Shape.
// cFEngRender::RenderMovie takes u/v to width/next_pow2(width), as if the frame sat in a power-of-two texture, so
// the TextureInfo reports the power-of-two size after creation and the quad samples the whole texture 1:1; with the
// movie's settings flag +0xC = 0 (front-end movies) it runs v bottom-up, so rows are stored bottom-up then.
// Audio: 6-channel float at 44.1 kHz (WMA Pro's own rate; Snd's mix rate) in a secondary buffer on the game's
// DirectSound device (host/audio mixes it with Snd), fed every 5 ms by a host thread from a 4 s ring. A second,
// audio-only decoder fills the ring on its own thread: the X360 files store audio ~1.4 s after the video it plays
// with. Audio that isn't there in time becomes silence and is skipped later, so it stays on the video's clock.
//
// Names: MOVIES\ealogo_english_ntsc.vp6 -> <x360>/ealogo_english_ntsc.wmv, case-insensitive, where <x360> is
// $NFS_X360_MOVIES, else the `movies` entry of roots.txt (a development checkout without roots.txt also tries a sibling x360/Movies). A missing file (or an empty directory) falls back
// to the game's VP6 player. Diagnostic mode writes movies.log and one PNG of the movie texture per movie.
#include <CoreGraphics/CoreGraphics.h>
#include <ImageIO/ImageIO.h>
#include <dirent.h>
#include <math.h>
#include <pthread.h>
#include <stdlib.h>
#include <strings.h>
#include <time.h>

#include "../host/audio/dsound_host.h"
#include "../media/movie_api.h"
#include "funcs.h"
#include "host.h"
#include "roots.h"

DsDevice *dsound_current_device(void);   // dsound.c
uint64_t diag_current_frame(void);       // diag.c

// ---- game addresses (speed.exe v1.3) ----
#define SKIP_MOVIES 0x00926144u          // SkipMovies
#define MOVIE_TEXTURE_INFO 0x0091CB28u   // TextureInfo MovieTextureInfo (+0x40 bytes, +0x44/+0x46 width/height)
#define CUR_SUBTITLER 0x0091CB1Cu        // SubTitler::gCurrentSubtitler_ (+0x18: start ticker)
#define UPDATE_RECURSE 0x0091CC80u       // UpdateFunction's static recursion guard
#define MOVIE_SHAPE 0x00982D18u          // Shape the frames are converted into; sizes the movie texture
#define MOVIE_D3DTEX 0x00983E58u         // its IDirect3DTexture9 (TextureInfoPlatInterface at 00983E40, +0x18)
#define FN_bGetTicker 0x0045CE40u
#define FN_PlatSetFirstMovieFrame 0x006E1880u   // cdecl (TextureInfo *, Shape *frame, bool)
#define FN_MovieShapeFree 0x006DD1D0u           // frees MOVIE_SHAPE and the texture (end of ~MoviePlayer)
#define FN_ShapeImage 0x007E9320u               // thiscall (Shape *): its image block (+0x18 width, +0x1C height)
#define FN_SYNCTASK_run 0x007EC59Cu
#define FN_THREAD_yield 0x007EC298u             // cdecl (int)
#define SHAPE_IMAGE_32BIT 0x7D                  // image block type for 32-bit texels (FUN_007EB150(0x20))
// MoviePlayer fields
#define MP_VOLUME 0x000      // 0..127 (GetMovieCategoryVolume)
#define MP_FLAG_C 0x00C
#define MP_SOUND 0x015       // byte: movie has sound
#define MP_FILENAME 0x018    // "MOVIES\name.vp6"
#define MP_PLAYS 0x118
#define MP_STATE 0x11C       // 5 = playing
#define MP_NEXT 0x120        // next state; 0 = finished
#define MP_AVPLAYER 0x140
#define MP_FRAME 0x144

#define RATE 44100
#define QN 6                             // decoded frames queued ahead
#define RING_SECONDS 4
#define DS_SECONDS 1
#define LEAD_FRAMES (RATE / 10)          // audio written ahead of the mixer's write cursor (100 ms)

typedef struct { uint8_t *bgra; double pts; } QFrame;

static struct Movie {
    int active;
    uint32_t player;
    char name[64], path[1024];
    MediaMovie *dec, *adec;              // video-only and audio-only decoders of the same file
    int w, h, has_audio;
    int tw, th, top, flip;               // texture: a 4:3 picture (tw x th) with the frame at row `top`, bottom-up if flip
    double duration, pts0;
    float gain;
    pthread_t dec_thread, aud_thread, feed_thread;
    int dec_running, aud_running, feed_running, quit, feed_quit, eof, audio_eof, lost;
    QFrame q[QN];
    int qhead, qcount;
    float *ring;                         // interleaved 6-ch, RING_SECONDS
    uint64_t ring_w, ring_r, apos;       // absolute frame indices: produced, consumed, delivered to DirectSound
    DsBuffer *buf;
    DsDevice *dev;                       // the device buf lives on (mixer stats at close)
    uint64_t mix_frames0;
    float *dsmem;
    uint32_t dsbytes, wpos;
    uint64_t start_ns;
    uint8_t *shown;                      // last frame written to the texture
    // counters for movies.log
    uint64_t frames_decoded, frames_shown, frames_dropped, first_frame, audio_real, audio_silence, audio_skipped;
    uint64_t audio_decoded, ds_restarts;
    double sumsq[6];
    uint32_t tex_w, tex_h;
    int png_saved;
} mv;
static pthread_mutex_t mv_mu = PTHREAD_MUTEX_INITIALIZER;   // guards the queue, the ring and the flags
static pthread_cond_t mv_cv = PTHREAD_COND_INITIALIZER;
static int shape_is_ours;                // MOVIE_SHAPE was sized here (the VP6 path must not reuse it)
static uint32_t scratch;                 // guest scratch: stand-in Shape (0x40) + D3DLOCKED_RECT/D3DSURFACE_DESC
static unsigned n_wmv, n_fallback;

// ---------------- log ----------------
static void mlog(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void mlog(const char *fmt, ...) {
    char line[1024];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    if (boot_trace) fprintf(stderr, "movies: %s\n", line);
    if (!diag_on) return;
    diag_event("movies: %s", line);
    char path[1024];
    snprintf(path, sizeof path, "%s/movies.log", diag_dir);
    FILE *f = fopen(path, "a");
    if (f) { fprintf(f, "frame %llu: %s\n", (unsigned long long)diag_current_frame(), line); fclose(f); }
}

// ---------------- names ----------------
static int dir_exists(const char *p) { DIR *d = opendir(p); if (d) closedir(d); return d != NULL; }
static const char *x360_dir(void) {
    static char dir[1024];
    static int done;
    if (done) return dir[0] ? dir : NULL;
    done = 1;
    const char *env = getenv("NFS_X360_MOVIES");
    if (env) snprintf(dir, sizeof dir, "%s", env);                 // used as given, even when empty or missing
    else if (!roots_movies_dir(dir, sizeof dir)) dir[0] = 0;
    mlog("X360 movie folder: %s%s", dir[0] ? dir : "(none found)", env ? " (NFS_X360_MOVIES)" : "");
    return dir[0] ? dir : NULL;
}
// PC name ("MOVIES\ealogo_english_ntsc.vp6") -> the X360 WMV with the same base name, case-insensitive.
static int find_wmv(const char *pc, char *path, size_t cap, char *why, size_t whycap) {
    const char *base = pc;
    for (const char *p = pc; *p; ++p) if (*p == '\\' || *p == '/') base = p + 1;
    size_t n = strlen(base);
    const char *dot = strrchr(base, '.');
    if (dot) n = (size_t)(dot - base);
    const char *dir = x360_dir();
    if (!dir) { snprintf(why, whycap, "no X360 movie folder"); return 0; }
    DIR *d = opendir(dir);
    if (!d) { snprintf(why, whycap, "can't open %s", dir); return 0; }
    struct dirent *e;
    int found = 0;
    while (!found && (e = readdir(d))) {
        if (strlen(e->d_name) == n + 4 && !strncasecmp(e->d_name, base, n) && !strcasecmp(e->d_name + n, ".wmv")) {
            snprintf(path, cap, "%s/%s", dir, e->d_name);
            found = 1;
        }
    }
    closedir(d);
    if (!found) snprintf(why, whycap, "no %.*s.wmv in %s", (int)n, base, dir);
    return found;
}
// For bootflow.c: whether the X360 folder has the movie in any language (<name>_*.wmv), e.g. "eahd_bumper", which
// the PC data has no VP6 of.
int movies_x360_has(const char *name) {
    const char *dir = x360_dir();
    DIR *d = dir ? opendir(dir) : NULL;
    if (!d) return 0;
    size_t n = strlen(name), len;
    struct dirent *e;
    int found = 0;
    while (!found && (e = readdir(d)))
        found = (len = strlen(e->d_name)) > n + 5 && !strncasecmp(e->d_name, name, n) && e->d_name[n] == '_' &&
                !strcasecmp(e->d_name + len - 4, ".wmv");
    closedir(d);
    return found;
}

// ---------------- decoder thread ----------------
static double now_s(void) { return mv.start_ns ? (host_ticks() - mv.start_ns) / 1e9 : 0; }

// Moves up to `max` decoded audio frames into the ring; returns the frames moved. Caller ensures the space.
static size_t take_audio(size_t max) {
    static float tmp[4096 * 6];
    const uint64_t cap = (uint64_t)RATE * RING_SECONDS;
    size_t n = media_movie_take_audio(mv.adec, tmp, max < 4096 ? max : 4096);
    pthread_mutex_lock(&mv_mu);
    for (size_t j = 0; j < n; ++j) memcpy(mv.ring + ((mv.ring_w + j) % cap) * 6, tmp + j * 6, 6 * sizeof(float));
    mv.ring_w += n;
    mv.audio_decoded += n;
    pthread_cond_broadcast(&mv_cv);
    pthread_mutex_unlock(&mv_mu);
    return n;
}
// Audio decoder thread: keeps the ring (RING_SECONDS) full, independently of the video.
static void *audio_main(void *p) {
    (void)p;
    const uint64_t cap = (uint64_t)RATE * RING_SECONDS;
    for (;;) {
        pthread_mutex_lock(&mv_mu);
        while (!mv.quit && cap - (mv.ring_w - mv.ring_r) < 4096) {
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            ts.tv_nsec += 20000000;
            if (ts.tv_nsec >= 1000000000) { ts.tv_sec++; ts.tv_nsec -= 1000000000; }
            pthread_cond_timedwait(&mv_cv, &mv_mu, &ts);
        }
        int quit = mv.quit;
        pthread_mutex_unlock(&mv_mu);
        if (quit) break;
        int more = media_movie_pump_audio(mv.adec, 4096);
        if (!take_audio(4096) && !more) {
            pthread_mutex_lock(&mv_mu);
            mv.audio_eof = 1;
            pthread_mutex_unlock(&mv_mu);
            break;
        }
    }
    return NULL;
}
static void *decoder_main(void *p) {
    (void)p;
    for (;;) {
        pthread_mutex_lock(&mv_mu);
        while (mv.qcount == QN && !mv.quit) pthread_cond_wait(&mv_cv, &mv_mu);
        int quit = mv.quit;
        pthread_mutex_unlock(&mv_mu);
        if (quit) break;
        int w = 0, h = 0;
        double pts = 0;
        const uint8_t *px = media_movie_next_frame(mv.dec, &w, &h, &pts);
        pthread_mutex_lock(&mv_mu);
        if (!px || w != mv.w || h != mv.h) {
            mv.eof = 1;
            pthread_mutex_unlock(&mv_mu);
            break;
        }
        QFrame *f = &mv.q[(mv.qhead + mv.qcount) % QN];
        memcpy(f->bgra, px, (size_t)w * h * 4);
        f->pts = pts - mv.pts0;
        ++mv.qcount;
        ++mv.frames_decoded;
        pthread_cond_broadcast(&mv_cv);
        pthread_mutex_unlock(&mv_mu);
    }
    return NULL;
}

// ---------------- audio ----------------
// Fills dst with the audio for content frames [apos, apos+frames): real audio where the ring has it, silence where
// the decoder is late (those frames are then dropped from the ring when they arrive).
static void fill_audio(float *dst, uint32_t frames) {
    const uint64_t cap = (uint64_t)RATE * RING_SECONDS;
    pthread_mutex_lock(&mv_mu);
    if (mv.ring_r < mv.apos) {
        uint64_t to = mv.apos < mv.ring_w ? mv.apos : mv.ring_w;
        mv.audio_skipped += to - mv.ring_r;
        mv.ring_r = to;
    }
    uint32_t real = 0;
    if (mv.ring_r == mv.apos && mv.ring_w > mv.ring_r) {
        uint64_t avail = mv.ring_w - mv.ring_r;
        real = avail < frames ? (uint32_t)avail : frames;
        for (uint32_t i = 0; i < real; ++i) {
            const float *s = mv.ring + ((mv.ring_r + i) % cap) * 6;
            for (int ch = 0; ch < 6; ++ch) {
                dst[i * 6 + ch] = s[ch] * mv.gain;
                mv.sumsq[ch] += (double)s[ch] * s[ch];
            }
        }
        mv.ring_r += real;
    }
    memset(dst + (size_t)real * 6, 0, (size_t)(frames - real) * 6 * sizeof(float));
    mv.audio_real += real;
    if (!(mv.audio_eof && mv.ring_r >= mv.ring_w)) mv.audio_silence += frames - real;   // not counted after the end
    mv.apos += frames;
    pthread_cond_broadcast(&mv_cv);
    pthread_mutex_unlock(&mv_mu);
}
// Writes `bytes` of content at the buffer offset wpos.
static int write_ds(uint32_t bytes) {
    uint32_t o1, l1, o2, l2;
    if (ds_buffer_lock(mv.buf, mv.wpos, bytes, 0, &o1, &l1, &o2, &l2) < 0) return 0;
    fill_audio((float *)((uint8_t *)mv.dsmem + o1), l1 / 24);
    if (l2) fill_audio((float *)((uint8_t *)mv.dsmem + o2), l2 / 24);
    ds_buffer_unlock(mv.buf, o1, l1, o2, l2);
    mv.wpos = (mv.wpos + bytes) % mv.dsbytes;
    return 1;
}
static void *feeder_main(void *p) {
    (void)p;
    const uint32_t lead = LEAD_FRAMES * 24;
    for (;;) {
        pthread_mutex_lock(&mv_mu);
        int quit = mv.feed_quit;
        pthread_mutex_unlock(&mv_mu);
        if (quit) break;
        uint32_t play, write;
        if (ds_buffer_get_current_position(mv.buf, &play, &write) < 0) { __atomic_store_n(&mv.lost, 1, __ATOMIC_RELEASE); break; }
        uint32_t ahead = (mv.wpos + mv.dsbytes - write) % mv.dsbytes;
        if (ahead > mv.dsbytes / 2) { mv.wpos = write; ahead = 0; }          // fell behind the mixer
        if (ahead < lead && !write_ds(lead - ahead)) { __atomic_store_n(&mv.lost, 1, __ATOMIC_RELEASE); break; }
        struct timespec ts = {0, 5000000};
        nanosleep(&ts, NULL);
    }
    return NULL;
}
// The device mix since audio_start (the movie plus whatever Snd plays), for movies.log.
static void mix_stats(char *out, size_t cap) {
    DsStats st;
    if (!mv.dev || mv.dev != dsound_current_device() || ds_device_get_stats(mv.dev, &st) != 0) { snprintf(out, cap, "no device stats"); return; }
    snprintf(out, cap, "mixer %llu frames, %u buffers playing, RMS FL %.3f FR %.3f FC %.3f LFE %.3f BL %.3f BR %.3f",
             (unsigned long long)(st.frames_written - mv.mix_frames0), st.playing, st.rms[0], st.rms[1], st.rms[2], st.rms[3], st.rms[4], st.rms[5]);
}
static void audio_stop(void) {
    if (mv.feed_running) {
        pthread_mutex_lock(&mv_mu); mv.feed_quit = 1; pthread_cond_broadcast(&mv_cv); pthread_mutex_unlock(&mv_mu);
        pthread_join(mv.feed_thread, NULL);
        mv.feed_running = 0;
        mv.feed_quit = 0;
    }
    if (mv.buf) { ds_buffer_stop(mv.buf); ds_buffer_release(mv.buf); mv.buf = NULL; }
}
// A looping 6-channel float buffer on the game's current DirectSound device, prefilled and playing.
static void audio_start(void) {
    DsDevice *dev = dsound_current_device();
    if (!dev || !mv.has_audio) return;
    uint8_t wfx[40] = {0};
    uint16_t *w16 = (uint16_t *)wfx;
    w16[0] = 0xFFFE; w16[1] = 6;                                         // WAVE_FORMAT_EXTENSIBLE, 6 ch
    *(uint32_t *)(wfx + 4) = RATE; *(uint32_t *)(wfx + 8) = RATE * 24;
    w16[6] = 24; w16[7] = 32; w16[8] = 22; w16[9] = 32;                  // block, bits, cbSize, valid bits
    *(uint32_t *)(wfx + 20) = 0x3F;                                      // FL FR FC LFE BL BR
    static const uint8_t ieee_float[16] = {3, 0, 0, 0, 0, 0, 0x10, 0, 0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71};
    memcpy(wfx + 24, ieee_float, 16);
    mv.dsbytes = RATE * DS_SECONDS * 24;
    if (!mv.dsmem) mv.dsmem = calloc(1, mv.dsbytes);
    DsResult r = ds_device_create_buffer(dev, 0x8000 | 0x10000, mv.dsbytes, wfx, mv.dsmem, &mv.buf);   // GLOBALFOCUS | GETCURRENTPOSITION2
    if (r < 0) { mlog("%s: CreateSoundBuffer failed %08X: no movie audio", mv.name, (uint32_t)r); mv.buf = NULL; return; }
    DsStats st;
    if (ds_device_get_stats(dev, &st) == 0) mv.mix_frames0 = st.frames_written;   // also restarts its RMS window
    mv.dev = dev;
    mv.apos = (uint64_t)(now_s() * RATE);                                // (re)start on the video clock
    mv.wpos = 0;
    mv.lost = 0;
    write_ds(LEAD_FRAMES * 24);
    ds_buffer_play(mv.buf, 1);                                           // DSBPLAY_LOOPING
    pthread_create(&mv.feed_thread, NULL, feeder_main, NULL);
    mv.feed_running = 1;
}

// ---------------- the movie texture ----------------
static uint32_t pow2(uint32_t v) { uint32_t p = 1; while (p < v) p <<= 1; return p; }
static uint32_t shape_dims(Cpu *c, uint32_t shape, uint32_t *w, uint32_t *h) {
    c->ecx = shape;
    uint32_t blk = call_guest(c, FN_ShapeImage, 0, NULL);
    if (blk) { *w = M32(blk + 0x18); *h = M32(blk + 0x1C); }
    return blk;
}
// Has the game create (or keep) MovieTextureInfo and its D3D texture at w x h.
// The PC movies are a 4:3 picture squeezed into 1024x512 (16:9 content letterboxed in rows 64-447), which the front
// end stretches over a 4:3 quad. The WMVs are full-frame 16:9, so they go into a 4:3 texture (1280x960) with bars.
static int movie_texture(Cpu *c, uint32_t self, int w, int h) {
    uint32_t fake = scratch, sw = 0, sh = 0;
    memset(G + fake, 0, 0x40);                     // a frame Shape stand-in: one 32-bit image block with the size
    M8(fake) = SHAPE_IMAGE_32BIT;
    M32(fake + 0x18) = (uint32_t)w;
    M32(fake + 0x1C) = (uint32_t)h;
    if (shape_dims(c, fake, &sw, &sh) != fake) { mlog("%s: stand-in frame Shape not recognised", mv.name); return 0; }
    uint32_t shp = M32(MOVIE_SHAPE);
    if (shp && (!shape_dims(c, shp, &sw, &sh) || sw != (uint32_t)w || sh != (uint32_t)h)) {
        mlog("%s: movie texture was %ux%u, re-created at %dx%d", mv.name, sw, sh, w, h);
        call_guest(c, FN_MovieShapeFree, 0, NULL);
    }
    uint32_t a[3] = {MOVIE_TEXTURE_INFO, fake, M32(self + MP_FLAG_C) == 0};   // (the flag is unused there)
    call_guest(c, FN_PlatSetFirstMovieFrame, 3, a);
    shape_is_ours = 1;
    shp = M32(MOVIE_SHAPE);
    if (!shp || !shape_dims(c, shp, &sw, &sh) || sw != (uint32_t)w || sh != (uint32_t)h) {
        mlog("%s: movie Shape is %ux%u, not %dx%d", mv.name, sw, sh, w, h);
        return 0;
    }
    // RenderMovie: u = width / next_pow2(width). Report the power-of-two size so the quad covers the texture.
    M16(MOVIE_TEXTURE_INFO + 0x44) = (uint16_t)pow2((uint32_t)w);
    M16(MOVIE_TEXTURE_INFO + 0x46) = (uint16_t)pow2((uint32_t)h);
    uint32_t tex = M32(MOVIE_D3DTEX);
    mv.tex_w = mv.tex_h = 0;
    if (tex) {
        uint32_t d[3] = {tex, 0, scratch + 0x40};   // GetLevelDesc: Width at +0x18, Height at +0x1C
        if (call_guest(c, M32(M32(tex) + 0x44), 3, d) == 0) { mv.tex_w = M32(scratch + 0x40 + 0x18); mv.tex_h = M32(scratch + 0x40 + 0x1C); }
    }
    if (mv.tex_w != (uint32_t)w || mv.tex_h != (uint32_t)h) {
        mlog("%s: D3D movie texture is %ux%u, not %dx%d", mv.name, mv.tex_w, mv.tex_h, w, h);
        return 0;
    }
    return 1;
}
static void upload(Cpu *c, const uint8_t *bgra) {
    uint32_t tex = M32(MOVIE_D3DTEX), lr = scratch + 0x80;
    if (!tex) return;
    uint32_t vt = M32(tex), a[5] = {tex, 0, lr, 0, 0};
    if (call_guest(c, M32(vt + 0x4C), 5, a) != 0) return;           // LockRect
    uint32_t pitch = M32(lr), bits = M32(lr + 4);
    for (int y = 0; y < mv.th; ++y) {                               // texture row y shows picture row py
        int py = mv.flip ? mv.th - 1 - y : y, fy = py - mv.top;
        uint8_t *dst = G + bits + (uint32_t)y * pitch;
        if (fy >= 0 && fy < mv.h) memcpy(dst, bgra + (size_t)fy * mv.w * 4, (size_t)mv.w * 4);
        else for (int x = 0; x < mv.tw; ++x) M32(bits + (uint32_t)y * pitch + 4u * (uint32_t)x) = 0xFF000000u;   // bars
    }
    if (diag_on && !mv.png_saved && mv.frames_shown >= 90) {        // ~3 s in: what the texture holds, upright
        char path[1100];
        snprintf(path, sizeof path, "%s/movie_%s_%dx%d_in_%dx%d.png", diag_dir, mv.name, mv.w, mv.h, mv.tw, mv.th);
        uint8_t *img = malloc((size_t)mv.tw * mv.th * 4);
        for (int y = 0; y < mv.th; ++y)
            memcpy(img + (size_t)y * mv.tw * 4, G + bits + (uint32_t)(mv.flip ? mv.th - 1 - y : y) * pitch, (size_t)mv.tw * 4);
        diag_png(path, img, (uint32_t)mv.tw, (uint32_t)mv.th, (uint32_t)mv.tw * 4, 0);
        free(img);
        mv.png_saved = 1;
    }
    uint32_t shp = M32(MOVIE_SHAPE), off = shp ? M32(shp + 8) : 0;   // the Shape keeps a copy, as FillInTextureInfo does
    if (off && M32(MOVIE_TEXTURE_INFO + 0x40) == (uint32_t)(mv.tw * mv.th * 4))
        for (int y = 0; y < mv.th; ++y) memcpy(G + shp + off + (uint32_t)(y * mv.tw * 4), G + bits + (uint32_t)y * pitch, (size_t)mv.tw * 4);
    uint32_t u[2] = {tex, 0};
    call_guest(c, M32(vt + 0x50), 2, u);                             // UnlockRect
    ++mv.frames_shown;
}

// ---------------- open / update / close ----------------
static void movie_close(const char *why) {
    if (!mv.active) return;
    char mix[256];
    mix_stats(mix, sizeof mix);
    audio_stop();
    pthread_mutex_lock(&mv_mu); mv.quit = 1; pthread_cond_broadcast(&mv_cv); pthread_mutex_unlock(&mv_mu);
    if (mv.dec_running) pthread_join(mv.dec_thread, NULL);
    if (mv.aud_running) pthread_join(mv.aud_thread, NULL);
    double t = now_s(), rms[6];
    for (int ch = 0; ch < 6; ++ch) rms[ch] = mv.audio_real ? sqrt(mv.sumsq[ch] / (double)mv.audio_real) : 0;
    mlog("%s: closed (%s) after %.2f s: video %llu decoded, %llu shown, %llu dropped; audio %llu frames decoded, "
         "%llu played, %llu silence, %llu skipped, %llu restarts; RMS FL %.3f FR %.3f FC %.3f LFE %.3f BL %.3f BR %.3f; %s",
         mv.name, why, t, (unsigned long long)mv.frames_decoded, (unsigned long long)mv.frames_shown,
         (unsigned long long)mv.frames_dropped, (unsigned long long)mv.audio_decoded, (unsigned long long)mv.audio_real,
         (unsigned long long)mv.audio_silence, (unsigned long long)mv.audio_skipped, (unsigned long long)mv.ds_restarts,
         rms[0], rms[1], rms[2], rms[3], rms[4], rms[5], mix);
    media_movie_close(mv.dec);
    if (mv.adec) media_movie_close(mv.adec);
    for (int i = 0; i < QN; ++i) free(mv.q[i].bgra);
    free(mv.ring);
    free(mv.shown);
    free(mv.dsmem);
    memset(&mv, 0, sizeof mv);
}

static int host_play(Cpu *c, uint32_t self) {
    const char *pc = gptr(self + MP_FILENAME);
    char path[1024], why[1200];
    if (!find_wmv(pc, path, sizeof path, why, sizeof why)) { ++n_fallback; mlog("%s: VP6 fallback: %s", pc, why); return 0; }
    MediaMovie *m = media_movie_open_streams(path, 6, RATE, 1, 0);
    if (!m) { ++n_fallback; mlog("%s: VP6 fallback: FFmpeg can't open %s", pc, path); return 0; }
    int w = media_movie_width(m), h = media_movie_height(m);
    double pts = 0;
    const uint8_t *first = (w > 0 && h > 0 && w <= 2048 && h <= 2048) ? media_movie_next_frame(m, &w, &h, &pts) : NULL;
    if (!first) { media_movie_close(m); ++n_fallback; mlog("%s: VP6 fallback: no usable video in %s", pc, path); return 0; }
    if (!scratch) scratch = galloc(0x100);
    const char *base = strrchr(path, '/') ? strrchr(path, '/') + 1 : path;
    snprintf(mv.name, sizeof mv.name, "%.*s", (int)(strlen(base) > 4 ? strlen(base) - 4 : strlen(base)), base);
    snprintf(mv.path, sizeof mv.path, "%s", path);
    mv.dec = m;
    mv.w = w;
    mv.h = h;
    mv.tw = w;
    mv.th = h > w * 3 / 4 || widescreen_fe_scale() < 1.0f ? h : w * 3 / 4;   // wide screens: no bars (widescreen.c)
    mv.top = (mv.th - h) / 2;
    mv.flip = M32(self + MP_FLAG_C) == 0;                  // RenderMovie then runs v from the bottom of the texture
    mv.player = self;
    mv.adec = M8(self + MP_SOUND) ? media_movie_open_streams(path, 6, RATE, 0, 1) : NULL;
    mv.has_audio = mv.adec && media_movie_source_channels(mv.adec) > 0;
    int vol = (int)M32(self + MP_VOLUME);
    mv.gain = vol <= 0 ? 0.0f : vol >= 127 ? 1.0f : vol / 127.0f;
    mv.duration = media_movie_duration(m);
    mv.pts0 = pts;
    mv.active = 1;
    if (!movie_texture(c, self, mv.tw, mv.th)) {
        movie_close("texture");
        ++n_fallback;
        mlog("%s: VP6 fallback: movie texture", pc);
        return 0;
    }
    for (int i = 0; i < QN; ++i) mv.q[i].bgra = malloc((size_t)w * h * 4);
    mv.ring = calloc((size_t)RATE * RING_SECONDS * 6, sizeof(float));
    mv.shown = malloc((size_t)w * h * 4);
    memcpy(mv.shown, first, (size_t)w * h * 4);
    ++mv.frames_decoded;
    if (mv.has_audio) {                                    // half a second of audio before the clock starts
        while (mv.ring_w < RATE / 2) {
            int more = media_movie_pump_audio(mv.adec, 4096);
            if (!take_audio(4096) && !more) { mv.audio_eof = 1; break; }
        }
        if (!mv.audio_eof) { pthread_create(&mv.aud_thread, NULL, audio_main, NULL); mv.aud_running = 1; }
    }
    upload(c, mv.shown);
    pthread_create(&mv.dec_thread, NULL, decoder_main, NULL);
    mv.dec_running = 1;
    // What the original does once its first frame is up.
    M32(self + MP_AVPLAYER) = 0;
    M32(self + MP_FRAME) = 0;
    M32(self + MP_PLAYS) += 1;
    uint32_t sub = M32(CUR_SUBTITLER);
    if (sub) M32(sub + 0x18) = call_guest(c, FN_bGetTicker, 0, NULL);
    M32(self + MP_STATE) = 5;
    M32(self + MP_NEXT) = 5;
    mv.first_frame = diag_current_frame();
    mv.start_ns = host_ticks();
    audio_start();
    ++n_wmv;
    mlog("%s: playing %s (%s; %s; %.2f s, volume %d%s) into the %ux%u movie texture (rows %d-%d%s); WMV %u, VP6 fallback %u so far",
         pc, path, media_movie_describe(m), mv.adec ? media_movie_describe(mv.adec) : "no audio decoder", mv.duration, vol, mv.has_audio ? (mv.buf ? "" : ", no device") : ", no sound",
         mv.tex_w, mv.tex_h, mv.top, mv.top + h - 1, mv.flip ? ", bottom-up" : "", n_wmv, n_fallback);
    return 1;
}

static void movie_update(Cpu *c) {
    double t = now_s();
    int got = 0;
    pthread_mutex_lock(&mv_mu);
    while (mv.qcount && mv.q[mv.qhead].pts <= t) {        // newest frame that is due; older ones are dropped
        if (got) ++mv.frames_dropped;
        uint8_t *tmp = mv.shown;
        mv.shown = mv.q[mv.qhead].bgra;
        mv.q[mv.qhead].bgra = tmp;
        mv.qhead = (mv.qhead + 1) % QN;
        --mv.qcount;
        got = 1;
    }
    // Done when the video is out and the last audio has been handed to the mixer and played (lead + margin).
    int audio_done = !mv.buf || (mv.audio_eof && ((mv.ring_r >= mv.ring_w && t * RATE >= mv.ring_w + 2.0 * LEAD_FRAMES) || t * RATE > mv.ring_w + RATE));
    int finished = mv.eof && !mv.qcount && audio_done;
    pthread_cond_broadcast(&mv_cv);
    pthread_mutex_unlock(&mv_mu);
    if (got) upload(c, mv.shown);
    if (__atomic_load_n(&mv.lost, __ATOMIC_ACQUIRE)) {     // Snd re-created its DirectSound device
        audio_stop();
        ++mv.ds_restarts;
        audio_start();
    }
    if (finished || (mv.duration > 0 && t > mv.duration + 5)) {
        uint32_t self = mv.player;
        movie_close(finished ? "end" : "timeout");
        M32(self + MP_NEXT) = 0;                           // Update tears down and tells the front end
    }
}

// ---------------- the replaced game functions (recomp/hooks.txt) ----------------
static uint32_t playing_player;                            // the MoviePlayer between Play and Stop/destruction (WMV or VP6)
int movies_playing(void) { return playing_player != 0; }
float movies_picture_aspect(void) { return mv.active && mv.th ? (float)mv.tw / (float)mv.th : 0; }   // what the movie texture holds   // for scripted input (BOOT_KEYS skipmovies)
// bFileExists (cdecl, const char *): FEngMovieStarter::Callback (00559680) checks MOVIES\<name>_<language>_ntsc.vp6
// before it plays and otherwise ends the movie at once. A movie the X360 folder has counts as there, so a movie with
// no PC VP6 (eahd_bumper, the X360's EA HD bumper, bootflow.c) plays its WMV through Play below.
void f_0065F7A0(Cpu *c) {
    const char *p = gptr(arg(c, 0));
    f_0065F7A0_orig(c);
    size_t n = p ? strlen(p) : 0;
    if (c->eax || n < 12 || strncasecmp(p, "MOVIES\\", 7) || strcasecmp(p + n - 4, ".vp6") || M32(SKIP_MOVIES)) return;
    char path[1024], why[1200];
    if (find_wmv(p, path, sizeof path, why, sizeof why)) { c->eax = 1; mlog("%s: no VP6, the X360 WMV stands in", p); }
}
void f_00542A40(Cpu *c) {                                  // MoviePlayer::Play (thiscall)
    uint32_t self = c->ecx;
    playing_player = self;
    ds_set_movie_playing(1);                               // WMV and fallback VP6: keep movie PCM/encoding unchanged
    movie_close("next movie");
    if (!M32(SKIP_MOVIES) && host_play(c, self)) { c->esp += 4; return; }
    if (shape_is_ours && M32(MOVIE_SHAPE)) call_guest(c, FN_MovieShapeFree, 0, NULL);   // VP6 sizes it from its frames
    shape_is_ours = 0;
    c->ecx = self;                                         // host_play may have called guest code
    f_00542A40_orig(c);
}
void f_00525F70(Cpu *c) {                                  // MoviePlayer::UpdateFunction (thiscall)
    if (!mv.active || c->ecx != mv.player) { f_00525F70_orig(c); return; }
    if (!M32(UPDATE_RECURSE)) {
        M32(UPDATE_RECURSE) = 1;
        call_guest(c, FN_SYNCTASK_run, 0, NULL);           // as the original: background loading runs during movies
        uint32_t zero = 0;
        call_guest(c, FN_THREAD_yield, 1, &zero);
        if (mv.active) movie_update(c);
        M32(UPDATE_RECURSE) = 0;
    }
    c->esp += 4;
}
void f_00517610(Cpu *c) {                                  // MoviePlayer::Stop (thiscall)
    if (c->ecx == playing_player) playing_player = 0;
    if (mv.active && c->ecx == mv.player) movie_close("stopped");
    f_00517610_orig(c);
    ds_set_movie_playing(playing_player != 0);
}
void f_00525E60(Cpu *c) {                                  // MoviePlayer::~MoviePlayer (thiscall)
    if (c->ecx == playing_player) playing_player = 0;
    if (mv.active && c->ecx == mv.player) movie_close("player destroyed");
    f_00525E60_orig(c);                                    // frees MOVIE_SHAPE too
    ds_set_movie_playing(playing_player != 0);
    shape_is_ours = 0;
}
