// Installation credentials. No key generation or filesystem work during initialization.
#pragma once
#include <stddef.h>
#include <stdint.h>
#define ROAM_AUTH_PUBLIC 65
#define ROAM_AUTH_PRIVATE 97
#define ROAM_AUTH_SIG 72
#define ROAM_HELLO_BASE (20 + ROAM_AUTH_PUBLIC)
#define ROAM_HELLO_PROOF (ROAM_HELLO_BASE + 20 + 1 + ROAM_AUTH_SIG)
#define ROAM_HELLO_BYTES (8 + ROAM_HELLO_PROOF)
typedef struct { uint8_t secret[ROAM_AUTH_PRIVATE]; int ready; } RoamAuth;
int roam_auth_generate(RoamAuth *a);
// Uses only USERDATA/online-identity/key, with owner-only directory and file access.
// Corruption/unsafe permissions fail closed; an existing credential is never silently replaced.
int roam_auth_load(RoamAuth *a, const char *userdata);
void roam_auth_clear(RoamAuth *a);
int roam_auth_sign(const RoamAuth *a, uint8_t version, const uint8_t hello[ROAM_HELLO_BASE],
                   const uint8_t cookie[20], uint8_t sig[ROAM_AUTH_SIG], uint8_t *length);
int roam_auth_verify(uint8_t version, const uint8_t hello[ROAM_HELLO_BASE], const uint8_t cookie[20],
                     const uint8_t sig[ROAM_AUTH_SIG], uint8_t length);
void roam_auth_digest(const void *bytes, size_t n, uint8_t out[32]);
// Reject noncanonical encodings before hashing. Curve validity is checked at import.
int roam_auth_identifier(const uint8_t public_key[ROAM_AUTH_PUBLIC], uint8_t out[32]);
void roam_auth_hex(const uint8_t id[32], char out[65]);
int roam_auth_unhex(const char *hex, uint8_t id[32]);
// Disable process core dumps before keeping credentials or endpoint data in RAM.
int roam_auth_no_dumps(void);
