#pragma once
#include <stdint.h>
/* Diagnostic sequence adapter: -1 stops input, 0 waits, 1 advances. */
int underglow_ui_step(const char *step, uint64_t elapsed, const char *pkg, const char **key);

/* Global native menu preference, default Off. */
unsigned underglow_pulse(void);
/* underglow_ui.c, diagnostic shop fixture: the front-end ride now shows this owned-car row. */
void underglow_light_fixture_owner(uint32_t profile, uint32_t row);
