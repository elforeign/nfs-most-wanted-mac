// In-menu documents and the ordered Create/View -> R3 shortcut.
#pragma once
#include <stdint.h>
#include <CoreGraphics/CoreGraphics.h>

enum { GUIDE_UP, GUIDE_DOWN, GUIDE_PAGE_UP, GUIDE_PAGE_DOWN, GUIDE_HOME, GUIDE_END, GUIDE_BACK };
int guide_active(void);
void guide_open(int testing);
void guide_close(void);
void guide_refresh(void);
void guide_command(int command);
void guide_scroll(double lines);
void guide_draw(CGContextRef ctx, double panel_height, CGRect viewport, int xbox);
double guide_scale_cap(void);

typedef struct {
    uint32_t last, consumed, modified;
    int last_home, last_touch, pending, ordered, combo, done, sticks, sticks_done;
    double create_at, combo_at, sticks_at, inject_until;
} GuideShortcut;
typedef struct { uint32_t hide, inject; int action; } GuideShortcutResult;
// Physical menu shortcut buttons, including the separate Home and Touchpad inputs.
static inline uint32_t guide_menu_buttons(int choice, int has_touchpad) {
    switch (choice) {
    case 0: return has_touchpad ? 8192u : 4096u | 64u;
    case 1: return 4096u;
    case 2: return 256u | 512u;
    case 3: return 64u;
    default: return 0;
    }
}
// action: 0 none, 1 toggle F10, 2 open Guide. No game-flow or button-layout remapping here.
GuideShortcutResult guide_shortcut(GuideShortcut *s, double now, uint32_t phys, int home, int touch,
                                  int has_touchpad, int choice, int enabled, int menu_open);
