// help_seam.h - the settings menu's Help rows (NFSMW 3.0.4): a bug report zip and an opt-in update check. The app
// host implements them (host/bug_report.cpp, host/update_check.cpp); other hosts get the weak defaults in
// runtime/mods_seam.cpp (no rows).
#pragma once
#include <stddef.h>

extern "C" {
// Writes one zip to the Desktop: the newest logs, the settings files and the mod reports; nothing is uploaded.
// Returns 1 on success; `msg` is a line for the menu either way.
int host_save_bug_report(char *msg, size_t cap);
int host_bug_report_available(void);
// The update check: only when the player presses it (one request to GitHub's latest release of the game's
// releases_repo). host_update_activate starts a check, or opens the newer release's page once one was found.
int host_update_available(void);
void host_update_activate(void);
// The row's value now ("", "Checking...", "Up to date (3.0.4)", "3.0.5 is out: press to open", "No answer").
void host_update_status(char *text, size_t cap);
}
