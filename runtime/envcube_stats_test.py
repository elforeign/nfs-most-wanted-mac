#!/usr/bin/env python3
"""The diagnostic cube readback (carbon_envcube_stats) must not hitch the frame.

Cause (measured in a 69-minute full-diagnostics session): every 120 frames it allocated a 50 MB buffer and scanned
6.3 million texels inside the Metal completion handler, one frame of about 44 ms (91 % of the race frames over
16.7 ms sat at frame%120==3). Fixed: the buffer is allocated once, a new readback is skipped while one is in flight,
and the scan and file write run on a private queue.
Source check (this file): production passes, the original function (SHA-256 pinned below) fails 3 of 3 rules.
Run check (CC_RUN_OLD=<diag dir> CC_RUN_NEW=<diag dir>, optional, quick race of the same length): race frames over
33 ms at frame%120==3, old RUN_OLD -> new RUN_NEW (cubestats.log rows must stay above zero).
Measured (two Quick Races with NFS_CARBON_ENVCUBE=1, 190 s, full diagnostics): old 11621 race frames, 100 over 33 ms,
97 of them at frame%120==3; new 9740 frames, 14 over 33 ms, 0 at frame%120==3; cubestats.log rows 294 -> 228.
"""
import hashlib
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / 'recomp' / 'private_runtime' / 'carbon_envcube.h'

ORIGINAL = r'''static void carbon_envcube_stats(Obj *t) {
    RenderTex *g=gpu_tex(t);
    id<MTLTexture> cube=g ? g->tex : nil;
    int half=cube && cube.pixelFormat==MTLPixelFormatRGBA16Float;
    if (!cube || (!half && cube.pixelFormat!=MTLPixelFormatBGRA8Unorm)) return;
    NSUInteger edge=cube.width, pixel=half?8:4, row=edge*pixel, face=row*edge;
    id<MTLBuffer> data=[cube.device newBufferWithLength:face*6 options:MTLResourceStorageModeShared];
    if (!data) return;
    end_pass(); ensure_cmd();
    id<MTLBlitCommandEncoder> b=[cmd blitCommandEncoder];
    for (NSUInteger f=0;f<6;++f)
        [b copyFromTexture:cube sourceSlice:f sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0)
            sourceSize:MTLSizeMake(edge,edge,1) toBuffer:data destinationOffset:f*face
            destinationBytesPerRow:row destinationBytesPerImage:face];
    [b endEncoding];
    unsigned long long frame=dev.frames;
    NSString *path=[NSString stringWithFormat:@"%s/cubestats.log",diag_dir];
    [cmd addCompletedHandler:^(id<MTLCommandBuffer> done) {
        const uint8_t *p=data.contents;
        unsigned n50=0,n90=0,n1=0,over=0,n4=0,n25=0; float peak=0;
        for (NSUInteger i=0;i<edge*edge*6;++i) {
            float m=0;
            for (unsigned k=0;k<3;++k) {
                uint16_t h; float v;
                if (half) { memcpy(&h,p+i*8+k*2,2); v=carbon_envcube_half(h); } else v=p[i*4+k]/255.f;
                if (v>m) m=v;
            }
            if (m>peak) peak=m;
            n50+=m>=.5f; n90+=m>=.9f; n1+=m>=1; over+=m>1; n4+=m>=4; n25+=m>=25;
        }
        FILE *f=fopen(path.UTF8String,"a"); if (!f) return;
        fprintf(f,"frame=%llu status=%lu format=%s edge=%lu peak=%.4f ge0.5=%u ge0.9=%u ge1=%u gt1=%u ge4=%u ge25=%u\n",
            frame,(unsigned long)done.status,half?"rgba16f":"bgra8",(unsigned long)edge,peak,n50,n90,n1,over,n4,n25);
        fclose(f);
    }];
}
'''
ORIGINAL_SHA = '6751396578bfcc60c21b02b1862a65238674e7779e2c23fa85ad6507430f3469'


def stats_function(source):
    m = re.search(r'^static void carbon_envcube_stats\(Obj \*t\) \{.*?^\}\n', source, re.M | re.S)
    assert m, 'carbon_envcube_stats not found'
    return m.group(0)


def rules(body):
    """Names of the rules the function breaks."""
    broken = []
    handler = body.find('addCompletedHandler:')
    scan = body.find('carbon_envcube_half(', handler)
    queued = body.find('dispatch_async(', handler)
    alloc = body.find('newBufferWithLength')
    if not (0 <= queued < scan): broken.append('scan not on a background queue')
    if not re.search(r'if \(!data[^)]*\) *data=\[[^\n]*newBufferWithLength', body) or 'static id<MTLBuffer> data' not in body:
        broken.append('buffer allocated every call')
    if not (0 <= body.find('if (busy) return;') < alloc): broken.append('no in-flight skip')
    return broken


def frames_over(path, over_ms=33):
    """(race frames, over over_ms, of those at frame%120==3) from frame_times.tsv: ns, frame, flow (6 = race)."""
    rows = [tuple(map(int, l.split('\t'))) for l in Path(path).read_text().splitlines() if l.count('\t') == 2]
    total = hit = periodic = 0
    for (t0, _, _), (t1, f1, flow) in zip(rows, rows[1:]):
        d = (t1 - t0) / 1e6
        if flow != 6 or d > 1000: continue
        total += 1
        if d > over_ms:
            hit += 1; periodic += f1 % 120 == 3
    return total, hit, periodic


if __name__ == '__main__':
    assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == ORIGINAL_SHA, 'pinned original changed'
    original = rules(ORIGINAL)
    assert len(original) == 3, original
    now = rules(stats_function(HEADER.read_text()))
    assert not now, now
    print('envcube stats: production 0 broken rules, original', len(original))
    old, new = os.environ.get('CC_RUN_OLD'), os.environ.get('CC_RUN_NEW')
    if old and new:
        a, b = frames_over(Path(old) / 'frame_times.tsv'), frames_over(Path(new) / 'frame_times.tsv')
        print('race frames, over 33 ms, at %120==3: old', a, 'new', b)
        assert a[2] > 10 * max(b[2], 1) and b[2] <= 2, (a, b)
        assert sum(1 for _ in open(Path(new) / 'cubestats.log')) > 0
