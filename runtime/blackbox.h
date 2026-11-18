/* Fixed, numeric flight recorder. Producers never format, allocate or call the guest. */
#ifndef NFS_BLACKBOX_H
#define NFS_BLACKBOX_H
#include <stdint.h>
#include <stdio.h>

enum { BLACKBOX_MW = 1, BLACKBOX_CARBON = 2 };
typedef enum { BB_F10, BB_HANG, BB_STALL, BB_CRASH } BlackboxTrigger;
/* Reserved typed slots for later feeds; no arbitrary strings or personal identifiers. */
typedef enum {
    BB_FLOW = 1, BB_HEARTBEAT, BB_MENU,
    BB_FE, BB_PACKAGE, BB_LOAD, BB_MOVIE, BB_NIS, BB_INPUT, BB_ERROR
} BlackboxKind;
typedef struct {
    float frame_ms, busy_ms, wait_ms;
    uint32_t draws, ticks;
} BlackboxTiming;
int blackbox_enabled(void);
uint64_t blackbox_now_ns(void);
void blackbox_record_frame(uint32_t game, uint64_t frame, uint32_t flow,
                           const BlackboxTiming *timing, uint64_t now);
/* Numeric FE/load observations. Labels are IDs from a fixed retail allowlist;
 * unknown strings never enter the recorder. valid distinguishes unreadable state. */
typedef struct {
    uint32_t index, address, vtable, state, start, previous, pending, wait, child;
    uint32_t screen, next;
} BlackboxFE;
typedef struct {
    uint32_t mask, busy, want, have, queued, garage_ready;
} BlackboxLoad;
uint32_t blackbox_fe_label(const char *name);
void blackbox_record_fe(const BlackboxFE *state, uint32_t valid, uint64_t frame, uint64_t now);
void blackbox_record_load(const BlackboxLoad *state, uint32_t valid, uint64_t frame, uint64_t now);
void blackbox_record_package(uint32_t label, uint32_t address, uint32_t count,
                             uint32_t truncated, uint64_t frame, uint64_t now);
/* Selected game adapter; flow is already sampled by the caller on the guest thread. */
void blackbox_game_frame(uint64_t frame, uint32_t flow, const BlackboxTiming *timing);
void blackbox_menu(int open);
void blackbox_menu_at(int open, uint64_t now);
void blackbox_export(FILE *f, BlackboxTrigger trigger);
void blackbox_export_at(FILE *f, BlackboxTrigger trigger, uint64_t now);
void blackbox_summary(FILE *f, BlackboxTrigger trigger);
#endif
