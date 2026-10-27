// render_query.m - occlusion queries on Metal visibility results (split from render.m; shared state in render_internal.h).
#include "render_internal.h"

// ---------------- occlusion queries ----------------
// D3DQUERYTYPE_OCCLUSION on Metal visibility results. The PC hides its sun this way: the sun's screen-space layers
// (FUN_006d04e0, SunTextures) scale their alpha by a visibility at 0x93E030 (FUN_006c0160, 006C024F), which
// FUN_006d0350 keeps from a ring of four occlusion queries (made by FUN_006c00d0): each frame a 32x32 quad at the
// sun's screen position, z 0.999, depth-tested against the scene, and the query from three frames back read with
// GetData(flags 0, 4 bytes), its return ignored (not ready reads as 0 pixels), as pixels / 1024, smoothed. The X360
// has the same object (LensFlare, 82440090). With every query answering "visible" the sun shone through tunnel and
// underpass ceilings.
// Every pass binds vis_buf; a query counts into a fresh 8-byte slot in each pass it spans (Metal writes a slot's
// count at the end of the pass and leaves the other slots alone), with that pass's sample count, since Metal counts
// samples and D3D9 games expect pixels. The counts are read once the frame's command buffer has completed.
static uint32_t vis_next;
static uint64_t stat_q_issued, stat_q_read, stat_q_hidden, stat_q_partial;
float q_bb[4];               // diag: the last counted fan's x/y range (the sun test quad's position)
void query_segment(void) {
    Obj *q = obj(q_active);
    if (!q || q->q_nseg >= 8) { [enc setVisibilityResultMode:MTLVisibilityResultModeDisabled offset:0]; return; }
    uint32_t s = vis_next++ % (VIS_SLOTS - 1024);   // the top 1024 slots are render.m's flare diag
    ((uint64_t *)vis_buf.contents)[s] = 0;
    q->q_slot[q->q_nseg] = s;
    q->q_samples[q->q_nseg++] = enc_samples;
    [enc setVisibilityResultMode:MTLVisibilityResultModeCounting offset:s * 8];
}
// Test only (NFS_TEST_OCCLUSION=ab, with BOOT_DIAG): same-camera before/after shots in one run. For 12 frames every
// query answers the old placeholder (1000 pixels, the sun always visible), then 12 frames the real counts, with a
// full-size shot at the end of each: once on the open road (race frame 1500, out of tunnels for 2 s) and 20 frames
// after entering each of the first 4 tunnels/underpasses (AmIinATunnel of the main view, 0x982BA4, set by 006D6820).
static int q_fake;
static void query_test_ab(void) {
    static int mode = -1, tunnels, open_done, out_frames;
    static uint64_t race_frame0, ab_start, tunnel_frames;
    if (mode < 0) { const char *e = getenv("NFS_TEST_OCCLUSION"); mode = e && !strcmp(e, "ab") && diag_on; }
    if (!mode) return;
    uint64_t f = dev.frames;
    if (!race_frame0) race_frame0 = f;
    int in = M32(0x00982BA4u) != 0;
    tunnel_frames = in ? tunnel_frames + 1 : 0;
    out_frames = in ? 0 : out_frames + 1;
    static int was_in;
    if (in != was_in) diag_event("render: test occlusion: in tunnel %d, frame %llu", in, (unsigned long long)f);
    was_in = in;
    if (!ab_start) {
        int open = !open_done && f >= race_frame0 + 1500 && out_frames >= 240;
        int tunnel = tunnels < 4 && tunnel_frames == 20;
        if (!open && !tunnel) return;
        if (open) open_done = 1; else ++tunnels;
        ab_start = f;
        diag_shot_request(f + 11); diag_shot_request(f + 23);
        diag_event("render: test occlusion: %s: frame %llu old answer (always visible), frame %llu real counts",
                   open ? "open road" : "tunnel", (unsigned long long)f + 11, (unsigned long long)f + 23);
    }
    q_fake = f < ab_start + 12;
    if (f >= ab_start + 23) ab_start = 0;
}
// Issue (D3DISSUE_END 1, D3DISSUE_BEGIN 2) of an occlusion query.
void render_query_issue(Obj *q, uint32_t flags) {
    if (!mtl) return;
    if (flags & 2) query_test_ab();
    Obj *a = obj(q_active);
    if (a && (flags & 2)) render_query_issue(a, 1);      // one query counts at a time (D3D9 doesn't nest them either)
    if (flags & 2) {
        q->q_state = 1; q->q_nseg = 0; q->q_cached = 0;
        q_active = q->guest;
        ++stat_q_issued;
        if (enc) query_segment();                        // else the next pass starts counting (begin_pass)
    } else if (flags & 1) {
        if (q->q_state != 1) { q->q_state = 2; q->q_nseg = 0; q->q_serial = frame_serial; return; }   // END alone: 0 pixels
        if (enc) [enc setVisibilityResultMode:MTLVisibilityResultModeDisabled offset:0];
        q->q_state = 2;
        q->q_serial = frame_serial;
        q_active = 0;
    }
}
// 1 with the visible pixels once the query's frame has finished on the GPU, 0 while it is still pending.
int render_query_result(Obj *q, uint32_t *pixels) {
    if (q->q_state != 2) return 0;
    if (q_fake) { *pixels = 1000; return 1; }
    if (q->q_cached) { *pixels = q->q_result; return 1; }
    if (q->q_serial >= frame_serial || in_flight(q->q_serial)) return 0;
    uint64_t n = 0;
    for (uint32_t i = 0; i < q->q_nseg; ++i) n += ((uint64_t *)vis_buf.contents)[q->q_slot[i]] / (q->q_samples[i] ? q->q_samples[i] : 1);
    q->q_result = n > 0xFFFFFFFFu ? 0xFFFFFFFFu : (uint32_t)n;
    q->q_cached = 1;
    ++stat_q_read;
    if (!q->q_result) ++stat_q_hidden;
    else if (q->q_result < 1024) ++stat_q_partial;
    // events.log: each change between hidden / partial / all 1024 pixels (the sun going behind something), capped
    static int last_class = -1, nlogged;
    int cls = !q->q_result ? 0 : q->q_result < 1024 ? 1 : 2;
    if (diag_on && cls != last_class && nlogged < 4000) {
        ++nlogged;
        diag_event("render: occlusion query %s (%u pixels), frame %llu, test quad x %.3f..%.3f y %.3f..%.3f",
                   cls == 0 ? "hidden" : cls == 1 ? "partial" : "visible", q->q_result, (unsigned long long)dev.frames,
                   q_bb[0], q_bb[2], q_bb[1], q_bb[3]);
    }
    last_class = cls;
    *pixels = q->q_result;
    return 1;
}
// events.log, every 300 frames: the query counts.
void query_stats_log(void) {
    if (diag_on && dev.frames % 300 == 0 && stat_q_issued)   // partial: under 1024 pixels, the sun test quad's 32x32
        diag_event("render: occlusion queries %llu issued, %llu read: %llu hidden, %llu partial; test quad x %.3f..%.3f y %.3f..%.3f",
                   (unsigned long long)stat_q_issued, (unsigned long long)stat_q_read, (unsigned long long)stat_q_hidden,
                   (unsigned long long)stat_q_partial, q_bb[0], q_bb[2], q_bb[1], q_bb[3]);
}
