/* Underglow in the game's own customise > Visual menu (CustomizeSub, 007BB870 family). Visual lists 0x301..0x307; a
 * 0x308 entry is appended there and opens a CustomizeSub page of eight colour options built with the same
 * CustomizeCategoryScreen::AddCustomOption (007BB560) the retail pages use (compare SetupDecalPositions 007BCBB0).
 * Hovering a colour previews it (written to the per-car store), Accept keeps it, leaving any other way restores the
 * colour the page opened with. Off is the default for every car; only owned career cars with a valid identity get the
 * entry. Hooks: AddCustomOption 007BB560 (SetupVisual 007BC210 calls it), Setup 007BFBD0, and the notification wrapper in maxperf_career.c. */
#include "host.h"
#include "underglow.h"
#include <stdlib.h>
#include <string.h>

#define UG_MODE 0x308u                 /* category id and CustomizeSub page mode */
#define UG_LIGHT_MODE 0x309u
#define UG_POOL_MODE 0x30Au
#define H_POOL (H_TITLE + 12u)
#define H_LIGHT (H_TITLE + 9u)
#define UG_ID0 0x901u                  /* colour option ids 0x901..0x908 = presets 0..7 (until retargeted at Visual) */
#define H_TITLE 0x55470A00u            /* label hashes: answered by underglow_menu_text only when the game has none */
#define H_ICON 0x028F88BCu             /* retail icon texture reused (the HUD colour entry's) */
/* Option i stores preset i, an RGB bit mask (R=1, G=2, B=4; underglow_photometry.c): labels follow the mask. */
static const char *k_names[8] = {"Off", "Red", "Green", "Yellow", "Blue", "Magenta", "Cyan", "White"};

/* One global pool choice, separate from per-car colour/Light sidecars.
 * Preview touches memory; Accept alone publishes the app preference. */
static unsigned pool_strength;
static int pool_loaded, pool_override=-1;
unsigned underglow_pool_strength(void) {
    if(!pool_loaded) {
        const char *v=getenv("NFS_UNDERGLOW_STRENGTH");
        if(v && !strcmp(v,"measured")) pool_override=0;
        else if(v && !strcmp(v,"stronger")) pool_override=1;
        v=app_pref("cars.underglow_pool");
        pool_strength=v && !strcmp(v,"stronger");
        pool_loaded=1;
    }
    return pool_override>=0 ? (unsigned)pool_override : pool_strength;
}
static void pool_preview(unsigned strength) {
    underglow_pool_strength();
    if(strength<2 && pool_override<0) pool_strength=strength;
}
static void pool_save(void) {
    if(pool_override<0) app_pref_set("cars.underglow_pool",pool_strength ? "stronger" : "measured");
}

uint32_t underglow_menu_text(uint32_t hash) {   /* guest copy of the label, 0 when the hash is not ours */
    static uint32_t guest[16];
    if (hash < H_TITLE || hash > H_TITLE + 15) return 0;
    uint32_t i = hash - H_TITLE;
    if (!guest[i]) {
        const char *extra[]={"Road pool: Measured","Road pool: Stronger","Measured","Stronger"};
        guest[i] = gstr(i>=12 ? extra[i-12] : i == 9 ? "Light: Off" : i == 10 ? "Light: On" : i == 11 ? "On" : i ? k_names[i - 1] : "Underglow");
    }
    return guest[i];
}

static uint32_t car_handle(uint32_t *profile) {
    uint32_t db = M32(0x0091CF90u), row = M32(0x009B9EC4u);
    *profile = db ? M32(db + 0x10) : 0;
    return row ? M32(row) : 0xFFFFFFFFu;
}
static int owned(void) {
    uint32_t p, h = car_handle(&p);
    if (p) underglow_adopt(p);
    int ok = p && underglow_owned(p, h);
    if (diag_on && !ok) {
        uint32_t row = p ? p + 0x414 + h * 20 : 0;
        diag_event("underglow menu: car not eligible profile=%08X handle=%u cur=%08X row=%08X id=%u kind=%u custom=%u", p, h,
                   M32(0x009B9EC4u), row, row ? M32(row) : 0, row ? M32(row + 12) & 6 : 0, row ? G[row + 16] : 0);
    }
    return ok;
}

static struct { int open, committed; unsigned saved, shown, light, shown_light, pool, shown_pool, mode; uint32_t profile, handle, opts[10]; } st;
static void settle(void) {                 /* Back restores the opening colour and light choice. */
    if (st.open && !st.committed) {
        underglow_set(st.profile, st.handle, st.saved);
        underglow_light_set(st.profile, st.handle, st.light);
        if(st.mode==UG_POOL_MODE) pool_preview(st.pool);
        if (diag_on) diag_event("underglow menu: cancelled, restored %u (stored %u)", st.saved, underglow_get(st.profile, st.handle));
    }
    st.open = 0;
}

static uint32_t last_added;
/* IconScrollerMenu::AddOption (fe_console.c hook) reports every option the page takes. */
void underglow_menu_option_added(uint32_t opt) { last_added = opt; }

static uint32_t add(Cpu *c, uint32_t self, uint32_t label, uint32_t id) {
    /* Retail passes (pkg, icon texture, label hash, id): the HUD entry's constant 0x78980A6B is "Custom Gauges". */
    uint32_t a[4] = {M32(0x00905EACu), H_ICON, label, id};
    c->ecx = self;
    last_added = 0;
    call_guest(c, 0x007BB560u, 4, a);
    uint32_t opt = last_added;
    if (opt && (M32(opt + 0x50) & 0xFFFFu) != id) opt = 0;
    /* The category/new/lock state is retail's per-id unlock table, which does not know our ids: show them plain. */
    if (opt) M32(opt + 0x54) = 1;
    return opt;
}

void f_007BB560_orig(Cpu *c);
void f_007BB560(Cpu *c) {                  /* CustomizeCategoryScreen::AddCustomOption (this, pkg, label, icon, id) */
    uint32_t self = c->ecx, id = arg(c, 3);
    settle();
    f_007BB560_orig(c);
    /* SetupVisual's last entry is HUD colour 0x307: ours follows it, before the page is finalised. */
    if (id != 0x307u || M32(self + 0x150) != 0x803u || !owned()) return;
    Cpu saved = *c;
    add(c, self, H_TITLE, UG_MODE);
    *c = saved;
    if (diag_on) diag_event("underglow menu: Visual entry added");
}

static unsigned colour_index(uint32_t self) {   /* retail current option at +38; 10 means none */
    uint32_t opt = M32(self + 0x38);
    for (unsigned i = 0; i < 10; ++i) if (opt && opt == st.opts[i]) return i;
    return 10;
}

void f_007BFBD0_orig(Cpu *c);
void f_007BFBD0(Cpu *c) {                  /* CustomizeSub::Setup */
    uint32_t self = c->ecx;
    f_007BFBD0_orig(c);
    unsigned mode=M32(self+0x150);
    if ((mode != UG_MODE && mode != UG_LIGHT_MODE && mode != UG_POOL_MODE) || !owned()) return;
    settle();
    Cpu saved = *c;
    st.profile = 0;
    st.handle = car_handle(&st.profile);
    st.saved = st.shown = underglow_get(st.profile, st.handle);
    st.light = st.shown_light = underglow_light_get(st.profile, st.handle);
    st.pool = st.shown_pool = underglow_pool_strength();
    st.mode = mode;
    memset(st.opts,0,sizeof st.opts);
    M32(self + 0x1B0) = mode==UG_MODE ? H_TITLE : mode==UG_LIGHT_MODE ? H_LIGHT+st.light : H_POOL+st.pool;
    M32(self + 0x14C) = M32(mode==UG_MODE ? 0x00905EA8u : 0x00905EACu);
    M32(self+0x154)=mode==UG_MODE ? 0x803u : UG_MODE;
    unsigned count=mode==UG_MODE ? 8 : 2;
    for (unsigned i = 0; i < count; ++i)
        st.opts[i] = add(c,self,mode==UG_MODE ? H_TITLE+1+i : mode==UG_POOL_MODE ? H_POOL+2+i : i ? H_TITLE+11 : H_TITLE+1,UG_ID0+i);
    if (mode==UG_MODE) st.opts[8]=add(c,self,H_LIGHT+st.light,UG_LIGHT_MODE);
    if (mode==UG_MODE) st.opts[9]=add(c,self,H_POOL+st.pool,UG_POOL_MODE);
    st.open = 1; st.committed = 0;
    uint32_t init = (mode==UG_MODE ? st.saved : mode==UG_LIGHT_MODE ? st.light : st.pool)+1;
    c->ecx = self + 0x2C;
    call_guest(c, M32(M32(self + 0x2C) + 0x40), 1, &init);
    /* Colours return to Visual; the light's native Off/On choices return to
     * Underglow. The Light row itself opens this same retail package in 309. */
    for (unsigned i = 0; i < count; ++i)
        if (st.opts[i]) {
            M32(st.opts[i]+0x4C)=M32(mode==UG_MODE ? 0x00905EA8u : 0x00905EACu);
            M32(st.opts[i]+0x50)=mode==UG_MODE ? (UG_MODE<<16)|0x803u : (mode<<16)|UG_MODE;
        }
    c->ecx = self;
    call_guest(c, M32(M32(self) + 0xC), 0, NULL);
    *c = saved;
    if (diag_on) diag_event("underglow menu: page opened car=%u saved=%u index=%u", st.handle, st.saved, colour_index(self));
}

/* After retail has handled a CustomizeSub notification: the hover preview and the Accept commit. */
void underglow_menu_notify(uint32_t self, uint32_t msg) {
    if (!st.open) return;
    if (M32(self+0x150)!=st.mode) { settle(); return; }
    unsigned i=colour_index(self);
    unsigned count=st.mode==UG_MODE ? 8 : 2;
    if (i<count) {
        if (st.mode==UG_MODE && i!=st.shown) {
            st.shown=i; underglow_set(st.profile,st.handle,i);
        } else if (st.mode==UG_LIGHT_MODE && i!=st.shown_light) {
            st.shown_light=i; underglow_light_set(st.profile,st.handle,i);
        } else if (st.mode==UG_POOL_MODE && i!=st.shown_pool) {
            st.shown_pool=i; pool_preview(i);
        }
    }
    if (msg==0x0C407210u && i<count) {
        st.committed=1;
        if(st.mode==UG_POOL_MODE) pool_save();
        if (diag_on && st.mode==UG_MODE) diag_event("underglow menu: accepted preset=%u label=%s", i, k_names[i]);
    }
    else if (msg==0xE1FDE1D1u) settle();
}
