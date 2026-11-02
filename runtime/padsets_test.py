#!/usr/bin/env python3
"""Default/Drag storage and retail serialization, actual hooks + translated save/load.
ASan/UBSan; synthetic memory and scratch files; no game or input devices.
PASS: 934 checks / 0 failures; pinned job 1: 99 / 545 failures.
Broken conflict clearing / Default serialization / newer-edit retention / retry
state: 1 / 22 / 2 / 1 failures out of 934 checks (all rejected).
Historical source is stored as text/hash deltas; verified PASS with Git unavailable.
"""
from pathlib import Path
import hashlib
import os
import re
import subprocess
import tempfile

R = Path(__file__).resolve().parent

def pinned_source(file, edits, digest):
    """Restore stored historical text; hashes pin both edited spans and full C."""
    text = (R/file).read_text()
    # Timing wrappers do not alter the stored binding baseline or its line offsets.
    if file == 'padbind.c':
        text = text.removeprefix('#include "save_timing.h"\n')
        text = text.replace('SAVE_TIME("stream.close", fclose(f))', 'fclose(f)')
        text = text.replace('SAVE_TIME("rename", rename(tmp, p))', 'rename(tmp, p)')
    lines = text.splitlines(keepends=True)
    for start, end, expected, original in reversed(edits):
        assert hashlib.sha256(''.join(lines[start:end]).encode()).hexdigest() == expected, file
        lines[start:end] = original.splitlines(keepends=True)
    source = ''.join(lines)
    assert hashlib.sha256(source.encode()).hexdigest() == digest, file
    return source


# 1c28036af8cc43427779c9fd3494119d81ec2a02:runtime/padbind.c; SHA-256 verifies the stored source.
PINNED_JOB1 = pinned_source('padbind.c', (
    (17, 24, 'a22b364cf2bfb0206b8d9b3d6a16068a3a70de9fd2cb6e8244e06ff13337d445', r'''//   brake, R3 speedbreaker, R2 / L2 shift up / down; in menus Square, Create and Options for the keys 2 / 3 / 4).
//   Menus: Cross accepts, Triangle goes back (FE_ACTION_ACCEPT / CANCEL), as the PS2. The PS button and the touchpad
//   click are bindable but bound to nothing (the touchpad click opens the port menu). Other pads use separate PS2/Xbox layout variants, with independent Z/Rz trigger axes.
// - Each controller type (the joystick's name) keeps its own set per profile: the game saves the active one in the
//   profile, this file all of them in USERDATA/pad_bindings.txt (published after a career commit; type changes stay
//   dirty in memory until that save), so
//   loading a profile with another pad, or changing pads while playing, brings that pad's own set back.
'''),
    (46, 53, 'cc46e4663ce4af6db2072f46e7d03996b527396f8051e15575dfadd9d0611c4c', r'''// user32.m's P_* (the PlayStation pad's DirectInput buttons)
enum { P_CROSS, P_CIRCLE, P_SQUARE, P_TRIANGLE, P_L1, P_R1, P_CREATE, P_OPTIONS, P_L3, P_R3, P_L2, P_R2, P_PS, P_TOUCHPAD,
       P_LPADDLE, P_RPADDLE };
enum { A_GAS, A_BRAKE, A_GAMEBREAKER = 5, A_SHIFTDOWN = 7, A_SHIFTUP = 8, A_NEXTSONG = 24, A_FE_B0 = 37, A_FE_B3 = 39, A_FE_B4 = 40 };

static uint32_t rec(int a, int slot) { return GA + 0x34u * (uint32_t)a + 4 + 12u * (uint32_t)slot; }
static void set_rec(uint32_t r, uint32_t t, uint32_t d, uint32_t o) { M32(r) = t; M32(r + 4) = d; M32(r + 8) = o; }
'''),
    (67, 75, '14868354f02d02d262e20745c5c0eb3601006d8ddfe20cfedbcf5a17bcb940d0', r'''static uint32_t legacy_defaults[GA_PAGE][2][3], nonps_defaults_page[GA_PAGE][2][3];
static int legacy_defaults_valid;
static int active_layout;
static uint32_t joy(void) { uint32_t d = M32(JOY); return d && d != 0xFFFFFFFCu ? d : 0; }
static char *joy_name(uint32_t dev) { return (char *)G + dev + 0x114; }
static const char *action_name(int a) { const char *n = gptr(M32(GA + 0x34u * (uint32_t)a)); return n ? n : "?"; }
'''),
    (235, 242, '1fd8aeda0e157fca543f39d9fcc2ef0ac09a346ab5be0ad4f09434d74e8f13fa', r'''void f_0063CD80(Cpu *c) {                      // cdecl (void): the joystick's defaults, by its name
    uint32_t dev = joy();
    if (!dev) { f_0063CD80_orig(c); return; }
    char name[64];
    snprintf(name, sizeof name, "%s", joy_name(dev));
    int ps = name_ps(name);
'''),
    (258, 273, '52f62c05d65411531a7f63dcc4bda04637f51b1924feeb9084bc035fbdb98a03', r'''                nonps_defaults_page[a][k][i] = M32(rec(a, k + 2) + 4u * (uint32_t)i);
    }
    active_layout = layout_for(name);
    char why[96];
    snprintf(why, sizeof why, "defaults for %s", name);
    dump(why);
}

// ---------------- the sets per controller type (USERDATA/pad_bindings.txt) ----------------
typedef struct { char profile[33], name[64]; uint32_t hash, r[GA_PAGE][2][3]; int layout; } Set;
static Set *sets;
static int nsets, sets_loaded, sets_dirty, migration_backup_needed;
static void sets_path(char *p, size_t n) { snprintf(p, n, "%s/pad_bindings.txt", userdata_dir()); }
'''),
    (281, 291, 'bf6e8e190ed73f709496f82d09bb95bc19048b6e30d6a9938f8490f09555bde8', r'''    sets_path(p, sizeof p);
    FILE *f = fopen(p, "r");
    if (!f) return;
    int v2 = 0;
    while (fgets(line, sizeof line, f)) {
        if (!strncmp(line, "# NFSMW pad bindings v2", 22)) v2 = 1;
        if (line[0] == '#' || line[0] == '\n') continue;
        Set row = {0};
        char *tab1 = strchr(line, '\t'), *tab2 = tab1 ? strchr(tab1 + 1, '\t') : NULL;
'''),
    (294, 301, '51a9588e13e322145dd1accf2c24f7ddcdb27d55e1d54581c41c73b34ce6cb05', r'''        row.hash = (uint32_t)strtoul(tab1 + 1, NULL, 16);
        snprintf(row.name, sizeof row.name, "%.*s", (int)(tab3 - tab2 - 1), tab2 + 1);
        char *q = tab3 + 1;
        if (v2) {
            char *tab4 = strchr(q, '\t');
            if (!tab4) continue;
            *tab4 = 0;
'''),
    (304, 318, 'f94d9644a4bfefbf78b03399ab2eb03468970837b6aaeeb7a8b959072869e2b4', r'''            row.layout = layout;
            q = tab4 + 1;
        } else row.layout = name_ps(row.name) || ps_hash(row.hash) ? L_NATIVE_PS : L_LEGACY;
        int ok = 1;
        for (int a = 0; a < GA_PAGE && ok; ++a)
            for (int k = 0; k < 2 && ok; ++k) {
'''),
    (320, 333, 'f5c1524cb52a7266a3c2ea458f05909cfcff5c87fe239a0497254aa2a5f01ea1', r'''                q += used;
            }
        if (!ok) continue;
        sets = realloc(sets, (size_t)(nsets + 1) * sizeof *sets);
        sets[nsets++] = row;
    }
    fclose(f);
}
// Keep the original text file byte-for-byte, once, before a migration writes v2. Legacy rows also stay in v2.
static int migration_backup(void) {
    char p[1100], backup[1110];
    sets_path(p, sizeof p); snprintf(backup, sizeof backup, "%s.v1", p);
'''),
    (352, 363, '471c5ca0e711d35e54ef1eebe007db2062fbe653464af9524acb5f05ccfadff1', r'''    sets_path(p, sizeof p); snprintf(tmp, sizeof tmp, "%s.tmp", p);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "# NFSMW pad bindings v2\n# profile, name hash, name, layout, 19 primary/secondary type,device,object triples\n"
               "# legacy rows are rollback copies; native_ps values and hashes are never migrated.\n");
    for (int i = 0; i < nsets; ++i) {
        fprintf(f, "%s\t%08X\t%s\t%s\t", sets[i].profile, sets[i].hash, sets[i].name, layout_names[sets[i].layout]);
        for (int a = 0; a < GA_PAGE; ++a)
            for (int k = 0; k < 2; ++k) fprintf(f, " %u,%u,%u", sets[i].r[a][k][0], sets[i].r[a][k][1], sets[i].r[a][k][2]);
        fputc('\n', f);
'''),
    (366, 397, 'c7a19ceccc86f31ee305d4e91049eef4a205762b9511041bb60ad074eb8012f5', r'''    if (ok && !rename(tmp, p)) sets_dirty = migration_backup_needed = 0;
    else remove(tmp);
}
static Set *set_find(const char *profile, uint32_t hash, int layout) {
    sets_load();
    for (int i = 0; i < nsets; ++i)
        if (sets[i].hash == hash && sets[i].layout == layout && !strcmp(sets[i].profile, profile)) return &sets[i];
    return NULL;
}
static Set *set_put(const char *profile, uint32_t hash, const char *name, int layout) {
    Set *row = set_find(profile, hash, layout);
    if (!row) {
        sets = realloc(sets, (size_t)(nsets + 1) * sizeof *sets);
        row = &sets[nsets++];
        memset(row, 0, sizeof *row);
        snprintf(row->profile, sizeof row->profile, "%s", profile);
        row->hash = hash; row->layout = layout;
        sets_dirty = 1;
    }
    if (name && *name && strcmp(row->name, name)) { snprintf(row->name, sizeof row->name, "%s", name); sets_dirty = 1; }
    return row;
}
static void set_from_table(Set *row) {
    for (int a = 0; a < GA_PAGE; ++a)
'''),
    (443, 686, 'dd582deb9c8e918a2434ef2e8415394042250312a0e48b8d791716be20f99ebd', r'''    if (diag_on) diag_event("padbind: migrated legacy %08X to %s (rollback retained)", hash, layout_names[layout]);
    return row;
}
static void install_defaults(Cpu *c) {
    uint32_t esp = c->esp, eax = c->eax, ecx = c->ecx, edx = c->edx;
    call_guest(c, 0x0063CD80u, 0, NULL);
    c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
}

uint32_t f_0056E180_orig(Cpu *c);
// BEGIN offline bindings transaction: no disk writes until the career save task publishes its file.
static Set pending_bindings;
static int pending_bindings_valid;
static void bindings_stage(const char *profile, const char *name, int layout) {
    memset(&pending_bindings, 0, sizeof pending_bindings);
    snprintf(pending_bindings.profile, sizeof pending_bindings.profile, "%s", profile);
    snprintf(pending_bindings.name, sizeof pending_bindings.name, "%s", name);
    pending_bindings.hash = name_hash(name); pending_bindings.layout = layout;
    int dirty = sets_dirty;
    set_from_table(&pending_bindings);
    sets_dirty = dirty;
    pending_bindings_valid = 1;
}
void padbind_save_complete(int committed) {
    if (committed) {
        if (pending_bindings_valid) {
            Set *row = set_put(pending_bindings.profile, pending_bindings.hash, pending_bindings.name, pending_bindings.layout);
            *row = pending_bindings; sets_dirty = 1;
        }
        sets_save();
    }
    pending_bindings_valid = 0;
}
// END offline bindings transaction
void f_0056E180(Cpu *c) {
    pending_bindings_valid = 0;
    f_0056E180_orig(c);
    uint32_t dev = joy();
    if (!dev) return;
    const char *name = joy_name(dev);
'''),
    (688, 695, '13b999c9164c2013af8be29b52802c3db69651ed49eae7b14da9d6aeabee8dd1', r'''}
void f_0056E200_orig(Cpu *c);
void f_0056E200(Cpu *c) {
    uint32_t buf = arg(c, 0), end = arg(c, 1), dev = joy();
    char cur[64] = "";
    if (dev) snprintf(cur, sizeof cur, "%s", joy_name(dev));
'''),
    (702, 709, '7119ef6d211b3df67fbaa2a2349f4f5095db7cfa98e5a83b0ad64c8cdea9247b', r'''    // A stale sidecar must never override or migrate a DualSense profile loaded with that same pad.
    if (name_ps(cur) && saved == cur_hash) {
        active_layout = L_NATIVE_PS;
        dump("profile loaded (native PS unchanged)");
        return;
    }
'''),
    (717, 724, '77ae625ba0fddcce927d19668deed828ec2a7435a5ce316d05b278d89cbcf71c', r'''    install_defaults(c);                     // menus/defaults for this identity and layout, joystick slots only
    Set *mine = set_for(prof, cur, layout_for(cur));
    if (mine) set_to_table(mine);
    // Migration stays dirty in memory until a career commit succeeds.
    dump("profile loaded (layout variant)");
}
'''),
    (776, 783, '0ddd7bc84cba12d5b09b421eaf5d0941118f3c3b16a8aa73fe6e6ddb692a675c', r'''    if (!fe || fe == 0xFFFFFF28u) return 0;
    for (uint32_t p = M32(fe + 0xE4); p; p = M32(p + 8)) {
        const char *n = gptr(M32(p + 0xC));
        if (n && strcasestr(n, "Options_PC_Controller")) return 1;
    }
    return 0;
}
'''),
    (829, 838, '0fa4b4aaef2a5339c298dbbf5e5dc9b99bad7a908d597a863fcdd3d9b0f72c3a', r'''static int swap_on;
void padbind_swap(int on) { swap_on = on; }
static void follow_pad(Cpu *c) {
    uint32_t dev = joy();
    char want[64];
    if (!dev || pad_identity(want, sizeof want) == PAD_KIND_NONE || !want[0]) return;
'''),
    (841, 854, 'a4d980c9ac8d4f39705e43194cfa9f197d83f6d9c3bbb9359ed37439fa24782e', r'''    const char *prof = profile_name();
    char old[64];
    snprintf(old, sizeof old, "%s", joy_name(dev));
    set_from_table(set_put(prof, name_hash(old), old, active_layout ? active_layout : layout_for(old)));
    snprintf(joy_name(dev), 64, "%s", want);
    install_defaults(c);
    Set *row = set_for(prof, want, layout);
    if (row) set_to_table(row);
    // Controller variants stay dirty in memory until a career commit succeeds.
    fprintf(stderr, "boot: controller bindings: %s -> %s / %s (%s set)\n", old, want, layout_names[layout], row ? "its own" : "default");
    char why[224];
'''),
    (912, 923, '9d251b9ec54401ff63902e765c8c8aefdd63cb2b927686a11493f527bb53a7ac', r'''    uint32_t dev = joy();
    if (slot < 1 || slot > 17 || !ctl[slot].t || !dev || !name_ps(joy_name(dev))) return fallback;
    for (int a = 0; a < GA_PAGE; ++a) {
        for (int s = 2; s < 4; ++s)
            if (M32(rec(a, s)) == ctl[slot].t &&
                M32(rec(a, s) + 8) == ctl[slot].o) return label[a];
    }
    return 0;
}
'''),
    (931, 942, 'decdbdc74da4949fb1612750c2c44a8fe5081df52504c184a48a3b4888e66315', r'''    uint32_t axis = right ? T_ROT_HI : T_AXIS_HI, button = right ? P_R2 : P_L2;
    int best = PADBIND_ROLE_NONE;
    for (int a = 0; a < GA_PAGE; ++a) {
        for (int s = 2; s < 4; ++s) {
            uint32_t t = M32(rec(a, s));
            uint32_t o = M32(rec(a, s) + 8);
            if (!((t == axis && o == 2) || (t == T_BUTTON && o == button))) continue;
            int role = a == A_GAS ? PADBIND_ROLE_GAS : a == A_BRAKE ? PADBIND_ROLE_BRAKE :
                       a == A_SHIFTUP || a == A_SHIFTDOWN ? PADBIND_ROLE_SHIFT : PADBIND_ROLE_OTHER;
'''),
), '2fb77f00e0c0830650fc70bf7862582938f49694d04982ba406ef7d6fc55078b')
PRE = r'''
#include "host.h"
#include <sys/stat.h>
#include <unistd.h>
#include "settings.h"
uint8_t *G;
int diag_on;
void settings_add(Setting *s) { (void)s; }
const char *diag_dir="";
static const char *scratch;
const char *userdata_dir(void) { return scratch; }
void diag_event(const char *fmt,...) { (void)fmt; }
int user32_pad_layout_xbox(void) { return 1; }
int portmenu_is_open(void) { return 0; }
int pad_count(void) { return 1; }
int pad_identity(char *name,size_t n) { snprintf(name,n,"Controller (DualSense)"); return PAD_KIND_DUALSENSE; }
int pad_identity_n(int p,char *name,size_t n) { (void)p; return pad_identity(name,n); }
void pedals_trace_sample(unsigned s,float l,float r) { (void)s;(void)l;(void)r; }
void f_006280C0_orig(Cpu *c) { (void)c; }
void f_00634900_orig(Cpu *c) { (void)c; }
void f_004F6EA0_orig(Cpu *c) { (void)c; }
void f_0063CD80_orig(Cpu *c) { c->esp+=4; }
void f_004D6150(Cpu *c) { c->eax=0; c->esp+=4; }
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) { (void)c;(void)fn;(void)n;(void)args; return 0; }
'''
PLUMBING = r'''
static uint32_t evaluated[19][6];
void f_006349B0_orig(Cpu *c) { for(int a=0;a<19;a++) memcpy(evaluated[a],G+rec(a,2),24); c->esp+=4; }
void f_00634B50_orig(Cpu *c) { c->esp+=4; }
void f_00628940(Cpu *c) { (void)c; }
void f_00634370(Cpu *c) { c->eax=0x4000; M32(0x4008)=0x5000; c->esp+=4; }
void f_00634DA0(Cpu *c) { c->eax=name_hash(joy_name(joy())); c->esp+=8; }
void f_00628CC0(Cpu *c) { c->eax=0x30; c->esp+=4; }
void f_00628CB0(Cpu *c) { c->eax=GA+0x34*arg(c,0)+4; c->esp+=8; }
'''
OLD_API = r'''
static Set *set_find_kind(const char *p,uint32_t h,int l,int s) { return s==0?set_find(p,h,l):NULL; }
int padbind_edit_set(int s) { return s==0; }
int padbind_input_set(void) { return 0; }
void padbind_race_update(Cpu *c) { (void)c; }
int padbind_effective_binding(int s,int a,uint32_t out[6]) { (void)s; memcpy(out,G+rec(a,2),24); return 1; }
int padbind_input_begin(int s) { (void)s; return 0; }
void padbind_input_end(int s) { (void)s; }
'''
MAIN = r'''
static int checks,failures;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; if(failures<12) printf("FAIL line %d: %s\n",__LINE__,#x); } } while(0)
static Cpu cpu(void) { Cpu c={0}; c.esp=0x600000; M32(c.esp)=0; return c; }
static void reset(void) { free(sets); sets=NULL; nsets=sets_loaded=sets_dirty=migration_backup_needed=0; pending_bindings_valid=0; }
static void fixture(int version) {
    char path[1100]; sets_path(path,sizeof path); FILE *f=fopen(path,"w");
    fprintf(f,"# NFSMW pad bindings v%d\n",version);
    for(int row=0;row<3;row++) {
        const char *name=row==0?"Controller (DualSense)":XBOX_NAME;
        fprintf(f,"P%d\t%08X\t%s\t",row,name_hash(name),name);
        if(version>=2) fprintf(f,"%s\t",layout_names[row==0?L_NATIVE_PS:row==1?L_LEGACY:L_NONPS_XBOX]);
        for(int a=0;a<19;a++) for(int k=0;k<2;k++) fprintf(f," %u,%u,%u",(unsigned)(a%10),100u+row,200u+6*a+3*k);
        fputc('\n',f);
    }
    fclose(f);
}
static void save(int hooked,uint32_t buf) {
    Cpu c=cpu(); M32(c.esp+4)=buf; M32(c.esp+8)=buf+0x394;
    if(hooked) f_0056E180(&c); else f_0056E180_orig(&c);
    CHECK(c.eax==buf+0x394 && c.esp==0x600004);
}
int main(int argc,char **argv) {
    (void)argc; scratch=argv[1]; mkdir(scratch,0700); G=calloc(1,0x1000000);
    M32(JOY)=0x100000; strcpy(joy_name(joy()),"Controller (DualSense)");
    M32(FEDB)=0x2000; M32(0x2010)=0x3000; strcpy((char *)G+0x3000,"Player"); active_layout=L_NATIVE_PS;
    char path[1100]; sets_path(path,sizeof path);
    for(int v=1;v<=2;v++) {
        reset(); fixture(v); sets_load(); CHECK(nsets==3);
        for(int row=0;row<3;row++) {
            char prof[8]; snprintf(prof,sizeof prof,"P%d",row);
            int layout=row==0?L_NATIVE_PS:v==1?L_LEGACY:row==1?L_LEGACY:L_NONPS_XBOX;
            Set *s=set_find_kind(prof,name_hash(row==0?"Controller (DualSense)":XBOX_NAME),layout,PADBIND_DEFAULT);
            CHECK(s!=NULL);
            if(s) for(int a=0;a<19;a++) for(int k=0;k<2;k++) {
                CHECK(s->r[a][k][0]==(uint32_t)(a%10)); CHECK(s->r[a][k][1]==100u+row);
                CHECK(s->r[a][k][2]==200u+6*a+3*k);
            }
        }
        // Upgrade and reload all rows without altering any Default triple.
        if(nsets==3) {
        Set saved[3]; memcpy(saved,sets,sizeof saved);
        for(int i=0;i<3;i++) { Set copy=saved[i];
#if !OLD_BINDINGS
            CHECK(drag_for(copy.profile,copy.name,copy.layout)!=NULL);
#endif
        }
        sets_dirty=1; padbind_save_complete(1); reset(); sets_load();
        for(int i=0;i<3;i++) {
            Set *row=set_find(saved[i].profile,saved[i].hash,saved[i].layout);
            CHECK(row && !memcmp(row->r,saved[i].r,sizeof row->r));
        }
        }
    }
    reset(); unlink(path);
    for(int a=0;a<19;a++) for(int k=0;k<4;k++) set_rec(rec(a,k),k<2?T_KEY:T_BUTTON,k<2?30+a:0,40+4*a+k);
    set_rec(rec(0,3),T_ROT_LO,0,1); set_rec(rec(1,3),T_ROT_HI,0,1);
    set_rec(rec(4,2),T_BUTTON,0,P_R1); set_rec(rec(6,3),T_BUTTON,0,P_L1);
    set_rec(rec(7,2),T_AXIS_HI,0,2); set_rec(rec(8,3),T_ROT_HI,0,2);
    uint8_t original[19*52]; memcpy(original,G+GA,sizeof original);
    save(0,0x700000); uint8_t retail[0x394]; memcpy(retail,G+0x700000,sizeof retail);
    Cpu loaded=cpu(); M32(loaded.esp+4)=0x700000; M32(loaded.esp+8)=0x700394;
    f_0056E200(&loaded); // seed once from the loaded Default page
    CHECK(set_find_kind("Player",name_hash(joy_name(joy())),L_NATIVE_PS,PADBIND_DRAG)!=NULL);
    CHECK(padbind_input_set()==PADBIND_DEFAULT);
    CHECK(padbind_edit_set(PADBIND_DRAG));
    CHECK(M32(rec(0,2))==7 && M32(rec(0,2)+8)==2);
    CHECK(M32(rec(1,2))==5 && M32(rec(1,2)+8)==2);
    CHECK(M32(rec(8,2))==2 && M32(rec(8,2)+8)==P_R1);
    CHECK(M32(rec(7,2))==2 && M32(rec(7,2)+8)==P_L1);
    CHECK(M32(rec(4,2))==0 && M32(rec(6,3))==0 && M32(rec(8,3))==0);
    CHECK(M32(rec(0,3))==T_ROT_LO && M32(rec(1,3))==T_ROT_HI);
    CHECK(padbind_trigger_role(0)==PADBIND_ROLE_SHIFT && padbind_trigger_role(1)==PADBIND_ROLE_SHIFT);
    CHECK(padbind_ps_label(3,0)==0xB70FF4A4u); // Default handbrake while editing Drag
    for(int a=0;a<19;a++) {
        CHECK(!memcmp(G+rec(a,0),original+52*a+4,24));
        set_rec(rec(a,2),T_BUTTON,0,100+a); set_rec(rec(a,3),T_BUTTON,1,200+a);
    }
    // Actual input hook resolves Default while the UI's table contains Drag.
    Cpu input=cpu(); input.ecx=0x500000; f_006349B0(&input);
    for(int a=0;a<19;a++) CHECK(!memcmp(evaluated[a],original+52*a+28,24));
    for(int a=0;a<19;a++) CHECK(M32(rec(a,2)+8)==100u+a);
    // Keyboard edits belong to both pages and remain in the retail bytes.
    set_rec(rec(18,0),T_KEY,77,88); memcpy(retail+4+18*48,G+rec(18,0),24);
    uint8_t edited[19*52]; memcpy(edited,G+GA,sizeof edited);
    save(1,0x710000); CHECK(!memcmp(retail,G+0x710000,sizeof retail));
    CHECK(!memcmp(edited,G+GA,sizeof edited)); CHECK(access(path,F_OK)!=0);
    int dirty=sets_dirty; padbind_save_complete(0);
    CHECK(!pending_bindings_valid && sets_dirty==dirty && access(path,F_OK)!=0);
    CHECK(padbind_edit_set(PADBIND_DEFAULT));
    for(int a=0;a<19;a++) CHECK(!memcmp(G+rec(a,2),original+52*a+28,24));
    CHECK(padbind_edit_set(PADBIND_DRAG));
    for(int a=0;a<19;a++) CHECK(M32(rec(a,2)+8)==100u+a && M32(rec(a,3)+8)==200u+a);
    // A failed sidecar publication must keep its snapshot dirty for retry.
    save(1,0x710000); char tmp[1110]; snprintf(tmp,sizeof tmp,"%s.tmp",path); mkdir(tmp,0700);
    padbind_save_complete(1); CHECK(sets_dirty && access(path,F_OK)!=0); rmdir(tmp);
    save(1,0x710000); set_rec(rec(18,2),T_BUTTON,0,999); padbind_save_complete(1);
    CHECK(!sets_dirty && access(path,F_OK)==0); CHECK(M32(rec(18,2)+8)==999);
    reset(); sets_load(); CHECK(nsets==2);
    Set *d=set_find_kind("Player",name_hash(joy_name(joy())),L_NATIVE_PS,PADBIND_DRAG);
    Set *b=set_find("Player",name_hash(joy_name(joy())),L_NATIVE_PS);
    CHECK(d && b);
    if(d && b) for(int a=0;a<19;a++) {
        CHECK(d->r[a][0][2]==100u+a && d->r[a][1][2]==200u+a);
        CHECK(!memcmp(b->r[a],original+52*a+28,24));
    }
    // A same-pad retail profile beats a stale Default sidecar; Drag stays separate.
    if(b) b->r[0][0][2]=9999;
    Cpu c=cpu(); M32(c.esp+4)=0x710000; M32(c.esp+8)=0x710394; f_0056E200(&c);
    CHECK(!memcmp(retail,G+0x710000,sizeof retail));
    for(int a=0;a<19;a++) CHECK(!memcmp(G+rec(a,2),original+52*a+28,24));
    CHECK(padbind_edit_set(PADBIND_DRAG)); CHECK(M32(rec(0,2)+8)==100);
    CHECK(padbind_edit_set(PADBIND_DEFAULT)); CHECK(M32(rec(0,2)+8)==42);
    // Switching pages while a task saves must retain newer in-memory edits.
    CHECK(padbind_edit_set(PADBIND_DRAG)); set_rec(rec(18,2),T_BUTTON,0,321);
    save(1,0x710000); set_rec(rec(18,2),T_BUTTON,0,654);
    CHECK(padbind_edit_set(PADBIND_DEFAULT)); padbind_save_complete(1);
    d=set_find_kind("Player",name_hash(joy_name(joy())),L_NATIVE_PS,PADBIND_DRAG);
    CHECK(d && d->r[18][0][2]==654); CHECK(sets_dirty);
    reset(); sets_load(); d=set_find_kind("Player",name_hash(joy_name(joy())),L_NATIVE_PS,PADBIND_DRAG);
    CHECK(d && d->r[18][0][2]==321); // disk is the serialization snapshot
    // Every Default action is independently editable too; Drag remains its saved page.
    CHECK(padbind_edit_set(PADBIND_DEFAULT));
    for(int a=0;a<19;a++) { set_rec(rec(a,2),T_POV,0,a); set_rec(rec(a,3),T_BUTTON,0,300+a); }
    CHECK(padbind_edit_set(PADBIND_DRAG));
    for(int a=0;a<18;a++) CHECK(M32(rec(a,2)+8)==100u+a);
    CHECK(M32(rec(18,2)+8)==321 && M32(rec(18,0)+4)==77);
    CHECK(padbind_edit_set(PADBIND_DEFAULT));
    for(int a=0;a<19;a++) CHECK(M32(rec(a,2)+8)==(uint32_t)a && M32(rec(a,3)+8)==300u+a);
    printf("padsets: %d checks, %d failures\n",checks,failures); reset(); free(G); return failures!=0;
}
'''


def translated(name):
    text = (R.parent/'recomp/out/speed_20.c').read_text()
    body = re.search(r'void f_'+name+r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}', text, re.S)[0]
    return re.sub(r'void f_'+name+r'(?:_orig)?\(', 'void f_'+name+'_orig(', body, count=1)


def main():
    if not (R.parent/'recomp/out/speed_20.c').exists() or not (R/'gen/controltype_buttons.inc').exists():
        print('SKIP: needs translated profile bindings and generated controller tables')
        return 3
    source = (R/'padbind.c').read_text()
    old = PINNED_JOB1
    variants = {'normal': source, 'job1': old}
    for name, before, after in (
        ('seed-conflict', 'memset(r, 0, 12);\n        }\n    const int actions', '(void)r;\n        }\n    const int actions'),
        ('serialize-drag', 'int swapped = padbind_input_begin(PADBIND_DEFAULT);', 'int swapped = 0;'),
        ('lose-newer-edit', 'if (restore[i]) {', 'if (0) {'),
        ('lose-retry', 'if (!f) return;\n    fprintf(f,', 'if (!f) { sets_dirty = 0; return; }\n    fprintf(f,'),
    ):
        assert before in source, name
        variants[name] = source.replace(before, after, 1)
    with tempfile.TemporaryDirectory(prefix='padsets-') as tmp:
        tmp = Path(tmp)
        (tmp/"funcs.h").write_text("")
        for name, pad in variants.items():
            pad = pad.replace('uint32_t f_0056E180_orig(Cpu *c);', 'void f_0056E180_orig(Cpu *c);')
            path = tmp/(name+'.c'); exe = tmp/name
            path.write_text('\n'.join(('#define OLD_BINDINGS '+str(int(name == 'job1')), PRE, pad, OLD_API if name == 'job1' else '', PLUMBING, '#include \"controltype.c\"',
                                      translated('0056E180'), translated('0056E200'), MAIN)))
            result = subprocess.run(['clang', '-std=c11', '-g', '-Wno-unused-function', '-fsanitize=address,undefined',
                                     '-fno-sanitize-recover=all', '-I', str(tmp), '-I', str(R), '-I', str(R.parent/'recomp'),
                                     str(path), '-o', str(exe)], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr[-4000:]
            result = subprocess.run([exe, tmp/(name+'-data')], capture_output=True, text=True)
            print(name+': '+result.stdout.strip())
            assert result.returncode == (0 if name == 'normal' else 1), result.stderr[-4000:]
    print('padsets_test: PASS')


if __name__ == '__main__':
    raise SystemExit(main())
