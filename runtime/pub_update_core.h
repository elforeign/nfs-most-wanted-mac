/* pub_update_core.h - the PUBLIC build's "Check for updates" logic, free of the OS so a test can drive it (pub_update.m
 * is the macOS side: one HTTPS request, the bundle version, opening the page). Ported from nfs-most-wanted-mac's
 * kit/host/update_check.cpp (MIT; Copyright (c) 2026 veritr1x, the permission notice is in NOTICE and must be kept with
 * this file). Opt-in: nothing goes online until the player presses the button. The
 * first press asks GitHub for the latest release; if it is newer than this app (CFBundleShortVersionString), the
 * second press opens its page in the browser. */
#ifndef PUB_UPDATE_CORE_H
#define PUB_UPDATE_CORE_H
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { PU_IDLE, PU_CHECKING, PU_UP_TO_DATE, PU_NEWER, PU_FAILED, PU_NOVERSION };
static int pu_state = PU_IDLE;
static char pu_latest[64], pu_current[64], pu_url[512];

/* "v3.0.10" vs "3.0.9": numeric parts left to right, a missing part is 0. A "-beta" style suffix ranks below the same
 * number without one ("4.0.0" > "4.0.0-rc1"); "+build" is ignored. Returns <0, 0, >0. */
static int pu_compare_versions(const char *a, const char *b) {
    if (*a == 'v' || *a == 'V') ++a;
    if (*b == 'v' || *b == 'V') ++b;
    for (;;) {
        char *ea, *eb;
        const long x = strtol(a, &ea, 10), y = strtol(b, &eb, 10);
        if (x != y) return x < y ? -1 : 1;
        a = ea; b = eb;
        const int da = *a == '.', db = *b == '.';
        if (da) ++a;
        if (db) ++b;
        if (!da && !db) break;   /* both numeric runs ended (a missing part compares as 0 while the other continues) */
        if (!da) { if (!(*b >= '0' && *b <= '9')) break; a = "0"; }
        if (!db) { if (!(*a >= '0' && *a <= '9')) break; b = "0"; }
    }
    const int pa = *a == '-', pb = *b == '-';   /* a pre-release suffix */
    if (pa != pb) return pa ? -1 : 1;
    return 0;
}

/* The top-level string value of `key` in a JSON object (nested objects such as "author" are skipped, so a release's
 * own "html_url" is not mistaken for its author's). 1 and `out` filled, or 0. */
static int pu_json_top_string(const char *json, const char *key, char *out, size_t cap) {
    int depth = 0;
    const size_t klen = strlen(key);
    for (const char *p = json; *p; ++p) {
        if (*p == '{' || *p == '[') { ++depth; continue; }
        if (*p == '}' || *p == ']') { --depth; continue; }
        if (*p != '"') continue;
        const char *s = ++p;   /* a string: find its end */
        while (*p && *p != '"') { if (*p == '\\' && p[1]) ++p; ++p; }
        if (!*p) return 0;
        const char *e = p, *q = p + 1;
        while (*q == ' ' || *q == '\n' || *q == '\t' || *q == '\r') ++q;
        if (depth != 1 || *q != ':' || (size_t)(e - s) != klen || strncmp(s, key, klen)) continue;
        ++q;
        while (*q == ' ' || *q == '\n' || *q == '\t' || *q == '\r') ++q;
        if (*q != '"') return 0;
        size_t n = 0;
        for (++q; *q && *q != '"'; ++q) {
            if (*q == '\\' && q[1]) ++q;
            if (n + 1 < cap) out[n++] = *q;
        }
        out[n] = 0;
        return *q == '"';
    }
    return 0;
}

/* The response arrived: `ok` 0 is a network failure or a non-200 answer; `body` is GitHub's release JSON. */
static void pu_done(int ok, const char *body) {
    char tag[64] = "";
    if (!ok || !body || !pu_json_top_string(body, "tag_name", tag, sizeof tag) || !tag[0]) { pu_state = PU_FAILED; return; }
    snprintf(pu_latest, sizeof pu_latest, "%s", tag[0] == 'v' || tag[0] == 'V' ? tag + 1 : tag);
    pu_url[0] = 0;
    pu_json_top_string(body, "html_url", pu_url, sizeof pu_url);
    pu_state = pu_compare_versions(pu_latest, pu_current) > 0 ? PU_NEWER : PU_UP_TO_DATE;
}

/* The button. `version` reads the app's version (0: none); `fetch` starts the request and returns 0 if it cannot;
 * `open` opens a page (https only). A press while checking does nothing. */
static void pu_press(int (*version)(char *, size_t), int (*fetch)(void), int (*open)(const char *)) {
    if (pu_state == PU_CHECKING) return;
    if (pu_state == PU_NEWER && !strncmp(pu_url, "https://", 8)) { open(pu_url); return; }
    pu_current[0] = 0;
    if (!version(pu_current, sizeof pu_current) || !pu_current[0]) { pu_state = PU_NOVERSION; return; }
    pu_state = PU_CHECKING;
    if (!fetch()) pu_state = PU_FAILED;
}

static void pu_status(char *text, int n) {
    switch (pu_state) {
    case PU_IDLE: snprintf(text, (size_t)n, "Press to check"); break;
    case PU_CHECKING: snprintf(text, (size_t)n, "Checking..."); break;
    case PU_UP_TO_DATE: snprintf(text, (size_t)n, "Up to date (%s)", pu_current); break;
    case PU_NEWER: snprintf(text, (size_t)n, "%s is out: press to open", pu_latest); break;
    case PU_NOVERSION: snprintf(text, (size_t)n, "This build has no version"); break;
    default: snprintf(text, (size_t)n, "No answer: press to retry"); break;
    }
}
#endif
