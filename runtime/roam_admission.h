// Stateless return-routability cookies and bounded, integer token buckets.
#pragma once
#include <stdint.h>
#include <string.h>
#include <netinet/in.h>
#ifdef __APPLE__
#include <CommonCrypto/CommonHMAC.h>
#include <stdlib.h>
#else
#include <openssl/hmac.h>
#include <openssl/rand.h>
#include <openssl/crypto.h>
#endif
#define ROAM_COOKIE_MS 10000u
#define ROAM_KEY_MS 5000u
#define ROAM_SOURCES 1024
#define ROAM_SOURCE_MS 60000u

typedef struct { uint64_t tokens; uint32_t ms; int started; } RoamBucket;
static inline void roam_refill(RoamBucket *b, uint32_t now, uint32_t rate, uint32_t burst) {
    uint64_t cap = (uint64_t)burst * 1000;
    if (!b->started) { b->tokens = cap; b->started = 1; }
    else {
        if ((int32_t)(now - b->ms) < 0) return;
        uint64_t add = (uint64_t)(uint32_t)(now - b->ms) * rate;
        b->tokens = add >= cap || b->tokens >= cap - add ? cap : b->tokens + add;
    }
    b->ms = now;
}
static inline int roam_take(RoamBucket *b, uint32_t cost) {
    uint64_t amount = (uint64_t)cost * 1000;
    if (b->tokens < amount) return 0;
    b->tokens -= amount; return 1;
}
static inline int roam_entropy(uint8_t key[32]) {
#ifdef __APPLE__
    arc4random_buf(key, 32); return 1;
#else
    return RAND_bytes(key, 32) == 1;
#endif
}
static inline int roam_cookie_make(const uint8_t key[32], const struct sockaddr_in *a,
                                   uint8_t version, const uint8_t hello[ROAM_HELLO_BASE], uint64_t now, uint8_t out[20]) {
    uint8_t msg[7 + ROAM_HELLO_BASE + 8], digest[32];
    for (int i = 0; i < 4; ++i) out[i] = (uint8_t)(now >> (i * 8));
    memcpy(msg, &a->sin_addr.s_addr, 4); memcpy(msg + 4, &a->sin_port, 2);
    msg[6] = version; memcpy(msg + 7, hello, ROAM_HELLO_BASE);
    // The opaque wire timestamp stays compact; the MAC authenticates its absolute era.
    for (int i = 0; i < 8; ++i) msg[7 + ROAM_HELLO_BASE + i] = (uint8_t)(now >> (i * 8));
#ifdef __APPLE__
    CCHmac(kCCHmacAlgSHA256, key, 32, msg, sizeof msg, digest);
#else
    unsigned len = 0;
    if (!HMAC(EVP_sha256(), key, 32, msg, sizeof msg, digest, &len) || len != 32) return 0;
#endif
    memcpy(out + 4, digest, 16); return 1;
}
static inline int roam_cookie_valid(const uint8_t key[32], const struct sockaddr_in *a,
                                    uint8_t version, const uint8_t hello[ROAM_HELLO_BASE], const uint8_t cookie[20], uint64_t now) {
    uint32_t stamp = (uint32_t)cookie[0] | (uint32_t)cookie[1] << 8 | (uint32_t)cookie[2] << 16 | (uint32_t)cookie[3] << 24;
    uint64_t issued = (now & ~UINT64_C(0xffffffff)) | stamp;
    if (issued > now) {
        if (issued < (UINT64_C(1) << 32)) return 0;
        issued -= UINT64_C(1) << 32;
    }
    if (now - issued > ROAM_COOKIE_MS) return 0;
    uint8_t expected[20];
    if (!roam_cookie_make(key, a, version, hello, issued, expected)) return 0;
#ifdef __APPLE__
    // CommonCrypto supplies the MAC; timingsafe_bcmp supplies the constant-time comparison.
    return timingsafe_bcmp(expected, cookie, 20) == 0;
#else
    return CRYPTO_memcmp(expected, cookie, 20) == 0;
#endif
}
