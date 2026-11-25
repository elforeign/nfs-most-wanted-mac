#!/usr/bin/env python3
"""Execute the production pipeline history across launches on Metal; automatically in offline_tests.

Each launch is a new process (new library and function objects, new addresses) sharing one USERDATA folder.
Fresh folder: 1 frame build, file 848 bytes (header + one record holding shader keys 1/2). Second launch: 0
frame builds (the replay without the history call, as on the old code: 1, exit 1). A flipped record byte,
a changed device/OS stamp, a truncated tail and a bad header are each discarded: 1 frame build, file rewritten
to 848 bytes. Accepting the flipped record (check ignored) replays 1 record against 0 (exit 1).
Review fixtures (production loader/writer, pinned pre-fix fragments):
invalid buffer index: before accepted=1 / after accepted=0, skipped=1;
10,000 duplicates: before accepted=1 (8,240,016 bytes) / after accepted=0, size rejected;
locked load: before blocked=0 / after blocked=1; short write: before clean=0 / after clean=1;
concurrent append at the bound: before physical=4103 / after physical=4096;
whole-record truncation: before accepted=1 / after accepted=0, truncated rejected.
Library-keyed (process pointer) states are never recorded: 0 records.
"""
from pathlib import Path
import struct
import hashlib
import fcntl
import time
import subprocess
import sys
import tempfile

import sys as _source_sys
_source_sys.path.insert(0, str(Path(__file__).resolve().parent))
from save_file_fixture import mw_source

ROOT = Path(__file__).resolve().parent
HEADER, RECORD = 24, 824

OLD_HISTORY = r'''
// Pipelines met on demand, remembered across launches in USERDATA/pipelinecache.bin and replayed at load on
// the catalogue's background queues. Records hold only stable identities: translated shader keys (bytecode,
// options and variant hashes) and API state at their concrete sample count. Library-keyed states carry process
// pointers and are never recorded. Any defect (header, stamp, check, truncated tail) discards the whole file.
// Included by pipeline_cache.m; inert until pipeline_history_open().
#define HISTORY_MAGIC 0x4850464Eu // "NFPH"
#define HISTORY_VERSION 1u        // bump when PipelineRecipe fields change meaning
#define HISTORY_MAX 4096
typedef struct { uint32_t magic, record_size; uint64_t stamp; } HistoryHeader;
typedef struct { PipelineRecipe recipe; uint64_t check; } HistoryRecord;
_Static_assert(sizeof(HistoryHeader)==16 && sizeof(HistoryRecord)==824, "history records have no implicit padding");
static NSMutableSet<NSData *> *history; // recorded identities; guarded by pipeline_cache_mu
static dispatch_queue_t history_q;      // file appends, off the frame path
static char history_file[1024];
static uint64_t history_stamp;
static size_t history_replayed;
static const char *history_defect;

static uint64_t history_hash(const void *p, size_t n, uint64_t h) {
    for (size_t i=0;i<n;++i) h=(h^((const uint8_t *)p)[i])*1099511628211ull;
    return h;
}
// A driver or OS change discards the history: the device name, OS build and format version form the stamp.
static uint64_t history_device_stamp(void) {
    NSString *s=[NSString stringWithFormat:@"%u|%zu|%@|%@",HISTORY_VERSION,sizeof(HistoryRecord),
        mtl.name,NSProcessInfo.processInfo.operatingSystemVersionString];
    return history_hash(s.UTF8String,strlen(s.UTF8String),1469598103934665603ull);
}
static void history_write_header(void) {
    HistoryHeader h={HISTORY_MAGIC,(uint32_t)sizeof(HistoryRecord),history_stamp};
    dispatch_async(history_q, ^{
        FILE *f=fopen(history_file,"wb");
        if (f) { fwrite(&h,sizeof h,1,f); fclose(f); }
    });
}
// Device thread at load, before pipeline_prewarm. Reads the file once; rewrites it only when absent or defective.
void pipeline_history_open(const char *dir) {
    if (!dir || !enabled()) return;
    snprintf(history_file,sizeof history_file,"%s/pipelinecache.bin",dir);
    history_stamp=history_device_stamp();
    history_q=dispatch_queue_create("nfsmw.pipeline.history",DISPATCH_QUEUE_SERIAL);
    NSMutableSet<NSData *> *loaded=[NSMutableSet new];
    history_defect=NULL;
    FILE *f=fopen(history_file,"rb");
    if (f) {
        HistoryHeader h; HistoryRecord rec; size_t got=0;
        if (fread(&h,sizeof h,1,f)!=1 || h.magic!=HISTORY_MAGIC || h.record_size!=sizeof rec) history_defect="header";
        else if (h.stamp!=history_stamp) history_defect="stamp";
        else {
            while ((got=fread(&rec,1,sizeof rec,f))==sizeof rec) {
                if (rec.check!=history_hash(&rec.recipe,sizeof rec.recipe,history_stamp)) { history_defect="record"; break; }
                if (loaded.count>=HISTORY_MAX) { history_defect="size"; break; }
                [loaded addObject:[NSData dataWithBytes:&rec.recipe length:sizeof rec.recipe]];
            }
            if (!history_defect && got) history_defect="truncated";
        }
        fclose(f);
    }
    if (history_defect) [loaded removeAllObjects];
    if (!f || history_defect) history_write_header();
    pthread_mutex_lock(&pipeline_cache_mu);
    history=loaded;
    pthread_mutex_unlock(&pipeline_cache_mu);
    if (diag_on) diag_event("pipeline_history: records=%lu discarded=%s",(unsigned long)loaded.count,history_defect?history_defect:"none");
}
// A successful demand build: remember it once, appended in the background.
static void history_note(const PipelineRecipe *r) {
    NSData *key=[NSData dataWithBytes:r length:sizeof *r];
    pthread_mutex_lock(&pipeline_cache_mu);
    int fresh=history && history.count<HISTORY_MAX && ![history containsObject:key];
    if (fresh) [history addObject:key];
    pthread_mutex_unlock(&pipeline_cache_mu);
    if (!fresh) return;
    HistoryRecord rec={*r,history_hash(r,sizeof *r,history_stamp)};
    dispatch_async(history_q, ^{
        FILE *f=fopen(history_file,"ab");
        if (f) { fwrite(&rec,sizeof rec,1,f); fclose(f); }
    });
}
'''
OLD_SHA256 = '79c8f17a120f664d08a332f7e1b2b42a4e269f07b7bbf619f5a5b595f0e639e9'

PRELUDE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <time.h>
#include "pipeline_cache.h"
static id<MTLDevice> mtl;
static int diag_on=1;
static uint64_t host_ticks(void) { struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1000000000ull+t.tv_nsec; }
static void diag_event(const char *s,...) { va_list a;va_start(a,s);vprintf(s,a);va_end(a);putchar('\n'); }
'''
FIXTURES = r'''
    {.vertex=1,.fragment=2,.samples=1,.color=MTLPixelFormatRGBA8Unorm,.mask=15,
     .srcRGB=1,.srcAlpha=1,.attributes={[0]={MTLVertexFormatFloat2,0,0}},.layouts={[0]={8,1,1}}},
'''
IO_INJECTION = r"""
#include <unistd.h>
#include <errno.h>
static int fail_record_write;
static ssize_t fixture_pwrite(int fd,const void *p,size_t n,off_t off) {
    if(fail_record_write && ((fail_record_write==1 && n==824) || (fail_record_write==2 && n==24))) {
        fail_record_write=0;return pwrite(fd,p,n==24 ? n-1 : n/2,off);
    }
    if(fail_record_write==3 && n==824){fail_record_write=0;errno=ENOSPC;return -1;}
    return pwrite(fd,p,n,off);
}
static size_t fixture_fwrite(const void *p,size_t n,size_t count,FILE *f) {
    if(fail_record_write && n*count==824){fail_record_write=0;fwrite(p,1,412,f);return 0;}
    return fwrite(p,n,count,f);
}
#define pwrite fixture_pwrite
#define fwrite fixture_fwrite
"""
INSPECT = r"""
    if(argc>2 && (!strcmp(argv[2],"inspect") || !strncmp(argv[2],"append",6))) {
        pipeline_history_open(argv[1]);
        if(!strncmp(argv[2],"append",6)) {
            PipelineRecipe r=recipes[0];r.mask=argc>3 ? (uint32_t)atoi(argv[3]) : 3;
            fail_record_write=argv[2][6] ? atoi(argv[2]+6) : 1;history_note(&r);dispatch_sync(history_q,^{});
        }
        printf("inspect: accepted=%lu discarded=%s\n",(unsigned long)history.count,history_defect?history_defect:"none");
        return 0;
    }
"""
MAIN = r'''
int main(int argc,const char **argv) { @autoreleasepool {
    mtl=MTLCreateSystemDefaultDevice();if(!mtl){puts("SKIP: needs Metal device");return 3;}
    NSError *error=nil;
    NSString *src=@"#include <metal_stdlib>\nusing namespace metal;struct A{float2 p [[attribute(0)]];};vertex float4 v(A a [[stage_in]]){return float4(a.p,0,1);}fragment float4 f(){return float4(.125,.25,.5,1);}";
    id<MTLLibrary> lib=[mtl newLibraryWithSource:src options:nil error:&error];if(!lib)return 2;
    pipeline_register_shader(1,[lib newFunctionWithName:@"v"]);pipeline_register_shader(2,[lib newFunctionWithName:@"f"]);
    dispatch_queue_t queues[4];for(int i=0;i<4;++i)queues[i]=dispatch_queue_create("history-test",DISPATCH_QUEUE_SERIAL);
    pipeline_history_open(argv[1]);
    pipeline_prewarm(queues,1);
    for(int i=0;i<4;++i)dispatch_sync(queues[i],^{});   // test only: frames never join these jobs
    pipeline_loading_done();
    // A pass met on demand, outside the catalogue: write mask 3.
    MTLRenderPipelineDescriptor *d=descriptor(&recipes[0]);d.colorAttachments[0].writeMask=3;
    unsigned long long before,after,ns;pipeline_frame_stats(&before,&ns);
    if(!pipeline_cached(d,1,2,"draw",&error))return 2;
    pipeline_frame_stats(&after,&ns);
    // An embedded library state: process pointer identity, never persisted.
    MTLRenderPipelineDescriptor *e=descriptor(&recipes[0]);e.colorAttachments[0].writeMask=1;
    if(argc>2 && !pipeline_cached_library(e,lib,"embedded",&error))return 2;
    if(history_q)dispatch_sync(history_q,^{});
    printf("history launch: frame_builds=%llu replayed=%zu discarded=%s\n",after-before,history_replayed,history_defect?history_defect:"none");
    return 0;
}}
'''


def build(source, td, name):
    src, exe = td / (name + '.m'), td / name
    src.write_text(PRELUDE + IO_INJECTION + source + MAIN.replace('    NSError *error=nil;', INSPECT + '    NSError *error=nil;', 1))
    p = subprocess.run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-unused-function',
                        '-fobjc-arc', '-I', str(ROOT), str(src), str(ROOT / 'blackbox.c'), str(ROOT / 'blackbox_errors.c'), '-framework', 'Foundation', '-framework', 'Metal',
                        '-o', str(exe)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-4000:]
    return exe


def launch(exe, folder, *extra):
    p = subprocess.run([str(exe), str(folder), *extra], capture_output=True, text=True, timeout=60)
    if p.returncode == 3:
        sys.exit(3)
    assert p.returncode == 0, p.stdout + p.stderr
    line = [l for l in p.stdout.splitlines() if l.startswith('history launch:')][0]
    fields = dict(kv.split('=') for kv in line.split(': ', 1)[1].split())
    return int(fields['frame_builds']), int(fields['replayed']), fields['discarded']


def inspect(exe, folder, mode='inspect', expected_skipped=None):
    p = subprocess.run([str(exe), str(folder), mode], capture_output=True, text=True, timeout=60)
    if p.returncode == 3:
        sys.exit(3)
    assert p.returncode == 0, p.stdout + p.stderr
    if expected_skipped is not None:
        assert f' skipped={expected_skipped}\n' in p.stdout, p.stdout
    line = next(l for l in p.stdout.splitlines() if l.startswith('inspect:'))
    return dict(kv.split('=') for kv in line.split(': ', 1)[1].split())


def checked_record(recipe, stamp):
    h = stamp
    for b in recipe:
        h = ((h ^ b) * 1099511628211) & ((1 << 64) - 1)
    return recipe + struct.pack('<Q', h)


def review_fixtures(source, td, folder, good):
    assert hashlib.sha256(OLD_HISTORY.lstrip('\n').encode()).hexdigest() == OLD_SHA256
    current = (ROOT / 'pipeline_history.h').read_text()
    prefix, suffix = current.split('static int history_known', 1)
    assert prefix in source
    baseline = source.replace(prefix, OLD_HISTORY.lstrip('\n'))
    for old, code in ((True, baseline), (False, source)):
        exe = build(code, td, 'before' if old else 'after')
        cache = folder / 'pipelinecache.bin'
        cache.unlink(missing_ok=True)
        launch(exe, folder)
        seed = cache.read_bytes()
        header = 16 if old else HEADER
        stamp = struct.unpack_from('<Q', seed, 8)[0]
        recipe = bytearray(seed[header:header + 816])
        # First attribute's bufferIndex: 16 shader bytes + 13 uint32 state fields + 8.
        struct.pack_into('<I', recipe, 76, 0xffffffff)
        invalid = seed[:header] + checked_record(recipe, stamp)
        cache.write_bytes(invalid)
        result = inspect(exe, folder, expected_skipped=None if old else 1)
        assert int(result['accepted']) == (1 if old else 0), result
        if not old:
            assert result['discarded'] == 'none', result
            # Descriptor scalars, the first attribute/layout and reserved word reject invalid values.
            invalid_fields = list(range(16, 68, 4)) + [68, 72, 76, 440, 444, 448, 812]
            for offset in invalid_fields:
                bad = bytearray(seed[header:header + 816])
                struct.pack_into('<I', bad, offset, 0xffffffff)
                pair_header = bytearray(seed[:header])
                struct.pack_into('<Q', pair_header, 16, 2)
                cache.write_bytes(pair_header + checked_record(bad, stamp) + seed[header:])
                field_result = inspect(exe, folder, expected_skipped=1)
                assert field_result == {'accepted': '1', 'discarded': 'none'}, (offset, field_result)
            print(f'field validation: {len(invalid_fields)} malformed fields skipped; valid neighbour preserved')
        record = seed[header:header + RECORD]
        duplicate_header = bytearray(seed[:header])
        if not old:
            struct.pack_into('<Q', duplicate_header, 16, 10000)
        cache.write_bytes(duplicate_header + record * 10000)
        result = inspect(exe, folder)
        assert int(result['accepted']) == (1 if old else 0), result
        if not old:
            assert result['discarded'] == 'size', result
        # Keep the committed count at two, but remove one complete record.
        trunc_header = bytearray(seed[:header])
        if not old:
            struct.pack_into('<Q', trunc_header, 16, 2)
        cache.write_bytes(trunc_header + record)
        result = inspect(exe, folder)
        assert int(result['accepted']) == (1 if old else 0), result
        if not old:
            assert result['discarded'] == 'truncated', result
        # Record append is deliberately short. Load must still see a clean empty file.
        cache.unlink()
        inspect(exe, folder, 'append')
        result = inspect(exe, folder)
        assert (result['discarded'] == 'none') == (not old), result
        if not old:
            for mode in ('append2', 'append3'):
                cache.unlink()
                inspect(exe, folder, mode)
                result = inspect(exe, folder)
                assert result == {'accepted': '0', 'discarded': 'none'}, (mode, result)
            # Old format is discarded by version stamp, without attempting migration.
            cache.write_bytes(seed[:16] + seed[HEADER:])
            assert inspect(exe, folder)['accepted'] == '0'
        cache.write_bytes(seed)
        with cache.open('r+b') as locked:
            fcntl.flock(locked, fcntl.LOCK_EX)
            child = subprocess.Popen([str(exe), str(folder), 'inspect'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                time.sleep(1)
                blocked = child.poll() is None
            finally:
                fcntl.flock(locked, fcntl.LOCK_UN)
                out, err = child.communicate(timeout=60)
            assert child.returncode == 0, out + err
            assert blocked == (not old), (old, blocked)
        # Concurrent writers near the physical limit may commit at most one more record.
        bound_header = bytearray(seed[:header])
        if not old:
            struct.pack_into('<Q', bound_header, 16, 4095)
        cache.write_bytes(bound_header + record * 4095)
        children = [subprocess.Popen([str(exe), str(folder), 'append0', str(mask)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for mask in (0, 1, 2, 4, 5, 6, 7, 8)]
        for child in children:
            out, err = child.communicate(timeout=60)
            assert child.returncode == 0, out + err
        physical = (cache.stat().st_size - header) // RECORD
        assert physical == (4103 if old else 4096), physical
        print(f'concurrent bound: physical={physical} limit=4096')
        print(f'review {"before" if old else "after"}: invalid accepted={int(old)}; duplicates accepted={int(old)}; '
              f'whole-record truncation accepted={int(old)}; lock blocked={int(blocked)}; short write clean={int(not old)}')
    cache.write_bytes(good)


def main():
    source = mw_source(ROOT / 'pipeline_cache.m').replace('#include "pipeline_cache.h"', '').replace('#include "d3d9_internal.h"', '')
    include = '#include "pipeline_history.h"\n'
    assert source.count(include) == 1
    source = source.replace(include, mw_source(ROOT / 'pipeline_history.h'))
    head, rest = source.split('static const PipelineRecipe recipes[] = {', 1)
    source = head + 'static const PipelineRecipe recipes[] = {' + FIXTURES + '\n};' + rest.split('\n};', 1)[1]
    failures = 0
    with tempfile.TemporaryDirectory(prefix='pipeline-history-test-') as td:
        td = Path(td)
        exe = build(source, td, 'normal')
        folder = td / 'user'; folder.mkdir()
        cache = folder / 'pipelinecache.bin'
        first = launch(exe, folder, 'embedded')
        size = cache.stat().st_size
        vertex, fragment = struct.unpack_from('<QQ', cache.read_bytes(), HEADER)
        second = launch(exe, folder)
        print(f'fresh: frame builds={first[0]} file={size} bytes keys={vertex}/{fragment}; second launch: '
              f'frame builds={second[0]} replayed={second[1]}')
        failures += first[0] != 1 or size != HEADER + RECORD or (vertex, fragment) != (1, 2) or second[:2] != (0, 1)
        good = cache.read_bytes()
        defects = {
            'record': good[:HEADER + 40] + bytes([good[HEADER + 40] ^ 1]) + good[HEADER + 41:],
            'stamp': good[:8] + bytes([good[8] ^ 1]) + good[9:],
            'truncated': good[:-100],
            'header': b'\0' * HEADER + good[HEADER:],
        }
        for reason, data in defects.items():
            cache.write_bytes(data)
            builds, replayed, discarded = launch(exe, folder)
            rewritten = cache.stat().st_size
            print(f'{reason} defect: discarded={discarded} frame builds={builds} replayed={replayed} file={rewritten} bytes')
            failures += discarded != reason or builds != 1 or replayed != 0 or rewritten != HEADER + RECORD
            cache.write_bytes(good)
        review_fixtures(source, td, folder, good)
        # Controls: no replay (old behaviour) and an ignored record check.
        controls = [('replay omitted', source.replace('    history_replay(queues,scheduled);\n', ''), good, (1, 0)),
                    ('check ignored', source.replace('if (rec.check!=history_hash(&rec.recipe,sizeof rec.recipe,history_stamp))', 'if (0)'),
                     defects['record'], (1, 1))]
        for label, broken, data, expected in controls:
            assert broken != source, label
            cache.write_bytes(data)
            builds, replayed, _ = launch(build(broken, td, 'control'), folder)
            print(f'{label} control: frame builds={builds} replayed={replayed}')
            failures += (builds, replayed) != expected
    print(f'pipeline history: failures={failures}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
