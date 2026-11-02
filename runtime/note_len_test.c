// note_len_test.c - offline check that an F10 text row (Help > Report a problem > Note) holds 256 characters and shows the caret:
//   clang -std=c11 -Wall -Wextra -Werror -g -fsanitize=address,undefined -I recomp runtime/note_len_test.c runtime/text_fit.c -o /tmp/nlt && /tmp/nlt
// Player report: typing stopped at 95 characters (the row's buffer was 96 bytes) and the end of a long line ran off the row.
// Checks: 240 typed characters are all stored (the same text_append the menu's character handler calls) and 300 stop at 256; the
// report writer prints the stored note unchanged (pinned fragment of report.m); the visible window of a long line being typed is
// its end with the caret last and fits the row; an idle long line shows its start and an ellipsis; short lines and UTF-8 stay whole.
// Result: PASS 165 checks, 0 failures. Broken run (-DNOTE_TEST_BROKEN: the old 96-byte buffer and no scrolling window,
// i.e. the original behaviour): FAIL 79 failures of 165 (the clear-all and repeat checks cover new helpers, so the old behaviour has none).
// Review proof: original UTF-8 window/repeat code 175 checks/10 failures; fixed 175/0.
// 127 two-byte characters: 6/6 window failures -> 6/0. Five-second holds at 10/30/60 Hz:
// original 48/71/75 deletes, fixed 94/93/93 (four assertions: 4 failures -> 0).
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "settings.h"
#include "text_fit.h"

static int checks, fails;
#define CHECK(x) do { ++checks; if (!(x)) { ++fails; printf("FAIL line %d: %s\n", __LINE__, #x); } } while (0)

// 10 px per character; the ellipsis is one character (3 bytes)
static double measure(const char *s, void *ctx) {
    (void)ctx; double w = 0;
    for (; *s; ++s) if (((unsigned char)*s & 0xC0) != 0x80) w += 10;
    return w;
}
static int utf8_ok(const char *s) {
    for (; *s; ++s) {
        unsigned char c = (unsigned char)*s; int need = c < 0x80 ? 0 : c >> 5 == 6 ? 1 : c >> 4 == 14 ? 2 : c >> 3 == 30 ? 3 : -1;
        if (need < 0) return 0;
        while (need--) if (((unsigned char)*++s & 0xC0) != 0x80) return 0;
    }
    return 1;
}
#ifdef NOTE_TEST_BROKEN
static void window(const char *s, int tail, double w, char *out, size_t n) { (void)tail; (void)w; snprintf(out, n, "%s", s); }
#define CAP 96
#else
static void window(const char *s, int tail, double w, char *out, size_t n) { text_window(s, tail, w, measure, NULL, out, n); }
#define CAP (sizeof ((Setting *)0)->text)
#endif

static char *read_file(const char *p) {
    FILE *f = fopen(p, "rb"); if (!f) return NULL;
    char *b = calloc(1, 1 << 20); fread(b, 1, (1 << 20) - 1, f); fclose(f); return b;
}

int main(void) {
    Setting row = {0};
    char want[300];
    for (int i = 0; i < 300; ++i) want[i] = (char)('a' + i % 26);
    want[300 - 1] = 0;
    CHECK(sizeof row.text > TEXT_FIELD_MAX);
    // 240 characters through the menu's append
    char buf[sizeof row.text] = "";
    for (int i = 0; i < 240; ++i) text_append(buf, CAP > sizeof buf ? sizeof buf : CAP, (unsigned char)want[i]);
    CHECK(strlen(buf) == 240);
    CHECK(!strncmp(buf, want, 240));
    for (int i = 240; i < 299; ++i) text_append(buf, CAP > sizeof buf ? sizeof buf : CAP, (unsigned char)want[i]);
    CHECK(strlen(buf) == TEXT_FIELD_MAX);
    CHECK(text_append(buf, sizeof buf, 'x') == 0 && strlen(buf) == TEXT_FIELD_MAX);
    CHECK(text_append(buf, sizeof buf, 7) == 0 && text_append(buf, sizeof buf, 127) == 0);
    // the report writer prints the whole stored note
    char *src = read_file("runtime/report.m");
    CHECK(src && strstr(src, "fprintf(f, \"note:       %s\\n\", note && *note ? note : \"(none)\");"));
    free(src);
    // visible window: width of the row 600 px = 60 characters
    char typed[sizeof buf + 2], shown[1024];
    snprintf(typed, sizeof typed, "%s_", buf);
    window(typed, 1, 600, shown, sizeof shown);
    CHECK(measure(shown, NULL) <= 600);
    CHECK(strlen(shown) > 3 && shown[strlen(shown) - 1] == '_');           // caret on screen
    CHECK(!strncmp(shown, "\xE2\x80\xA6", 3));                              // ellipsis in front
    CHECK(strstr(typed, shown + 3) && !strcmp(strstr(typed, shown + 3) + strlen(shown + 3), ""));   // it is the line's end
    CHECK(measure(shown, NULL) >= 590);                                      // and fills the row
    window(buf, 0, 600, shown, sizeof shown);
    CHECK(measure(shown, NULL) <= 600);
    CHECK(!strncmp(shown, buf, 10) && !strcmp(shown + strlen(shown) - 3, "\xE2\x80\xA6"));
    char shortl[] = "short note_";
    window(shortl, 1, 600, shown, sizeof shown); CHECK(!strcmp(shown, shortl));
    window(shortl, 0, 600, shown, sizeof shown); CHECK(!strcmp(shown, shortl));
    // UTF-8 never cut inside a character
    char u[400] = "";
    for (int i = 0; i < 70; ++i) strcat(u, "\xC3\xA9\xE6\x97\xA5");
    for (int w = 0; w <= 700; w += 10) {
        window(u, 1, w, shown, sizeof shown); CHECK(utf8_ok(shown) && measure(shown, NULL) <= (w < 10 ? 10 : w));
        window(u, 0, w, shown, sizeof shown); CHECK(utf8_ok(shown));
    }
    // Review regressions: searching byte offsets must not discard valid UTF-8 windows.
    char accents[256] = "";
    for (int i = 0; i < 127; ++i) strcat(accents, "\xC3\xA9");
    for (int tail = 0; tail <= 1; ++tail) {
        text_window(accents, tail, 600, measure, NULL, shown, sizeof shown);
        CHECK(utf8_ok(shown) && measure(shown, NULL) == 600);
        CHECK(strlen(shown) == 3 + 59 * 2);
        CHECK(tail ? !strcmp(shown + 3, accents + strlen(accents) - 59 * 2)
                   : !strncmp(shown, accents, 59 * 2));
    }
    // clear-all and hold-repeat delete (keyboard keys and the controller's repeat timing)
    char line[64] = "hello";
    CHECK(text_edit_key(line, 0x08, 0, 0) == 1 && !strcmp(line, "hell"));          // Backspace: one character
    CHECK(text_edit_key(line, 0x08, 1, 0) == 1 && !line[0]);                        // Cmd+Backspace: all
    strcpy(line, "hello"); CHECK(text_edit_key(line, 'U', 0, 1) == 1 && !line[0]);  // Ctrl+U: all
    strcpy(line, "hello"); CHECK(text_edit_key(line, 'U', 0, 0) == 0 && !strcmp(line, "hello"));
    double next = 0; int total = 0, before04 = 0;
    for (int f = 0; f <= 60; ++f) {                                                 // 1.0 s held, polled at 60 Hz
        double now = f / 60.0; int d = text_delete_repeat(&next, 1, now);
        total += d; if (now < 0.4) before04 += d;
    }
    CHECK(before04 == 1);                                                           // one at the press, none until 0.4 s
    CHECK(total >= 12 && total <= 15);                                              // then about 20 per second
    CHECK(text_delete_repeat(&next, 0, 1.1) == 0 && next == 0);
    int counts[3] = {0};
    const int rates[] = {10, 30, 60};
    for (int r = 0; r < 3; ++r) {
        next = 0;
        for (int f = 0; f <= 5 * rates[r]; ++f)
            counts[r] += text_delete_repeat(&next, 1, 10 + f / (double)rates[r]);
        CHECK(abs(counts[r] - 94) <= 1);
    }
    CHECK(abs(counts[1] - counts[2]) <= 1);
    printf("review windows: 6 checks; repeat 10/30/60 Hz: %d/%d/%d deletes in 5 s\n", counts[0], counts[1], counts[2]);
    printf("%s: %d checks, %d failures\n", fails ? "FAIL" : "PASS", checks, fails);
    return fails ? 1 : 0;
}
