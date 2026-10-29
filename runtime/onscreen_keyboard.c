// Controller text entry. Guest calls and queue draining run only on the guest thread.
#include "funcs.h"
#include "host.h"
#include "onscreen_keyboard.h"
#include <string.h>
#include <math.h>

#define KM 0x0091D4B0u
int prompts_last_input(void);
void prompts_input(int pad);
int portmenu_is_open(void);

static OskCapture game, note;
static uint64_t serial;
static int last_pad, fe_port = -1, fe_depth;
static char *note_text;
static size_t note_capacity;
static char note_original[256];
static uint32_t physical[2], held[2];
static int directions[2], present[2], xbox_pad[2], armed[2];
static uint64_t seen_generation[2];
static double repeat_at[2];
static int row, column, shifted, page;
typedef struct { uint64_t generation; uint32_t target; int note, action; unsigned ch; } Event;
static Event events[32];
static unsigned event_read, event_write;

static uint32_t invoke(Cpu *c, void (*fn)(Cpu *), uint32_t self, unsigned n, const uint32_t *args) {
    Cpu q = *c;
    q.ecx = self; q.esp -= 128 + n * 4;
    M32(q.esp) = 0;
    for (unsigned i = 0; i < n; ++i) M32(q.esp + 4 + i * 4) = args[i];
    fn(&q);
    return q.eax;
}
static void layout_reset(void) { row = column = shifted = page = 0; }
const OskCapture *osk_capture(void) { return note.active ? &note : &game; }
int osk_note_active(void) { return note.active; }
int osk_last_pad(void) { return last_pad; }
int osk_xbox(void) { const OskCapture *s = osk_capture(); return s->owner >= 0 && s->owner < 2 && xbox_pad[s->owner]; }
int osk_visible(void) {
    const OskCapture *s = osk_capture();
    return s->active && prompts_last_input() == 1 && s->owner >= 0 && s->owner < 2 &&
           present[s->owner] && (s->note || !portmenu_is_open());
}
static void utf8(char *out, size_t *n, unsigned ch) {
    if (*n + 4 >= 512) return;
    if (ch < 128) out[(*n)++] = (char)ch;
    else if (ch < 2048) { out[(*n)++] = (char)(0xC0 | ch >> 6); out[(*n)++] = (char)(0x80 | (ch & 63)); }
    else { out[(*n)++] = (char)(0xE0 | ch >> 12); out[(*n)++] = (char)(0x80 | ((ch >> 6) & 63)); out[(*n)++] = (char)(0x80 | (ch & 63)); }
    out[*n] = 0;
}
static void note_snapshot(void) {
    note.length = (uint32_t)strlen(note_text);
    size_t n = 0; note.text[0] = 0;
    for (unsigned i = 0; i < note.length; ++i) utf8(note.text, &n, (uint8_t)note_text[i]);
}
void osk_note_begin(char *text, size_t capacity, int owner) {
    osk_note_end(0);
    if (!text || capacity < 1 || capacity > sizeof note_original) return;
    note_text = text; note_capacity = capacity;
    memcpy(note_original, text, capacity);
    memset(&note, 0, sizeof note);
    note.active = note.note = 1; note.generation = ++serial;
    note.owner = owner; note.limit = (uint32_t)capacity - 1;
    for (unsigned i = 32; i < 256; ++i) note.allowed[i] = i != 127;
    note_snapshot(); layout_reset();
    if (diag_on) diag_event("osk: note capture generation=%llu owner=%d limit=%u", (unsigned long long)note.generation, owner, note.limit);
}
void osk_note_end(int cancel) {
    if (!note.active) return;
    if (cancel) memcpy(note_text, note_original, note_capacity);
    note.active = 0; note.generation = ++serial;
    note_text = NULL;
    layout_reset();
}
static void retire_game(void) {
    if (game.active) { game.active = 0; game.generation = ++serial; }
}
static void game_snapshot(Cpu *c) {
    uint32_t target = M32(KM + 0x414);
    if (!M8(KM + 0x410) || !target) { retire_game(); return; }
    if (!game.active || target != game.target) {
        memset(&game, 0, sizeof game);
        game.active = 1; game.target = target; game.generation = ++serial;
        game.owner = fe_port >= 0 && prompts_last_input() == 1 ? fe_port : last_pad;
        uint32_t db = M32(0x0091CF90u);
        game.player = -1;
        for (int i = 0; db && i < 2; ++i) if (M8(db + 12 + i) == game.owner) game.player = i;
        game.type = M32(KM + 0x404); game.limit = M32(KM + 0x40C);
        for (unsigned ch = 32; ch < 256; ++ch) game.allowed[ch] = invoke(c, f_0056C820, KM, 1, &ch) & 0xFF;
        layout_reset();
        if (diag_on) diag_event("osk: game capture generation=%llu owner=%d player=%d type=%u limit=%u", (unsigned long long)game.generation,
                               game.owner, game.player, game.type, game.limit);
    }
    game.length = M32(KM + 0x300);
    game.connected = present[game.owner];
    size_t n = 0; game.text[0] = 0;
    for (unsigned i = 0; i < game.length && i < 155; ++i) utf8(game.text, &n, game.type == 5 ? '*' : M16(KM + 0x100 + 2 * i));
}
void osk_queue(uint64_t generation, int action, unsigned ch) {
    const OskCapture *s = osk_capture();
    if (event_write - event_read == 32) return;
    events[event_write++ % 32] = (Event){generation, s->target, s->note, action, ch};
}
void osk_pass(Cpu *c) {
    game_snapshot(c);
    if (note.active) note_snapshot();
    while (event_read != event_write) {
        Event e = events[event_read++ % 32];
        const OskCapture *s = osk_capture();
        if (!s->active || e.generation != s->generation || e.target != s->target || e.note != s->note || prompts_last_input() != 1) continue;
        if (e.note) {
            size_t n = strlen(note_text);
            if (e.action == OSK_TYPE && e.ch < 256 && note.allowed[e.ch] && n < note.limit) { note_text[n] = (char)e.ch; note_text[n + 1] = 0; }
            else if (e.action == OSK_DELETE && n) note_text[n - 1] = 0;
            else if (e.action == OSK_ACCEPT || e.action == OSK_CANCEL) osk_note_end(e.action == OSK_CANCEL);
            if (note.active) note_snapshot();
        } else {
            // Recheck the live target immediately before every call, including queued bursts.
            if (!M8(KM + 0x410) || M32(KM + 0x414) != e.target) { retire_game(); continue; }
            unsigned ch = e.action == OSK_DELETE ? 8 : e.ch;
            if (e.action == OSK_TYPE || e.action == OSK_DELETE) invoke(c, f_005A8700, KM, 1, &ch);
            else if (e.action == OSK_ACCEPT) invoke(c, f_00599100, e.target, 0, NULL);
            else if (e.action == OSK_CANCEL) invoke(c, f_005991B0, e.target, 0, NULL);
            game_snapshot(c);
        }
        if (diag_on) diag_event("osk: action=%d generation=%llu length=%u active=%d", e.action, (unsigned long long)e.generation,
                               osk_capture()->length, osk_capture()->active);
    }
}

void f_0057F1D0_orig(Cpu *c);
void f_0057F1D0(Cpu *c) {
    int manager = c->ecx == KM;
    if (manager) retire_game();
    f_0057F1D0_orig(c);
    if (manager) {
        game_snapshot(c);
        // Existing FE actions include the press that opened the field.
        uint32_t fe = M32(0x0091CAD0u);
        if (fe) invoke(c, f_005247A0, fe, 0, NULL);
    }
}
void f_00586580_orig(Cpu *c);
void f_00586580(Cpu *c) {
    if (game.active && M32(c->ecx + 0x24) == game.target) retire_game();
    f_00586580_orig(c);
}
void f_00516520_orig(Cpu *c);
void f_00516520(Cpu *c) {
    uint32_t mask = arg(c, 2);
    int saved = fe_port;
    if (mask == 1 || mask == 2) {
        uint32_t p = invoke(c, f_00572FF0, 0, 1, &mask);
        if (p < 2) fe_port = (int)p;
    }
    ++fe_depth;
    f_00516520_orig(c);
    if (--fe_depth && saved >= 0) fe_port = saved;
}

// The layout uses physical positions, independent of the game's remappable bindings.
static const char *letters[] = {"1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"};
static const char symbols[] = "!@#$%^&*()_+-=[]{};:'\"\\|,.<>/?`~ ";
static unsigned page_keys[64];
static int page_count(void) {
    const OskCapture *s = osk_capture(); int n = 0;
    if (page == 1) { for (const char *p = symbols; *p; ++p) if (s->allowed[(uint8_t)*p]) page_keys[n++] = (uint8_t)*p; }
    else if (page == 2) for (unsigned ch = 192; ch < 256; ++ch) if (s->allowed[ch]) page_keys[n++] = ch;
    return n;
}
int osk_key_count(int r) {
    if (r == 4) return 3;
    if (r < 0 || r > 4) return 0;
    if (!page) return (int)strlen(letters[r]);
    int n = page_count() - r * 16;
    return n < 0 ? 0 : n > 16 ? 16 : n;
}
unsigned osk_key(int r, int col) {
    if (col < 0 || col >= osk_key_count(r)) return 0;
    if (r == 4) return OSK_SHIFT + col;
    unsigned ch = page ? page_keys[r * 16 + col] : (uint8_t)letters[r][col];
    if (!page && shifted && ch >= 'A' && ch <= 'Z') ch += 32;
    return ch;
}
int osk_key_enabled(unsigned key) {
    if (key == OSK_SHIFT) return 1;
    if (key == OSK_SPACE) key = ' ';
    if (key == OSK_SYMBOLS) {
        const OskCapture *s = osk_capture();
        for (const char *p = symbols; *p; ++p) if (s->allowed[(uint8_t)*p]) return 1;
        for (unsigned ch = 192; ch < 256; ++ch) if (s->allowed[ch]) return 1;
        return 0;
    }
    return key < 256 && osk_capture()->allowed[key];
}
const char *osk_key_label(unsigned key, char label[16]) {
    if (key == OSK_SHIFT) return shifted ? "Shift: abc" : "Shift: ABC";
    if (key == OSK_SPACE) return "Space";
    if (key == OSK_SYMBOLS) return page == 1 ? "Latin-1" : page == 2 ? "ABC" : "Symbols";
    size_t n = 0; label[0] = 0;
    // All layout characters fit in two UTF-8 bytes.
    if (key < 128) label[n++] = (char)key;
    else { label[n++] = (char)(0xC0 | key >> 6); label[n++] = (char)(0x80 | (key & 63)); }
    label[n] = 0; return label;
}
int osk_selected(int r, int col) { return r == row && col == column; }
void osk_navigation(int dx, int dy) {
    if (dy) { do { row = (row + dy + 5) % 5; } while (!osk_key_count(row)); }
    int n = osk_key_count(row);
    column = dx ? (column + dx + n) % n : column < n ? column : n - 1;
}
void osk_toggle(int sym) {
    if (!sym) shifted = !shifted;
    else if (osk_key_enabled(OSK_SYMBOLS)) { do { page = (page + 1) % 3; } while (page && !page_count()); row = column = 0; }
}
void osk_choose(void) {
    unsigned key = osk_key(row, column);
    if (!osk_key_enabled(key)) return;
    if (key == OSK_SHIFT || key == OSK_SYMBOLS) osk_toggle(key == OSK_SYMBOLS);
    else osk_queue(osk_capture()->generation, OSK_TYPE, key == OSK_SPACE ? ' ' : key);
}
int osk_pad(int port, uint32_t buttons, int up, int down, int left, int right,
            float lx, float ly, int connected, int xbox) {
    if (port < 0 || port > 1) return 0;
    int dir = up || ly > .5f ? 1 : down || ly < -.5f ? 2 : left || lx < -.5f ? 3 : right || lx > .5f ? 4 : 0;
    uint32_t edge = buttons & ~physical[port];
    int changed = edge || (dir && dir != directions[port]);
    physical[port] = buttons; present[port] = connected; xbox_pad[port] = xbox;
    if (changed && connected) { last_pad = port; prompts_input(1); }
    const OskCapture *s = osk_capture();
    int taking = s->active && prompts_last_input() == 1 && (s->note || !portmenu_is_open());
    if (taking) {
        held[port] = buttons | (dir ? 0x10000u : 0);
        // Let the primary pad's menu shortcut suspend either text adapter.
        if (port == 0 && port == s->owner && (buttons & OSK_MENU_SHORTCUT)) {
            directions[port] = dir;
            return 0;
        }
        if (seen_generation[port] != s->generation) { seen_generation[port] = s->generation; armed[port] = 0; }
        if (!buttons && !dir) armed[port] = 1;
        if (port == s->owner && connected && armed[port]) {
            double now = host_ticks() / 1e9;
            if (dir && (dir != directions[port] || now >= repeat_at[port])) {
                repeat_at[port] = now + (dir != directions[port] ? .4 : .09);
                osk_navigation(dir == 3 ? -1 : dir == 4 ? 1 : 0, dir == 1 ? -1 : dir == 2 ? 1 : 0);
            }
            if (edge & 1) osk_choose();
            if (edge & 2) osk_queue(s->generation, OSK_DELETE, 0);
            if (edge & 4) osk_queue(s->generation, OSK_ACCEPT, 0);
            if (edge & 8) osk_queue(s->generation, OSK_CANCEL, 0);
            if (edge & 16) osk_toggle(0);
            if (edge & 32) osk_toggle(1);
        }
    } else {
        if (!buttons && !dir) { held[port] = 0; armed[port] = 0; }
        else if (held[port]) taking = 1;
    }
    directions[port] = dir;
    return taking;
}
