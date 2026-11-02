// text_fit.c - see text_fit.h. Offline check: runtime/note_len_test.c.
#include <stdio.h>
#include <string.h>
#include "text_fit.h"

#define ELLIPSIS "\xE2\x80\xA6"

int text_append(char *buf, size_t cap, unsigned ch) {
    size_t n = strlen(buf);
    if (ch < 32 || ch >= 256 || ch == 127 || n + 1 >= cap) return 0;
    buf[n] = (char)ch; buf[n + 1] = 0;
    return 1;
}

static int boundary(const char *s, size_t i) { return ((unsigned char)s[i] & 0xC0) != 0x80; }

void text_window(const char *s, int tail, double maxw, TextMeasure measure, void *ctx, char *out, size_t n) {
    if (!n) return;
    size_t len = strlen(s);
    char line[2 * (TEXT_FIELD_MAX + 8)];
    if (len + 4 > sizeof line) len = sizeof line - 4;
    memcpy(line, s, len); line[len] = 0;
    if (measure(line, ctx) <= maxw) { snprintf(out, n, "%s", line); return; }
    // Search character counts, not byte counts: an invalid byte cut says nothing about larger windows.
    size_t cuts[sizeof line], count = 0;
    for (size_t i = 0; i <= len; ++i) if (boundary(line, i)) cuts[count++] = i;
    size_t lo = 0, hi = count - 1;              // kept characters (0 = just the ellipsis)
    while (lo < hi) {
        size_t mid = (lo + hi + 1) / 2, at = cuts[tail ? count - 1 - mid : mid];
        char t[sizeof line + 4];
        if (tail) snprintf(t, sizeof t, ELLIPSIS "%s", line + at);
        else { memcpy(t, line, at); strcpy(t + at, ELLIPSIS); }
        if (measure(t, ctx) <= maxw) lo = mid; else hi = mid - 1;
    }
    size_t at = cuts[tail ? count - 1 - lo : lo];
    if (tail) snprintf(out, n, ELLIPSIS "%s", line + at);
    else { char t[sizeof line + 4]; memcpy(t, line, at); strcpy(t + at, ELLIPSIS); snprintf(out, n, "%s", t); }
}

int text_delete_repeat(double *next, int held, double now) {
    if (!held) { *next = 0; return 0; }
    if (*next == 0) { *next = now + .4; return 1; }
    if (now >= *next) {
        int count = (int)((now - *next) / .05) + 1;
        *next += count * .05;
        return count;
    }
    return 0;
}

int text_edit_key(char *buf, unsigned vk, int cmd, int ctrl) {
    size_t n = strlen(buf);
    if ((vk == 0x08 && cmd) || (vk == 'U' && ctrl)) { buf[0] = 0; return n != 0; }
    if (vk == 0x08 && n) { buf[n - 1] = 0; return 1; }
    return 0;
}
