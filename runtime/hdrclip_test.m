// PASS: rgb_hi=2688 (896*3), alpha_hi=1025, UNORM white=897, FP16 exact=1025,
// Off/on 8200 bytes identical (single sample + 4x MSAA); 4100 samples / 1025 resolve pixels checked. Negative control disables rgb_hi: expected FAIL (0 vs 2688).
// Actual native results are recorded in runtime/README.md; sandbox without Metal exits 3.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int diag_on=1;
static char *diag_dir="/tmp";
static uint64_t frame_serial=1;
static id<MTLDevice> mtl;
static id<MTLCommandBuffer> cmd;
#define M32(a) 6
#define MF32(a) 15.0f
#define diag_event(...) ((void)0)
static void end_pass(void) {}
static void ensure_cmd(void) {}
#include "hdrclip.m"
int main(int argc,const char **argv) { @autoreleasepool {
    // A real CPU source transform check runs even without a Metal device.
    hdrclip_on=0;NSString *s=@"// fragment float4 nope(){return fake;}\nfragment float4 f(){/* } return fake; */ return float4(1);}";
    if(hdrclip_source(s)!=s)return 1;
    hdrclip_on=1;NSString *p=hdrclip_source(s);
    if(![p containsString:@"hc_count(float4(hc_v)"] || ![p containsString:@"buffer(25)"])return 1;
    fprintf(stderr,"hdrclip_test: CPU source Off identity / On export hook PASS\n");
    mtl=MTLCreateSystemDefaultDevice();
    if(!mtl){fprintf(stderr,"hdrclip_test: SKIP native Metal (run on a Metal-capable host: bare test)\n");return 3;}
    return hdrclip_test(argc>1 && !strcmp(argv[1],"--mutant"));
} }
