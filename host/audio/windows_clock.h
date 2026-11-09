// The Windows mix clock uses nanoseconds. Mac keeps its original Mach clock.
#pragma once
struct mach_timebase_info_data_t { unsigned numer = 1, denom = 1; };
inline void mach_timebase_info(mach_timebase_info_data_t *) {}
inline uint64_t mach_absolute_time() {
    return (uint64_t)std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}
inline void usleep(unsigned microseconds) {
    std::this_thread::sleep_for(std::chrono::microseconds(microseconds));
}
inline void pthread_set_qos_class_self_np(int, int) {}
#define QOS_CLASS_USER_INTERACTIVE 0
