#!/usr/bin/env python3
"""Shader frame-path regression, without Metal or a game launch.

Production lookup/scheduling: normal frame-thread compile_entry calls 0; broken 1.
Cache round-trip: normal 8 records; broken AUX whitelist 1 record;
broken Clear-lamp whitelist 6 records; broken film whitelist 7 records.
Readiness after publication: normal pending 0; broken 1.
Concurrent identical-source requests: normal 1 compile; broken cache 4.
Cold profile (2026-10-28): source compiles before/after 13/0, hangs 0/0,
stalls 0/0, deferred draws 635515/0 (game_run runs 1 and 5).
MSL identity: normal 1744 comparisons / 0 differences; broken shadow mask
rejected at its first difference. These are source comparisons, not frame proof.
Contact shadows (free-roam entry stalled 500+ ms on a frame-path compile): synchronous
newLibraryWithSource calls in x_contact_scene_impl, normal 0; the previous source 2.
Optional --diag DIR checks real frame_sync counters and hang/stall reports.
The translator check compares exact MSL before/after option canonicalization.
"""
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "runtime/render.m").read_text()


def between(start, end):
    return SRC.split(start, 1)[1].split(end, 1)[0]


def run(args, expected=0):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       cwd=ROOT, timeout=90)
    assert p.returncode == expected, f"exit {p.returncode}, expected {expected}: {p.stdout[-3000:]}"
    return p.stdout


def diagnostic(path):
    text = (path / "events.log").read_text()
    counts = [int(n) for n in re.findall(r"shader_compile:.*?frame_sync=(\d+)", text)]
    assert counts, "missing shader_compile frame_sync counter"
    hangs = len(list(path.glob("hang_*.txt")))
    stalls = len(list(path.glob("stall_*.txt")))
    print(f"game: frame_sync={max(counts)} hangs={hangs} stalls={stalls}")
    return int(max(counts) != 0 or hangs != 0 or stalls != 0)


PRELUDE = r'''
#import <Foundation/Foundation.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "host/shaders/sm_translate.h"
enum { MAT_VARIANT_WATER=0x100, MAT_VARIANT_GLASS, MAT_VARIANT_TREES,
       MAT_VARIANT_BONE, MAT_VARIANT_SOFT_SMOKE, MAT_VARIANT_SOFT_SMOKE_RESOLVED,
       MAT_VARIANT_SOFT_SMOKE_LATE, MAT_VARIANT_CARBON_LAMP, MAT_VARIANT_HEADLAMP_CLEAR, MAT_VARIANT_ROAD_FILM, MAT_VARIANT_AUX=0x200 };
typedef struct {
 uint64_t key; SmResult refl; volatile int pending; int ok; dispatch_semaphore_t ready;
 uint32_t *tokens, ntokens; SmOptions opt; uint32_t variant;
} ShaderEntry;
typedef struct {
 uint64_t tokens_hash, last_key, memo_key[2]; int last_idx, memo_next;
 uint8_t memo_opt[2][48]; uint32_t memo_variant[2], vc_serial;
 uint32_t *tokens, ntokens;
} Obj;
static ShaderEntry **shaders;
static int nshaders, capshaders;
static dispatch_queue_t shader_q, shader_workers[4];
static unsigned shader_worker_next;
static dispatch_semaphore_t allow_compile, started;
static int frame_sync, compiled;
static const char *scratch;
static const char *userdata_dir(void) { return scratch; }
static int diag_on;
static uint64_t frame_serial=1;
static unsigned long long stat_sync_shader;
static void diag_event(const char *fmt, ...) { (void)fmt; }
@protocol MTLLibrary @end
@class MTLCompileOptions;
static int library_requests;
@interface CompileHitchDevice : NSObject
- (id<MTLLibrary>)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options error:(NSError **)error;
@end
@implementation CompileHitchDevice
- (id<MTLLibrary>)newLibraryWithSource:(NSString *)source options:(MTLCompileOptions *)options error:(NSError **)error {
 (void)options;
 __atomic_add_fetch(&library_requests,1,__ATOMIC_RELAXED);
 usleep(10000);
 if ([source isEqualToString:@"invalid"]) { *error=[NSError errorWithDomain:@"fixture" code:7 userInfo:nil];return nil; }
 return (id<MTLLibrary>)[NSObject new];
}
@end
static CompileHitchDevice *mtl;
static uint64_t host_ticks(void) {
 struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
 return (uint64_t)t.tv_sec*1000000000ull+t.tv_nsec;
}
static void shader_catalogue_warm(void) {}
static void compile_entry(ShaderEntry *e) {
 if (pthread_main_np()) { ++frame_sync; }
 else {
   dispatch_semaphore_signal(started);
   dispatch_semaphore_wait(allow_compile, DISPATCH_TIME_FOREVER);
 }
 e->ok=1;
 __atomic_add_fetch(&compiled,1,__ATOMIC_RELAXED);
 __atomic_store_n(&e->pending,0,__ATOMIC_RELEASE);
 dispatch_semaphore_signal(e->ready);
}
static int check(int good, const char *why) {
 if (!good) fprintf(stderr,"FAIL: %s frame_sync=%d compiled=%d\n",why,frame_sync,compiled);
 return !good;
}
'''

MAIN = r'''
int main(int argc, char **argv) { @autoreleasepool {
 scratch=argv[1];
 allow_compile=dispatch_semaphore_create(0); started=dispatch_semaphore_create(0);
 shader_cache_warm();
 // Original synthetic fixture shared with the translator tests (no game bytecode).
 FILE *fixture=fopen(argv[2],"rb"); if(!fixture)return 2;
 fseek(fixture,0,SEEK_END);long token_bytes=ftell(fixture);rewind(fixture);
 if(token_bytes<=0 || token_bytes%4)return 2;
 uint32_t *tokens=malloc(token_bytes);
 if(fread(tokens,1,token_bytes,fixture)!=(size_t)token_bytes)return 2;fclose(fixture);
 Obj s={0}; s.tokens=tokens; s.ntokens=token_bytes/4;
 SmOptions o; sm_default_options(&o);
 ShaderEntry *e=get_shader_variant(&s,&o,0);
 if (check(frame_sync==0,"no compile on frame thread")) return 1;
 if (check(__atomic_load_n(&e->pending,__ATOMIC_ACQUIRE),"cold lookup returns pending")) return 1;
 if (check(!dispatch_semaphore_wait(started,dispatch_time(DISPATCH_TIME_NOW,1000000000)),"background started")) return 1;
 if (check(get_shader_variant(&s,&o,0)==e,"pending memo lookup returns")) return 1;
 if (check(shader_pending(e),"bounded base wait returns while compiler is blocked")) return 1;
 Obj other={0}; other.tokens=tokens; other.ntokens=s.ntokens;
 if (check(get_shader_variant(&other,&o,0)==e,"pending shared lookup returns")) return 1;
 SmOptions irrelevant=o; irrelevant.sampler_type[7]=SM_TEX_CUBE;
 if (check(get_shader_variant(&s,&irrelevant,0)==e && get_shader_variant(&s,&irrelevant,0)==e,
           "unread texture options share the pending shader")) return 1;
 uint32_t variants[]={MAT_VARIANT_AUX,MAT_VARIANT_SOFT_SMOKE,
    MAT_VARIANT_SOFT_SMOKE_RESOLVED,MAT_VARIANT_SOFT_SMOKE_LATE,MAT_VARIANT_CARBON_LAMP,MAT_VARIANT_HEADLAMP_CLEAR,MAT_VARIANT_ROAD_FILM};
 for (int i=0;i<7;i++) get_shader_variant(&s,&o,variants[i]);
 for (int i=0;i<8;i++) dispatch_semaphore_signal(allow_compile);
 for (int i=0;i<4;i++) dispatch_sync(shader_workers[i], ^{});
 mtl=[CompileHitchDevice new];diag_on=1;
 id results[4];
 dispatch_group_t group=dispatch_group_create();
 for (int i=0;i<4;i++) {
   id __strong *slot=&results[i];
   dispatch_group_async(group,shader_workers[i],^{ @autoreleasepool {
     NSError *error=nil; *slot=shader_library(@"same exact source",nil,&error);
   }});
 }
 dispatch_group_wait(group,DISPATCH_TIME_FOREVER);
 printf("identical-source requests=%d frame_sync=%llu\n",library_requests,stat_sync_shader);
 if(check(library_requests==1 && !stat_sync_shader,"concurrent exact-source compile sharing"))return 1;
 for(int i=1;i<4;i++)if(check(results[i]==results[0],"shared library identity"))return 1;
 dispatch_sync(shader_workers[0],^{ @autoreleasepool {
   NSError *a=nil,*b=nil;
   id one=shader_library(@"invalid",nil,&a),two=shader_library(@"invalid",nil,&b);
   if(one||two||a.code!=7||b.code!=7)abort();
 }});
 if(check(library_requests==2,"failed compile publishes error and wakes followers"))return 1;
 if (check(!e->pending && e->ok && compiled==8,"release/acquire publication")) return 1;
 if (check(!shader_pending(e),"ready shader no longer pending")) return 1;
 if (check(get_shader_variant(&s,&o,0)==e,"ready lookup reused")) return 1;
 // Keep shape metadata, but rebuild the shader/cache index exactly as a new launch does.
 nshaders=capshaders=0; shaders=NULL; memset(vcache,0,sizeof vcache);
 shader_cache_warm();
 printf("cache records=%d frame_sync=%d\n",nshaders,frame_sync);
 if (check(nshaders==8,"cache accepts AUX, soft smoke, lamp and film profiles")) return 1;
 for (int i=0;i<8;i++) dispatch_semaphore_signal(allow_compile);
 for (int i=0;i<4;i++) dispatch_sync(shader_workers[i], ^{});
 int comparisons=0;
 for (int a=2;a<argc;a++) {
   FILE *f=fopen(argv[a],"rb"); if(!f)return 2;
   fseek(f,0,SEEK_END); long bytes=ftell(f); rewind(f);
   uint32_t *t=malloc(bytes); if(fread(t,1,bytes,f)!=(size_t)bytes)return 2; fclose(f);
   uint64_t hash=fnv(t,bytes,1469598103934665603ull);
   ShaderShape *shape=shader_shape(t,bytes/4,hash);
   for (unsigned k=0;k<16;k++) {
     SmOptions raw; sm_default_options(&raw);
     for (int st=0;st<16;st++) raw.sampler_type[st] = k&1 ? (shape && shape->types[st] ? shape->types[st] : SM_TEX_CUBE) : 0;
     raw.shadow_samplers=(uint16_t)(k*0x1111);
     raw.vs_input_int=k&2 ? 0xffff:0; raw.vs_input_uint=k&4 ? 0xffff:0;
     for(int st=0;st<8;st++)raw.tex_projected[st]=k&8 ? 4:0;
     SmOptions normalized=shader_options(shape,&raw); SmResult before={0},after={0};
     int b=sm_translate(t,bytes/4,&raw,&before), c=sm_translate(t,bytes/4,&normalized,&after);
     if (check(b==c && (b || !strcmp(before.msl,after.msl)),argv[a]))return 1;
     sm_result_free(&before);sm_result_free(&after);++comparisons;
   }
   free(t);
 }
 printf("PASS: frame_sync=%d; exact MSL comparisons=%d\n",frame_sync,comparisons);
 return 0;
}}
'''


def contact_sync_compiles(post):
    body = post.split("static id<MTLTexture> x_contact_scene_impl(id<MTLTexture> scene", 1)[1]
    body = body.split("\n}\n", 1)[0]
    return len(re.findall(r"newLibraryWithSource:[^\]]*error:", body))


def contact_off_frame():
    post = (ROOT / "runtime/render_post.m").read_text()
    normal = contact_sync_compiles(post)
    old = subprocess.run(["git", "-C", str(ROOT), "show", "0f5b708c:runtime/render_post.m"], capture_output=True, text=True)
    assert normal == 0, f"contact shadows compile on the frame: {normal}"
    if old.returncode == 0:
        broken = contact_sync_compiles(old.stdout)
        assert broken > 0, "previous source should show the frame-path compile"
        print(f"contact shadows: frame-path compiles normal {normal}, previous source {broken}")


def offline():
    contact_off_frame()
    shape = between("// BEGIN offline shader options test\n", "// END offline shader options test")
    submit = between("// BEGIN offline shader scheduling test\n", "// END offline shader scheduling test")
    ready = between("// BEGIN offline shader readiness test\n", "// END offline shader readiness test")
    library = "typedef struct ShaderLibrary" + between("typedef struct ShaderLibrary", "static pthread_mutex_t shader_source_mu")
    new = "static ShaderEntry *new_entry" + between("static ShaderEntry *new_entry", "// BEGIN offline shader scheduling test")
    cache = "static void cache_path" + between("static void cache_path", "// Lookup cache in front")
    lookup = "typedef struct { uint32_t serial, variant;" + between("typedef struct { uint32_t serial, variant;", "// The game's fx_2_0 resources")
    fnv = "static uint64_t fnv" + between("static uint64_t fnv", "// ").split("\n}\n", 1)[0] + "\n}\n"
    code = PRELUDE + fnv + shape + new + submit + ready + library + cache + lookup + MAIN
    synthetic = ROOT / "host/shaders/testdata_synth/synth/vs20_math.bin"
    fixtures = sorted((ROOT / "host/shaders/testdata").glob("**/*.bin"))
    with tempfile.TemporaryDirectory(prefix="compilehitch-") as d:
        tmp = Path(d)
        objects = []
        for source in ("sm_parse.cpp", "sm_msl.cpp"):
            obj = tmp / (source + ".o")
            run(["clang++", "-std=c++17", "-O1", "-c", str(ROOT / "host/shaders" / source), "-o", str(obj)])
            objects.append(str(obj))
        def build(name, contents):
            path = tmp / (name + ".m"); path.write_text(contents)
            exe = tmp / name
            run(["clang", "-O1", "-fobjc-arc", "-I", str(ROOT), str(path), *objects,
                 "-framework", "Foundation", "-lc++", "-o", str(exe)])
            profile = tmp / (name + "-profile"); profile.mkdir()
            return [str(exe), str(profile), str(synthetic)]
        print(run(build("normal", code) + [str(p) for p in fixtures]).strip())
        broken = code.replace("    shader_submit(e);\n    cache_append(e);", "    compile_entry(e);\n    cache_append(e);")
        assert broken != code
        print("broken scheduling: " + run(build("broken", broken), 1).strip())
        broken_cache = code.replace(" && variant != MAT_VARIANT_AUX", "")
        assert broken_cache != code
        print("broken cache: " + run(build("broken-cache", broken_cache), 1).strip())
        broken_clear = code.replace(" && variant != MAT_VARIANT_HEADLAMP_CLEAR", "")
        assert broken_clear != code
        print("broken Clear cache: " + run(build("broken-clear-cache", broken_clear), 1).strip())
        broken_film = code.replace(" && variant != MAT_VARIANT_ROAD_FILM", "")
        assert broken_film != code
        print("broken film cache: " + run(build("broken-film-cache", broken_film), 1).strip())
        broken_ready = code.replace("if (!__atomic_load_n(&e->pending, __ATOMIC_ACQUIRE)) return 0;",
                                    "if (!__atomic_load_n(&e->pending, __ATOMIC_ACQUIRE)) return 1;")
        assert broken_ready != code
        print("broken readiness: " + run(build("broken-ready", broken_ready), 1).strip())
        broken_library = code.replace("ShaderLibrary *s = shader_libraries;", "ShaderLibrary *s = NULL;")
        assert broken_library != code
        print("broken library sharing: " + run(build("broken-library", broken_library), 1).strip())
        broken_options = code.replace("    opt.vs_input_int &= s->inputs;", "    opt.shadow_samplers = 0;\n    opt.vs_input_int &= s->inputs;")
        assert broken_options != code
        if fixtures:
            print("broken options: " + run(build("broken-options", broken_options) + [str(p) for p in fixtures], 1).strip())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--diag":
        sys.exit(diagnostic(Path(sys.argv[2])))
    offline()
