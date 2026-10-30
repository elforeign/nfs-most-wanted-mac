// Shared HDR request policy and SDR capture boundary.
#pragma once
#include <ctype.h>
#include <stdlib.h>
#include <strings.h>

// Sampled on the main thread by the presenter, before float-scene startup.
float render_hdr_display_potential(void);
static inline int nfs_hdr_output_mode(void) {
    const char *value=getenv("NFS_HDR_OUTPUT");
    if(!value)return 0; // Auto
    while(isspace((unsigned char)*value))++value;
    const char *end=value;
    while(*end)++end;
    while(end>value && isspace((unsigned char)end[-1]))--end;
    if(end-value==3 && !strncasecmp(value,"edr",3))return 2;
    if(end-value==4 && !strncasecmp(value,"auto",4))return 0;
    return 1; // Off, including unsupported spellings
}
static inline int nfs_hdr_output_requested(void) {
    int mode=nfs_hdr_output_mode();
    return mode==2 || (mode==0 && render_hdr_display_potential()>1);
}

#ifdef __OBJC__
#import <Metal/Metal.h>
// BGRA8 passes through; FP16 composition/transport becomes tone-mapped SDR.
id<MTLTexture> hdr_capture_source(id<MTLCommandBuffer> cb, id<MTLTexture> src);
#endif
