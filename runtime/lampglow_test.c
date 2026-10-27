// Unit test of the real emitter context and settings; no game or GPU required.
#include <assert.h>
#include <stdlib.h>
#include "host.h"
#include "settings.h"
uint8_t *G;
int diag_on;
int lamp_batch_alt;   // render.m's NFS_LAMP_BATCH=alt flag, read by lampglow_diag_second
static Setting *setting;
static const char *variant = "";
void settings_add(Setting *s) { setting = s; s->value = s->def; }
int postfx_look_variant(const char *s) { return !strcmp(s, variant); }
void diag_event(const char *fmt, ...) {}
int main(void) {
    assert(setting && setting->def == 0 && !lampglow_enabled());
    assert(!strcmp(setting->env, "NFS_LAMP_GLOW"));
    setting->value = 1; assert(lampglow_enabled());
    variant = "lampglowoff"; assert(!lampglow_enabled());
    setting->value = 0; variant = "lampglow"; assert(lampglow_enabled());
    variant = ""; assert(!lampglow_enabled());
    G = calloc(1, 4096); Cpu c = {.esp = 128};
    M32(c.esp + 4) = 1024; M32(c.esp + 8) = 2048;
    unsigned checked = 0, selected = 0;
    for (unsigned view = 0; view <= 3; ++view)
        for (unsigned type = 0; type <= 16; ++type)
            for (unsigned reflection = 0; reflection <= 1; ++reflection) {
                M32(1028) = view; M8(2048 + 0x2c) = type; M32(c.esp + 20) = reflection;
                lampglow_begin(&c);
                int want = view == 1 && !reflection && (type == 5 || type == 6) ? type : 0;
                assert(lampglow_type() == want);
                selected += want != 0; ++checked;
                lampglow_end(); assert(lampglow_type() == 0);
            }
    assert(selected == 2);
    M32(c.esp + 4) = M32(c.esp + 8) = 0; lampglow_begin(&c); assert(!lampglow_type()); lampglow_end();
    free(G);
    printf("PASS emitter context: %u combinations, %u selected (main red/blue only); default Off and held overrides\n", checked, selected);
    return 0;
}
