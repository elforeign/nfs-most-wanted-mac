#pragma once
#include <stddef.h>
// Typed-text helpers for F10 text rows and the controller keyboard: the stored length and the part of a long line that is shown.
#define TEXT_FIELD_MAX 256                       // characters a text row holds (plus the terminating NUL)
typedef double (*TextMeasure)(const char *s, void *ctx);    // drawn width of a NUL-terminated line
int text_append(char *buf, size_t cap, unsigned ch);        // adds one typed byte; 0 when full or not printable
// What fits in maxw: the whole line, else with an ellipsis. tail=1 keeps the end (a line being typed, caret last), tail=0 the start.
// Cuts only on UTF-8 boundaries.
void text_window(const char *s, int tail, double maxw, TextMeasure measure, void *ctx, char *out, size_t n);
// Deleting: a held delete button removes one character at once, then repeats after 0.4 s at 20 per second. Call every poll with the
// button's state; returns how many characters to delete now. *next is per button (0 at rest).
int text_delete_repeat(double *next, int held, double now);
// A keyboard key in a text row: Backspace deletes one character, Cmd+Backspace or Ctrl+U clears the whole field. 1 when it changed buf.
int text_edit_key(char *buf, unsigned vk, int cmd, int ctrl);
