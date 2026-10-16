#pragma once
#include "present.h"

// Same mailbox, pool, retirement and metric as production; no AppKit or GPU.
void host_present_test_begin(bool automatic_ack = true, bool offscreen = false,
                             unsigned display_frames = 1);
unsigned host_present_test_flight_count();
void host_present_test_seal(uint64_t id, HostScreenClass cls = HOST_SCREEN_GAMEPLAY,
                            bool had_draws = true, bool prefix_pending = false);
void host_present_test_command_done(uint64_t id);
void host_present_test_presented(uint64_t id, double ts);
void host_present_test_prefix_done(uint64_t id);
bool host_present_test_released(uint64_t id);
uint64_t host_present_test_last_id();
// Run 39: repeats that composed again because the menu was redrawn while the guest was parked.
uint64_t host_present_test_recomposed();
uint64_t host_present_test_page_id();   // run 39: the cached menu page (a fake service's stand-in handle)
uint8_t host_present_test_last_pixel();
bool host_present_test_class_valid();
void host_present_test_fail_allocations(unsigned count);
int host_present_test_requested_width();
bool host_present_test_read_rgba(uint8_t *out, size_t bytes);

uint8_t host_present_test_last_ui_red();

// Fake window events: seal wake, display-link callback, or worker timeout.
bool host_present_test_window_wake(double ts, bool display_tick = false, bool timeout = false);
uint64_t host_present_test_in_flight();
unsigned host_present_test_faults();
bool host_present_test_input_legacy();
// Drive the production commit path (present handlers, minimum duration,
// completion acknowledgement) through the installed gpu::Device - a
// gpu::FakeDevice in tests - with a drawable from `chain`.
void host_present_test_commit_swapchain(gpu::Swapchain chain);
