#!/usr/bin/env python3
"""




"""
from pathlib import Path
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
source = (RT/'render_present.m').read_text()
present = source[source.index('static void present_show(void) {'):].split('\n}', 1)[0]+'\n}\n'
PRE = r'''
#import <Cocoa/Cocoa.h>
#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
static unsigned checks, failures, clips, quads, commits, shows, completions;
#define CHECK(x) do { ++checks; if(!(x)) { ++failures; if(failures<9) fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); } }while(0)
@interface Texture : NSObject
@property NSUInteger width, height;
@property MTLPixelFormat pixelFormat;
@end
@implementation Texture
@end
static id<MTLTexture> expected_src;
static id<MTLCommandBuffer> expected_cb;
static float expected_headroom;
@interface Drawable : NSObject
@property id<MTLTexture> texture;
- (void)addPresentedHandler:(MTLDrawablePresentedHandler)handler;
@end
@implementation Drawable
- (void)addPresentedHandler:(MTLDrawablePresentedHandler)handler { (void)handler; ++completions; }
@end
static Drawable *drawable_now;
@interface FakeLayer : NSObject
@property MTLPixelFormat pixelFormat;
- (id<CAMetalDrawable>)nextDrawable;
@end
@implementation FakeLayer
- (id<CAMetalDrawable>)nextDrawable { return (id)drawable_now; }
@end
@interface Buffer : NSObject
- (void)presentDrawable:(id<MTLDrawable>)drawable;
- (void)presentDrawable:(id<MTLDrawable>)drawable afterMinimumDuration:(CFTimeInterval)duration;
- (void)commit;
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler;
@end
static double wanted_duration;
@implementation Buffer
- (void)presentDrawable:(id<MTLDrawable>)d { CHECK(d==(id)drawable_now); CHECK(!wanted_duration); ++shows; }
- (void)presentDrawable:(id<MTLDrawable>)d afterMinimumDuration:(CFTimeInterval)t {
 CHECK(d==(id)drawable_now); CHECK(t==wanted_duration); ++shows;
}
- (void)commit { CHECK(clips==1); CHECK(shows==1); ++commits; }
- (void)addCompletedHandler:(MTLCommandBufferHandler)handler { (void)handler; }
@end
@interface Queue : NSObject
- (id<MTLCommandBuffer>)commandBuffer;
@end
@implementation Queue
- (id<MTLCommandBuffer>)commandBuffer { return expected_cb; }
@end
#define CAMetalLayer FakeLayer
static FakeLayer *layer;
static Queue *queue;
static id<MTLTexture> hdr_present_tex, present_tex;
static id<MTLCommandBuffer> cmd;
static dispatch_queue_t present_q;
static int hdr_output_on, diag_on, hdr_output_requested, presenting;
static uint64_t hdr_nil_drawables;
typedef struct { double duration_ms; } RenderPresentation;
static float render_hdr_headroom(void) { return expected_headroom; }
static void render_presentation_stats(RenderPresentation *p) { p->duration_ms=wanted_duration*1000; }
static void (^after_commit)(void), (^on_queue)(void);
static void rt_after_commit(id<MTLCommandBuffer> cb, void (^b)(void)) { CHECK(cb==cmd); after_commit=[b copy]; }
static void fake_async(dispatch_queue_t q, void (^b)(void)) { CHECK(q==present_q); on_queue=[b copy]; }
#define dispatch_async fake_async
static void pipeline_frame_scope(int value) { (void)value; }
static void hdr_output_diag(const char *label, MTLPixelFormat format) { (void)label; (void)format; }
static void hdr_output_error(const char *label, NSError *error) { (void)label; (void)error; }
static void presented(id<MTLDrawable> drawable) { (void)drawable; }
static void quad_into(id<MTLCommandBuffer> cb,id<MTLTexture> src,MTLRegion r,id<MTLTexture> dst,int a,int b,MTLRegion out,int c) {
 (void)a;(void)b;(void)c; CHECK(cb==expected_cb); CHECK(src==expected_src);
 CHECK(r.size.width==expected_src.width && r.size.height==expected_src.height);
 CHECK(dst==drawable_now.texture); CHECK(out.size.width==dst.width && out.size.height==dst.height); ++quads;
}
static void cliprec_present(id<MTLCommandBuffer> cb,id<MTLTexture> src,id<CAMetalDrawable> d,float headroom) {
 ++clips; CHECK(cb==expected_cb); CHECK(src==expected_src); CHECK(d==(id)drawable_now);
 CHECK(src.width==expected_src.width && src.height==expected_src.height);
 CHECK(src.pixelFormat==expected_src.pixelFormat); CHECK(headroom==expected_headroom);
 CHECK(quads==1 && !commits && !shows);
}
static void overlay_present(id<MTLCommandBuffer> cb) { CHECK(cb==expected_cb); }
'''
POST = r'''
static id<MTLTexture> texture(NSUInteger w,NSUInteger h,MTLPixelFormat f) {
 Texture *t=[Texture new]; t.width=w;t.height=h;t.pixelFormat=f;return (id)t;
}
int main(void) { @autoreleasepool {
 layer=[FakeLayer new];queue=[Queue new];cmd=(id)[Buffer new];present_q=dispatch_get_main_queue();
 unsigned sizes[][2]={{1280,720},{1920,1080},{3840,2160}};
 unsigned cases=0;
 for(unsigned size=0;size<3;size++) for(int hdr=0;hdr<3;hdr++) for(int paced=0;paced<2;paced++) for(int missing=0;missing<2;missing++) {
  NSUInteger w=sizes[size][0],h=sizes[size][1];
  present_tex=texture(w,h,MTLPixelFormatBGRA8Unorm);
  hdr_present_tex=texture(w,h,MTLPixelFormatRGBA16Float);
  hdr_output_on=hdr>0;expected_headroom=hdr==2?4.0f:1.0f;
  expected_src=hdr_output_on?hdr_present_tex:present_tex;
  expected_cb=(id)[Buffer new];wanted_duration=paced?0.02:0;
  drawable_now=missing?nil:[Drawable new];
  drawable_now.texture=texture(2560,1440,hdr_output_on?MTLPixelFormatRGBA16Float:MTLPixelFormatBGRA8Unorm);
  clips=quads=commits=shows=completions=0;after_commit=nil;on_queue=nil;presenting=1;
  present_show();CHECK(after_commit!=nil);CHECK(!clips && !commits && !on_queue);
  after_commit();CHECK(on_queue!=nil);CHECK(!clips && !commits);
  on_queue();CHECK(clips==(missing?0:1));CHECK(quads==(missing?0:1));
  CHECK(commits==(missing?0:1));CHECK(shows==(missing?0:1));CHECK(completions==(missing?0:1));CHECK(!presenting);
  ++cases;
 }
 printf("%u cases, %u checks, %u failures\n",cases,checks,failures);return failures?1:0;
} }
'''

def main():
    call = 'cliprec_present(cb, src, d, clip_headroom);'
    assert present.count(call)==1
    variants = [('production',present),
                ('dropped-recorder',present.replace(call,'(void)clip_headroom;')),
                ('SDR-only',present.replace('hdr_output_on ? hdr_present_tex : present_tex','present_tex')),
                ('headroom-one',present.replace(call,'cliprec_present(cb, src, d, 1);'))]
    with tempfile.TemporaryDirectory(prefix='clip-present-') as d:
        f,exe=Path(d)/'t.m',Path(d)/'t'
        for label,body in variants:
            assert label=='production' or body!=present,label
            f.write_text(PRE+body+POST)
            p=subprocess.run(['clang','-O1','-Wall','-Wextra','-Werror','-Wno-unused-variable','-Wno-unused-function','-fobjc-arc','-fblocks',str(f),'-framework','Cocoa','-framework','Metal','-framework','QuartzCore','-o',str(exe)],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-3500:]
            p=subprocess.run([str(exe)],capture_output=True,text=True)
            summary=p.stdout.strip()
            assert re.fullmatch(r'36 cases, \d+ checks, \d+ failures',summary),(label,p.stdout,p.stderr[-2000:])
            print(label+': '+summary)
            assert p.returncode==(0 if label=='production' else 1),(label,p.stdout,p.stderr[-2000:])

if __name__=='__main__':
    main()
