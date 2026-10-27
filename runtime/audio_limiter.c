// Snd's final float -> int16 conversion, BEFORE DirectSound. Float planes use 16-bit sample units.
// 008233BC calls 00823104 for <=256 frames; 0082333F..00823395 calls all six master converters in order,
// then 00825C0B interleaves their int16 outputs. Capture the six planes after their filter chains, limit
// together, and replace the six outputs before that interleave. Off/movie/unrelated converters call original.
#include "host.h"
#include "../host/audio/dsound_host.h"
#include <math.h>

extern DsDevice *dsound_current_device(void);
void f_00825957_orig(Cpu *c);

// This is the original 0082598A..008259C1 rounding/saturation, including the biased source write.
static uint16_t pcm16(float sample, float *biased) {
    float v = sample + 12582912.0f;
    uint32_t bits;
    memcpy(&bits, &v, sizeof bits);
    bits &= 0xfffff;
    if (bits > 0x7fff && bits < 0xf8000) bits = bits < 0x80000 ? 0x7fff : 0xffff8000;
    if (biased) *biased = v;
    return (uint16_t)bits;
}

// BOOT_DIAG: a NaN/Inf in a master plane converts to exact 0 here (pcm16: NaN's low mantissa bits are 0), so a
// non-finite value that sticks in a bus filter's state silences that bus with no clip or limiter trace. Log the
// first one per plane and a once-a-second count (`mixnan:`).
static void nan_check(unsigned ch, uint32_t obj, float sample, uint32_t i) {
    static uint64_t count[6], first_logged[6], last_log;
    if (isfinite(sample)) return;
    ++count[ch];
    if (!first_logged[ch]) {
        first_logged[ch] = 1;
        uint32_t child = M32(obj + 8);
        diag_event("mixnan: first non-finite sample on master plane %u (obj %08X, filter chain %08X vt %08X) frame %u: %f",
                   ch, obj, child, child ? M32(child) : 0, i, sample);
    }
    uint64_t now = host_ticks();
    if (now - last_log >= 1000000000ull) {
        last_log = now;
        diag_event("mixnan: non-finite samples so far per plane %llu %llu %llu %llu %llu %llu",
                   (unsigned long long)count[0], (unsigned long long)count[1], (unsigned long long)count[2],
                   (unsigned long long)count[3], (unsigned long long)count[4], (unsigned long long)count[5]);
    }
}

// BOOT_DIAG mixer summary: per master plane the energy before Snd's filter chain (pre), after it (post) and after the
// peak limiter (out), so a silence shows where the signal stops. Once a second `mixdiag:`; `mixsilent:` once when the
// post-chain mix stays below 1e-6 RMS for over 1 s in game (game-flow 6) while the pre-chain mix is not silent, or
// when the whole chain is silent in game for over 1 s (then the voices/banks are the suspects: see x360abk lines).
static struct { double pre[6], post[6], out[6]; uint64_t n[6], nonfinite_pre[6]; uint64_t since; int silent_logged; uint64_t quiet_since; } md;
static void mixdiag_tick(uint32_t obj0) {
    uint64_t now = host_ticks();
    if (!md.since) md.since = now;
    if (now - md.since < 1000000000ull) return;
    double pre[6], post[6], out[6], spre = 0, spost = 0;
    for (int k = 0; k < 6; ++k) {
        double n = md.n[k] ? (double)md.n[k] : 1;
        pre[k] = sqrt(md.pre[k] / n); post[k] = sqrt(md.post[k] / n); out[k] = sqrt(md.out[k] / n);
        spre += pre[k]; spost += post[k];
    }
    uint32_t flow = M32(0x00925E90u);
    diag_event("mixdiag: flow %u pre %.4f %.4f %.4f %.4f %.4f %.4f post %.4f %.4f %.4f %.4f %.4f %.4f out %.4f %.4f %.4f %.4f %.4f %.4f "
               "nonfinite_pre %llu chain %08X", flow, pre[0], pre[1], pre[2], pre[3], pre[4], pre[5], post[0], post[1], post[2],
               post[3], post[4], post[5], out[0], out[1], out[2], out[3], out[4], out[5],
               (unsigned long long)(md.nonfinite_pre[0] + md.nonfinite_pre[1] + md.nonfinite_pre[2] + md.nonfinite_pre[3] +
                                    md.nonfinite_pre[4] + md.nonfinite_pre[5]), M32(obj0 + 8));
    if (flow == 6 && spost < 6e-6) {
        if (!md.quiet_since) md.quiet_since = now;
        else if (!md.silent_logged && now - md.quiet_since > 1000000000ull) {
            md.silent_logged = 1;
            diag_event("mixsilent: game mix silent after the filter chain for >1 s in game (pre-chain sum %.6f: %s); master obj %08X "
                       "words %08X %08X %08X %08X %08X %08X %08X %08X", spre,
                       spre > 6e-6 ? "signal reaches the master planes, the chain/bus gain drops it" : "nothing reaches the master planes (voices)",
                       obj0, M32(obj0), M32(obj0 + 4), M32(obj0 + 8), M32(obj0 + 12), M32(obj0 + 16), M32(obj0 + 20), M32(obj0 + 24), M32(obj0 + 28));
        }
    } else { md.quiet_since = 0; if (flow != 6) md.silent_logged = 0; }
    memset(md.pre, 0, sizeof md.pre); memset(md.post, 0, sizeof md.post); memset(md.out, 0, sizeof md.out);
    memset(md.n, 0, sizeof md.n); memset(md.nonfinite_pre, 0, sizeof md.nonfinite_pre);
    md.since = now;
}

void f_00825957(Cpu *c) {
    enum { MASTER = 0x009C2478, STRIDE = 0x70, MAX_FRAMES = 256 };
    static _Thread_local struct {
        float pcm[MAX_FRAMES * 6];
        uint32_t dst[6], frames, mask;
        DsDevice *dev;
    } block;
    uint32_t obj = arg(c, 0), frames = arg(c, 1), src = arg(c, 2), dst = arg(c, 3);
    DsDevice *dev = dsound_current_device();
    if (!ds_peak_limiter_enabled() || !dev || M8(0x009C2319) != 6 || obj < MASTER ||
        obj >= MASTER + 6 * STRIDE || (obj - MASTER) % STRIDE || !frames || frames > MAX_FRAMES) {
        if (!ds_peak_limiter_enabled()) block.mask = 0;
        // BOOT_DIAG: the limiter path is off (e.g. NFS_AUDIO=null has no device), so measure the master planes around
        // the original converter instead: pre = float plane before the filter chain, post = its int16 output.
        int master = obj >= MASTER && obj < MASTER + 6 * STRIDE && !((obj - MASTER) % STRIDE) && frames && frames <= MAX_FRAMES;
        if (diag_on) {
            static int declined_logged;
            if (!declined_logged && master) {
                declined_logged = 1;
                diag_event("mixdiag: limiter path declined (limiter %d, device %p, channels %u, frames %u): measuring around the original converter",
                           ds_peak_limiter_enabled(), (void *)dev, M8(0x009C2319), frames);
            }
        }
        if (!diag_on || !master) { f_00825957_orig(c); return; }
        unsigned ch = (obj - MASTER) / STRIDE;
        for (uint32_t i = 0; i < frames; ++i) {
            float v = MF32(src + 4 * i);
            nan_check(ch, obj, v, i);
            if (!isfinite(v)) { md.nonfinite_pre[ch]++; continue; }
            v *= 1.0f / 32768.0f;
            md.pre[ch] += (double)v * v;
        }
        f_00825957_orig(c);
        for (uint32_t i = 0; i < frames; ++i) {
            double v = (int16_t)M16(dst + 2 * i) * (1.0 / 32768.0);
            md.post[ch] += v * v; md.out[ch] += v * v;
        }
        md.n[ch] += frames;
        if (ch == 5) mixdiag_tick(MASTER);
        return;
    }
    unsigned ch = (obj - MASTER) / STRIDE;
    if (!ch) { block.mask = 0; block.frames = frames; block.dev = dev; }
    // Preserve the optional filter-chain call from the original converter (dst/src are swapped).
    uint32_t child = M32(obj + 8);
    if (diag_on) for (uint32_t i = 0; i < frames; ++i) {
        float v = MF32(src + 4 * i) * (1.0f / 32768.0f);
        if (!isfinite(v)) { md.nonfinite_pre[ch]++; continue; }
        md.pre[ch] += (double)v * v;
    }
    if (child) {
        uint32_t args[] = {child, frames, dst, src, M8(obj + 0x1a)};
        int32_t got = (int32_t)call_guest(c, M32(child), 5, args);
        if (got <= 0) { block.mask = 0; c->eax = got; c->esp += 4; return; }
    }
    // Keep the original writes until a complete six-plane group exists: if a filter fails or a
    // non-master converter is interposed, the original output remains valid instead of stale data.
    for (uint32_t i = 0; i < frames; ++i) {
        float sample = MF32(src + 4 * i), biased;
        if (diag_on) { nan_check(ch, obj, sample, i); if (isfinite(sample)) md.post[ch] += (double)sample * sample * (1.0 / (32768.0 * 32768.0)); md.n[ch]++; }
        block.pcm[i * 6 + ch] = sample * (1.0f / 32768.0f);
        M16(dst + 2 * i) = pcm16(sample, &biased);
        MF32(src + 4 * i) = biased;
    }
    if (block.frames == frames && block.dev == dev && block.mask == (1u << ch) - 1) {
        block.dst[ch] = dst;
        block.mask |= 1u << ch;
        if (ch == 5) {
            ds_device_limit_game_mix(dev, block.pcm, frames, M16(0x009C2046));
            if (diag_on) {
                for (uint32_t i = 0; i < frames; ++i) for (unsigned k = 0; k < 6; ++k) {
                    float v = block.pcm[i * 6 + k];
                    if (isfinite(v)) md.out[k] += (double)v * v;
                }
                mixdiag_tick(MASTER);
            }
            for (uint32_t i = 0; i < frames; ++i) for (unsigned k = 0; k < 6; ++k)
                M16(block.dst[k] + 2 * i) = pcm16(block.pcm[i * 6 + k] * 32768.0f, NULL);
            block.mask = 0;
        }
    } else block.mask = 0;
    c->eax = frames;
    c->esp += 4; // cdecl; all six results are consumed only after the caller's conversion loop
}
