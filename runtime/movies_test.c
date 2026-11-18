// Production feeder recovery across the DirectSound buffer wrap. A 4410-frame
// overtake leaves 4410 frames of A/V offset on the old feeder and 0 on the fixed feeder.
#include "movies.c"

static float output[LEAD_FRAMES * 6];
static const uint32_t mixer_write = (RATE / 20) * 24;

DsResult ds_buffer_get_current_position(DsBuffer *buf, uint32_t *play, uint32_t *write) {
    (void)buf;
    *play = *write = mixer_write;
    return DS_OK;
}

DsResult ds_buffer_lock(DsBuffer *buf, uint32_t offset, uint32_t bytes, uint32_t flags,
                        uint32_t *o1, uint32_t *l1, uint32_t *o2, uint32_t *l2) {
    (void)buf;
    (void)flags;
    if (offset != mixer_write || bytes != sizeof output) return DSERR_INVALIDPARAM;
    *o1 = *o2 = *l2 = 0;
    *l1 = bytes;
    return DS_OK;
}

DsResult ds_buffer_unlock(DsBuffer *buf, uint32_t o1, uint32_t l1, uint32_t o2, uint32_t l2) {
    (void)buf;
    (void)o1;
    (void)l1;
    (void)o2;
    (void)l2;
    mv.feed_quit = 1;
    return DS_OK;
}

int main(void) {
    const uint64_t content_at_write = RATE + RATE / 20;
    mv.apos = mv.ring_r = RATE - RATE / 20;
    mv.wpos = (uint32_t)mv.apos * 24;
    mv.dsbytes = RATE * DS_SECONDS * 24;
    mv.dsmem = output;
    mv.gain = 1.0f;
    mv.ring_w = content_at_write + LEAD_FRAMES;
    mv.ring = calloc(RATE * RING_SECONDS * 6, sizeof *mv.ring);
    if (!mv.ring) return 2;
    mv.ring[content_at_write * 6] = 0.5f;
    feeder_main(NULL);
    int64_t offset = (int64_t)(content_at_write + LEAD_FRAMES) - (int64_t)mv.apos;
    printf("movie feeder offset=%lld frames, skipped=%llu, first sample=%.1f\n",
           (long long)offset, (unsigned long long)mv.audio_skipped, output[0]);
    free(mv.ring);
    return mv.lost || offset != 0 || output[0] != 0.5f;
}
