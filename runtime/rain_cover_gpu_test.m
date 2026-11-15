/* Compile the production ripple + shelter patch and render the roof fixture.
 * Fixed 0/24 channel failures; inverted compare 15/24 FAIL. The paired Python
 * driver runs both. Film dry/wet: fixed 0/48; film omitted 18/48 FAIL.
 * No game process, installed assets or window.
 */
#define main rain_cover_cpu_main
#include "rain_cover_test.m"
#undef main

static char *read_source(const char *path) {
    FILE *f=fopen(path,"rb");if(!f)return NULL;
    fseek(f,0,SEEK_END);long n=ftell(f);rewind(f);
    char *s=calloc((size_t)n+1,1);
    if(fread(s,1,(size_t)n,f)!=(size_t)n){free(s);s=NULL;}fclose(f);return s;
}
static int emit(const char *input,const char *output,int stage,int film) {
    char *s=read_source(input);if(!s)return 2;
    SmResult r={.msl=s,.shader_type=stage};int id=1;
    char *out=rain_cover_shader(NULL,0,&r,&id,film);free(s);
    if(!out){fprintf(stderr,"shader patch failed: %s\n",input);return 2;}
    FILE *f=fopen(output,"wb");if(!f){free(out);return 2;}fputs(out,f);fclose(f);free(out);return 0;
}
/* The upload is lazy and immutable: only a published generation can replace
 * the texture, and earlier command buffers retain their old resource. */
static int upload_lifecycle(void) {
    id<MTLTexture> first=rain_cover_texture();
    uint64_t generation=map.generation;
    if (!first || rain_cover_texture()!=first) {fprintf(stderr,"upload check line %d\n",__LINE__);return 1;}
    /* Make the pending build outlast a slice even on a fast host. */
    unsigned vertices=0;quad(-64,-64,64,64,10,&vertices);
    M8(TEXTURE+0x55)=1;
    MF32(CAMERA+0x40)=45;++dev.frames;prepare(RAIN);
    if (!build.active || map.generation!=generation || rain_cover_texture()!=first) {fprintf(stderr,"upload check line %d\n",__LINE__);return 1;}
    settle();
    id<MTLTexture> second=rain_cover_texture();
    if (!second || second==first || gpu_generation!=map.generation || rain_cover_texture()!=second) {fprintf(stderr,"upload check line %d\n",__LINE__);return 1;}
    float height;
    [first getBytes:&height bytesPerRow:sizeof(float) fromRegion:MTLRegionMake2D(268,268,1,1) mipmapLevel:0];
    if (height!=10) {fprintf(stderr,"upload check line %d\n",__LINE__);return 1;}
    /* Invalid front maps expose neither a partial texture nor its constants. */
    M32(0x982b9c)=0x800; // fixture roof's 0x80000 variant disappears
    ++dev.frames;prepare(RAIN);
    float constants[4];rain_cover_constants(constants);
    if (rain_cover_sample(&map,3,3,5) || (map.valid && map.triangles)) {
        fprintf(stderr,"detail change kept old roof\n");return 1;
    }
    /* An empty replacement can complete in the invalidating frame. A lost
     * camera must expose neither the old texture nor a pending map. */
    M32(VIEW+0x40)=0;rain_cover_constants(constants);
    if (map.valid || rain_cover_texture() || constants[3]) return 1;
    M32(VIEW+0x40)=CAMERA;
    M8(TEXTURE+0x55)=0;scene(1);
    M32(0x982b9c)=0;MF32(CAMERA+0x40)=0;refresh();
    printf("GPU upload lifecycle: PASS (stable front, one upload/swap, retained old texture, invalid fails open)\n");
    return 0;
}
static int gpu(const char *vertex,const char *pixel,int film) {
    mtl=MTLCreateSystemDefaultDevice();
    if(!mtl){puts("SKIP: no Metal device");return 3;}
    NSError *error=nil;
    NSString *vs=[NSString stringWithContentsOfFile:@(vertex) encoding:NSUTF8StringEncoding error:&error];
    NSString *ps=[NSString stringWithContentsOfFile:@(pixel) encoding:NSUTF8StringEncoding error:&error];
    id<MTLLibrary> vl=[mtl newLibraryWithSource:vs options:nil error:&error];
    if(!vl){fprintf(stderr,"VS: %s\n",error.description.UTF8String);return 2;}
    id<MTLLibrary> pl=[mtl newLibraryWithSource:ps options:nil error:&error];
    if(!pl){fprintf(stderr,"PS: %s\n",error.description.UTF8String);return 2;}
    MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];
    pd.vertexFunction=[vl newFunctionWithName:@"sm_main"];pd.fragmentFunction=[pl newFunctionWithName:@"sm_main"];
    pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Float;
    id<MTLRenderPipelineState> pipeline=[mtl newRenderPipelineStateWithDescriptor:pd error:&error];
    if(!pipeline){fprintf(stderr,"pipeline: %s\n",error.description.UTF8String);return 2;}
    id<MTLCommandQueue> queue=[mtl newCommandQueue];
    MTLTextureDescriptor *d=[MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA32Float width:1 height:1 mipmapped:NO];
    d.storageMode=MTLStorageModeShared;d.usage=MTLTextureUsageShaderRead;
    id<MTLTexture> reflection=[mtl newTextureWithDescriptor:d];
    const float colour[4]={.4f,.6f,.8f,1};
    [reflection replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 withBytes:colour bytesPerRow:16];
    d.textureType=MTLTextureType2DArray;d.arrayLength=16;
    id<MTLTexture> ripple=[mtl newTextureWithDescriptor:d];
    const float rip[4]={.8f,.6f,.2f,1};
    for(unsigned i=0;i<16;++i)[ripple replaceRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0 slice:i withBytes:rip bytesPerRow:16 bytesPerImage:16];
    d.textureType=MTLTextureType2D;d.arrayLength=1;d.usage=MTLTextureUsageRenderTarget;
    id<MTLTexture> output=[mtl newTextureWithDescriptor:d];
    MTLSamplerDescriptor *sd=[MTLSamplerDescriptor new];sd.minFilter=sd.magFilter=MTLSamplerMinMagFilterNearest;
    id<MTLSamplerState> sampler=[mtl newSamplerStateWithDescriptor:sd];
    fixture();scene(1);rain_cover_set(1);
    const uint32_t platform=0x120000;M32(VIEW)=platform;identity(platform+0x80);
    for(int i=0;i<4;++i)dev.vsc[i][i]=1;
    settle();
    if (upload_lifecycle()) {fprintf(stderr,"GPU upload lifecycle FAIL\n");return 1;}
    SmConstant wvp={.name="WorldViewProj",.set=2,.count=4,.reg=0};
    SmResult reflection_info={.constants=&wvp,.n_constants=1};
    const float points[][3]={{3,3,5},{30,30,5},{0,0,5},{3,3,12},{1,2,3},{30,30,5}};
    int bad=0,channels=0;
    for(unsigned sample=0;sample<(film?12u:6u);++sample) {
        unsigned point_index=sample%6;
        int wet=sample>=6;
        memset(dev.vsc,0,sizeof dev.vsc);for(int i=0;i<4;++i)dev.vsc[i][i]=1;
        /* Local (1,2,3) becomes world (3,3,5): compare the actual renderer
         * constant binding, not an identity-only shader approximation. */
        if(point_index==4){dev.vsc[0][3]=2;dev.vsc[1][3]=1;dev.vsc[2][3]=2;}
        M32(RAIN+0x234)=point_index==5;
        MatConsts material={0};material.k[0][0]=1;
        float constants[2][4]={{0}};
        constants[1][3]=wet?1:0;
        MTLRenderPassDescriptor *rp=[MTLRenderPassDescriptor renderPassDescriptor];
        rp.colorAttachments[0].texture=output;rp.colorAttachments[0].loadAction=MTLLoadActionClear;
        rp.colorAttachments[0].storeAction=MTLStoreActionStore;
        id<MTLCommandBuffer> cb=[queue commandBuffer];
        id<MTLRenderCommandEncoder> e=[cb renderCommandEncoderWithDescriptor:rp];
        [e setRenderPipelineState:pipeline];
        float point[4]={points[point_index][0],points[point_index][1],points[point_index][2],0};
        [e setVertexBytes:point length:sizeof point atIndex:0];
        [e setFragmentBytes:constants length:sizeof constants atIndex:0];
        [e setFragmentBytes:&material length:sizeof material atIndex:4];
        [e setFragmentTexture:reflection atIndex:3];[e setFragmentSamplerState:sampler atIndex:3];
        [e setFragmentTexture:ripple atIndex:15];rain_cover_bind(e,&reflection_info);
        [e drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];[e endEncoding];
        [cb commit];[cb waitUntilCompleted];
        if(cb.status==MTLCommandBufferStatusError){fprintf(stderr,"command: %s\n",cb.error.description.UTF8String);return 2;}
        float got[4];[output getBytes:got bytesPerRow:16 fromRegion:MTLRegionMake2D(0,0,1,1) mipmapLevel:0];
        int covered=point_index==0||point_index==4||point_index==5;
        /* At normal incidence, air/water reflectance is ((1.33-1)/(1.33+1))^2.
         * Shelter removes ripples while preserving this wet film response. */
        const float fresnel=.0200593122f;
        for(int j=0;j<4;++j){
            float expected=j==3?1:covered?colour[j]:(colour[j]+.2f)*.5f;
            if(wet&&j<3)expected*=fresnel;
            ++channels;bad+=!isfinite(got[j])||fabsf(got[j]-expected)>1e-5f;
        }
    }
    printf("GPU cover: %d failures / %d channels (roof/open/gap/above/transform/authored)\n",bad,channels);
    rain_cover_set(0);map=(RainCoverMap){0};back=(RainCoverMap){0};gpu_map=nil;free(G);G=NULL;
    return bad?1:0;
}
int main(int argc,char **argv) { @autoreleasepool {
    if(argc==5&&!strcmp(argv[1],"--emit"))return emit(argv[2],argv[3],atoi(argv[4]),0);
    if(argc==5&&!strcmp(argv[1],"--emit-film"))return emit(argv[2],argv[3],atoi(argv[4]),1);
    if(argc==4&&!strcmp(argv[1],"--gpu"))return gpu(argv[2],argv[3],0);
    if(argc==4&&!strcmp(argv[1],"--gpu-film"))return gpu(argv[2],argv[3],1);
    if(argc==3&&!strcmp(argv[1],"--compile")){
        mtl=MTLCreateSystemDefaultDevice();if(!mtl)return 3;NSError *error=nil;
        NSString *s=[NSString stringWithContentsOfFile:@(argv[2]) encoding:NSUTF8StringEncoding error:&error];
        if(![mtl newLibraryWithSource:s options:nil error:&error]){fprintf(stderr,"%s\n",error.description.UTF8String);return 2;}return 0;
    }
    return 2;
} }
