// lampbatch.m: recording stand-in for an MTLRenderCommandEncoder (police lamp-glow batching, render.m).
#pragma once
#import <Metal/Metal.h>
id lamprec_new(void);
unsigned lamprec_ops(id rec);                                // messages recorded since the last replay
unsigned lamprec_refused(id rec);                            // messages that could not be recorded safely (expected 0)
void lamprec_replay(id rec, id<MTLRenderCommandEncoder> e);  // invoke in order on e, then clear
