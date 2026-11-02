#!/usr/bin/env python3
"""Controls page cycle/snapshot/cancel/defaults, actual hooks and retail comparisons.
ASan/UBSan; synthetic FE snapshot/widget builder, no game or input device.
Each front-end/pause mode: 422 checks / 0 failures; pinned job 4: 3 / 422 failures
(N2O behavioral assertions); pinned job 3: 127 / 418 failures.
Broken snapshot / cancel / defaults / exit: 44 / 99 / 1 / 1 failures
out of 418 / 422 / 422 / 422 checks (all rejected).
Historical source is stored as text/hash deltas; verified PASS with Git unavailable.
"""
from pathlib import Path
import importlib.util
import hashlib
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


# 769cf707777678101f30ee142bbdf4669fd145cf:runtime/padbind.c; SHA-256 verifies the stored source.
PINNED_JOB3 = pinned_source('padbind.c', (
    (46, 53, 'cc46e4663ce4af6db2072f46e7d03996b527396f8051e15575dfadd9d0611c4c', r'''// user32.m's P_* (the PlayStation pad's DirectInput buttons)
enum { P_CROSS, P_CIRCLE, P_SQUARE, P_TRIANGLE, P_L1, P_R1, P_CREATE, P_OPTIONS, P_L3, P_R3, P_L2, P_R2, P_PS, P_TOUCHPAD,
       P_LPADDLE, P_RPADDLE };
enum { A_GAS, A_BRAKE, A_GAMEBREAKER = 5, A_SHIFTDOWN = 7, A_SHIFTUP = 8, A_NEXTSONG = 24, A_FE_B0 = 37, A_FE_B3 = 39, A_FE_B4 = 40 };

static uint32_t rec(int a, int slot) { return GA + 0x34u * (uint32_t)a + 4 + 12u * (uint32_t)slot; }
static void set_rec(uint32_t r, uint32_t t, uint32_t d, uint32_t o) { M32(r) = t; M32(r + 4) = d; M32(r + 8) = o; }
'''),
    (447, 480, 'a2545a68aa67158568063a5b06c5f2e32e83ce7702ba9b492a01a12cfa162bf5', r'''static void seed_drag(Set *row, const Set *base) {
    *row = *base;
    row->binding_set = PADBIND_DRAG;
    for (int a = 0; a < GA_PAGE; ++a)
        for (int k = 0; k < 2; ++k) {
            uint32_t *r = row->r[a][k];
            if (!r[1] && ((r[2] == 2 && (r[0] == T_AXIS_HI || r[0] == T_ROT_HI)) ||
                (r[0] == T_BUTTON && (r[2] == P_R1 || r[2] == P_L1 || r[2] == P_R2 || r[2] == P_L2))))
                memset(r, 0, 12);
        }
    const int actions[] = {A_GAS, A_BRAKE, A_SHIFTUP, A_SHIFTDOWN};
    const uint32_t bindings[][3] = {{T_ROT_HI, 0, 2}, {T_AXIS_HI, 0, 2},
                                   {T_BUTTON, 0, P_R1}, {T_BUTTON, 0, P_L1}};
    for (int i = 0; i < 4; ++i) memcpy(row->r[actions[i]][0], bindings[i], 12);
}
static Set *drag_for(const char *profile, const char *name, int layout) {
    uint32_t hash = name_hash(name);
'''),
    (510, 517, '2732193740139d88965e4f12b1d95d1587391bf7499d492deea735ee0affc52c', r'''    edited_set = binding_set;
    return 1;
}
int portmenu_is_open(void);
static uint32_t input_race_params;
static int input_race_drag;
'''),
    (602, 624, '48e10ac742d60bc6f14817ae824e47cdf215d2701643b9dc4d2c775609c6a92a', r'''    call_guest(c, 0x0063CD80u, 0, NULL);
    c->esp = esp; c->eax = eax; c->ecx = ecx; c->edx = edx;
}

void f_0056E180_orig(Cpu *c);
// BEGIN offline bindings transaction: no disk writes until the career save task publishes its file.
'''),
), 'f31a2d3c37587a4abfbe8ab3ac9dc3d4b3a1041a9f7240b4d18ba7afd1e572ea')

# abd117e2c91b16e96fd7b501b5a7717f7467f833:runtime/padbind.c; SHA-256 verifies the stored source.
PINNED_JOB4 = pinned_source('padbind.c', (
    (46, 53, 'cc46e4663ce4af6db2072f46e7d03996b527396f8051e15575dfadd9d0611c4c', r'''// user32.m's P_* (the PlayStation pad's DirectInput buttons)
enum { P_CROSS, P_CIRCLE, P_SQUARE, P_TRIANGLE, P_L1, P_R1, P_CREATE, P_OPTIONS, P_L3, P_R3, P_L2, P_R2, P_PS, P_TOUCHPAD,
       P_LPADDLE, P_RPADDLE };
enum { A_GAS, A_BRAKE, A_GAMEBREAKER = 5, A_SHIFTDOWN = 7, A_SHIFTUP = 8, A_NEXTSONG = 24, A_FE_B0 = 37, A_FE_B3 = 39, A_FE_B4 = 40 };

static uint32_t rec(int a, int slot) { return GA + 0x34u * (uint32_t)a + 4 + 12u * (uint32_t)slot; }
static void set_rec(uint32_t r, uint32_t t, uint32_t d, uint32_t o) { M32(r) = t; M32(r + 4) = d; M32(r + 8) = o; }
'''),
    (447, 480, 'a2545a68aa67158568063a5b06c5f2e32e83ce7702ba9b492a01a12cfa162bf5', r'''static void seed_drag(Set *row, const Set *base) {
    *row = *base;
    row->binding_set = PADBIND_DRAG;
    for (int a = 0; a < GA_PAGE; ++a)
        for (int k = 0; k < 2; ++k) {
            uint32_t *r = row->r[a][k];
            if (!r[1] && ((r[2] == 2 && (r[0] == T_AXIS_HI || r[0] == T_ROT_HI)) ||
                (r[0] == T_BUTTON && (r[2] == P_R1 || r[2] == P_L1 || r[2] == P_R2 || r[2] == P_L2))))
                memset(r, 0, 12);
        }
    const int actions[] = {A_GAS, A_BRAKE, A_SHIFTUP, A_SHIFTDOWN};
    const uint32_t bindings[][3] = {{T_ROT_HI, 0, 2}, {T_AXIS_HI, 0, 2},
                                   {T_BUTTON, 0, P_R1}, {T_BUTTON, 0, P_L1}};
    for (int i = 0; i < 4; ++i) memcpy(row->r[actions[i]][0], bindings[i], 12);
}
static Set *drag_for(const char *profile, const char *name, int layout) {
    uint32_t hash = name_hash(name);
'''),
    (510, 517, '2732193740139d88965e4f12b1d95d1587391bf7499d492deea735ee0affc52c', r'''    edited_set = binding_set;
    return 1;
}
int portmenu_is_open(void);
static uint32_t input_race_params;
static int input_race_drag;
'''),
    (612, 613, '18fd0d21fe24c959645b483527a7b449533e58f77800379fb96921698c009bb7', '    Set base = {0}, drag;\n'),
), '68d72018ae806eebe4acc1814cc3a94079a547581c8ecc1254ba042ad3b6d4c1')
spec = importlib.util.spec_from_file_location('padsets', R/'padsets_test.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

EXTRA = r'''
#define SCREEN 0x400000u
static int checks, failures, header_calls, snapshot_calls;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; if(failures<14) printf("FAIL %d: %s\n",__LINE__,#x); } } while(0)
static Cpu cpu(uint32_t self) { return (Cpu){.ecx=self,.esp=0x600000,.esi=11,.edi=12,.ebx=13,.ebp=14}; }
static void invoke(void (*fn)(Cpu *),uint32_t self) {
 Cpu c=cpu(self); fn(&c); CHECK(c.esp==0x600004);
 CHECK(c.esi==11 && c.edi==12 && c.ebx==13 && c.ebp==14);
}
void f_0056E180_orig(Cpu *c) { c->esp+=4; }
void f_0056E200_orig(Cpu *c) { c->esp+=4; }
void f_00634370(Cpu *c) { c->eax=0x4000; M32(0x4008)=0x5000; c->esp+=4; }
void f_00628C50(Cpu *c) { c->eax=joy()!=0; c->esp+=4; }
void f_00628CB0(Cpu *c) { c->eax=GA+0x34*arg(c,0)+4; c->esp+=8; }
void f_006284D0(Cpu *c) { c->esp+=4; }
void f_0051D410(Cpu *c) { c->esp+=4; }
void f_0052A780_orig(Cpu *c) { c->esp+=4; }
void f_0052AB90_orig(Cpu *c) { ++header_calls; c->esp+=4; }
void f_0052AC70_orig(Cpu *c) {
 uint32_t self=c->ecx; int keyboard=M32(self+0xe4)==1;
 ++snapshot_calls;
 M8(self+0x2b0)=M8(0x3084);
 for(int a=0;a<(keyboard?18:19);a++) memcpy(G+self+0xe8+24*a,G+rec(a,keyboard?0:2),24);
 c->esp+=4;
}
uint32_t gstr(const char *s) { strcpy((char *)G+0x710000,s); return 0x710000; }
static int title_calls;
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *args) {
 (void)n;
 if(fn==0x0063cd80) { Cpu q=*c; q.esp-=4; f_0063CD80(&q); }
 else if(fn==0x005252d0) {
  CHECK(args[0]==M32(SCREEN+0x10) && args[1]==0x4e11cf90 && args[2]==0x8941f8);
  CHECK(!strcmp((char *)G+args[3],"DRAG RACES")); ++title_calls;
 } else { fprintf(stderr,"call %x\n",fn); abort(); }
 return 0;
}
void dispatch(Cpu *c,uint32_t t) { CHECK(t==0x52ab90); f_0052AB90(c); }
void bad_jump(Cpu *c,uint32_t t) { (void)c; fprintf(stderr,"jump %x\n",t); abort(); }
static int unchanged(void) { Cpu c=cpu(SCREEN); f_0051D4B0(&c); CHECK(c.esp==0x600004); return (c.eax&255)!=0; }
static void page(uint32_t out[19][6]) { for(int a=0;a<19;a++) memcpy(out[a],G+rec(a,2),24); }
static int equal(uint32_t expected[19][6]) { uint32_t now[19][6]; page(now); return !memcmp(now,expected,sizeof now); }
static void drag_defaults_check(void) {
 CHECK(M32(rec(0,2))==T_ROT_HI && M32(rec(0,2)+4)==0 && M32(rec(0,2)+8)==2);
 CHECK(M32(rec(1,2))==T_AXIS_HI && M32(rec(1,2)+4)==0 && M32(rec(1,2)+8)==2);
 CHECK(M32(rec(8,2))==T_BUTTON && M32(rec(8,2)+4)==0 && M32(rec(8,2)+8)==P_R1);
 CHECK(M32(rec(7,2))==T_BUTTON && M32(rec(7,2)+4)==0 && M32(rec(7,2)+8)==P_L1);
 CHECK(M32(rec(6,2))==T_BUTTON && M32(rec(6,2)+4)==0 && M32(rec(6,2)+8)==P_CROSS);
 int cross=0;
 for(int a=0;a<19;a++) for(int k=2;k<4;k++)
  if(M32(rec(a,k))==T_BUTTON && M32(rec(a,k)+4)==0 && M32(rec(a,k)+8)==P_CROSS) ++cross;
 CHECK(cross==1);
}
'''
MAIN = r'''
int main(int argc,char **argv) {
 (void)argc; scratch=argv[1]; G=calloc(1,0x1000000);
 M32(JOY)=0x100000; strcpy(joy_name(joy()),"Controller (DualSense)");
 M32(FEDB)=0x2000; M32(0x2010)=0x3000; strcpy((char *)G+0x3000,"Player"); active_layout=L_NATIVE_PS;
 M32(SCREEN+0xe4)=1; M32(SCREEN+0x2c)=0x720000; M32(0x720008)=0x52ab90;
 M32(SCREEN+0x10)=0x730000; M8(SCREEN+0xe0)=argc>2;
 strcpy((char *)G+0x730000,argc>2?"Pause_PC_Controller.fng":"Options_PC_Controller.fng");
 for(int a=0;a<76;a++) for(int k=0;k<4;k++) set_rec(rec(a,k),k<2?T_KEY:T_BUTTON,k<2?30+a:0,40+4*a+k);
 // Native PS Default maps Xbox button 0 to Cross gas, button 6 to L1 N2O.
 set_rec(rec(0,2),T_BUTTON,0,drive_map[0]);
 set_rec(rec(6,2),T_BUTTON,0,drive_map[6]); set_rec(rec(6,3),0,0,0);
 // A saved secondary Cross binding must also be cleared only in Drag.
 set_rec(rec(2,3),T_BUTTON,0,P_CROSS);
 CHECK(M32(rec(0,2)+8)==P_CROSS && M32(rec(6,2)+8)==P_L1);
 uint32_t original[19][6]; page(original);
 uint8_t keyboard[19][24]; for(int a=0;a<19;a++) memcpy(keyboard[a],G+rec(a,0),24);
 invoke(f_0052AC70,SCREEN); CHECK(unchanged());
 invoke(f_0052AF20,SCREEN); CHECK(M32(SCREEN+0xe4)==3 && edited_set==PADBIND_DEFAULT); CHECK(unchanged());
 // Accept a Default edit by cycling. The new Drag snapshot must contain Drag.
 set_rec(rec(18,3),T_BUTTON,0,29); CHECK(!unchanged()); page(original);
 invoke(f_0052AF20,SCREEN); CHECK(M32(SCREEN+0xe4)==3 && edited_set==PADBIND_DRAG); CHECK(unchanged());
 CHECK(title_calls>0);
 uint32_t drag[19][6]; page(drag);
 printf("seeded Drag N2O: %u,%u,%u / %u,%u,%u\n",drag[6][0],drag[6][1],drag[6][2],drag[6][3],drag[6][4],drag[6][5]);
 drag_defaults_check();
 CHECK(M32(rec(0,2))==T_ROT_HI && M32(rec(0,2)+8)==2);
 CHECK(M32(rec(1,2))==T_AXIS_HI && M32(rec(1,2)+8)==2);
 CHECK(M32(rec(8,2))==T_BUTTON && M32(rec(8,2)+8)==P_R1);
 CHECK(M32(rec(7,2))==T_BUTTON && M32(rec(7,2)+8)==P_L1);
 for(int a=0;a<19;a++) {
  for(int k=2;k<4;k++) {
   set_rec(rec(a,k),T_BUTTON,0,100+a+k); CHECK(!unchanged());
   invoke(f_0051D590,SCREEN); CHECK(equal(drag)); CHECK(unchanged());
  }
 }
 // Shared keyboard is outside a controller cancel transaction.
 M32(rec(18,0)+4)=77; keyboard[18][4]=77;
 invoke(f_0051D590,SCREEN); CHECK(M32(rec(18,0)+4)==77);
 // Drag defaults leave the full Default page, keyboard and menu records intact.
 set_rec(rec(18,3),T_BUTTON,0,31);
 uint8_t menus[(76-19)*52]; memcpy(menus,G+GA+19*52,sizeof menus);
 M8(0x3084)=0; invoke(f_0051D6E0,SCREEN); CHECK(M8(0x3084)==0);
 drag_defaults_check();
 CHECK(!memcmp(menus,G+GA+19*52,sizeof menus));
 CHECK(M32(rec(0,2))==T_ROT_HI && M32(rec(1,2))==T_AXIS_HI);
 CHECK(M32(rec(18,3)+8)!=31);
 CHECK(M32(rec(8,2)+8)==P_R1 && M32(rec(7,2)+8)==P_L1);
 for(int a=0;a<19;a++) CHECK(!memcmp(keyboard[a],G+rec(a,0),24));
 CHECK(!unchanged()); invoke(f_0051D590,SCREEN); CHECK(equal(drag));
 // Revisit each logical page: accepted Default edits survive a Drag cancel.
 invoke(f_0052AF20,SCREEN); CHECK(M32(SCREEN+0xe4)==1 && edited_set==PADBIND_DEFAULT); CHECK(equal(original));
 invoke(f_0052AF20,SCREEN); CHECK(M32(SCREEN+0xe4)==3 && equal(original));
 set_rec(rec(0,2),T_BUTTON,0,99); CHECK(!unchanged()); invoke(f_0051D590,SCREEN); CHECK(equal(original));
 // Default's retail reset/cancel must also leave the stored Drag page intact.
 invoke(f_0051D6E0,SCREEN); CHECK(M8(0x3084)==1); CHECK(!equal(original));
 for(int a=0;a<19;a++) {
  uint32_t resolved[6]; CHECK(padbind_effective_binding(PADBIND_DRAG,a,resolved));
  CHECK(!memcmp(resolved,drag[a],24));
 }
 CHECK(!unchanged()); invoke(f_0051D590,SCREEN); CHECK(equal(original) && M8(0x3084)==0);
 invoke(f_0052AF20,SCREEN); CHECK(M32(SCREEN+0xe4)==3 && equal(drag));
 set_rec(rec(18,3),T_BUTTON,0,30); page(drag);
 invoke(f_0052A780,SCREEN); CHECK(edited_set==PADBIND_DEFAULT && equal(original));
 CHECK(padbind_edit_set(PADBIND_DRAG)); CHECK(equal(drag)); CHECK(padbind_edit_set(PADBIND_DEFAULT));
 // Reused screen addresses start on Keyboard; no-pad cycle remains Keyboard.
 M32(SCREEN+0xe4)=1; invoke(f_0052AC70,SCREEN); invoke(f_0052A780,SCREEN);
 M32(JOY)=0; invoke(f_0052AC70,SCREEN); invoke(f_0052AF20,SCREEN); CHECK(M32(SCREEN+0xe4)==1);
 // Both front-end and pause packages prevent controller identity swaps.
 M32(0x91cadc)=0x8000; M32(0x8000)=0x9000; M32(0x90e4)=0xa000; M32(0xa00c)=0xb000;
 strcpy((char *)G+0xb000,"Pause_PC_Controller.fng"); CHECK(controls_screen_open());
 strcpy((char *)G+0xb000,"Options_PC_Controller.fng"); CHECK(controls_screen_open());
 printf("controlpages: %d checks, %d failures; %d snapshots, %d headers\n",checks,failures,snapshot_calls,header_calls);
 free(sets); free(G); return failures!=0;
}
'''


def translated(name):
    s=(R.parent/'recomp/out'/('speed_18.c' if name.startswith('0052') else 'speed_17.c')).read_text()
    body=re.search(r'void f_'+name+r'(?:_orig)?\(Cpu \*restrict c\) \{.*?\n\}',s,re.S)[0]
    return re.sub(r'void f_'+name+r'(?:_orig)?\(', 'void f_'+name+'_orig(', body,count=1)


def main():
    if not (R.parent/'recomp/out/speed_18.c').exists():
        print('SKIP: needs translated Controls functions'); return 3
    hooks=['0052AF20','0052AB90','0052AC70','0051D4B0','0051D590','0051D6E0','0052A780']
    source=(R/'controlpages.c').read_text()
    old=PINNED_JOB3
    aliases='\n'.join('void f_'+a+'(Cpu *c) { f_'+a+'_orig(c); }' for a in hooks)
    variants={'normal':((R/'padbind.c').read_text(),source),'job3':(old,aliases)}
    job4=PINNED_JOB4
    variants['job4']=(job4,source)
    for name,before,after in (
        ('wrong-snapshot','controls_page = PAGE_DRAG;','controls_page = PAGE_CONTROLLER;'),
        ('wrong-cancel','f_0051D590_orig(c);','/* discarded cancel */ c->esp += 4;'),
        ('wrong-defaults','padbind_drag_defaults(c);','/* discarded defaults */'),
        ('wrong-exit','padbind_edit_set(PADBIND_DEFAULT);','/* leaked Drag */'),
    ):
        assert before in source,name
        variants[name]=((R/'padbind.c').read_text(),source.replace(before,after,1))
    pre=re.sub(r'uint32_t call_guest\(.*?\n', '', h.PRE)
    with tempfile.TemporaryDirectory(prefix='controlpages-') as tmp:
        tmp=Path(tmp); (tmp/'funcs.h').write_text('')
        for name,(pad,ui) in variants.items():
            path=tmp/(name+'.c'); exe=tmp/name
            decls='\n'.join('void f_'+a+'(Cpu *c); void f_'+a+'_orig(Cpu *c);' for a in hooks)
            path.write_text('\n'.join([pre,pad,decls,EXTRA]+[translated(a) for a in ('0052AF20','0051D4B0','0051D590','0051D6E0')]+[ui,MAIN]))
            result=subprocess.run(['clang','-std=c11','-g','-Wno-unused-function','-fsanitize=address,undefined','-fno-sanitize-recover=all','-I',str(tmp),'-I',str(R),'-I',str(R.parent/'recomp'),str(path),'-o',str(exe)],capture_output=True,text=True)
            assert result.returncode==0,result.stderr[-5000:]
            for pause in (False,True):
                label=name+('-pause' if pause else '-front')
                data=tmp/(label+'-data'); data.mkdir()
                result=subprocess.run([exe,data]+(['pause'] if pause else []),capture_output=True,text=True)
                print(label+': '+result.stdout.strip())
                assert result.returncode==(0 if name=='normal' else 1),result.stderr[-4000:]
    print('controlpages_test: PASS')

if __name__=='__main__':
    raise SystemExit(main())
