/* 3.0.4 (GitHub issue 8): a player's 3.0.3 log showed the game's sound-bank fix-up (0083104c, called from 006ff0df,
 * 006ff15d, 006ff1d6) writing over the kit's DirectSound buffer: the bank sat on memory the kit had given the sound
 * output. Each bank's address and table sizes are logged (the first 16), so the next log shows where the game puts its
 * banks; with the kit's "heap: guest block" lines it tells which allocation a bank belongs to. 0083104c(bank): counts
 * at bank+0xa / +0xc / +0xe, tables from bank+0x28. Diagnostics only: nothing is changed. */
static uint32_t g_snd_banks_logged;
static void snd_bank_log(const PopModApi *api, pop_cpu_v1 *c, PopHookInvocation *i, void *u) {
    (void)i; (void)u;
    if (g_snd_banks_logged >= 16u) return;
    uint32_t bank = 0, caller = 0;
    uint16_t n1 = 0, n2 = 0, n3 = 0;
    api->guest_read_u32(api, c->esp + 4u, &bank);
    api->guest_read_u32(api, c->esp, &caller);
    if (bank) {
        api->guest_read_u16(api, bank + 0x0au, &n1);
        api->guest_read_u16(api, bank + 0x0cu, &n2);
        api->guest_read_u16(api, bank + 0x0eu, &n3);
    }
    char line[160];
    snprintf(line, sizeof line, "core.nfsmw: sound bank %u at %08x: %u / %u / %u table entries (from %08x)",
             ++g_snd_banks_logged, bank, n1, n2, n3, caller);
    api->log(api, line);
}
static void snd_bank_log_init(const PopModApi *api) {
    if (install(0x0083104cu, snd_bank_log, POP_HOOK_BEFORE, 111) != POP_OK)
        api->log(api, "core.nfsmw: sound bank log unavailable (hook)");
}
