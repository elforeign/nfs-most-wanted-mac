/* Controller mapping (1.0.2).
 *
 * The game's own Options -> Controls screen remaps the driving actions on a
 * controller: pick an action, press a button. Up to 1.0.1 this port rewrote
 * the whole binding table every frame with its PlayStation layout, so a
 * change made on that screen was undone on the next frame (a player set gas
 * to R2 and saw it come back as "Button 1"). Now:
 *
 * - Driving rows 0..18 (the rows the Controls screen edits and the profile
 *   saves, see 0051d410/0051d590 and 0056e180/0056e200) belong to the player.
 *   The PlayStation layout is their DEFAULT: written once at start, by the
 *   screen's Restore Defaults (0051d6e0 -> 0063cd80), and when a profile saved
 *   with another controller loads (0056e200 -> 0063cd80). When the controller
 *   is created again (0063f850 tail-jumps to 0063cd80, e.g. after a Bluetooth
 *   reconnect) the player's rows are put back instead of any defaults.
 * - Front-end rows (19 and up) are not on the Controls screen; the layout keeps
 *   them every frame, as before.
 * - A trigger bound to any driving action (as a button, L2/R2, or as the axis
 *   the screen captures, Rx/Ry) acts as a full analog pedal: the game reads
 *   these axes as centred, so on its own a trigger would only count in the
 *   second half of its travel.
 * - The F10 controller settings still apply at once, but only to their own
 *   rows: shift buttons to rows 7/8, L3 skip-track to row 14, "Triggers as
 *   pedals" to rows 7/8 (no shifting on a pedal) plus its analog throttle and
 *   brake.
 * - Names on the Controls screen are the controller's own (Cross / A, R2 / RT,
 *   Left Stick Up) instead of DirectInput's "Button 1" or "Y Rotation".
 *
 * Kinds: 2 button, 3 POV, 4/5 negative/positive translation axis, 6/7
 * negative/positive rotation axis. Axis order (game.toml): X Y Z = left stick
 * X, left stick Y, right stick X; Rx Ry = L2 R2; Rz = right stick Y. */

#define PAD_DRIVE_ROWS 19u
#define PAD_RESTORE_DEFAULTS_RET 0x0051d707u  /* 0051d6e0: Restore Defaults, controller */
#define PAD_LOAD_OTHER_PAD_RET 0x0056e282u    /* 0056e200: profile saved with another controller */
#define PAD_STATE_RX 0x20cu                  /* L2, unsigned 0..65535 at rest 0 */
#define PAD_STATE_RY 0x210u                  /* R2 */

static PopModStatus install(uint32_t addr, PopHookFn fn, int32_t mode, int slot); /* nfsmw.c, lifecycle */

static uint32_t g_pad_rows[PAD_DRIVE_ROWS][2][3]; /* the player's driving rows, controller slots */
static int g_pad_rows_valid;

static void pad_rows_save(void) {
    for (unsigned r = 0; r < PAD_DRIVE_ROWS; ++r)
        for (unsigned s = 0; s < 2; ++s)
            for (unsigned k = 0; k < 3; ++k)
                g_pad_rows[r][s][k] = get_u32(GAME_BINDINGS + r * 52u + 28u + s * 12u + k * 4u);
    g_pad_rows_valid = 1;
}
static void pad_rows_restore(void) {
    for (unsigned r = 0; r < PAD_DRIVE_ROWS; ++r)
        for (unsigned s = 0; s < 2; ++s)
            for (unsigned k = 0; k < 3; ++k)
                g_api->guest_write_u32(g_api, GAME_BINDINGS + r * 52u + 28u + s * 12u + k * 4u,
                                       g_pad_rows[r][s][k]);
}

/* Shift rows 7 (down) and 8 (up) from the F10 settings. A trigger that is a pedal never also shifts. */
static void pad_shift_rows(void) {
    static const char *keys[2][2] = {{"shift_down", "shift_down_secondary"}, {"shift_up", "shift_up_secondary"}};
    for (unsigned action = 0; action < 2; ++action)
        for (unsigned slot = 0; slot < 2; ++slot) {
            int button = (int)setting(keys[action][slot], slot ? -1 : 6 + (int)action);
            if (setting("trigger_pedals", 0) && (button == 6 || button == 7)) button = -1;
            pad_binding(7 + action, slot, button < 0 ? 0 : 2, button < 0 ? 0 : (uint32_t)button);
        }
}
static void pad_skip_row(void) {
    int on = (int)setting("l3_skip_track", 1);
    pad_binding(14, 0, on ? 2 : 0, on ? 10 : 0);   /* row 14 is the T key's skip-track action */
}

/* The PlayStation layout for the driving rows: the default, not a rule. */
static void pad_drive_defaults(void) {
    static const struct { unsigned row, kind, control; } rows[] = {
        {0, 2, 1},  /* gas: Cross */
        {1, 2, 0},  /* brake: Square */
        {2, 4, 0}, {3, 5, 0}, /* steer: left stick X, both halves */
        {4, 2, 2},  /* handbrake: Circle */
        {5, 2, 4},  /* Speedbreaker: L1 */
        {6, 2, 5},  /* nitrous: R1 */
        {9, 2, 8},  /* reset: Create */
        {10, 3, 0}, {11, 3, 2}, {12, 3, 3}, {13, 3, 1}, /* HUD: dpad */
        {15, 2, 11}, /* camera: R3 */
        {16, 2, 3}, /* look back: Triangle */
        {17, 0, 0}, /* no competing pull-back binding */
        {18, 2, 9}, /* pause: Options */
    };
    for (unsigned i = 0; i < sizeof rows / sizeof rows[0]; ++i) {
        pad_binding(rows[i].row, 0, rows[i].kind, rows[i].control);
        pad_binding(rows[i].row, 1, 0, 0);
    }
    /* Right-stick Y is Rz (rotation axis 2), forward negative: an analog alternative to Cross/Square. */
    pad_binding(0, 1, 6, 2);
    pad_binding(1, 1, 7, 2);
    pad_shift_rows();
    pad_skip_row();
}

/* Front-end rows: not on the Controls screen, so the layout keeps them every frame. */
static void pad_frontend_rows(void) {
    static const struct { unsigned row, kind, control; } rows[] = {
        {19, 4, 0}, {20, 5, 0}, {21, 4, 1}, {22, 5, 1}, /* menu stick */
        {23, 2, 9}, /* skip cinematic: Options */
        {29, 2, 7}, {30, 2, 6},
        {32, 2, 1}, {33, 2, 3}, {34, 2, 9}, /* confirm/back/start */
        /* FE_ACTION_LTRIG/RTRIG (keyboard 9/0) feed camera actions 43/44 in 007a2310, unlike F1/F2 rows 29/30
         * above. Assign the triggers here so Showcase zoom matches its L2/R2 prompts. Keep L1/R1 for contextual
         * actions without a second tab event. */
        {35, 2, 6}, {36, 2, 7},
        {37, 2, 5}, /* FE_ACTION_B0 / keyboard 3: Showcase, R1 */
        {38, 2, 4}, /* FE_ACTION_B1 / keyboard M: L1 */
        {39, 2, 11}, /* FE_ACTION_B3 / keyboard 4: R3 */
        {40, 2, 0}, /* FE_ACTION_B4 / keyboard 2: secondary action, Square */
        {41, 2, 2}, /* FE_ACTION_B5 / keyboard 1: tertiary/delete/sell, Circle */
    };
    for (unsigned i = 0; i < sizeof rows / sizeof rows[0]; ++i) {
        pad_binding(rows[i].row, 0, rows[i].kind, rows[i].control);
        pad_binding(rows[i].row, 1, 0, 0);
    }
    pad_binding(19, 1, 3, 3);
    pad_binding(20, 1, 3, 1);
    pad_binding(21, 1, 3, 0);
    pad_binding(22, 1, 3, 2);
    /* The old PC defaults also alias controller inputs to numpad keys: no unrelated actions alongside this layout. */
    for (unsigned row = 63; row <= 73; ++row) {
        pad_binding(row, 0, 0, 0);
        pad_binding(row, 1, 0, 0);
    }
}

/* 0 none, 1 L2, 2 R2: a trigger used by this controller slot (button 6/7, or the Rx/Ry axis). */
static int pad_slot_trigger(unsigned row, unsigned slot) {
    uint32_t at = GAME_BINDINGS + row * 52u + 28u + slot * 12u, kind = get_u32(at), control = get_u32(at + 8);
    if (kind == 2 && (control == 6 || control == 7)) return (int)control - 5;
    if ((kind == 6 || kind == 7) && control <= 1) return (int)control + 1;
    return 0;
}

/* Settings that own rows: applied when they change (and the layout once at start), never every frame. */
static void pad_settings_poll(void) {
    static int started, ps2, pedals, skip;
    static int64_t shifts[4];
    int now_ps2 = (int)setting("ps2_controls", 1), now_pedals = (int)setting("trigger_pedals", 0);
    int now_skip = (int)setting("l3_skip_track", 1);
    int64_t now_shifts[4] = {setting("shift_down", 6), setting("shift_up", 7),
                             setting("shift_down_secondary", -1), setting("shift_up_secondary", -1)};
    if (!started) {
        started = 1;
        if (now_ps2 && !g_pad_rows_valid) pad_drive_defaults();
    } else if (now_ps2 && !ps2) {
        pad_drive_defaults();   /* switched back on: the layout again */
    } else if (now_ps2) {
        if (now_pedals != pedals || memcmp(now_shifts, shifts, sizeof shifts)) pad_shift_rows();
        if (now_skip != skip) pad_skip_row();
    }
    /* Switching the layout off leaves the rows as they are; Restore Defaults then gives the game's own. */
    ps2 = now_ps2; pedals = now_pedals; skip = now_skip;
    memcpy(shifts, now_shifts, sizeof shifts);
    /* A trigger that is a pedal never also shifts, whatever an older save holds. */
    if (now_ps2 && now_pedals)
        for (unsigned row = 7; row <= 8; ++row)
            for (unsigned slot = 0; slot < 2; ++slot)
                if (pad_slot_trigger(row, slot)) pad_binding(row, slot, 0, 0);
}

/* After the game evaluated the table: full-travel analog triggers for every driving action bound to one, and the
 * "Triggers as pedals" throttle and brake. The game combines an action's inputs by maximum; so does this. */
static void pad_trigger_analog(const PopModApi *api, uint32_t device) {
    uint32_t values = get_u32(device + 32), pad = get_u32(0x0091f150u), state = 0;
    uint32_t getter = pad ? get_u32(get_u32(pad) + 4) : 0;
    if (!values || !getter || api->guest_call(api, getter, pad, NULL, 0, &state) != POP_OK || !state) return;
    float trig[3] = {0.0f, fminf(get_u32(state + PAD_STATE_RX) / 65535.0f, 1.0f),
                     fminf(get_u32(state + PAD_STATE_RY) / 65535.0f, 1.0f)};
    for (unsigned row = 0; row < PAD_DRIVE_ROWS; ++row) {
        float v = 0.0f;
        for (unsigned slot = 0; slot < 2; ++slot) v = fmaxf(v, trig[pad_slot_trigger(row, slot)]);
        if (v > 0.0f) put_float(values + row * 4u, fmaxf(get_float(values + row * 4u), v));
    }
    if (setting("ps2_controls", 1) && setting("trigger_pedals", 0)) {
        put_float(values, fmaxf(get_float(values), trig[2]));        /* gas: R2 */
        put_float(values + 4, fmaxf(get_float(values + 4), trig[1])); /* brake: L2 */
    }
}

/* 0063cd80 sets the controller defaults (for this controller type). Restore Defaults and a profile saved with
 * another controller get the layout; the controller being created again (a reconnect) keeps the player's rows. */
static void pad_defaults_hook(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    uint32_t ret = get_u32(cpu->esp);
    api->call_original(api, cpu->target, cpu);
    if (!setting("ps2_controls", 1)) return;
    if (ret != PAD_RESTORE_DEFAULTS_RET && ret != PAD_LOAD_OTHER_PAD_RET && g_pad_rows_valid) {
        pad_rows_restore();
        api->log(api, "core.nfsmw: controller created again; your button layout kept");
    } else {
        pad_drive_defaults();
        api->log(api, ret == PAD_RESTORE_DEFAULTS_RET ? "core.nfsmw: controller layout restored to the defaults"
                                                       : "core.nfsmw: controller layout set to the defaults");
    }
    pad_rows_save();
}

/* 006284d0 resets the whole table to the PC defaults; the game calls it for Restore Defaults on the KEYBOARD.
 * Keep the controller's driving rows (the front-end rows are rewritten every frame anyway). */
static void pad_table_defaults_hook(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    int keep = setting("ps2_controls", 1) && g_pad_rows_valid;
    if (keep) pad_rows_save();
    api->call_original(api, cpu->target, cpu);
    if (keep) pad_rows_restore();
}

/* 00628230(char *out, int device, int row, char primary): the name shown for a binding on the Controls screen.
 * For the controller, write the button's own name. */
static const char *pad_name(uint32_t kind, uint32_t control) {
    static const char *const buttons[] = {"Square / X", "Cross / A", "Circle / B", "Triangle / Y", "L1 / LB",
                                          "R1 / RB", "L2 / LT", "R2 / RT", "Create / View", "Options / Menu",
                                          "L3", "R3", "PS / Guide"};
    static const char *const pov[] = {"D-Pad Up", "D-Pad Right", "D-Pad Down", "D-Pad Left"};
    static const char *const stick[2][3] = {{"Left Stick Left", "Left Stick Up", "Right Stick Left"},
                                            {"Left Stick Right", "Left Stick Down", "Right Stick Right"}};
    switch (kind) {
    case 2: return control < 13 ? buttons[control] : NULL;
    case 3: return control < 4 ? pov[control] : NULL;
    case 4: case 5: return control < 3 ? stick[kind - 4][control] : NULL;
    case 6: case 7:
        if (control == 0) return "L2 / LT";
        if (control == 1) return "R2 / RT";
        if (control == 2) return kind == 6 ? "Right Stick Up" : "Right Stick Down";
        return NULL;
    }
    return NULL;
}
static void pad_names_hook(const PopModApi *api, pop_cpu_v1 *cpu, PopHookInvocation *inv, void *user) {
    (void)inv; (void)user;
    uint32_t out = get_u32(cpu->esp + 4), device = get_u32(cpu->esp + 8), row = get_u32(cpu->esp + 12);
    uint32_t primary = get_u32(cpu->esp + 16) & 0xffu;
    api->call_original(api, cpu->target, cpu);
    if (device == 1 || row >= 76u || !out) return;
    uint32_t at = GAME_BINDINGS + row * 52u + 4u + (primary ? 2u : 3u) * 12u;
    const char *name = pad_name(get_u32(at), get_u32(at + 8));
    char *dst = NULL;
    if (name && api->guest_ptr(api, out, (uint32_t)strlen(name) + 1, (void **)&dst) == POP_OK && dst)
        memcpy(dst, name, strlen(name) + 1);
}

static void pad_mapping_install(void) {
    if (install(0x0063cd80u, pad_defaults_hook, POP_HOOK_REPLACE, 120) != POP_OK ||
        install(0x006284d0u, pad_table_defaults_hook, POP_HOOK_REPLACE, 121) != POP_OK)
        g_api->log(g_api, "core.nfsmw: controller defaults hooks unavailable");
    if (install(0x00628230u, pad_names_hook, POP_HOOK_REPLACE, 122) != POP_OK)
        g_api->log(g_api, "core.nfsmw: controller button names unavailable (hook 00628230)");
}

/* TEST ONLY (NFSMW_TEST_PADMAP=1, offscreen): a remap must survive later frames and a controller re-creation, and
 * the Controls screen must show the controller's own names. Logs "padmap:" lines; changes nothing otherwise. */
static void pad_mapping_test(const PopModApi *api) {
    static int on = -1;
    static unsigned frame;
    if (on < 0) on = getenv("NFSMW_TEST_PADMAP") != NULL;
    if (!on) return;
    ++frame;
    char line[160];
    if (frame == 300) {   /* what the Controls screen writes when the player presses R2 for Gas and L2 for Brake */
        pad_binding(0, 0, 7, 1);
        pad_binding(1, 0, 2, 6);
        api->log(api, "padmap: remapped gas to R2 (axis) and brake to L2 (button)");
    }
    if (frame == 400) {   /* the controller created again (0063f850 tail-jumps here) */
        uint32_t eax = 0;
        PopModStatus st = api->guest_call(api, 0x0063cd80u, 0, NULL, 0, &eax);
        snprintf(line, sizeof line, "padmap: controller defaults called (%d)", (int)st);
        api->log(api, line);
    }
    if (frame == 600) {
        uint32_t buf = 0;
        snprintf(line, sizeof line, "padmap: gas %u/%u brake %u/%u shift-up %u/%u handbrake %u/%u",
                 get_u32(GAME_BINDINGS + 28), get_u32(GAME_BINDINGS + 36), get_u32(GAME_BINDINGS + 52 + 28),
                 get_u32(GAME_BINDINGS + 52 + 36), get_u32(GAME_BINDINGS + 8 * 52 + 28),
                 get_u32(GAME_BINDINGS + 8 * 52 + 36), get_u32(GAME_BINDINGS + 4 * 52 + 28),
                 get_u32(GAME_BINDINGS + 4 * 52 + 36));
        api->log(api, line);
        if (api->guest_alloc(api, 0x104, &buf) == POP_OK) {
            static const uint32_t rows[] = {0, 1, 2, 4, 10, 16};
            for (unsigned i = 0; i < sizeof rows / sizeof rows[0]; ++i) {
                uint32_t args[4] = {buf, 3, rows[i], 1}, eax = 0;
                char *text = NULL;
                api->guest_call(api, 0x00628230u, 0, args, 4, &eax);
                api->guest_ptr(api, buf, 0x40, (void **)&text);
                snprintf(line, sizeof line, "padmap: row %u shows \"%.40s\"", rows[i], text ? text : "?");
                api->log(api, line);
            }
            api->guest_free(api, buf);
        }
    }
}
