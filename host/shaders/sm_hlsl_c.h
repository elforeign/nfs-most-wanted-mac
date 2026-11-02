// Bounded, caller-owned interface to the SM1.1 HLSL translator.
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define SM_HLSL_MAX_BYTECODE 65536u
#define SM_HLSL_MAX_SOURCE 262144u

typedef enum sm_hlsl_status {
    SM_HLSL_OK = 0,
    SM_HLSL_BUFFER_TOO_SMALL = 1,
    SM_HLSL_INVALID_ARGUMENT = 2,
    SM_HLSL_REJECTED = 3,
    SM_HLSL_INTERNAL_ERROR = 4
} sm_hlsl_status;

// stage: 0 = vertex, 1 = pixel. Texture bit N binds tN/sN and TEXCOORDN.
// Vertex inputs are indexed by v#; usage is D3DDECLUSAGE (0/10/5).
// A zero input_mask means no vertex declarations. VS constants bind at b0.
typedef struct sm_hlsl_interface {
    uint32_t stage, texture_mask, input_mask;
    uint32_t input_usage[4], input_usage_index[4];
} sm_hlsl_interface;

// Bytecode is little endian and need not be aligned. No pointers are retained.
// All storage belongs to the caller; no free function or shared state exists.
// source_size is required and receives bytes INCLUDING NUL on OK/BUFFER_TOO_SMALL,
// zero on failure. Query with source=NULL, capacity=0; no partial source is returned.
// info is optional, filled on OK/BUFFER_TOO_SMALL, zeroed on failure.
// error is optional when error_capacity=0; otherwise always NUL terminated.
// Buffers/size/info must not overlap each other or bytecode. Capacities describe
// actual writable storage; NULL is permitted only with zero capacity.
sm_hlsl_status sm_hlsl_translate(const void *bytecode, size_t bytecode_size,
    char *source, size_t source_capacity, size_t *source_size,
    sm_hlsl_interface *info, char *error, size_t error_capacity);
#ifdef __cplusplus
}
#endif
