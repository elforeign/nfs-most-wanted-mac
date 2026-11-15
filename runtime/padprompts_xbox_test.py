#!/usr/bin/env python3
"""Real CoreGraphics Xbox glyphs + production registration, no private assets.

Oracle: 20/20 nonempty, pairwise distinct BGRA images; four independently
specified face colour families each cover >=500 pixels. Default (unset) and on: 20/20; off/0: 0/20.
Broken controls: PS family under Xbox names 0/4 families, missing glyph 0/20
usable (all-or-nothing); both rejected. Complete X360 set: 20/20, 0 drawn.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Independent public prompt vocabulary, not derived from the production table.
NAMES = ('CROSS TRIANGLE SQUARE CIRCLE L1 R1 L2 R2 R3 START SELECT DPAD '
         'DPAD_DOWN DPAD_LEFT DPAD_RIGHT DPAD_LEFTRIGHT RIGHT_ANALOG '
         'RIGHT_ANALOG_UP RIGHT_ANALOG_DOWN LEFT_ANALOG').split()
PRE = r'''
#include <CoreGraphics/CoreGraphics.h>
#include <CoreText/CoreText.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include "host.h"
uint8_t *G;
int diag_on;
const char *diag_dir;
static unsigned next_data = 0x10000, drawn, assets;
static int glyphs_xbox(void) { return 1; }
static int mode(void) { return 1; }
void diag_event(const char *s, ...) { (void)s; }
void diag_png(const char *p, const void *b, uint32_t w, uint32_t h, uint32_t stride, int bgra) {
    (void)p;(void)b;(void)w;(void)h;(void)stride;(void)bgra;
}
uint32_t gmalloc(uint32_t n) { unsigned p=next_data; next_data+=n; return p; }
uint32_t extra_texture_add(Cpu *c, const uint8_t rec[124], const uint8_t pl[32],
                          uint32_t fmt, uint32_t data, uint32_t size, int add) {
    (void)c;(void)rec;(void)pl;(void)fmt;(void)size;(void)add;
    ++drawn; return data;
}
uint32_t x360_texture_load(Cpu *c,const char *f,uint32_t h,uint32_t as) {
    (void)c;(void)f;(void)h;(void)as;
    static unsigned calls;
    return calls++ < assets ? 0xF00000 : 0;
}
void f_00503400_orig(Cpu *c) { (void)c; abort(); }
static uint32_t str_hash(const char *s) {
    uint32_t h=0xFFFFFFFFu;
    for(;*s;++s)h=h*33+(uint32_t)(*s>='a'&&*s<='z'?*s-32:*s);
    return h;
}
'''
POST = r'''
int main(int argc, char **argv) {
    if(argc!=3)return 2;
    assets=atoi(argv[2]);
    G=calloc(1,16*1024*1024); Cpu c={0}; prompts_textures(&c);
    printf("drawn=%u usable=%d\n",drawn-20,have_art());
    for(unsigned i=0;i<NGLYPH;++i)if(glyph_ti[1][i] && glyph_ti[1][i]!=0xF00000) {
        char path[1024];snprintf(path,sizeof path,"%s/%s.bgra",argv[1],glyphs[i].name);
        FILE *f=fopen(path,"wb");if(!f)return 2;
        fwrite(G+glyph_ti[1][i],1,S*S*4,f);fclose(f);
    }
    free(G);return 0;
}
'''


def oracle(folder):
    images = {}
    for name in NAMES:
        path = folder / (name + '.bgra')
        if not path.exists():
            return False, 'missing ' + name
        data = path.read_bytes()
        if len(data) != 64*64*4 or sum(a > 128 for a in data[3::4]) < 200:
            return False, 'empty ' + name
        images[name] = data
    if len(set(images.values())) != 20:
        return False, 'duplicate glyph'
    # BGRA, with thresholds independent of the implementation's RGB constants.
    families = {
        'CROSS': lambda r,g,b: g > 120 and g > r*1.5 and g > b*1.5,
        'TRIANGLE': lambda r,g,b: r > 150 and r > g*1.8 and r > b*1.8,
        'SQUARE': lambda r,g,b: b > 150 and b > r*1.8 and b > g*1.4,
        'CIRCLE': lambda r,g,b: r > 170 and g > 130 and b < 70,
    }
    passed = 0
    for name, family in families.items():
        px = images[name]
        count = sum(px[i+3] > 200 and family(px[i+2],px[i+1],px[i])
                    for i in range(0,len(px),4))
        passed += count >= 500
    return passed == 4, f'families={passed}/4, nonempty=20/20, distinct=20/20'


def main():
    source = (ROOT/'runtime/padprompts.c').read_text()
    body = source[source.index('enum { A_CROSS'):source.index('// ---------------- the hooks')]
    wrong = body.replace('if (xb) draw_xbox_art(g, xb); else draw_art(g, art);',
                         'if (xb) { for(unsigned i=0;i<NGLYPH;++i) if(!strcmp(xb,glyphs[i].xb)) draw_art(g,glyphs[i].ps); } else draw_art(g, art);')
    missing = body.replace('return make_glyph(c, 0, xb, name, hash);',
                           'if (!strcmp(xb,"RIGHT_ANALOG_DOWN")) return 0; return make_glyph(c, 0, xb, name, hash);')
    assert wrong != body and missing != body
    with tempfile.TemporaryDirectory(prefix='xbox-prompts-') as tmp:
        tmp=Path(tmp)
        for variant, text in [('production',body),('wrong-family',wrong),('missing-glyph',missing)]:
            cfile=tmp/'check.c';exe=tmp/'check';cfile.write_text(PRE+text+POST)
            command=['clang','-O1','-g','-fsanitize=address,undefined','-fno-sanitize-recover=all',
                     '-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),
                     str(cfile),'-framework','CoreGraphics','-framework','CoreText','-framework','CoreFoundation','-o',str(exe)]
            p=subprocess.run(command,capture_output=True,text=True)
            assert p.returncode == 0,p.stderr[-2500:]
            cases=[('on','0')]
            if variant=='production': cases += [('off','0'),('unset','0'),('1','0'),('invalid','0'),('0','0'),('on','20'),('on','19')]
            for switch, assets in cases:
                folder=tmp/f'{variant}-{switch}-{assets}';folder.mkdir()
                env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'};env.pop('NFS_XBOX_PROMPT_ART',None)
                if switch!='unset':env['NFS_XBOX_PROMPT_ART']=switch
                p=subprocess.run([str(exe),str(folder),assets],capture_output=True,text=True,env=env)
                assert p.returncode==0,p.stderr[-2500:]
                ok, detail=oracle(folder)
                if variant!='production':
                    assert not ok,variant+' escaped oracle'
                elif assets=='20':
                    assert 'drawn=0 usable=1' in p.stdout and not list(folder.glob('*.bgra')),p.stdout
                elif switch in ('off','0'):
                    assert 'drawn=0 usable=0' in p.stdout and not list(folder.glob('*.bgra')),p.stdout
                else:
                    assert ok and 'drawn=20 usable=1' in p.stdout,(detail,p.stdout)
                print(f'{variant} {switch} assets={assets}: {p.stdout.strip()}; {detail}')
    print('PADPROMPTS XBOX PASS: 20 default/on / 0 off; wrong-family and missing-glyph controls rejected')


if __name__=='__main__':main()
