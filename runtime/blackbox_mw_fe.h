#ifndef NFS_BLACKBOX_MW_FE_H
#define NFS_BLACKBOX_MW_FE_H
#include <stdint.h>

enum {
    BB_MW_PACKAGE_LIMIT = 1,
    BB_MW_FADE_PACKAGE_LIMIT = 2,
    BB_MW_FADE_OBJECT_LIMIT = 4,
    BB_MW_FADE_SCRIPT_INVALID = 8
};
#define BB_MW_FADE_ON_SCRIPT 0x5079C8F8u
typedef struct {
    uint32_t flow, continuation, stage_name, loading_name;
    uint32_t manager, requested_name, pause_request, garage_type;
    uint32_t controller_errors_lo, controller_errors_hi;
    uint32_t control_package, control_label, control_hash, list_signature;
    uint32_t fade_package, fade_object, fade_script;
    uint32_t stage_label, loading_label, requested_label;
    uint8_t manager_0, manager_1, boot, screen_seen;
    uint8_t tips_done, engine_ready, package_count, limits;
} BlackboxMWFE;

void blackbox_mw_fe(uint64_t frame, uint32_t flow, uint64_t now);
uint32_t blackbox_mw_stage_label(const char *name);
void blackbox_record_mw_fe(const BlackboxMWFE *state, uint64_t frame, uint64_t now);
#endif
