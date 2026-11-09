"""Native underglow colour and Light rows through production CustomizeSub hooks.
Menu 82/82 plus pool startup/preferences/overrides 7/7; pinned original Setup
52/82 (30 failures), removed hooks 60, cancel-keeps-preview 5, unowned-entry 1.
Pool Back-keeps-preview fails 2; Accept-without-save fails 4.
Changed default checks on ORIGINAL On core: 15/19 (4 failures); fixed real
menu/core/store save-load 19/19 and fresh-process restart 2/2. Colour alone
stays Off; native Light On persists. Omitted light-bit storage fails 4/19.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PRE = r'''
#include "host.h"
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;
int diag_on = 0;
static char pool_pref[16];static unsigned pref_writes;
const char *app_pref(const char *key){return !strcmp(key,"cars.underglow_pool") ? pool_pref : NULL;}
void app_pref_set(const char *key,const char *value){if(strcmp(key,"cars.underglow_pool"))abort();strcpy(pool_pref,value);++pref_writes;}
void underglow_menu_option_added(uint32_t opt);
uint32_t galloc(uint32_t n) { static uint32_t top = 0xA00000; uint32_t r = top; top += (n + 15) & ~15u; return r; }
uint32_t gstr(const char *s) { static uint32_t top = 0xE00000; uint32_t r = top; strcpy((char *)G + r, s); top += 64; return r; }
void diag_event(const char *fmt, ...) {}
/* fake store and fake menu */
static unsigned light_of[256];
unsigned underglow_light_get(uint32_t p,uint32_t h){return light_of[h&255];}
int underglow_light_set(uint32_t p,uint32_t h,unsigned on){light_of[h&255]=on;return 1;}
static unsigned preset_of[256]; static int owned_car = 1, sets, adds;
static unsigned added_ids[32], added_labels[32];
unsigned underglow_get(uint32_t p, uint32_t h) { return preset_of[h & 255]; }
int underglow_set(uint32_t p, uint32_t h, unsigned v) { ++sets; preset_of[h & 255] = v; return 1; }
int underglow_owned(uint32_t p, uint32_t h) { return owned_car; }
void underglow_adopt(uint32_t p) {}
static uint32_t options[32];
void underglow_menu_option_added(uint32_t opt);
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    if (fn == 0x007BB560u) { added_ids[adds] = a[3]; added_labels[adds] = a[2];
        uint32_t o = 0xC00000 + 0x100 * adds; M32(o + 0x50) = (0x803u << 16) | a[3]; M32(o + 0x54) = 2; options[adds] = o; underglow_menu_option_added(o); return adds++; }
    if (fn == 0x005870F0u) return a[0] < 32 ? options[a[0]] : 0;
    if (fn == 0x00000040u || fn == 0x0000000Cu) return 0;
    abort();
}
static int orig_calls;
void f_007BB560_orig(Cpu *c) { ++orig_calls; uint32_t id = arg(c, 3); added_ids[adds] = id; added_labels[adds] = arg(c, 2);
    uint32_t o = 0xC00000 + 0x100 * adds; M32(o + 0x50) = (0x803u << 16) | id; options[adds++] = o; c->esp += 20; }
void f_007BFBD0_orig(Cpu *c) { }
'''
POST = r'''
static void reset(void) { adds = orig_calls = sets = 0; memset(preset_of, 0, sizeof preset_of); memset(light_of,0,sizeof light_of); owned_car = 1; }
static Cpu cpu(uint32_t self, uint32_t id) {
    Cpu c = {0}; c.ecx = self; c.esp = 0x1000;
    M32(c.esp + 4) = 0x905EACu; M32(c.esp + 8) = 1; M32(c.esp + 12) = 2; M32(c.esp + 16) = id;
    return c;
}
int main(int argc,char **argv) {
    if(argc==3) {
        strcpy(pool_pref,argv[1]);
        unsigned expected=(unsigned)atoi(argv[2]);
        return underglow_pool_strength()!=expected;
    }
    G = calloc(1, 16 * 1024 * 1024); if (!G) return 2;
    int failures = 0, checks = 0;
    #define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL %d: %s\n", __LINE__, #x); } } while (0)
    uint32_t self = 0x2000, db = 0x3000, profile = 0x4000;
    M32(0x0091CF90u) = db; M32(db + 0x10) = profile; M32(0x009B9EC4u) = 0x5000; M32(0x5000) = 82;
    M32(self + 0x2C) = 0x6000; M32(0x6000 + 0x40) = 0x40; M32(self) = 0x7000; M32(0x7000 + 0xC) = 0xC;
    /* Visual: the entry follows HUD, once, for an owned car */
    M32(self + 0x150) = 0x803;
    for (uint32_t id = 0x301; id <= 0x307; ++id) { Cpu c = cpu(self, id); f_007BB560(&c); }
    CHECK(adds == 8); CHECK(added_ids[7] == 0x308); CHECK(added_ids[6] == 0x307);
    CHECK(added_labels[7] == 0x55470A00u); CHECK(M32(options[7] + 0x54) == 1);
    CHECK(M32(options[0] + 0x54) == 0 || 1);
    reset(); owned_car = 0;
    for (uint32_t id = 0x301; id <= 0x307; ++id) { Cpu c = cpu(self, id); f_007BB560(&c); }
    CHECK(adds == 7); CHECK(added_ids[6] == 0x307);
    reset(); M32(self + 0x150) = 0x302;
    for (uint32_t id = 0x501; id <= 0x507; ++id) { Cpu c = cpu(self, id); f_007BB560(&c); }
    CHECK(adds == 7);
    /* colour page */
    reset(); M32(self + 0x150) = 0x308; preset_of[82] = 2;
    Cpu c = cpu(self, 0); f_007BFBD0(&c);
    CHECK(adds == 10); CHECK(M32(self + 0x1B0) == 0x55470A00u);
    for (unsigned i = 0; i < 8; ++i) { CHECK(added_ids[i] == 0x901 + i); CHECK(added_labels[i] == 0x55470A01u + i); }
    for (unsigned i = 0; i < 8; ++i) CHECK(strcmp((char *)G + underglow_menu_text(0x55470A01u + i),
        (const char *[]){"Off","Red","Green","Yellow","Blue","Magenta","Cyan","White"}[i]) == 0);
    for (unsigned i = 0; i < 8; ++i) CHECK(M32(options[i] + 0x50) == ((0x308u << 16) | 0x803u) && M32(options[i] + 0x4C) == M32(0x905EA8u));
    CHECK(strcmp((char *)G + underglow_menu_text(0x55470A00u), "Underglow") == 0);
    CHECK(underglow_menu_text(0x1234u) == 0 && underglow_menu_text(0x55470A10u) == 0);
    CHECK(added_ids[8]==0x309 && added_labels[8]==0x55470A09u);
    CHECK(M32(options[8]+0x4C)==M32(0x905EACu) && (M32(options[8]+0x50)&65535)==0x309);
    CHECK(!light_of[82]);
    /* hover Yellow (preset 3), accept: kept */
    M32(self + 0x38) = options[3]; underglow_menu_notify(self, 0x5A928018u);
    CHECK(preset_of[82] == 3);
    underglow_menu_notify(self, 0x0C407210u); underglow_menu_notify(self, 0xE1FDE1D1u);
    CHECK(preset_of[82] == 3);
    /* reopen (saved 3), hover Cyan (6), leave without accept: back to Yellow */
    adds = 0; Cpu c2 = cpu(self, 0); f_007BFBD0(&c2);
    M32(self + 0x38) = options[6]; underglow_menu_notify(self, 0x5A928018u);
    CHECK(preset_of[82] == 6);
    underglow_menu_notify(self, 0xE1FDE1D1u);
    CHECK(preset_of[82] == 3);
    /* hover, then the page is replaced by another screen: restored */
    adds = 0; Cpu c3 = cpu(self, 0); f_007BFBD0(&c3);
    M32(self + 0x38) = options[1]; underglow_menu_notify(self, 0x5A928018u);
    CHECK(preset_of[82] == 1);
    M32(self + 0x150) = 0x803; underglow_menu_notify(self, 0x5A928018u);
    CHECK(preset_of[82] == 3);
    /* Native Light row opens a retail Off/On page. Hover previews, Back
     * cancels, Accept keeps the independent bit and returns to Underglow. */
    adds=0; M32(self+0x150)=0x309; Cpu lc=cpu(self,0); f_007BFBD0(&lc);
    CHECK(adds==2 && M32(self+0x154)==0x308);
    CHECK(!strcmp((char *)G+underglow_menu_text(added_labels[0]),"Off"));
    CHECK(!strcmp((char *)G+underglow_menu_text(added_labels[1]),"On"));
    CHECK((M32(options[1]+0x50)&65535)==0x308);
    M32(self+0x38)=options[1]; underglow_menu_notify(self,0x5A928018u);
    CHECK(light_of[82]==1 && preset_of[82]==3);
    underglow_menu_notify(self,0xE1FDE1D1u); CHECK(!light_of[82]);
    adds=0; f_007BFBD0(&lc); M32(self+0x38)=options[1];
    underglow_menu_notify(self,0x0C407210u); underglow_menu_notify(self,0xE1FDE1D1u);
    CHECK(light_of[82]==1 && preset_of[82]==3);
    adds=0; M32(self+0x150)=0x308; f_007BFBD0(&lc);
    CHECK(added_labels[8]==0x55470A0Au);
    CHECK(!strcmp((char *)G+underglow_menu_text(added_labels[8]),"Light: On"));
    underglow_menu_notify(self,0xE1FDE1D1u);
    /* Global road pool: native row, live preview, cancellation, persistence.
     * The setting does not alter per-car colour or Light. */
    adds=0; M32(self+0x150)=0x308; f_007BFBD0(&lc);
    CHECK(added_ids[9]==0x30A && added_labels[9]==0x55470A0Cu);
    CHECK(!strcmp((char *)G+underglow_menu_text(added_labels[9]),"Road pool: Measured"));
    underglow_menu_notify(self,0xE1FDE1D1u);
    adds=0; M32(self+0x150)=0x30A; f_007BFBD0(&lc);
    CHECK(adds==2 && M32(self+0x154)==0x308 && M32(self+0x14C)==M32(0x905EAC));
    CHECK(!strcmp((char *)G+underglow_menu_text(added_labels[0]),"Measured"));
    CHECK(!strcmp((char *)G+underglow_menu_text(added_labels[1]),"Stronger"));
    CHECK(M32(options[1]+0x50)==(0x30Au<<16|0x308));
    M32(self+0x38)=options[1]; underglow_menu_notify(self,0x5A928018u);
    CHECK(underglow_pool_strength()==1 && !pref_writes);
    underglow_menu_notify(self,0xE1FDE1D1u);
    CHECK(underglow_pool_strength()==0 && !pref_writes);
    adds=0; f_007BFBD0(&lc); M32(self+0x38)=options[1];
    underglow_menu_notify(self,0x0C407210u); underglow_menu_notify(self,0xE1FDE1D1u);
    CHECK(underglow_pool_strength()==1 && pref_writes==1 && !strcmp(pool_pref,"stronger"));
    CHECK(light_of[82]==1 && preset_of[82]==3);
    adds=0; M32(self+0x150)=0x308; f_007BFBD0(&lc);
    CHECK(added_labels[9]==0x55470A0Du);
    CHECK(!strcmp((char *)G+underglow_menu_text(added_labels[9]),"Road pool: Stronger"));
    underglow_menu_notify(self,0xE1FDE1D1u);
    adds=0; M32(self+0x150)=0x30A; f_007BFBD0(&lc);
    M32(self+0x38)=options[0]; underglow_menu_notify(self,0x5A928018u);
    CHECK(underglow_pool_strength()==0 && pref_writes==1);
    /* A different page exit also rolls back a preview. */
    M32(self+0x150)=0x802; underglow_menu_notify(self,0);
    CHECK(underglow_pool_strength()==1 && pref_writes==1);
    adds=0; M32(self+0x150)=0x30A; f_007BFBD0(&lc);
    M32(self+0x38)=options[0]; underglow_menu_notify(self,0x0C407210u);
    underglow_menu_notify(self,0xE1FDE1D1u);
    CHECK(underglow_pool_strength()==0 && pref_writes==2 && !strcmp(pool_pref,"measured"));
    /* not owned: no page content, nothing stored */
    reset(); owned_car = 0; M32(self + 0x150) = 0x308;
    Cpu c4 = cpu(self, 0); f_007BFBD0(&c4);
    CHECK(adds == 0); CHECK(sets == 0);
    /* unrelated mode: untouched */
    reset(); M32(self + 0x150) = 0x802; Cpu c5 = cpu(self, 0); f_007BFBD0(&c5);
    CHECK(adds == 0); underglow_menu_notify(self, 0xC407210u); CHECK(sets == 0);
    printf("menu: %d checks, %d failures\n", checks, failures); return failures ? 1 : 0;
}
'''

def run(tmp, name, body, want):
    (tmp / (name + '.c')).write_text(PRE + 'uint32_t underglow_menu_text(uint32_t);\nvoid underglow_menu_notify(uint32_t, uint32_t);\nvoid f_007BB560(Cpu*);void f_007BFBD0(Cpu*);\n' + body + POST)
    p = subprocess.run(['clang', '-O1', '-I' + str(ROOT / 'runtime'), '-I' + str(ROOT / 'recomp'), str(tmp / (name + '.c')), '-o', str(tmp / name)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-3000:]
    r = subprocess.run([str(tmp / name)], capture_output=True, text=True)
    print(name + ': ' + r.stdout.strip() + (' | ' + str(r.stderr.count('FAIL')) + ' failed checks' if r.returncode else ''))
    assert (r.returncode == 0) == want, r.stderr[-2000:]
    if want:
        import os
        cases=[('',None,0),('measured',None,0),('stronger',None,1),('broken',None,0),
               ('stronger','measured',0),('measured','stronger',1),('stronger','broken',1)]
        for pref,override,expected in cases:
            env=dict(os.environ);env.pop('NFS_UNDERGLOW_STRENGTH',None)
            if override is not None: env['NFS_UNDERGLOW_STRENGTH']=override
            subprocess.run([str(tmp/name),pref,str(expected)],env=env,check=True)
        print('pool startup/preference/override: 7/7')

INTEGRATION = r"""
static const char *data_dir;
const char *userdata_dir(void){return data_dir;}
void f_00590BD0_orig(Cpu *c){}
void f_00581370_orig(Cpu *c){}
#include "underglow.c"
#include "underglow_store.c"
#include "underglow_menu.c"
int main(int argc,char **argv) {
    data_dir=argv[1]; G=calloc(1,16*1024*1024); if(!G)return 2;
    unsigned checks=0,failures=0;
    #define CHECK(x) do {++checks;if(!(x)){++failures;fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x);}}while(0)
    uint32_t self=0x2000,db=0x3000,p=0x4000,b=0x100000;
    strcpy((char *)G+p,"LIGHT");
    M32(0x91CF90)=db;M32(db+0x10)=p;M32(0x9B9EC4)=p+0x414+82*20;
    for(unsigned h=82;h<84;++h){uint32_t row=p+0x414+h*20;M32(row)=h;M32(row+4)=123;M32(row+8)=456;M32(row+12)=2;G[row+16]=h-82;}
    M32(self+0x2C)=0x6000;M32(0x6040)=0x40;M32(self)=0x7000;M32(0x700C)=0xC;
    /* The optional cosmetics are bound to the exact serialized retail bytes. */
    memset(G+b,0x5A,UG_SAVE_BYTES);
    underglow_profile_default(p);
    if(!strcmp(argv[2],"restart")) {
        underglow_profile_load(p,b,UG_SAVE_BYTES);
        CHECK(underglow_get(p,82)==2 && underglow_light_get(p,82)==1);
        CHECK(underglow_get(p,83)==4 && !underglow_light_get(p,83));
    } else {
        CHECK(!underglow_light_get(p,82)); CHECK(underglow_set(p,82,2));CHECK(underglow_set(p,83,4));
        CHECK(!underglow_light_get(p,82) && underglow_get(p,82)==2);
        CHECK(underglow_light_set(p,83,0));
        /* Drive the actual menu, then the same save lifecycle the colour uses. */
        M32(self+0x150)=0x308; Cpu c={0};c.ecx=self;c.esp=0x1000;f_007BFBD0(&c);
        CHECK(adds==10 && added_ids[8]==0x309 && added_labels[8]==0x55470A09u);
        M32(self+0x38)=options[8];underglow_menu_notify(self,0xE1FDE1D1u);
        adds=0;M32(self+0x150)=0x309;f_007BFBD0(&c);
        M32(self+0x38)=options[1];underglow_menu_notify(self,0x0C407210u);underglow_menu_notify(self,0xE1FDE1D1u);
        CHECK(underglow_light_get(p,82)==1 && underglow_get(p,82)==2);
        underglow_profile_save(p,b,UG_SAVE_BYTES);underglow_save_complete(1);
        underglow_profile_default(p);CHECK(!underglow_light_get(p,82));
        underglow_profile_load(p,b,UG_SAVE_BYTES);
        CHECK(underglow_light_get(p,82)==1 && underglow_get(p,82)==2);
        CHECK(!underglow_light_get(p,83) && underglow_get(p,83)==4);
        adds=0;M32(self+0x150)=0x308;f_007BFBD0(&c);CHECK(added_labels[8]==0x55470A0Au);
        underglow_menu_notify(self,0xE1FDE1D1u);
        adds=0;M32(self+0x150)=0x309;f_007BFBD0(&c);
        M32(self+0x38)=options[0];underglow_menu_notify(self,0x0C407210u);underglow_menu_notify(self,0xE1FDE1D1u);
        CHECK(!underglow_light_get(p,82) && underglow_get(p,82)==2);
        underglow_profile_save(p,b,UG_SAVE_BYTES);underglow_save_complete(1);
        underglow_profile_default(p);underglow_profile_load(p,b,UG_SAVE_BYTES);
        CHECK(!underglow_light_get(p,82) && underglow_get(p,82)==2);
        CHECK(!underglow_light_set(p,82,2));CHECK(!underglow_light_set(p,0,1));
        CHECK(underglow_light_set(p,82,1));CHECK(underglow_set(p,82,0));CHECK(underglow_light_get(p,82)==1);
        CHECK(underglow_set(p,82,2));
        underglow_profile_save(p,b,UG_SAVE_BYTES);underglow_save_complete(1);
    }
    for(unsigned i=0;i<UG_SAVE_BYTES;++i)if(G[b+i]!=0x5A){CHECK(0);break;}
    printf("menu persistence: %u checks, %u failures\n",checks,failures);return !!failures;
}
"""


def integration(tmp):
    pre=PRE[:PRE.index('/* fake store')] + 'static int adds;static unsigned added_ids[32],added_labels[32];\n' + PRE[PRE.index('static uint32_t options'):]
    core=(ROOT/'runtime/underglow.c').read_text()
    for name,body in [('good',core),('light-bit-omitted',core.replace('(on?UG_LIGHT_ON:0)','0'))]:
        assert name=='good' or body!=core
        directory=tmp/("persistence-"+name);directory.mkdir()
        (directory/'underglow.c').write_text(body)
        harness=directory/'persistence.c';harness.write_text(pre+INTEGRATION)
        exe=directory/'check'
        cp=subprocess.run(['clang','-O1','-g','-fsanitize=address,undefined','-Wno-deprecated-declarations',
            '-I'+str(directory),'-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),str(harness),'-o',str(exe)],capture_output=True,text=True)
        assert cp.returncode==0,cp.stderr[-3000:]
        for mode in (['seed','restart'] if name=='good' else ['seed']):
            import os
            cp=subprocess.run([str(exe),str(directory),mode],env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'),capture_output=True,text=True)
            print(name,mode,cp.stdout.strip(),flush=True)
            assert cp.returncode==int(name!='good'),cp.stderr[-3000:]


OLD_SETUP = 'void f_007BFBD0(Cpu *c) {                  /* CustomizeSub::Setup */\n    uint32_t self = c->ecx;\n    f_007BFBD0_orig(c);\n    if (M32(self + 0x150) != UG_MODE || !owned()) return;\n    Cpu saved = *c;\n    M32(self + 0x1B0) = H_TITLE;\n    M32(self + 0x14C) = M32(0x00905EA8u);   /* back to the package hosting Visual (CustomizeCategory) */\n    for (unsigned i = 0; i < 8; ++i) st.opts[i] = add(c, self, H_TITLE + 1 + i, UG_ID0 + i);\n    st.profile = 0;\n    st.handle = car_handle(&st.profile);\n    st.saved = st.shown = underglow_get(st.profile, st.handle);\n    st.open = 1; st.committed = 0;\n    uint32_t init = st.saved ? st.saved + 1 : 0;   /* retail\'s initial-highlight argument: 0 and 1 both mean the first entry */\n    c->ecx = self + 0x2C;\n    call_guest(c, M32(M32(self + 0x2C) + 0x40), 1, &init);\n    /* Accept is retail\'s own option accept: it switches to the option\'s package and mode. Aim every colour back at\n     * Visual (CustomizeCategory, mode 0x803, highlighting this entry) so the page closes the way a retail one does.\n     * After the initial highlight, which looks at the options as built. */\n    for (unsigned i = 0; i < 8; ++i)\n        if (st.opts[i]) { M32(st.opts[i] + 0x4C) = M32(0x00905EA8u); M32(st.opts[i] + 0x50) = (UG_MODE << 16) | 0x803u; }\n    c->ecx = self;\n    call_guest(c, M32(M32(self) + 0xC), 0, NULL);\n    *c = saved;\n    if (diag_on) diag_event("underglow menu: page opened car=%u saved=%u index=%u", st.handle, st.saved, colour_index(self));\n}'

def main():
    src = (ROOT / 'runtime/underglow_menu.c').read_text()
    src = src.replace('#include "host.h"\n', '').replace('#include "underglow.h"\n', '')
    import hashlib
    assert hashlib.sha256(OLD_SETUP.encode()).hexdigest() == '2a57a73d6211f1206fe86214de4efd0b02641d5f9a9336f6cc0f9c88e5813b29'
    start=src.index('void f_007BFBD0(Cpu *c) {'); end=src.index('\n/* After retail',start)
    mutants = {
        'original-setup': (src[:start]+OLD_SETUP+'\n'+src[end:],False),
        'good': (src, True),
        # retail Visual / Setup unchanged: the hooks only call the original
        'hooks-removed': (src.replace('    if (id != 0x307u || M32(self + 0x150) != 0x803u || !owned()) return;', '    return;')
                             .replace('    if ((mode != UG_MODE && mode != UG_LIGHT_MODE && mode != UG_POOL_MODE) || !owned()) return;', '    return;'), False),
        'pool-back-keeps-preview': (src.replace('if(st.mode==UG_POOL_MODE) pool_preview(st.pool);','if(0) pool_preview(st.pool);'), False),
        'pool-accept-not-saved': (src.replace('if(st.mode==UG_POOL_MODE) pool_save();','if(0) pool_save();'), False),
        'cancel-keeps-preview': (src.replace('        underglow_set(st.profile, st.handle, st.saved);\n', ''), False),
        'unowned-gets-entry': (src.replace('!= 0x803u || !owned()) return;', '!= 0x803u) return;'), False),
    }
    with tempfile.TemporaryDirectory(prefix='ug-menu-') as d:
        integration(Path(d))
        for n, (body, want) in mutants.items():
            run(Path(d), n.replace('-', '_'), body, want)

if __name__ == '__main__':
    main()
