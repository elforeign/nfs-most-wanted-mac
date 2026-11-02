// Ordered native operations for the Metal recorder. The D3D12 renderer will
// need the same ordering, parameter snapshot and GPU resource lifetime contract.
#pragma once
#import <Metal/Metal.h>

// Called once, in command-stream order, with the real, uncommitted buffer.
// End any active encoder before calling rt_native_encode. The callback owns
// its encoders and must end them before returning; it must not commit or wait.
// bytes is an immutable copy of size bytes, valid through GPU completion.
// Store values in bytes, not pointers to mutable caller state. resources is a
// copied array retaining its elements through completion, including resources
// the driver does not retain. Resource contents/configuration are not copied:
// use distinct resources for overlapping frames or synchronize their mutation.
typedef void (*RTNativeEncode)(id<MTLCommandBuffer> buffer, const void *bytes,
                               size_t size, NSArray *resources);

// With a direct Metal buffer encoding happens now; with a recorder it happens
// at this exact position during replay. No native storage or completion work
// is added to buffers which never call this function. Recording is single-threaded.
void rt_native_encode(id<MTLCommandBuffer> buffer, RTNativeEncode encode,
                      const void *bytes, size_t size, NSArray *resources);
