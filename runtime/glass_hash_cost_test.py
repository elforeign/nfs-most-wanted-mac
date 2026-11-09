"""Glass diagnostics: 1,000 draws, full hashes 1,000 -> 7, stale count 500 -> 500.

The original SHA-256-pinned function fails the <=7-hash budget (exit 1).
Middle-only mutations, repeated stale draws, reversions and edge rebases use
identical input bytes; the stale counter counts draws, not mutations. Diag Off
performs zero full hashes. Foundation mocks avoid Metal and private assets.
Autodiscovered by runtime/offline_tests.sh.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
BASELINE = 'static GlassUV1 *glass_uv1_for(Obj *vb) {\n    if (!vb || !vb->size || vb->size % 36) return nil;\n    const uint8_t *b = G + vb->mem[0];\n    uint32_t edge = vb->size < 64 ? vb->size : 64;\n    uint64_t check = fnv(b + vb->size - edge, edge, fnv(b, edge, vb->size));\n    GlassUV1 *u = (__bridge GlassUV1 *)map_get(&glass_uv1_cache, vb->guest);\n    uint64_t full = diag_on ? fnv(b, vb->size, 1469598103934665603ull) : 0;\n    if (u && u->check == check) { if (diag_on && u->full != full) ++ad1.glass_stale_mid; return u; }\n    u = [GlassUV1 new];\n    u->check = check; u->full = full; u->n = vb->size / 36;\n    const float *y = NULL;\n    u->row = mat_glass_uv1(b, vb->size, &y, &u->matched);\n    u->buf = [mtl newBufferWithLength:u->n * sizeof(float) options:MTLResourceStorageModeShared];\n    float *f = u->buf.contents;\n    for (uint32_t i = 0; i < u->n; ++i) f[i] = y ? y[i] : GLASS_UV1_NONE;\n    map_put(&glass_uv1_cache, vb->guest, u);\n    return u;\n}'
BASELINE_SHA = 'eae98edcbb4c95404b4c48083fedd506b54b4060fc18953d96ddd23bff2cdaee'


def function(source, name):
    match = re.search(r'^static GlassUV1 \*'+name+r'\([^;]*?\)\s*\{', source, re.M)
    assert match
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


PRE = r'''
#import <Foundation/Foundation.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
@protocol MTLBuffer
@property(readonly) void *contents;
@end
@interface Buffer:NSObject<MTLBuffer> { NSMutableData *data; }
-(id)initWithLength:(NSUInteger)n;
@end
@implementation Buffer
-(id)initWithLength:(NSUInteger)n {self=[super init];if(self)data=[NSMutableData dataWithLength:n];return self;}
-(void *)contents{return data.mutableBytes;}
@end
@interface Device:NSObject
-(id<MTLBuffer>)newBufferWithLength:(NSUInteger)n options:(unsigned)o;
@end
@implementation Device
-(id<MTLBuffer>)newBufferWithLength:(NSUInteger)n options:(unsigned)o{(void)o;return [[Buffer alloc]initWithLength:n];}
@end
static Device *mtl;
enum {MTLResourceStorageModeShared=0,GLASS_UV1_NONE=-2};
typedef struct {uint32_t guest,size,mem[1];} Obj;
static uint8_t G[512];
static int diag_on=1,hashes,rows;
static struct {unsigned long long glass_stale_mid;} ad1;
static uint64_t fnv(const void *ptr,size_t n,uint64_t h){
    if(h==1469598103934665603ull)++hashes;
    const uint8_t *p=ptr;while(n--)h=(h^*p++)*1099511628211ull;return h;
}
'''
POST = r'''
typedef struct {GlassUV1 *row;} Map;
static Map glass_uv1_cache;
static void *map_get(Map *m,uint64_t key){(void)key;return (__bridge void *)m->row;}
static void map_put(Map *m,uint64_t key,id row){(void)key;m->row=row;}
static int mat_glass_uv1(const uint8_t *b,uint32_t n,const float **y,uint32_t *matched){
    (void)b;(void)n;*y=NULL;*matched=0;++rows;return -1;
}
'''
MAIN = r'''
int main(int argc,char **argv){@autoreleasepool{
    (void)argv;if(argc>1)diag_on=0;
    mtl=[Device new];Obj vb={1,288,{0}};
    for(int i=0;i<1000;i++){
        if(i==200)G[128]=1;
        if(i==400)G[128]=0;
        if(i==500)G[0]=1;
        if(i==600)G[180]=1;
        if(i==800)G[0]=0;
        if(i==900)G[180]=0;
        GlassUV1 *u=glass_uv1_for(&vb);
        if(!u || u->n!=8 || u->row!=-1 || u->matched)return 2;
        float *f=u->buf.contents;
        for(int v=0;v<8;v++)if(f[v]!=GLASS_UV1_NONE)return 2;
        unsigned want=i<200?0:i<400?i-199:i<600?200:i<800?i-399:i<900?400:i-499;
        if(ad1.glass_stale_mid!=(diag_on?want:0))return 2;
    }
    if(glass_uv1_for(NULL))return 2;
    Obj bad={2,37,{0}};if(glass_uv1_for(&bad))return 2;
    printf("hashes=%d stale=%llu rows=%d\n",hashes,ad1.glass_stale_mid,rows);
    return hashes>(diag_on?7:0);
}}
'''


def main():
    assert hashlib.sha256(BASELINE.encode()).hexdigest()==BASELINE_SHA
    render=(RT/'render.m').read_text()
    interface=render[render.index('@interface GlassUV1'):render.index('static struct { __unsafe_unretained',render.index('@interface GlassUV1'))]
    with tempfile.TemporaryDirectory(prefix='glass-hash-') as td:
        td=Path(td)
        for label,source in (('old',BASELINE),('current',function(render,'glass_uv1_for'))):
            path=td/(label+'.m');exe=td/label
            path.write_text(PRE+interface+POST+source+MAIN)
            p=subprocess.run(['clang','-fobjc-arc','-Wall','-Wextra','-Werror',str(path),'-framework','Foundation','-o',str(exe)],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2500:]
            p=subprocess.run([str(exe)],capture_output=True,text=True)
            expected=f'hashes={1000 if label=="old" else 7} stale=500 rows=3'
            assert p.returncode==(1 if label=='old' else 0),(label,p.returncode,p.stdout,p.stderr)
            assert p.stdout.strip()==expected,p.stdout
            print(label+': '+p.stdout.strip())
            p=subprocess.run([str(exe),'off'],capture_output=True,text=True)
            assert p.returncode==0 and p.stdout.strip()=='hashes=0 stale=0 rows=3',(p.stdout,p.stderr)
            print(label+' diag Off: '+p.stdout.strip())


if __name__=='__main__':
    main()
