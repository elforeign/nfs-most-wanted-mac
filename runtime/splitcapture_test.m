// GPU companion: production surface_target resolves the bank's face/texture ownership.
// Persistent private cubes, partial face updates, independently sized road targets and depth.
                                                                                     
#import <Metal/Metal.h>
#import <Foundation/Foundation.h>
#include <stdio.h>
#include <string.h>

typedef struct { id<MTLTexture> tex, ms; } RenderTex;
typedef struct { uint32_t parent,level,face,usage,fmt; RenderTex *gpu; } Obj;
static struct { uint32_t backbuffer,autodepth; } dev;
static RenderTex bb_rt,ad_rt,gpu[512];
static id<MTLTexture> backbuffer_tex,autodepth_tex;
static Obj objects[512];
static Obj *obj(uint32_t h) { return h<512 ? &objects[h]:NULL; }
static RenderTex *gpu_tex(Obj *o) { return o?o->gpu:NULL; }
static int is_depth_fmt(uint32_t f) { return f==75; }
#include "splitcapture_surface.inc"

static void clear(id<MTLCommandBuffer> cb,uint32_t handle,MTLClearColor colour,id<MTLTexture> depth) {
    NSUInteger level,slice;
    id<MTLTexture> t=surface_target(handle,&level,&slice);
    MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
    rp.colorAttachments[0].texture=t;rp.colorAttachments[0].level=level;rp.colorAttachments[0].slice=slice;
    rp.colorAttachments[0].loadAction=MTLLoadActionClear;rp.colorAttachments[0].storeAction=MTLStoreActionStore;
    rp.colorAttachments[0].clearColor=colour;
    if(depth) {
        rp.depthAttachment.texture=depth;rp.depthAttachment.loadAction=MTLLoadActionClear;
        rp.depthAttachment.storeAction=MTLStoreActionStore;rp.depthAttachment.clearDepth=.25;
    }
    [[cb renderCommandEncoderWithDescriptor:rp] endEncoding];
}

int main(int argc,const char **argv) { @autoreleasepool {
    int shared=argc>1&&!strcmp(argv[1],"shared"),stale=argc>1&&!strcmp(argv[1],"stale");
    id<MTLDevice> d=MTLCreateSystemDefaultDevice();
    if(!d) { puts("GPU PENDING: no Metal device");return 2; }
    id<MTLCommandQueue> q=[d newCommandQueue];
    id<MTLTexture> cubes[2],roads[2],depths[2];
    for(unsigned p=0;p<2;p++) {
        MTLTextureDescriptor *desc=[MTLTextureDescriptor textureCubeDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm size:64 mipmapped:NO];
        desc.storageMode=MTLStorageModePrivate;desc.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        cubes[p]=p&&shared?cubes[0]:[d newTextureWithDescriptor:desc];
        unsigned parent=100+p*100;gpu[parent].tex=cubes[p];objects[parent].gpu=&gpu[parent];
        for(unsigned f=0;f<6;f++) { objects[parent+f+1].parent=parent;objects[parent+f+1].face=f; }
        desc=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:32 height:17+p mipmapped:NO];
        desc.storageMode=MTLStorageModePrivate;desc.usage=MTLTextureUsageRenderTarget|MTLTextureUsageShaderRead;
        roads[p]=[d newTextureWithDescriptor:desc];
        parent=300+p*100;gpu[parent].tex=roads[p];objects[parent].gpu=&gpu[parent];objects[parent+1].parent=parent;
        desc.pixelFormat=MTLPixelFormatDepth32Float;depths[p]=[d newTextureWithDescriptor:desc];
    }
    // Known black first makes missing initialization deterministic.
    id<MTLCommandBuffer> cb=[q commandBuffer];
    for(unsigned p=0;p<2;p++)for(unsigned f=0;f<6;f++)clear(cb,101+p*100+f,MTLClearColorMake(0,0,0,1),nil);
    [cb commit];[cb waitUntilCompleted];
    unsigned expected[2][6]={{0}},checks=0,bad=0;
    for(unsigned frame=0;frame<5;frame++) {
        cb=[q commandBuffer];
        for(unsigned p=0;p<2;p++) {
            for(unsigned f=0;f<6;f++)if((!frame&&!stale)||f==frame%2) {
                unsigned value=32+p*96+frame*8+f;expected[p][f]=value;
                clear(cb,101+p*100+f,MTLClearColorMake(p?0:value/255.,0,p?value/255.:0,1),nil);
            }
            if(!frame&&stale)for(unsigned f=1;f<6;f++)expected[p][f]=32+p*96+f;
            clear(cb,301+p*100,MTLClearColorMake(p?0:1,0,p?1:0,1),depths[p]);
        }
        id<MTLBuffer> read=[d newBufferWithLength:2*6*64*64*4+2*32*18*4 options:MTLResourceStorageModeShared];
        id<MTLBlitCommandEncoder> blit=[cb blitCommandEncoder];
        for(unsigned p=0;p<2;p++)for(unsigned f=0;f<6;f++) {
            NSUInteger level,slice;id<MTLTexture> t=surface_target(101+p*100+f,&level,&slice);
            [blit copyFromTexture:t sourceSlice:slice sourceLevel:level sourceOrigin:MTLOriginMake(0,0,0)
                sourceSize:MTLSizeMake(64,64,1) toBuffer:read destinationOffset:(p*6+f)*64*64*4
                destinationBytesPerRow:64*4 destinationBytesPerImage:64*64*4];
        }
        for(unsigned p=0;p<2;p++) {
            NSUInteger level,slice;id<MTLTexture> t=surface_target(301+p*100,&level,&slice);
            [blit copyFromTexture:t sourceSlice:slice sourceLevel:level sourceOrigin:MTLOriginMake(0,0,0)
                sourceSize:MTLSizeMake(32,17+p,1) toBuffer:read destinationOffset:2*6*64*64*4+p*32*18*4
                destinationBytesPerRow:32*4 destinationBytesPerImage:32*(17+p)*4];
        }
        [blit endEncoding];[cb commit];[cb waitUntilCompleted];
        if(cb.error) { fprintf(stderr,"Metal error: %s\n",cb.error.localizedDescription.UTF8String);return 2; }
        const uint8_t *bytes=read.contents;
        for(unsigned p=0;p<2;p++)for(unsigned f=0;f<6;f++)for(unsigned px=0;px<64*64;px++) {
            const uint8_t *b=bytes+((p*6+f)*64*64+px)*4;
            ++checks;bad+=b[p?0:2]!=expected[p][f]||b[p?2:0]!=0||b[1]!=0||b[3]!=255;
        }
        for(unsigned p=0;p<2;p++)for(unsigned px=0;px<32*(17+p);px++) {
            const uint8_t *b=bytes+2*6*64*64*4+p*32*18*4+px*4;
            ++checks;bad+=b[p?0:2]!=255||b[p?2:0]!=0||b[1]!=0||b[3]!=255;
        }
    }
    printf("splitcapture GPU: %u pixels, %u bad (%s)\n",checks,bad,shared?"shared":stale?"stale":"positive");
    return bad?1:0;
} }
