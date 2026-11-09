// Absolute frame-slot wait: signals may abort Mach waits before the slot arrives.
#pragma once
#include <stdint.h>
#include <mach/mach_time.h>
#include <mach/kern_return.h>

static inline void pacewait_until(uint64_t deadline) {
    // Retry the same slot; an uninterrupted wait needs only the return-value check.
    while (mach_wait_until(deadline) == KERN_ABORTED) {}
}
