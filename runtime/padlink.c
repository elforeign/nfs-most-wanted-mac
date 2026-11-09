// Controller link notes. Framework and HID identities cannot reliably be correlated:
// keep separate labeled trails rather than guessing from enumeration or product names.
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include "host.h"
void report_note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static uint64_t epoch;
static struct Link {
    void *id;
    uint64_t token, since, battery_at, sec;
    int transport, battery_state, low_noted, battery_noted;
    float battery;
    unsigned total[2], peak[2], in_sec[2], failed, failed_sec;
} links[2];
static const char *label(int i) { return i ? "rumble HID" : "pad"; }
static const char *transport_name(int t) { return t == 1 ? "Bluetooth" : t == 0 ? "USB" : "an unknown transport"; }
static const char *state_name(int s) { return s == 0 ? "discharging" : s == 1 ? "charging" : s == 2 ? "full" : "state unknown"; }
static void roll(struct Link *l, int i, uint64_t now, int force) {
    if (!l->sec || (!force && now - l->sec < 1000000000ull)) return;
    for (int k = 0; k < 2; ++k) if (l->in_sec[k] > l->peak[k]) l->peak[k] = l->in_sec[k];
    if (diag_on && (l->in_sec[0] || l->in_sec[1]))
        diag_event("padlink: %s second rumble %u (failed %u) fx %u", label(i), l->in_sec[0], l->failed_sec, l->in_sec[1]);
    memset(l->in_sec, 0, sizeof l->in_sec); l->failed_sec = 0; l->sec = 0;
}
static void close_link(int i, uint64_t now) { // mu: emit before replacing identity
    struct Link *l = &links[i];
    if (!l->id) return;
    roll(l, i, now, 1);
    char bat[96];
    if (l->battery_at) snprintf(bat, sizeof bat, "battery %d %% (%s, read %.1f s before)",
        (int)(l->battery * 100 + .5f), state_name(l->battery_state), (double)(now - l->battery_at) / 1e9);
    else snprintf(bat, sizeof bat, "battery not reported");
    report_note("%s: disconnected after %.1f s over %s, %s", label(i), (double)(now - l->since) / 1e9, transport_name(l->transport), bat);
    if (!diag_on) report_note("%s: outputs not sampled (BOOT_DIAG off)", label(i));
    else if (i) report_note("rumble HID: sent: %u reports (peak %u/s, %u failed)", l->total[0], l->peak[0], l->failed);
    else report_note("pad: sent: framework %u (peak %u/s); HID outputs logged separately", l->total[1], l->peak[1]);
    memset(l, 0, sizeof *l); l->transport = -1;
}
static void select_link(int i, void *id, const char *what, int transport) {
    pthread_mutex_lock(&mu);
    struct Link *l = &links[i];
    if (l->id != id) {
        uint64_t now = host_ticks(); close_link(i, now);
        if (id) {
            *l = (struct Link){.id=id, .token=++epoch, .since=now, .transport=transport, .battery=-1, .battery_state=-1};
            report_note("%s: connected: %s over %s, battery not reported", label(i), what, transport_name(transport));
        }
    }
    pthread_mutex_unlock(&mu);
}
void padlink_select(void *id, const char *what) { select_link(0, id, what ? what : "controller", -1); }
void padlink_hid(void *id, int transport) { select_link(1, id, "DualSense output device", transport); }
void padlink_detach(void *id) {
    pthread_mutex_lock(&mu);
    for (int i = 0; i < 2; ++i) if (links[i].id == id) close_link(i, host_ticks());
    pthread_mutex_unlock(&mu);
}
uint64_t padlink_token(void *id) {
    if (!diag_on || !id) return 0;
    uint64_t token = 0;
    pthread_mutex_lock(&mu);
    for (int i = 0; i < 2; ++i) if (links[i].id == id) token = links[i].token;
    pthread_mutex_unlock(&mu);
    return token;
}
void padlink_output(uint64_t token, int kind, int ok) {
    if (!diag_on) return;
    if (!token || kind < 0 || kind > 1) return;
    pthread_mutex_lock(&mu);
    for (int i = 0; i < 2; ++i) {
        struct Link *l = &links[i];
        if (!l->id || l->token != token) continue;
        uint64_t now = host_ticks(); roll(l, i, now, 0);
        if (!l->sec) l->sec = now;
        ++l->total[kind]; ++l->in_sec[kind];
        if (!ok) { ++l->failed; ++l->failed_sec; }
    }
    pthread_mutex_unlock(&mu);
}
void padlink_battery(void *id, float level, int state) {
    if (!(level >= 0 && level <= 1) || state < 0 || state > 2) return;
    pthread_mutex_lock(&mu);
    struct Link *l = &links[0];
    if (id && l->id == id) {
        l->battery = level; l->battery_state = state; l->battery_at = host_ticks();
        int low = level <= .10f && state == 0 && !l->low_noted;
        if (low) { l->low_noted = 1; report_note("pad: battery low: %d %% (%s)", (int)(level*100+.5f), state_name(state)); }
        else if (!l->battery_noted) report_note("pad: battery %d %% (%s)", (int)(level*100+.5f), state_name(state));
        l->battery_noted = 1;
    }
    pthread_mutex_unlock(&mu);
}
void padlink_idle(int force) {
    if (!diag_on) return;
    pthread_mutex_lock(&mu);
    uint64_t now = host_ticks();
    for (int i = 0; i < 2; ++i) roll(&links[i], i, now, force);
    pthread_mutex_unlock(&mu);
}
