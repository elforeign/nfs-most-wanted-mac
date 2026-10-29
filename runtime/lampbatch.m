// Police lamp-glow batching (render.m lamp_record/lamp_flush): a stand-in render command encoder that records
// every message draw_impl sends while a cop flare is replayed into the lamp target, so the scene's encoder stays
// open; the recording is invoked in order on one real lamp encoder later. Each message becomes a retained
// NSInvocation (objects retained; the bytes behind set*Bytes copied, as Metal itself copies them at the call).
#import <Metal/Metal.h>
#import <objc/runtime.h>
#include "lampbatch.h"

@interface LampRec : NSObject {
@public
    NSMutableArray<NSInvocation *> *ops;
    NSMutableArray<NSData *> *bytes;
    unsigned unknown;
}
@end

@implementation LampRec
- (instancetype)init {
    if ((self = [super init])) { ops = [NSMutableArray new]; bytes = [NSMutableArray new]; }
    return self;
}
static const char *enc_types(SEL s) {
    Protocol *ps[2] = {@protocol(MTLRenderCommandEncoder), @protocol(MTLCommandEncoder)};
    for (int i = 0; i < 2; ++i)
        for (int req = 1; req >= 0; --req) {
            struct objc_method_description d = protocol_getMethodDescription(ps[i], s, (BOOL)req, YES);
            if (d.name) return d.types;
        }
    return NULL;
}
- (NSMethodSignature *)methodSignatureForSelector:(SEL)s {
    const char *t = enc_types(s);
    if (t) return [NSMethodSignature signatureWithObjCTypes:t];
    return [super methodSignatureForSelector:s];
}
- (void)forwardInvocation:(NSInvocation *)inv {
    SEL s = inv.selector;
    if (s == @selector(setVertexBytes:length:atIndex:) || s == @selector(setFragmentBytes:length:atIndex:)) {
        const void *p = NULL; NSUInteger n = 0;
        [inv getArgument:&p atIndex:2]; [inv getArgument:&n atIndex:3];
        NSData *d = [NSData dataWithBytes:p length:n];
        [bytes addObject:d];
        const void *q = d.bytes;
        [inv setArgument:&q atIndex:2];
    } else if (s == @selector(endEncoding)) { ++unknown; return; }   // never: render.m ends the scene encoder instead
    // Every encoder message draw_impl sends takes objects, scalars or structs by value except the two above;
    // any other pointer argument would dangle, so it is counted and the batch still replays what is safe.
    NSMethodSignature *sig = inv.methodSignature;
    for (NSUInteger i = 2; i < sig.numberOfArguments; ++i) {
        const char *ty = [sig getArgumentTypeAtIndex:i];
        while (*ty == 'r' || *ty == 'n' || *ty == 'N' || *ty == 'o' || *ty == 'O' || *ty == 'R' || *ty == 'V') ++ty;
        if (*ty == '^' && s != @selector(setVertexBytes:length:atIndex:) && s != @selector(setFragmentBytes:length:atIndex:)) { ++unknown; return; }
    }
    inv.target=nil; // queued calls own their arguments, never their recorder
    [inv retainArguments];
    [ops addObject:inv];
}
@end

id lamprec_new(void) { return [LampRec new]; }
unsigned lamprec_ops(id rec) { return rec ? (unsigned)((LampRec *)rec)->ops.count : 0; }
unsigned lamprec_refused(id rec) { return rec ? ((LampRec *)rec)->unknown : 0; }
void lamprec_replay(id rec, id<MTLRenderCommandEncoder> e) {
    LampRec *r = rec;
    if (!r) return;
    if (e) for (NSInvocation *inv in r->ops) [inv invokeWithTarget:e];
    [r->ops removeAllObjects]; [r->bytes removeAllObjects]; r->unknown = 0;
}
