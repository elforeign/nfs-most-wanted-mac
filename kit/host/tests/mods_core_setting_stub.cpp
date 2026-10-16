// mods_core_setting_stub.cpp - the core-mod setting reader for host test binaries that link host/controls without
// the runtime (controls_tests, gamepad_device_probe): every setting reads its default, as with no core mod (3.0.4).
#include <stdint.h>

extern "C" int64_t mods_core_setting(const char *, int64_t fallback) {
    return fallback;
}
