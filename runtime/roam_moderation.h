// Bounded local administration and installation bans. No player endpoints on disk.
#pragma once
#include <stddef.h>
#include <stdint.h>
#define ROAM_BANS_MAX 1024
#define ROAM_ADMIN_INPUT 160
#define ROAM_ADMIN_OUTPUT 3072
#define ROAM_BAN_MAX_SECONDS 2592000u
struct RoamRelay;
typedef struct RoamModeration RoamModeration;
// Both directories must already exist, owned by this uid and mode 0700.
// A missing bans file means no bans; any unsafe/corrupt file refuses startup.
RoamModeration *roam_moderation_open(const char *state_directory, const char *admin_directory);
void roam_moderation_close(RoamModeration *m);
int roam_moderation_banned(RoamModeration *m, const uint8_t id[32], uint64_t utc_seconds);
int roam_moderation_ban(RoamModeration *m, const uint8_t verified_id[32], uint64_t expiry, uint64_t now);
int roam_moderation_unban(RoamModeration *m, const uint8_t id[32], uint64_t now);
// Numeric errors only: no paths or rejected input in logs/replies.
void roam_moderation_service(RoamModeration *m, struct RoamRelay *relay, uint64_t monotonic_ms);
int roam_moderation_command(RoamModeration *m, struct RoamRelay *relay, const char *line, size_t n, char out[ROAM_ADMIN_OUTPUT]);
uint64_t roam_moderation_utc(void);
