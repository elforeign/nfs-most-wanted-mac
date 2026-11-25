/* Failure-only numeric observations. Never pass error messages or asset paths. */
#ifndef NFS_BLACKBOX_ERRORS_H
#define NFS_BLACKBOX_ERRORS_H
#include "blackbox.h"
typedef enum {
    BB_ERR_SHADER = 1, BB_ERR_D3D, BB_ERR_METAL, BB_ERR_ASSET,
    BB_ERR_ASSERT, BB_ERR_FATAL, BB_ERR_COM
} BlackboxErrorClass;
typedef enum {
    BB_OP_TRANSLATE = 1, BB_OP_LIBRARY, BB_OP_PIPELINE, BB_OP_HRESULT,
    BB_OP_OPEN, BB_OP_READ, BB_OP_BREAKPOINT, BB_OP_FATAL,
    BB_OP_FRAME_COMMAND, BB_OP_FINAL_COMMAND, BB_OP_FUNCTION
} BlackboxErrorOperation;
typedef struct {
    uint32_t subsystem, operation;
    int64_t code;
    uint64_t identifier;
    uint32_t caller;
} BlackboxError;
/* serial=0 uses the latest published guest frame; completion hooks pass theirs. */
void blackbox_record_error(const BlackboxError *error, uint64_t serial);
void blackbox_error(uint32_t subsystem, uint32_t operation, int64_t code,
                    uint64_t identifier, uint32_t caller, uint64_t serial);
/* Only called on a high-bit result. Filters scalar/handle returns before recording. */
void blackbox_error_hresult(const char *method, uint32_t result, uint32_t caller);
#endif
