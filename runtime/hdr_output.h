// Shared HDR request policy and SDR capture boundary.
#pragma once
#include <ctype.h>
#include <stdlib.h>
#include <strings.h>

static inline int nfs_hdr_output_requested(void) {
    const char *value=getenv("NFS_HDR_OUTPUT");
    if(!value)return 0;
    while(isspace((unsigned char)*value))++value;
    if(strncasecmp(value,"edr",3))return 0;
    value+=3;
    while(isspace((unsigned char)*value))++value;
    return !*value;
}

#ifdef __OBJC__
#import <Metal/Metal.h>
// BGRA8 passes through; FP16 composition/transport becomes tone-mapped SDR.
id<MTLTexture> hdr_capture_source(id<MTLCommandBuffer> cb, id<MTLTexture> src);
#endif
