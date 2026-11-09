// Independent source trims after the mix map's volume conversion, before Snd sums voices.
#include "host.h"
#include "settings.h"

static const char *const trims[] = {"-2 dB", "-3 dB", "-4 dB"};
static const char *const values[] = {"2", "3", "4"};
static void engine_rebase(int value);
static void effects_rebase(int value);
static Setting engine = {.id = "audio.music_engine_trim", .category = SET_AUDIO, .order = 42,
    .label = "Music space: engine", .help = "Lower the player engine to leave more space for music.",
    .type = SET_CHOICE, .choices = trims, .values = values, .nchoices = 3, .def = 0, .hidden = 1,
    .env = "NFS_MUSIC_ENGINE_TRIM", .apply = engine_rebase};
static Setting effects = {.id = "audio.music_effects_trim", .category = SET_AUDIO, .order = 43,
    .label = "Music space: effects", .help = "Lower driving effects, traffic and sirens. Speech and music keep their levels.",
    .type = SET_CHOICE, .choices = trims, .values = values, .nchoices = 3, .def = 0, .hidden = 1,
    .env = "NFS_MUSIC_EFFECTS_TRIM", .apply = effects_rebase};
__attribute__((constructor)) static void register_balance(void) {
    settings_add(&engine);
    settings_add(&effects);
}

// GetSndBase_Object 004AEB10: bits 16..23 = state, 11..15 = instance,
// 4..10 = SFX object. Only decoded gameplay objects are eligible. MAIN (speech,
// HUD, NIS, moment streams) and MUSIC (licensed, score, ambience) remain untouched.
// Object membership is checked against all four PC/X360 MAPOUTPUT variants.
static int category(uint32_t id) {
    if ((id & 0xe0000000u) != 0x40000000u) return 0;
    unsigned state = (id >> 16) & 255, object = (id >> 4) & 127;
    switch (state) {
    case 2: // PLAYERCAR: single Ginsu / engine; other decoded driving effects
        if (object == 1 || object == 2) return 1;
        switch (object) {
        case 3: case 4: case 5: case 6: case 7: case 8: case 9:
        case 11: case 12: case 13: case 16: case 18: case 19: return 2;
        }
        return 0;
    case 3: return object <= 2 || object == 4 || object == 5 || object == 7 ? 2 : 0; // AI
    case 4: return object == 0 || object == 7 || object == 17 ? 2 : 0; // cops / sirens
    case 5: return object <= 3 ? 2 : 0; // traffic
    case 6: case 7: case 8: case 11: return object == 0 ? 2 : 0; // world, collision, driveby, heli
    case 12: return object == 0 || object == 2 || object == 14 || object == 19 || object == 20 ? 2 : 0;
    default: return 0;
    }
}

void f_004E9B30_orig(Cpu *c);
void f_004E9B30(Cpu *c) {
    uint32_t map = c->ecx;
    f_004E9B30_orig(c);
    int e = engine.value, f = effects.value;
    // Q24 multipliers round pow(10, -dB/20). Scale the final Q15 gain, after the
    // original clamp, so even an authored positive level gets the requested trim.
    static const uint32_t gain[] = {13326616, 11877359, 10585708};
    if (e < 0 || e > 2 || f < 0 || f > 2) return;
    uint32_t base = M32(map + 0x124), words = M32(map + 0x1d4);
    enum { MAX_OBJECTS = 4096 };
    if (!base || !words || words % 16 || words > MAX_OBJECTS * 16) return;
    // A source can have several volume/pitch/filter records, and duplicate volume
    // destinations. Each halfword is trimmed once, regardless of record order.
    static _Thread_local uint32_t seen[MAX_OBJECTS];
    memset(seen, 0, words / 16 * sizeof *seen);
    uint32_t proc = M32(map + 0x16c), count = M32(map + 0x188);
    for (uint32_t i = 0; i < count; ++i, proc += 8) {
        uint32_t shared = M32(proc), unique = M32(proc + 4);
        int cat = category(M32(unique));
        int step = cat == 1 ? e : cat == 2 ? f : -1;
        if (step < 0) continue;
        uint32_t out = M32(shared + 12), format = M32(out);
        if (((format >> 24) & 15) != 0) continue; // only Q15 volume; never pitch/filter/angle
        uint32_t target = M32(unique + 16), offset = target - base;
        if (offset % 64 || offset / 4 >= words || !(M8(target + 60) & 1)) continue;
        uint32_t *mask = &seen[offset / 64];
        for (unsigned j = 0; j < (format & 15); ++j) {
            uint32_t destination = M32(out + 4 + j * 4);
            // MixMasterChannels' 3D bypass copies angle words even inside a
            // volume record. Those copies are not amplitude controls.
            if ((destination & 0x80000000u) &&
                ((destination >> 21) & 31) < (M8(shared + 10) & 31)) continue;
            unsigned field = (destination >> 26) & 31;
            if (field >= 30 || (*mask & (1u << field))) continue;
            *mask |= 1u << field;
            uint32_t at = target + field * 2, level = M16(at);
            if (level > 32767) continue; // unknown/non-Q15 values fail closed
            M16(at) = (uint16_t)(((uint64_t)level * gain[step] + (1u << 23)) >> 24);
        }
    }
}

// Native Audio rows use the same owned FEToggleWidget layout as Audio Mode.
// Only Act/Draw are replaced; the retail menu owns focus, mouse/pad input and
// destruction. Preview stays live, Back restores, Accept saves through settings.
static struct {
    uint32_t screen, widget[2];
    int original[2], accepted;
} audio_options;
static Setting *const balance_settings[] = {&engine, &effects};
static uint32_t balance_vtable;
static void engine_rebase(int value) { audio_options.original[0] = value; }
static void effects_rebase(int value) { audio_options.original[1] = value; }
static int balance_row(uint32_t widget) {
    for (int i = 0; i < 2; ++i)
        if (widget && widget == audio_options.widget[i]) return i;
    return -1;
}
static int balance_draw(Cpu *c) {
    int row = balance_row(c->ecx);
    if (row >= 0) {
        Setting *s = balance_settings[row];
        uint32_t objects[] = {M32(c->ecx + 0x2c), M32(c->ecx + 0x30)};
        static uint32_t labels[2], choices[3];
        if (!labels[row]) labels[row] = gstr(s->label);
        if (!choices[s->value]) choices[s->value] = gstr(trims[s->value]);
        uint32_t text[] = {labels[row], choices[s->value]};
        for (int i = 0; i < 2; ++i) if (objects[i]) {
            Cpu q = *c;
            q.ecx = objects[i];
            uint32_t arg = text[i];
            // FEString::SetString(char const *), using a direct text value.
            // Bit 1 selects explicit text instead of the language-hash label.
            M32(objects[i] + 0x1c) |= 2u;
            call_guest(&q, 0x0050D880u, 1, &arg);
        }
    }
    RET(0, 0);
}
static int balance_act(Cpu *c) {
    int row = balance_row(c->ecx);
    uint32_t message = arg(c, 1);
    if (row >= 0 && (message == 0x9120409eu || message == 0xb5971bf1u)) {
        Setting *s = balance_settings[row];
        if (!s->locked) {
            s->value = (s->value + (message == 0x9120409eu ? 2 : 1)) % 3;
            audio_options.accepted = 0;
            M8(c->ecx + 0x2a) = 1;
        }
        Cpu q = *c;
        call_guest(&q, M32(M32(c->ecx) + 0x38), 1, &message);
        q = *c;
        balance_draw(&q);
    }
    RET(0, 2);
}
// event: 0 RestoreOriginals, 1 NotificationMessage, 2 destruction.
void music_balance_options(Cpu *c, int event) {
    if (audio_options.screen != c->ecx) return;
    uint32_t message = event == 1 ? arg(c, 0) : 0;
    if (event == 0 || (event == 1 && message == 0x911ab364u) ||
        (event == 2 && !audio_options.accepted)) {
        for (int i = 0; i < 2; ++i) balance_settings[i]->value = audio_options.original[i];
        audio_options.accepted = 0;
    }
    if (event == 1 && (message == 0x406415e3u || message == 0x4d0d83b9u || message == 0xe1fde1d1u)) {
        for (int i = 0; i < 2; ++i) {
            Setting *s = balance_settings[i];
            int value = s->value;
            s->value = audio_options.original[i];
            settings_set(s, value);
        }
        audio_options.accepted = 1;
    }
    if (event == 2) memset(&audio_options, 0, sizeof audio_options);
}
void f_00529310_orig(Cpu *c);
void f_00529310(Cpu *c) {
    uint32_t screen = c->ecx;
    f_00529310_orig(c);
    if (audio_options.screen != screen) {
        memset(&audio_options, 0, sizeof audio_options);
        audio_options.screen = screen;
        for (int i = 0; i < 2; ++i) audio_options.original[i] = balance_settings[i]->value;
    }
    for (int i = 0; i < 2; ++i) {
        Cpu q = *c;
        uint32_t size = 0x5c;
        uint32_t widget = call_guest(&q, 0x00652AD0u, 1, &size);
        if (!widget) continue;
        q = *c; q.ecx = widget;
        uint32_t enabled = 1;
        call_guest(&q, 0x0051A7B0u, 1, &enabled);
        if (!balance_vtable) {
            balance_vtable = galloc(0x40);
            uint32_t act = host_fn(balance_act, "Music space Act");
            uint32_t draw = host_fn(balance_draw, "Music space Draw");
            for (int j = 0; j < 16; ++j) {
                uint32_t fn = M32(M32(widget) + j * 4);
                M32(balance_vtable + j * 4) = fn == 0x0050F600u ? act : fn == 0x0051B2D0u ? draw : fn;
            }
        }
        M32(widget) = balance_vtable;
        audio_options.widget[i] = widget;
        q = *c; q.ecx = screen;
        uint32_t args[] = {widget, 1};
        call_guest(&q, 0x00588570u, 2, args);
    }
}
