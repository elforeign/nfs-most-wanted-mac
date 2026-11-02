#pragma once
#include <stdint.h>
/* Diagnostic sequence adapter: -1 stops input, 0 waits, 1 advances. */
int underglow_ui_step(const char *step, uint64_t elapsed, const char *pkg, const char **key);
