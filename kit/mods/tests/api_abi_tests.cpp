// api_abi_tests.cpp - the public header's ABI promises, checked as facts and
// not as intentions: a plugin compiled a year from now reads these offsets.
#include "mods_tests.h"
#include "../pop_mod_api.h"
#include <stddef.h>
#include <string.h>

MOD_TEST_SUITE(api_abi) {
    MOD_CHECK_EQ(offsetof(PopModApi, version), 0u);
    MOD_CHECK_EQ(offsetof(PopModApi, size), 4u);
    MOD_CHECK_EQ(POP_MOD_API_VERSION, 1);
    MOD_CHECK_EQ(offsetof(PopModApi, open_settings_page), 296u);
    MOD_CHECK_EQ(offsetof(PopModApi, set_anchor), 304u);
    MOD_CHECK_EQ(offsetof(PopModApi, clear_anchor), 312u);
    MOD_CHECK_EQ(offsetof(PopModApi, ui_elements), 320u);
    MOD_CHECK_EQ(offsetof(PopModApi, host_aspect), 328u);
    MOD_CHECK_EQ(offsetof(PopModApi, display_transition), 336u);
    MOD_CHECK_EQ(offsetof(PopModApi, set_scene_domain), 344u);
    MOD_CHECK_EQ(offsetof(PopModApi, hook_install_at_callsite), 352u);
    MOD_CHECK_EQ(offsetof(PopModApi, hook_install_ex), 360u);
    MOD_CHECK_EQ(offsetof(PopModApi, texture_override_provider_ex), 368u);
    MOD_CHECK_EQ(offsetof(PopModApi, guest_call), 376u);
    MOD_CHECK_EQ(offsetof(PopModApi, screen_size), 384u);
    MOD_CHECK_EQ(offsetof(PopModApi, rumble_pulse), 392u);
    MOD_CHECK_EQ(offsetof(PopModApi, audio_output), 400u);
    MOD_CHECK_EQ(offsetof(PopModApi, d3d9_texture_tag), 408u); // NFSMW Remaster A1 tail (e6c5bb1)
    MOD_CHECK_EQ(offsetof(PopModApi, pointer_box), 416u);      // NFSU1 run 9 tail (shared kit, run 37)
    MOD_CHECK_EQ(sizeof(PopModApi), 424u);

    pop_cpu_v1 cpu;
    pop_cpu_v1_init(&cpu);
    MOD_CHECK_EQ(cpu.size, (uint32_t)sizeof(pop_cpu_v1));
    MOD_CHECK_EQ(offsetof(pop_cpu_v1, size), 0u);
    MOD_CHECK_EQ(offsetof(pop_cpu_v1, eax), 4u);
    // The minimum an old plugin may declare: through edi. Anything shorter is
    // not a pop_cpu_v1 at all and the loader refuses it.
    MOD_CHECK_EQ(POP_CPU_V1_MIN_SIZE, (uint32_t)(offsetof(pop_cpu_v1, edi) + sizeof(uint32_t)));
    MOD_CHECK(POP_CPU_V1_MIN_SIZE < POP_CPU_V1_BASELINE_SIZE);

    MOD_CHECK_EQ(POP_OK, 0);
    MOD_CHECK_EQ(POP_E_NOSYMBOL, -1);
    MOD_CHECK_EQ(POP_E_CONFLICT, -2);
    MOD_CHECK_EQ(POP_E_REENTRY, -3);
    MOD_CHECK_EQ(POP_E_LIMIT, -4);
    MOD_CHECK_EQ(POP_E_STATE, -5);
    MOD_CHECK_EQ(POP_E_WRONG_THREAD, -6);
    MOD_CHECK_EQ(POP_E_ABI, -11);
    MOD_CHECK_EQ(sizeof(PopModAbi), 16u);

    // Every function pointer takes the API instance first (normative
    // interface 3), which is what makes a call attributable. Checked
    // structurally: a signature without it does not compile here.
    PopModStatus (*install)(const PopModApi *, uint32_t, PopHookFn, int32_t, void *, uint32_t *) =
        nullptr;
    PopModApi probe;
    memset(&probe, 0, sizeof probe);
    probe.hook_install = install;
    MOD_CHECK(probe.hook_install == nullptr);
}
