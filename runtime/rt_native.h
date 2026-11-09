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

// Replays a recorded render list into a target 1/div the size of the one it was recorded for: viewports and
// scissors are divided (scissors clamped to width x height), fragment texture `from` is bound as `to`, and when
// bytes_length is non-zero the fragment-bytes block of that index and length gets bytes_value at bytes_offset.
// keep: leave the list's recording in place for a later replay (a diagnostic second replay of the same draws).
typedef struct { uint32_t div, width, height; void *from, *to; uint32_t bytes_index, bytes_length, bytes_offset; float bytes_value; } RTListScale;
void rt_render_list_replay_scaled(id list, id<MTLRenderCommandEncoder> encoder, const RTListScale *s, int keep);
