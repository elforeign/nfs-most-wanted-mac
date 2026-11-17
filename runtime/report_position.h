// Capture on the calling thread; the bundle writer only sees formatted text.
#include <math.h>

#if NFS_GAME == NFS_GAME_MW
int diag_car_state(float pos[3], float *speed);
static int report_player_position(float pos[3], float forward[3], char track[64]) {
    float speed;
    if (!diag_car_state(pos, &speed)) return 0;
    uint32_t state = M32(M32(0x009142D0u) + 0x34);
    // 004D5C80 reads forward at +10 (also used by the engine panner); translation is +40.
    memcpy(forward, G + state + 0x10, 3 * sizeof(float));
    // LoadedTrackInfo (00722400); the native stream/track identifier is +40.
    uint32_t info = M32(0x009A3A20u);
    if (info) snprintf(track, 64, "%.63s", G + info + 0x40);
    return 1;
}
#else
int private_report_position(float pos[3], float forward[3], char track[64]);
#define report_player_position private_report_position
#endif

static void report_position(FILE *f, uint32_t flow) {
    float pos[3], forward[3];
    char track[64] = "";
    if (flow != 6) {
        fprintf(f, "position:   not in a race or free roam\n");
        return;
    }
    int have = report_player_position(pos, forward, track);
    for (int i = 0; have && i < 3; ++i)
        have = isfinite(pos[i]) && isfinite(forward[i]);
    if (!have) {
        fprintf(f, "position:   unavailable\n");
        return;
    }
    fprintf(f, "position:   x=%.6f y=%.6f z=%.6f; heading (forward)=%.6f,%.6f,%.6f%s%s\n",
            pos[0], pos[1], pos[2], forward[0], forward[1], forward[2],
            track[0] ? "; track=" : "", track);
}
