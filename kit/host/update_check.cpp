// update_check.cpp - Settings > Mods > Help > Check for updates (NFSMW 3.0.4; the idea is SpeedBreaker's "Check for
// Updates", github.com/SpeedBreakerProject/speedbreaker, no code taken). Opt-in: nothing goes online until the
// player presses it, and then one request asks GitHub for the latest release of the game's releases_repo (game.toml
// [launcher]). A newer one is shown as "X is out: press to open", and a second press opens its page in the browser.
#include "../runtime/help_seam.h"
#include "game_config.h"

#include <atomic>
#include <mutex>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>

#ifndef RECOMP_RELEASES_REPO
#define RECOMP_RELEASES_REPO ""
#endif

// host/sdl/os_update_macos.mm; elsewhere these weak defaults say no (the row then reports no answer).
extern "C" __attribute__((weak)) int os_update_fetch_latest(const char *, void (*)(int, const char *, const char *)) {
    return 0;
}
extern "C" __attribute__((weak)) int os_app_version(char *out, size_t cap) {
    if (out && cap)
        out[0] = 0;
    return 0;
}
extern "C" __attribute__((weak)) int os_open_url(const char *) {
    return 0;
}

namespace {
enum State { IDLE, CHECKING, UP_TO_DATE, NEWER, FAILED };
std::mutex g_mutex;
State g_state = IDLE;
std::string g_latest, g_url, g_current;

// "v3.0.10" vs "3.0.9": numeric parts left to right; a missing part is 0.
int compare_versions(const char *a, const char *b) {
    if (*a == 'v' || *a == 'V')
        ++a;
    if (*b == 'v' || *b == 'V')
        ++b;
    while (*a || *b) {
        const long x = strtol(a, (char **)&a, 10), y = strtol(b, (char **)&b, 10);
        if (x != y)
            return x < y ? -1 : 1;
        while (*a && *a != '.')
            ++a;
        while (*b && *b != '.')
            ++b;
        if (*a == '.')
            ++a;
        if (*b == '.')
            ++b;
    }
    return 0;
}

void fetched(int ok, const char *tag, const char *url) {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (!ok || !tag || !*tag) {
        g_state = FAILED;
        printf("[host] update check: no answer from GitHub\n");
        fflush(stdout);
        return;
    }
    g_latest = tag[0] == 'v' || tag[0] == 'V' ? tag + 1 : tag;
    g_url = url ? url : "";
    g_state = compare_versions(g_latest.c_str(), g_current.c_str()) > 0 ? NEWER : UP_TO_DATE;
    printf("[host] update check: latest %s, this app %s\n", g_latest.c_str(), g_current.c_str());
    fflush(stdout);
}
} // namespace

extern "C" int host_update_available(void) {
    return RECOMP_RELEASES_REPO[0] != 0;
}

extern "C" void host_update_activate(void) {
    if (!host_update_available())
        return;
    std::string open;
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (g_state == CHECKING)
            return;
        if (g_state == NEWER && !g_url.empty()) {
            open = g_url;
        } else {
            char v[64] = "";
            os_app_version(v, sizeof v);
            g_current = v[0] ? v : "0";
            g_state = CHECKING;
        }
    }
    if (!open.empty()) {
        os_open_url(open.c_str());
        return;
    }
    if (!os_update_fetch_latest(RECOMP_RELEASES_REPO, fetched)) {
        std::lock_guard<std::mutex> lock(g_mutex);
        g_state = FAILED;
    }
}

extern "C" void host_update_status(char *text, size_t cap) {
    if (!text || !cap)
        return;
    std::lock_guard<std::mutex> lock(g_mutex);
    switch (g_state) {
    case IDLE:
        snprintf(text, cap, "Ask");
        break;
    case CHECKING:
        snprintf(text, cap, "Checking...");
        break;
    case UP_TO_DATE:
        snprintf(text, cap, "Up to date (%s)", g_current.c_str());
        break;
    case NEWER:
        snprintf(text, cap, "%s is out: press to open", g_latest.c_str());
        break;
    case FAILED:
        snprintf(text, cap, "No answer: press to retry");
        break;
    }
}
