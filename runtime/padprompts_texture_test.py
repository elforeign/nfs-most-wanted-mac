#!/usr/bin/env python3
"""Prompt registration/render regression, real production C with synthetic textures.

The old 36-slot registration with 26 occupied slots loses 24/40 layout/glyph
lookups after flare writes (including SELECT/L1/R1); fixed: 0/40 missing.
L1/R1 renders: old 0/4, fixed 4/4. Broken lookup/capacity controls must fail.
No game data, graphics device or controller required. Optional --source checks
an older padprompts.c against the same assertions.
"""
from pathlib import Path
import argparse
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PRE = r'''
#include "host.h"
#include <stdlib.h>
#include <assert.h>
uint8_t *G;
int diag_on;
static int layout, available_xbox = 1, renders, original_lookups;
static uint32_t drawn, next_texture = 0x100000;
static int glyphs_xbox(void) { return layout; }
static int mode(void) { return 1; }
static int console(void) { return 1; }
static int keep_get(uint32_t o) { (void)o; return 0; }
static uint32_t cap_get(uint32_t o) { (void)o; return 0; }
static void render_inventory(Cpu *c, uint32_t o, uint32_t f, uint32_t tex, uint32_t sub) {
    (void)c; (void)o; (void)f; (void)tex; (void)sub;
}
void diag_event(const char *fmt, ...) { (void)fmt; }
static uint32_t str_hash(const char *s) {
    uint32_t h = 0xFFFFFFFFu;
    for (; *s; ++s) h = h * 33 + (uint32_t)(*s >= 'a' && *s <= 'z' ? *s - 32 : *s);
    return h;
}
#define FN_ADD_OTHER_TEXTURE 0x004FAF10u
#define OTHER_TEX_HASH 0x009159A0u
#define OTHER_TEX_TI 0x00915A30u
#define OTHER_TEX_N 0x00915FA8u
#define FN_GET_TEXTURE_INFO 0x00503400u
static uint32_t fake_texture(uint32_t hash, int xb) {
    uint32_t ti = next_texture; next_texture += 0x100;
    M32(ti + 0x24) = hash; M32(ti + 0x70) = 0xA000u + xb;
    return ti;
}
uint32_t gmalloc(uint32_t n) {
    uint32_t p = next_texture; next_texture += (n + 255) & ~255u; return p;
}
static uint32_t make_ps_glyph(Cpu *c, int art, const char *name, uint32_t hash) {
    (void)c; (void)art; (void)name; return fake_texture(hash, 0);
}
uint32_t x360_texture_load(Cpu *c, const char *file, uint32_t hash, uint32_t as_hash) {
    (void)c; (void)file; (void)hash;
    return available_xbox ? fake_texture(as_hash, 1) : 0;
}
void f_00503400_orig(Cpu *c) {
    ++original_lookups;
    c->eax = arg(c, 1) ? 0xF000u : 0;
    for (uint32_t i = 0; i < M32(OTHER_TEX_N); ++i)
        if (M32(OTHER_TEX_HASH + 4*i) == arg(c, 0)) { c->eax = M32(OTHER_TEX_TI + 4*i); break; }
    c->esp += 4;
}
void f_00503400(Cpu *c);
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) {
    if (fn == 0x006DDD90u) { M32(M32(c->ecx) + 0x18) = 0x123456; return 0; }
    if (fn == FN_ADD_OTHER_TEXTURE) {
        uint32_t i = M32(OTHER_TEX_N)++;
        assert(n == 2 && i < 36);
        M32(OTHER_TEX_HASH + 4*i) = a[0]; M32(OTHER_TEX_TI + 4*i) = a[1];
        return 0;
    }
    assert(fn == FN_GET_TEXTURE_INFO && n == 3);
    Cpu q = *c; q.esp = 0x8000;
    for (int i = 0; i < n; ++i) M32(q.esp + 4 + 4*i) = a[i];
    f_00503400(&q); assert(q.esp == 0x8004);
    return q.eax;
}
void f_005A45B0_orig(Cpu *c) {
    uint32_t o = arg(c, 0), a[] = {M32(o + 0x24), 1, 0};
    drawn = call_guest(c, FN_GET_TEXTURE_INFO, 3, a); ++renders;
    c->esp += 12;
}
'''
POST = r'''
int main(int argc, char **argv) {
    unsigned occupied = argc > 1 ? (unsigned)atoi(argv[1]) : 26;
    available_xbox = argc < 3 || atoi(argv[2]);
    G = calloc(1, 16*1024*1024); assert(G);
    M32(OTHER_TEX_N) = occupied;
    for (unsigned i = 0; i < occupied; ++i) {
        M32(OTHER_TEX_HASH + 4*i) = 0xCA000000 + i;
        M32(OTHER_TEX_TI + 4*i) = 0xCB000000 + i;
    }
    Cpu c = {0}; c.esp = 0x4000; c.eax = 11; c.ecx = 22; c.edx = 33;
    prompts_textures(&c); assert(c.esp == 0x4000 && c.eax == 11 && c.ecx == 22 && c.edx == 33);
    unsigned missing = 0, shoulders = 0;
    for (layout = 0; layout < 2; ++layout) {
        assert(have_art() == (available_xbox || !layout));
        // RenderSunAsFlare (00503830) writes these two view-exclusion masks.
        // They alias the old prompt registration's L1/R1 pointer slots 30/31.
        M32(0x00915AA8u) = 0xFE; M32(0x00915AACu) = 0xFC00F6;
        for (unsigned i = 0; i < NGLYPH; ++i) {
            uint32_t sub = glyph_sub(str_hash(glyphs[i].name)), a[] = {sub, 1, 0};
            uint32_t ti = call_guest(&c, FN_GET_TEXTURE_INFO, 3, a);
            uint32_t want = glyph_ti[available_xbox ? layout : 0][i];
            if (!sub || ti != want) ++missing;
        }
        for (unsigned i = 4; i <= 5; ++i) {
            uint32_t o = 0x5000;
            M32(o + 0x10) = i; M32(o + 0x18) = 1;
            M32(o + 0x1C) = 0x42000000; M32(o + 0x24) = str_hash(glyphs[i].name);
            c.esp = 0x4000; M32(c.esp + 4) = o;
            render_prompt_object(&c);
            if (drawn == glyph_ti[available_xbox ? layout : 0][i]) ++shoulders;
            assert(c.esp == 0x400C && M32(o + 0x24) == str_hash(glyphs[i].name));
        }
    }
    // The private art must not consume or overwrite a single existing extra texture.
    unsigned changed = M32(OTHER_TEX_N) != occupied;
    for (unsigned i = 0; i < occupied; ++i)
        changed += M32(OTHER_TEX_HASH + 4*i) != 0xCA000000 + i || M32(OTHER_TEX_TI + 4*i) != 0xCB000000 + i;
    c.esp = 0x4000; M32(c.esp + 4) = 0xDEADBEEF; M32(c.esp + 8) = 0;
    f_00503400(&c); assert(c.eax == 0 && c.esp == 0x4004 && original_lookups);
    // The extra-texture API must stop at the decoded 30-entry boundary.
    uint8_t rec[124] = {0}, pl[32] = {0}; uint32_t h = 0xDA000001;
    memcpy(rec + 0x24, &h, 4);
    M32(OTHER_TEX_N) = 30; uint32_t before = next_texture;
    if (extra_texture_add(&c, rec, pl, 21, 0, 4, 1) != 0) {
        puts("extra capacity: overflow accepted at slot 30 (expected rejection)"); free(G); return 1;
    }
    assert(next_texture == before && M32(OTHER_TEX_N) == 30 && M32(0x00915AA8u) == 0xFE);
    assert(extra_texture_add(&c, rec, pl, 21, 0, 4, 0));
    assert(M32(OTHER_TEX_N) == 30);
    M32(OTHER_TEX_N) = 29;
    assert(extra_texture_add(&c, rec, pl, 21, 0, 4, 1));
    assert(M32(OTHER_TEX_N) == 30 && M32(OTHER_TEX_HASH + 29*4) == h);
    printf("occupied %u, Xbox assets %d: missing %u/40, shoulders %u/4, extra changes %u\n", occupied, available_xbox, missing, shoulders, changed);
    free(G); return missing || shoulders != 4 || changed;
}
'''


def body(source, extra):
    table = source.split('enum { A_CROSS', 1)[1].split('#define S 64', 1)[0]
    art = source.split('static void use_layout(void)', 1)[1].split('// ---------------- the hooks', 1)[0]
    render = source.split('static void render_prompt_object(Cpu *c)', 1)[1].split('// Scope even', 1)[0]
    fallback = '' if 'void f_00503400(Cpu *c)' in art else 'void f_00503400(Cpu *c) { f_00503400_orig(c); }\n'
    return PRE + extra + 'enum { A_CROSS' + table + 'static void use_layout(void)' + art + fallback + 'static void render_prompt_object(Cpu *c)' + render + POST


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT / 'runtime/padprompts.c')
    args = parser.parse_args()
    source = args.source.read_text()
    extra = (ROOT / 'runtime/x360_textures.m').read_text().split('uint32_t extra_texture_add(', 1)[1].split('// The top level of an X360 texture', 1)[0]
    extra = 'uint32_t extra_texture_add(' + extra
    variants = [('production', source, extra)]
    if args.source == ROOT / 'runtime/padprompts.c':
        no_lookup = source.replace('if (art_made && hash)', 'if (0)')
        no_cap = extra.replace('if (add && M32(0x00915FA8u) >= 30)', 'if (0)')
        assert no_lookup != source and no_cap != extra
        variants += [('broken-lookup', no_lookup, extra), ('broken-capacity', source, no_cap)]
    with tempfile.TemporaryDirectory(prefix='prompt-textures-') as tmp:
        tmp = Path(tmp)
        for label, text, texture_api in variants:
            cfile, exe = tmp / 'check.c', tmp / 'check'
            cfile.write_text(body(text, texture_api))
            p = subprocess.run(['clang', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                                '-I' + str(ROOT / 'runtime'), '-I' + str(ROOT / 'recomp'), '-I' + str(ROOT / 'recomp/out'),
                                str(cfile), '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-3000:]
            cases = [('26', '1')] if label != 'production' or args.source != ROOT / 'runtime/padprompts.c' else [('0', '1'), ('26', '1'), ('30', '1'), ('26', '0')]
            for case in cases:
                p = subprocess.run([str(exe), *case], capture_output=True, text=True)
                print(label + ': ' + p.stdout.strip())
                if label == 'production':
                    assert p.returncode == 0, p.stdout + p.stderr[-2000:]
                else:
                    assert p.returncode == 1, p.stdout + p.stderr[-2000:]
        print('PADPROMPTS TEXTURE PASS: 4 production cases; 2 broken controls rejected')


if __name__ == '__main__':
    main()
