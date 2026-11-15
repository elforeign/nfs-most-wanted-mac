// Existing scene fixtures have no temporal consumer. Fail on any tracking work.
// The enabled lifecycle is exercised separately by temporal_state_test.c.
#pragma once
#include "temporal_state.h"
#include <stdlib.h>
int temporal_state_requested;
void temporal_invalidate(unsigned reason) { (void)reason; abort(); }
void temporal_guest_capture(uint32_t view, uint32_t platform) { (void)view; (void)platform; abort(); }
void temporal_guest_scene_mode(int split) { (void)split; abort(); }
void temporal_guest_frame(uint64_t frame) { (void)frame; abort(); }
void temporal_car_construct(uint32_t car) { (void)car; abort(); }
void temporal_guest_pose_record(uint32_t record) { (void)record; abort(); }
void temporal_pose_clear(void) { abort(); }
