#pragma once
#include <stdint.h>
#include <stddef.h>

typedef struct {
    uint64_t generation;
    uint32_t target, type, limit, length;
    int active, note, owner, player, connected;
    uint8_t allowed[256];
    char text[512];                    // UTF-8; hidden fields contain only masking characters
} OskCapture;
enum { OSK_TYPE, OSK_DELETE, OSK_ACCEPT, OSK_CANCEL };
#define OSK_MENU_SHORTCUT 0x80000000u
const OskCapture *osk_capture(void);
int osk_visible(void);
void osk_note_begin(char *text, size_t capacity, int owner);
void osk_note_end(int cancel);
int osk_note_active(void);
int osk_last_pad(void);
// Physical input is sampled before either overlay consumes it. Returns all-pad suppression.
int osk_pad(int port, uint32_t buttons, int up, int down, int left, int right,
            float lx, float ly, int connected, int xbox);
void osk_queue(uint64_t generation, int action, unsigned ch);
void osk_navigation(int dx, int dy);
void osk_toggle(int symbols);
void osk_choose(void);
int osk_key_count(int row);
unsigned osk_key(int row, int column);
const char *osk_key_label(unsigned key, char label[16]);
int osk_key_enabled(unsigned key);
int osk_selected(int row, int column);
int osk_xbox(void);
enum { OSK_SHIFT = 256, OSK_SPACE, OSK_SYMBOLS };
