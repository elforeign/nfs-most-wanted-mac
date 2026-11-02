"""Execute the production sequence-capture block without a GPU.
Six requested bursts export 384 frames, at most 64 retained buffers; the
original missing-reset control exports only 64 and fails. Near AB exports zero
legacy frames; removing its guard duplicates 384 frames and fails. HDR source selection
and single-shot export stay in the production block, with recording sinks.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <assert.h>
static unsigned live, peak, exported, copies, hdr_calls, singles;
@interface Buffer : NSObject @end
@implementation Buffer
- (instancetype)init { if ((self = [super init])) { ++live; if (live > peak) peak = live; } return self; }
- (void)dealloc { --live; }
@end
@interface Sink : NSObject
@property NSUInteger width, height;
- (id)newBufferWithLength:(NSUInteger)n options:(MTLResourceOptions)o;
- (void)copyFromTexture:(id)t sourceSlice:(NSUInteger)s sourceLevel:(NSUInteger)l
 sourceOrigin:(MTLOrigin)origin sourceSize:(MTLSize)size toBuffer:(id)b
 destinationOffset:(NSUInteger)offset destinationBytesPerRow:(NSUInteger)row
 destinationBytesPerImage:(NSUInteger)image;
- (void)endEncoding;
@end
static Sink *mtl, *backbuffer_tex, *capture_tex, *encoder;
@implementation Sink
- (id)newBufferWithLength:(NSUInteger)n options:(MTLResourceOptions)o {
 assert(n == 16*8*4 && o == MTLResourceStorageModeShared); return [Buffer new];
}
- (void)copyFromTexture:(id)t sourceSlice:(NSUInteger)s sourceLevel:(NSUInteger)l
 sourceOrigin:(MTLOrigin)origin sourceSize:(MTLSize)size toBuffer:(id)b
 destinationOffset:(NSUInteger)offset destinationBytesPerRow:(NSUInteger)row
 destinationBytesPerImage:(NSUInteger)image {
 assert(t == capture_tex && b && !s && !l && !offset);
 assert(!origin.x && !origin.y && !origin.z && size.width == 16 && size.height == 8 && size.depth == 1);
 assert(row == 64 && image == 512); ++copies;
}
- (void)endEncoding {}
@end
static struct { uint64_t frames; } dev;
static int diag_on = 1, near_ab;
static int shadow_near_ab_enabled(void) {return near_ab;}
static const char *diag_dir = "capture";
static id cmd;
// Sign diagnostic hooks inside the extracted block stay off in this harness.
static int sign_diag_on = 0, show = 0;
static int sign_diag_active(void) { return 0; }
static void sign_diag_present(id c, id t) { (void)c; (void)t; }
static id hdr_shot_source(id c, int s) { (void)c; (void)s; return nil; }
static id<MTLBuffer> shot;
static char shot_path[32] = "single.png";
static id<MTLTexture> hdr_capture_source(id c, id t) {
 (void)c; assert(t == backbuffer_tex); ++hdr_calls; return (id)capture_tex;
}
static id<MTLBlitCommandEncoder> gpt_blit(id c, const char *label) {
 (void)c; assert(!strcmp(label,"blit diag shot")); return (id)encoder;
}
static int diag_frame_wanted(uint64_t frame, const char *key) {
 assert(!strcmp(key,"BOOT_DIAG_SEQ"));
 return frame >= 100 && frame <= 600 && frame % 100 == 0;
}
static void png_after(id c, id b, const char *path, uint32_t w, uint32_t h, int single) {
 (void)c; assert(b && w == 16 && h == 8);
 if (single) { assert(!strcmp(path,"single.png")); ++singles; return; }
 unsigned long long frame = 0; assert(sscanf(path,"capture/seq_%llu.png",&frame) == 1);
 assert(frame == 100 + (exported / 64)*100 + exported % 64); ++exported;
}
static void present(void) {
/* PRODUCTION */
}
int main(int argc,char **argv) { @autoreleasepool {
 (void)argv;near_ab=argc>1;
 setenv("BOOT_DIAG_SEQ","100,200,300,400,500,600:99",1);
 mtl=[Sink new]; backbuffer_tex=[Sink new]; capture_tex=[Sink new]; encoder=[Sink new];
 backbuffer_tex.width=16; backbuffer_tex.height=8;
 for (dev.frames=1; dev.frames<=700; ++dev.frames) {
  @autoreleasepool { present(); }
 }
 // Exercise the separate one-shot path after all bursts, without counting its buffer.
 shot=(id)[NSObject new]; present(); shot=nil;
 unsigned expected=near_ab?0:384, expected_peak=near_ab?0:64;
 printf("capture: exported=%u/%u peak=%u/%u live=%u HDR=%u copies=%u single=%u\n",
        exported,expected,peak,expected_peak,live,hdr_calls,copies,singles);
 return exported != expected || peak != expected_peak || live || copies != expected || hdr_calls != expected || singles != 1;
} }
'''


def main():
    source = (ROOT / 'runtime/render_present.m').read_text()
    block = source.split('    static id<MTLBuffer> seq_buf[64];', 1)[1]
    block = '    static id<MTLBuffer> seq_buf[64];' + block.split('    uint64_t serial = frame_serial;', 1)[0]
    reset = 'seq_have = 0; seq_start = 0;'
    assert block.count(reset) == 1, 'bounded-burst reset missing'
    with tempfile.TemporaryDirectory() as d:
        for name, code, args, expected in [
                ('production',block,[],0),
                ('near-ab',block,['--near'],0),
                ('missing-reset',block.replace(reset,''),[],1),
                ('duplicate-near',block.replace(' && !shadow_near_ab_enabled()',''),['--near'],1)]:
            src, binary = Path(d)/f'{name}.m', Path(d)/name
            src.write_text(HARNESS.replace('/* PRODUCTION */', code))
            subprocess.run(['clang', '-fobjc-arc', '-Wall', '-Wextra', '-Werror',
                            '-Wno-unused-parameter', '-Wno-unused-function', '-framework', 'Foundation',
                            str(src), '-o', str(binary)], check=True, capture_output=True)
            run = subprocess.run([str(binary)]+args, capture_output=True, text=True)
            print(name + ': ' + run.stdout.strip())
            assert run.returncode == expected, run.stderr
    print('SHADOW CAPTURE PASS (original first-burst-only control rejected)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
