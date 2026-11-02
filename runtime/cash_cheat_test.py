#!/usr/bin/env python3
"""Synthetic career cash fixture; no game, assets, preferences or profile files.
Pre-review controls: online 10 failures / 547 checks; input 28 / 549;
cap 7 / 545; headers 4 / 535; all four 49 / 553. Production: 0 / 541.
Pre-feature (rows absent / no frame hook): 106 / 340;
missing award: 45 / 501; missing refresh: 6 / 518.
Registry integration: 167 retained / 0 dropped; old capacity: 160 / 7.
Translated AwardCash/GetCash/marker add, all four F10 actions, reward headroom,
raw paste / Unicode typing / grouped ASCII parsing, online entry at queue and
frame, profile replacement, one-shot dispatch, CPU restoration and cash labels.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r'^void ' + name + r'\(Cpu \*[^\n]*\) \{', source, re.M)
    assert match, name
    return source[match.start():source.index('\n}', match.end()) + 2] + '\n'


PRE = r'''
#include "host.h"
#include "settings.h"
#include "cash_cheat.h"
#include "roam_online.h"
#import <Foundation/Foundation.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
uint8_t *G;
atomic_int roam_online_active;
static Setting *registry[8];
static unsigned nrows,checks,failures,awards,refreshes,prints,sheet_prints,localizations;
static uint32_t last_award,last_cash;
static char message[256];
static const uint32_t db=0x310000,profile=0x320000,race=0x330000,sp=0x800000;
#define CHECK(x) do{++checks;if(!(x)){++failures;if(failures<8)printf("FAIL %d: %s\n",__LINE__,#x);}}while(0)
void settings_add(Setting *s){if(nrows<8)registry[nrows++]=s;else abort();}
Setting *settings_find(const char *id){for(unsigned i=0;i<nrows;i++)if(!strcmp(id,registry[i]->id))return registry[i];return NULL;}
void portmenu_status(const char *fmt,...){va_list ap;va_start(ap,fmt);vsnprintf(message,sizeof message,fmt,ap);va_end(ap);}
uint32_t gstr(const char *s){static uint32_t heap=0x700000;uint32_t p=heap;strcpy((char*)G+p,s);heap+=(uint32_t)strlen(s)+1;return p;}
void abi_violation(Cpu*c,uint32_t fn,uint32_t b,uint32_t s,uint32_t i,uint32_t p,uint32_t st){(void)c;(void)fn;(void)b;(void)s;(void)i;(void)p;(void)st;abort();}
void f_00566010(Cpu *c);
void f_0050E300(Cpu *c);
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a){
 uint32_t esp=c->esp;for(int i=n-1;i>=0;i--){c->esp-=4;M32(c->esp)=a[i];}c->esp-=4;M32(c->esp)=0;
 switch(fn){
 case 0x00566010:CHECK(n==1&&c->ecx==profile+0xa8);last_award=arg(c,0);++awards;f_00566010(c);CHECK(c->esp==esp);break;
 case 0x00591E90:CHECK(n==1);c->eax=arg(c,0)==0x610000?0x620000:arg(c,0)==0x610100?0x620100:0;break;
 case 0x007B0620:CHECK(n==0&&c->ecx==0x620000);++refreshes;last_cash=M32(profile+0xb4);break;
 case 0x005AF1C0:CHECK(n==1&&!strcmp(gptr(arg(c,0)),"CASH_DATA"));c->eax=0x12345678;break;
 case 0x0056BC10:CHECK(n==1&&arg(c,0)==0x073B79E0);++localizations;c->eax=0x610300;break;
 case 0x005252D0:
  if(n==4){CHECK(arg(c,0)==0x610100&&arg(c,1)==0x12345678&&!strcmp(gptr(arg(c,2)),"%$d"));CHECK(arg(c,3)==M32(profile+0xb4));}
  else{CHECK(n==5&&arg(c,0)==0x610000&&arg(c,1)==0xF91A59F6&&!strcmp(gptr(arg(c,2)),"%s %$d"));CHECK(arg(c,3)==0x610300&&arg(c,4)==M32(profile+0xb4));++sheet_prints;}
  ++prints;break;
 default:fprintf(stderr,"unexpected call %x\n",fn);abort();
 }
 c->esp=esp;return c->eax;
}
// An in-memory pasteboard double: no OS clipboard or desktop access.
static NSString *clipboard;
static NSString *const NSPasteboardTypeString = @"text";
@interface NSPasteboard : NSObject
+ (instancetype)generalPasteboard;
- (NSString *)stringForType:(NSString *)type;
@end
@implementation NSPasteboard
+ (instancetype)generalPasteboard { static NSPasteboard *p; if(!p)p=[NSPasteboard new]; return p; }
- (NSString *)stringForType:(NSString *)type { (void)type; return clipboard; }
@end
static int is_open=1,editing=1,dirty;
static int report_github_active(void){return 0;}
static Setting *current(void){return settings_find("gameplay.cash_amount");}
#define HEAD(var,id_,cat,ord,text,helptext) static Setting var={.id=id_,.category=cat,.order=ord,.label=text,.help=helptext,.type=SET_HEADING}
#define REG(row) settings_add(&row)
'''

POST = r'''
static Cpu cpu(void){Cpu c={.ecx=0x1234,.esp=sp,.eax=4,.ebx=5,.edx=6,.esi=7,.edi=8,.ebp=9,.cw=0x37f};return c;}
static void tick(void){Cpu c=cpu(),saved=c;cash_cheat_frame(&c);CHECK(!memcmp(&c,&saved,sizeof c));}
static void setup(void){
 atomic_store(&roam_online_active,0);memset(G,0,0x700000);M32(0x91CF90)=db;M32(db+0x10)=profile;M8(profile+0x20)=1;
 M32(profile+0xac)=1;M32(db+0x12c)=1;M32(0x925e90)=6;M32(0x91e000)=race;M32(race+0x1964)=2;
 M32(profile+0xb4)=123;awards=refreshes=prints=sheet_prints=localizations=last_award=last_cash=0;message[0]=0;
 tick();
}
static void click(const char *id){Setting *s=settings_find(id);if(s&&s->apply&&(s->type==SET_ACTION||s->type==SET_TEXT))s->apply(0);}
static void balance(uint32_t want){
 Cpu c=cpu();c.ecx=profile+0xa8;f_0050E300(&c);CHECK(c.eax==want&&c.esp==sp+4);
 CHECK(M32(profile+0xb0)==0&&M32(profile+0xb8)==0&&M32(profile+0xac)==1);
}
static void ui(void){
 // Loaded parent shop plus safehouse and an unrelated screen. No control flag
 // on the parent: cash refresh must not depend on which package owns input.
 M32(0x91cadc)=0x600000;M32(0x600000)=0x600100;M32(0x6001e4)=0x600200;
 M32(0x600208)=0x600240;M32(0x60020c)=0x610000;
 M32(0x600248)=0x600280;M32(0x60024c)=0x610100;M32(0x60028c)=0x610200;
 strcpy((char*)G+0x610000,"CustomizeShoppingCart.fng");strcpy((char*)G+0x610100,"MainMenu_Sub.fng");strcpy((char*)G+0x610200,"Dialog.fng");
 M32(0x620000)=0x630000;M32(0x63000c)=0x7b0620;
 M32(0x620100)=0x630100;M32(0x63010c)=0xDEADBEEF; // must never dispatch an unrelated virtual
}
int main(void){ @autoreleasepool {
 G=calloc(1,0x1000000);if(!G)return 2;register_rows();setup();
 CHECK(nrows==5);Setting *heading=settings_find("gameplay.h_cash");CHECK(heading&&heading->type==SET_HEADING&&!strcmp(heading->label,"Add cash"));
 const char *ids[]={"gameplay.cash_20000","gameplay.cash_50000","gameplay.cash_100000","gameplay.cash_amount"};
 const uint32_t amounts[]={20000,50000,100000,7654321};
 for(unsigned i=0;i<4;i++){
  setup();Setting *s=settings_find(ids[i]);CHECK(s&&s->category==SET_GAMEPLAY&&s->pref&&!s->pref[0]&&!s->env&&!s->restart);
  CHECK(s&&s->type==(i==3?SET_TEXT:SET_ACTION));if(s&&i==3)strcpy(s->text,"7654321");
  click(ids[i]);balance(123);tick();balance(123+amounts[i]);CHECK(awards==1&&last_award==amounts[i]);
  tick();balance(123+amounts[i]);CHECK(awards==1);if(s&&i==3)CHECK(!s->text[0]);
 }
 // Multiple clicks before Present are distinct one-shot requests.
 setup();click(ids[0]);click(ids[1]);click(ids[2]);tick();balance(170123);CHECK(awards==1&&last_award==170000);
 // Exact limit, crossing it, already at it, and corrupted signed-negative cash.
 const uint32_t ceiling=2137483647u; // signed display range minus 10,000,000
 for(unsigned i=0;i<5;i++){
  setup();uint32_t start=i==0?ceiling-20000u:i==1?ceiling-7u:i==2?ceiling:i==3?ceiling+1:0xffffffffu;
  M32(profile+0xb4)=start;click(ids[0]);tick();balance(i>=3?start:ceiling);CHECK(awards==(i<2));
 }
 // Top up, then run the translated native marker addition. No negative reward wrap.
 setup();M32(profile+0xb4)=ceiling-7;click(ids[0]);tick();
 Cpu reward_cpu=cpu();reward_cpu.esi=0x650000;reward_cpu.edx=0;M32(0x650000)=10000000;
 marker_reward(&reward_cpu);balance(INT32_MAX);CHECK((int32_t)M32(profile+0xb4)>0);
 const char *invalid[]={"","0","-1","+1"," 20","20 ","1.0","1e3","10000000","999999999999999999999999999999999","1\n","−20000","１２3","1,23","1 23","1,000 000","1,,000",",123","123,","1\t000"};
 for(unsigned i=0;i<sizeof invalid/sizeof *invalid;i++){
  setup();Setting *s=settings_find(ids[3]);if(s)strcpy(s->text,invalid[i]);click(ids[3]);tick();balance(123);
  CHECK(awards==0);CHECK(strstr(message,"digits only")!=NULL);
 }
 const char *valid[]={"1","9999999","0000020","20,000","1 234 567","9,999,999"};const unsigned want[]={1,9999999,20,20000,1234567,9999999};
 for(unsigned i=0;i<6;i++){setup();Setting *s=settings_find(ids[3]);if(s)strcpy(s->text,valid[i]);click(ids[3]);tick();balance(123+want[i]);CHECK(awards==1);}
 if(current()){
 // Pasted / typed input takes the real menu path before the amount parser.
 const char *pastes[]={"−20000","１２3","20\n000","20\t000","1.0","+20","20,000","9 999 999"};
 for(unsigned i=0;i<8;i++){
  setup();clipboard=[NSString stringWithUTF8String:pastes[i]];paste_text();click(ids[3]);tick();
  balance(i<6?123:i==6?20123:10000122);CHECK(awards==(i>=6));
 }
 setup();strcpy(current()->text,"20");clipboard=[[@"0" stringByPaddingToLength:94 withString:@"0" startingAtIndex:0] stringByAppendingString:@"1x"];
 paste_text();click(ids[3]);tick();balance(123);CHECK(!awards);
 setup();unichar nul_text[]={'2','0','0',0,'x'};clipboard=[NSString stringWithCharacters:nul_text length:5];
 paste_text();click(ids[3]);tick();balance(123);CHECK(!awards);
 for(unsigned i=0;i<2;i++){
  setup();current()->text[0]=0;portmenu_char(i?0xff11:0x2212);portmenu_char('2');portmenu_char('0');
  click(ids[3]);tick();balance(123);CHECK(!awards);
 }
 setup();memset(current()->text,'0',94);current()->text[94]='1';current()->text[95]=0;
 portmenu_char(0x2212);click(ids[3]);tick();balance(123);CHECK(!awards);
 // Online entry, stale callbacks, and a session starting between queue and frame.
 setup();atomic_store(&roam_online_active,1);tick();
 for(unsigned i=0;i<4;i++){CHECK(settings_find(ids[i])->type==SET_INFO);click(ids[i]);}
 cash_cheat_20000(0);cash_cheat_50000(0);cash_cheat_100000(0);
 strcpy(current()->text,"20");cash_cheat_typed(0);tick();balance(123);CHECK(!awards);
 setup();click(ids[0]);atomic_store(&roam_online_active,1);tick();balance(123);CHECK(!awards);
 setup();atomic_store(&roam_online_active,1);cash_cheat_20000(0);atomic_store(&roam_online_active,0);tick();balance(123);CHECK(!awards);
 }
 // Every reason a career is unavailable, including retained FE career bit in
 // Quick Race / Challenge and loading. Disabled rows keep the hint and no action.
 for(unsigned state=0;state<9;state++){
  setup();switch(state){case 0:M32(0x91cf90)=0;break;case 1:M32(db+0x10)=0;break;
  case 2:M8(profile+0x20)=0;break;case 3:M32(profile+0xac)=0;break;case 4:M32(db+0x12c)=2;break;
  case 5:M32(race+0x1964)=0;break;case 6:M32(0x91e000)=0;break;case 7:M32(0x925e90)=4;break;case 8:M32(race+0x1964)=1;break;}
  tick();for(unsigned i=0;i<4;i++){Setting *s=settings_find(ids[i]);char hint[96]="";CHECK(s&&s->type==SET_INFO);
   if(s){s->value_text(hint,sizeof hint);CHECK(strstr(hint,"Career")&&strstr(s->help,"Career"));click(ids[i]);}}
  CHECK(awards==0&&M32(profile+0xb4)==123);
  // Even a stale callback cannot queue an award while unavailable.
  cash_cheat_100000(0);tick();CHECK(awards==0&&M32(profile+0xb4)==123);
 }
 setup();M32(0x925e90)=3;tick();click(ids[0]);tick();balance(20123);CHECK(awards==1); // career front end
 setup();click(ids[0]);M32(db+0x10)=0x340000;M8(0x340020)=1;M32(0x3400ac)=1;tick();CHECK(!awards&&M32(profile+0xb4)==123&&M32(0x3400b4)==0);
 setup();click(ids[0]);M32(race+0x1964)=0;tick();CHECK(!awards&&M32(profile+0xb4)==123);
 setup();ui();click(ids[0]);tick();balance(20123);CHECK(refreshes==1&&prints==1&&last_cash==20123);
 tick();CHECK(refreshes==1&&prints==1); // no repeat on the following frame
 if(current()){
 // Primary MenuScreen vtables from 0055A760 / 0055A9A0. Header virtuals
 // live at secondary +0x2C, slot +8; primary slot +0xC is flow work.
 const uint32_t sheet_vtables[]={0x89D7AC,0x89EEDC};
 for(unsigned i=0;i<2;i++){
  setup();ui();M32(0x620000)=sheet_vtables[i];M32(sheet_vtables[i]+0xc)=0xdeadbeef;
  M32(0x620018)=0x12345678;M32(0x620048)=0x87654321; // selection sentinels
  click(ids[0]);tick();balance(20123);CHECK(!refreshes&&prints==2&&sheet_prints==1&&localizations==1);
  CHECK(M32(0x620018)==0x12345678&&M32(0x620048)==0x87654321);
  tick();CHECK(prints==2&&sheet_prints==1);
 }
 }
 printf("cash fixture: %u checks, %u failures\n",checks,failures);free(G);return failures?1:0;
}}
'''


REGISTRY = r'''
#include "host.h"
#include "settings.h"
#include <stdlib.h>
int diag_on;
const char *game_dir;
const char *app_pref(const char *key){(void)key;abort();}
void app_pref_set(const char *key,const char *value){(void)key;(void)value;abort();}
void app_pref_batch_begin(void){abort();}
void app_pref_batch_end(void){abort();}
void report_note(const char *fmt,...){(void)fmt;abort();}
void diag_event(const char *fmt,...){(void)fmt;abort();}
int main(void){
 // A crowded registry, including 14 per-car rows, must retain both the five
 // cash rows and later feature registrations. Do not initialize preferences.
 Setting rows[167]={0};char ids[167][32];unsigned dropped=0;
 for(unsigned i=0;i<167;i++){
  snprintf(ids[i],sizeof ids[i],"fixture.%u",i);
  rows[i]=(Setting){.id=ids[i],.category=SET_GAMEPLAY,.type=SET_INFO};settings_add(&rows[i]);
 }
 for(unsigned i=0;i<167;i++)dropped+=settings_find(ids[i])!=&rows[i];
 printf("registry: %d retained / %u dropped\n",settings_count(),dropped);
 return dropped?1:0;
}
'''


# Frozen pre-review function fragments; each digest pins the failing control.
BEFORE = {
    'career': (r'''static uint32_t career_profile(void) {
    uint32_t db = M32(FE_DATABASE), profile = db ? M32(db + 0x10) : 0;
    // IsProfileNamed (0056DB10), HasCareerStarted (0050E310), FE career bit.
    if (!profile || !M8(profile + 0x20) || !(M32(profile + 0xAC) & 1) || !(M32(db + 0x12C) & 1)) return 0;
    // Quick races can retain a career profile: Game_IsCareerMode (00605610)
    // uses GRaceStatus::mRaceMode == 2, rather than the front-end mode bit.
    if (M32(GAME_FLOW) == 6) {
        uint32_t race = M32(RACE_STATUS);
        if (!race || M32(race + 0x1964) != 2) return 0;
    } else if (M32(GAME_FLOW) != 3) return 0; // no awards during loading
    return profile;
}
''',
        'd37af13caff1cd2b659464ab074b6505f4a96e5a42bbbe0f0f78bfd916b40108'),
    'typed': (r'''void cash_cheat_typed(int value) {
    (void)value;
    Setting *row = settings_find("gameplay.cash_amount");
    if (!row) return;
    const unsigned char *p = (const unsigned char *)row->text;
    uint32_t amount = 0;
    if (!*p) goto invalid;
    for (; *p; ++p) {
        if (*p < '0' || *p > '9' || amount > (ENTRY_MAX - (*p - '0')) / 10) goto invalid;
        amount = amount * 10 + (*p - '0');
    }
    if (!amount) goto invalid;
    queue_cash(amount);
    row->text[0] = 0; // a submitted entry cannot be replayed by pressing Enter
    return;
invalid:
    portmenu_status("Enter digits only, from 1 to 9,999,999.");
}
''',
        'ed0b9612b032ff3a3e97c2f28ea23f6f10be31f6e5b31b0c60c8eff5e93f11a2'),
    'refresh': (r'''static void refresh_cash(Cpu *c, uint32_t cash) {
    uint32_t instance = M32(CFENG_INSTANCE), fe = instance ? M32(instance) : 0;
    if (!fe || fe == 0xFFFFFF28u) return;
    // Include parent packages: a dialog can own control over the shop's header.
    for (uint32_t package = M32(fe + 0xE4); package; package = M32(package + 8)) {
        uint32_t name = M32(package + 0xC);
        if (!name) continue;
        uint32_t screen = call_guest(c, 0x00591E90u, 1, &name); // FEngFindScreen
        uint32_t vtable = screen ? M32(screen) : 0, fn = vtable ? M32(vtable + 0xC) : 0;
        if (cash_header(fn)) {
            c->ecx = screen;
            call_guest(c, fn, 0, NULL); // recomputes cash, cart balance and prices
        }
        // uiCareerCrib::Setup (00532220) prints this only on construction.
        // Update its string without rebuilding the menu or resetting selection.
        if (!strcasecmp((const char *)gptr(name), "MainMenu_Sub.fng")) {
            static uint32_t format, key;
            if (!format) {
                format = gstr("%$d");
                uint32_t text = gstr("CASH_DATA");
                key = call_guest(c, 0x005AF1C0u, 1, &text); // FEHashUpper
            }
            uint32_t args[] = {name, key, format, cash};
            call_guest(c, 0x005252D0u, 4, args); // FEPrintf
        }
    }
}
''',
        '1e61023b0dd9fc41bc21ff82e7c2a2c0b81c8a6eabfeeb79fa4b5566ee9ccd4d'),
    'paste': (r'''static void paste_text(void) {
    Setting *s = current();
    if (!is_open || !editing || !s || s->type != SET_TEXT) return;
    NSString *paste = [NSPasteboard.generalPasteboard stringForType:NSPasteboardTypeString];
    if (!paste) return;
    const char *p = paste.UTF8String; size_t used = 0;
    while (p && *p && used + 1 < sizeof s->text) {
        unsigned char ch = (unsigned char)*p++;
        if (ch >= 32 && ch < 127) s->text[used++] = (char)ch;
    }
    s->text[used] = 0; dirty = 1;
}
''',
        'fd02d51b0ab4780717090add019c204f47b1e366271d386cd67b74e2881e4503'),
    'char': (r'''int portmenu_char(uint32_t ch) {
    if (!is_open) return 0;
    if (report_github_active()) return 1;
    Setting *s = current();
    if (editing && s && s->type == SET_TEXT && ch >= 32 && ch < 256 && ch != 127) {
        size_t n = strlen(s->text);
        if (n + 1 < sizeof s->text) { s->text[n] = (char)ch; s->text[n + 1] = 0; dirty = 1; }
    }
    return 1;
}
''',
        '37ae7e3c2a49a0a9abbf58f9dd92350af0fc94eb572349e0146bb566e699d973'),
    'cap': (r'''#define CASH_MAX ((uint32_t)INT32_MAX)''',
        '1bf54d6acd3703e0370a01bbed57d92766f7e7aea9bfd8e964c5d082e40269c7'),
}


def fragment(source, prefix):
    start = source.index(prefix)
    return source[start:source.index("\n}", start) + 2] + "\n"


def main():
    if any(not (ROOT / 'recomp/out' / name).is_file() for name in ('speed_20.c', 'speed_16.c', 'speed_52.c')):
        print('SKIP: translated cash functions unavailable; build the recompiler first')
        raise SystemExit(3)
    port = (ROOT / 'runtime/settings_port.c').read_text()
    rows = port[port.index('#include "cash_cheat.h"'):port.index('// ---------------- Display')]
    registers = re.search(r'^    REG\(h_cash\);[^\n]+', port, re.M).group()
    assert 'cash_cheat.c' in (ROOT / 'runtime/build.sh').read_text()
    assert 'cash_cheat_frame(c);' in (ROOT / 'runtime/d3d9.m').read_text()
    award = function((ROOT / 'recomp/out/speed_20.c').read_text(), 'f_00566010')
    getter = function((ROOT / 'recomp/out/speed_16.c').read_text(), 'f_0050E300')
    feature = (ROOT / 'runtime/cash_cheat.c').read_text()
    registration = '\nstatic void register_rows(void){' + registers + '}\n'
    menu = (ROOT / 'runtime/portmenu.m').read_text()
    paste = fragment(menu, 'static void paste_text(')
    typed_char = fragment(menu, 'int portmenu_char(')
    menu_input = paste + typed_char
    marker_source = (ROOT / 'recomp/out/speed_52.c').read_text()
    marker = 'static void marker_reward(Cpu *c){\nuint8_t cf=0,pf=0,af=0,zf=0,sf=0,of=0;\n'
    marker += marker_source[marker_source.index('    /* 007B3DDA:'):marker_source.index('    /* 007B3DF6:')] + '}\n'
    for body, digest in BEFORE.values():
        assert hashlib.sha256(body.encode()).hexdigest() == digest
    before_online = feature.replace(fragment(feature, 'static uint32_t career_profile('), BEFORE['career'][0])
    before_input = feature.replace(fragment(feature, 'void cash_cheat_typed('), BEFORE['typed'][0])
    before_cap = feature.replace('#define CASH_MAX ((uint32_t)INT32_MAX - 10000000u)', BEFORE['cap'][0])
    before_headers = feature.replace(fragment(feature, 'static void refresh_cash('), BEFORE['refresh'][0])
    before_all = before_online.replace(fragment(before_online, 'void cash_cheat_typed('), BEFORE['typed'][0])
    before_all = before_all.replace('#define CASH_MAX ((uint32_t)INT32_MAX - 10000000u)', BEFORE['cap'][0])
    before_all = before_all.replace(fragment(before_all, 'static void refresh_cash('), BEFORE['refresh'][0])
    # Before this feature there are no cash rows and no frame callback. This
    # frozen replacement contains neither implementation; no git history read.
    baseline = '''
static void register_rows(void){}
void cash_cheat_frame(Cpu*c){(void)c;}
void cash_cheat_100000(int v){(void)v;}
'''
    assert hashlib.sha256(baseline.encode()).hexdigest() == '3879ef7293ac98f4eaa1fb6b7dcf26322b3b0c3a05a295906c0fac9468141cc8'
    variants = [('production', feature, menu_input, 0),
                ('pre-feature', baseline + 'void cash_cheat_20000(int v){}\nvoid cash_cheat_50000(int v){}\nvoid cash_cheat_typed(int v){}\n', menu_input, 1),
                ('missing-award', feature.replace('call_guest(c, 0x00566010u, 1, &award);', '(void)award;'), menu_input, 1),
                ('missing-refresh', feature.replace('refresh_cash(c, M32(profile + 0xB4));', ''), menu_input, 1),
                ('online-before', before_online, menu_input, 1),
                ('input-before', before_input, BEFORE['paste'][0] + BEFORE['char'][0], 1),
                ('cap-before', before_cap, menu_input, 1),
                ('headers-before', before_headers, menu_input, 1),
                ('review-before', before_all, BEFORE['paste'][0] + BEFORE['char'][0], 1)]
    with tempfile.TemporaryDirectory(prefix='cash-cheat-') as directory:
        tmp = Path(directory)
        for name, body, input_body, expected in variants:
            source = tmp / (name + '.m')
            registered = '' if name == 'pre-feature' else rows + registration
            source.write_text(PRE + award + getter + marker + registered + body + input_body + POST)
            result = subprocess.run(['clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined',
                                     '-I' + str(ROOT / 'runtime'), '-I' + str(ROOT / 'recomp'),
                                     '-include', str(ROOT / 'runtime/text_fit.h'), '-fobjc-arc', '-framework', 'Foundation',
                                     str(source), str(ROOT / 'runtime/text_fit.c'), '-o', str(tmp / name)],
                                    capture_output=True, text=True)   # the menu's text editing lives in text_fit.c
            assert result.returncode == 0, result.stderr[-3000:]
            result = subprocess.run([str(tmp / name)], capture_output=True, text=True)
            print(name + ': ' + result.stdout.strip())
            assert result.returncode == expected, result.stderr[-3000:]
        registry = (ROOT / 'runtime/settings.c').read_text()
        for name, body, expected in [('registry', registry, 0),
                                     ('old-capacity', re.sub(r'#define MAX_SETTINGS \d+', '#define MAX_SETTINGS 160', registry), 1)]:
            (tmp / 'settings.c').write_text(body)
            (tmp / 'registry.c').write_text(REGISTRY)
            result = subprocess.run(['clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined',
                                     '-I' + str(ROOT / 'runtime'), '-I' + str(ROOT / 'recomp'),
                                     str(tmp / 'registry.c'), str(tmp / 'settings.c'), '-o', str(tmp / name)], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr[-3000:]
            result = subprocess.run([str(tmp / name)], capture_output=True, text=True)
            print(name + ': ' + result.stdout.strip())
            assert result.returncode == expected, result.stderr[-3000:]


if __name__ == '__main__':
    main()
