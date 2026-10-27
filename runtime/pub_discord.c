// pub_discord.c - the PUBLIC build's Discord status row and per-frame hand-over (NFS_PUBLIC only; see pub_discord.h).
// diag_frame (diag.c) calls pub_discord_tick once a frame with the game flow; the worker thread does the socket work.
#include "pub_discord.h"
#include "settings.h"

static int discord_get_flow;   // last flow the tick saw, for the log

static const char *const kOffOnD[] = {"Off", "On"}, *const kOffOnDV[] = {"off", "on"};
static Setting s_discord = {.id = "online.discord_status", .category = SET_DISPLAY, .order = 62, .label = "Discord status",
    .help = "Shows on your Discord profile that you are playing, driving or in the menus, and your time played, while the Discord app is open. Off shares nothing and clears it at once.",
    .type = SET_TOGGLE, .choices = kOffOnD, .values = kOffOnDV, .nchoices = 2, .def = 0, .pref = "discord_status", .own_pref = 1};
static Setting h_online = {.id = "online.h_online", .category = SET_DISPLAY, .order = 60, .label = "Online", .type = SET_HEADING,
    .help = "Optional: nothing here talks to anything until you switch it on or press it."};

                                                                                                            
void pub_discord_tick(uint32_t flow) {
    static int failed;
    if (failed) return;
    discord_get_flow = (int)flow;
    const int want = settings_get("online.discord_status") != 1 ? DS_OFF : flow == 6u ? DS_DRIVING : DS_MENUS;
    char line[240];
    if (ds_set_want(want, line, sizeof line)) {
        fprintf(stderr, "boot: Discord status: %s\n", line);
        if (!g_ds_started && want != DS_OFF) failed = 1;
    }
}

void pub_discord_stop(void) { discord_status_stop(); }   // never waits on Discord

__attribute__((constructor)) static void pub_discord_register(void) {
    settings_add(&h_online);
    settings_add(&s_discord);
    atexit(pub_discord_stop);
}
