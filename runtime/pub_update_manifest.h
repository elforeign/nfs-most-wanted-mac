/* Signed update wire format and admission primitives. Link Security and
 * CoreFoundation. Parsing alone does not authenticate a manifest: use pum_verify
 * on the retained original bytes, then verify both assets before using them.
 * All status functions return 1 on success, 0 on refusal (including invalid input).
 * Wire integers are canonical decimal; sizes are positive uint64, version parts
 * are uint32. Digests are exactly 64 lowercase hex characters. */
#ifndef PUB_UPDATE_MANIFEST_H
#define PUB_UPDATE_MANIFEST_H
#include <CoreFoundation/CoreFoundation.h>
#include <Security/Security.h>
#include <CommonCrypto/CommonDigest.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include "pub_update_keys.h"

#define PUM_MANIFEST_MAX 4096
#define PUM_SIGNATURE_MAX 80
#define PUM_VERSION_MAX 43

typedef struct {
    char name[128];
    uint64_t bytes;
    char sha256[65];
} PUMAsset;
typedef struct {
    char repo[128], product[16], version[PUM_VERSION_MAX + 1], tag[PUM_VERSION_MAX + 2];
    PUMAsset dmg, checksum;
} PUMManifest;

static inline int pum_decimal(const char *s, uint64_t maximum, uint64_t *out) {
    if (!s || !*s || (s[0] == '0' && s[1])) return 0;
    uint64_t value = 0;
    for (; *s; ++s) {
        if (*s < '0' || *s > '9') return 0;
        unsigned digit = (unsigned)(*s - '0');
        if (value > (maximum - digit) / 10) return 0;
        value = value * 10 + digit;
    }
    *out = value;
    return 1;
}

static inline int pum_version_parts(const char *s, uint32_t out[4]) {
    if (!s || !out) return 0;
    memset(out, 0, 4 * sizeof *out);
    unsigned count = 0;
    while (*s) {
        char part[11];
        size_t n = 0;
        while (*s && *s != '.') {
            if (n == sizeof part - 1) return 0;
            part[n++] = *s++;
        }
        part[n] = 0;
        uint64_t value;
        if (count == 4 || !pum_decimal(part, UINT32_MAX, &value)) return 0;
        out[count++] = (uint32_t)value;
        if (*s == '.' && !*++s) return 0;
    }
    return count >= 2;
}

/* An invalid version is not an ordering result. Missing parts compare as zero. */
static inline int pum_compare_versions(const char *a, const char *b, int *order) {
    uint32_t x[4], y[4];
    if (!order || !pum_version_parts(a, x) || !pum_version_parts(b, y)) return 0;
    *order = 0;
    for (unsigned i = 0; i < 4; ++i) {
        if (x[i] != y[i]) { *order = x[i] < y[i] ? -1 : 1; break; }
    }
    return 1;
}

static inline int pum_copy(char *out, size_t cap, const char *s) {
    size_t n = strlen(s);
    if (n >= cap) return 0;
    memcpy(out, s, n + 1);
    return 1;
}

static inline int pum_hex(const char *s) {
    if (!s || strlen(s) != 64) return 0;
    for (unsigned i = 0; i < 64; ++i)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return 0;
    return 1;
}

static inline int pum_parse_asset(char *s, const char *expected, PUMAsset *out) {
    char *size = strchr(s, '|');
    if (!size) return 0;
    *size++ = 0;
    char *hash = strchr(size, '|');
    if (!hash) return 0;
    *hash++ = 0;
    return !strcmp(s, expected) && pum_copy(out->name, sizeof out->name, s) &&
        pum_decimal(size, UINT64_MAX, &out->bytes) && out->bytes > 0 &&
        pum_hex(hash) && pum_copy(out->sha256, sizeof out->sha256, hash);
}

/* Bind all three expectations to trusted caller constants / the requested tag.
 * Recognized products: mw and carbon, with their fixed repository and asset names.
 * On failure out is cleared, so a previous successful parse cannot survive. */
static inline int pum_parse(const void *raw, size_t length, const char *repo,
                            const char *product, const char *tag, PUMManifest *out) {
    if (!out) return 0;
    memset(out, 0, sizeof *out);
    if (!raw || !length || length > PUM_MANIFEST_MAX || !repo || !product || !tag) return 0;
    const unsigned char *bytes = raw;
    if (bytes[length - 1] != '\n') return 0;
    for (size_t i = 0; i < length; ++i)
        if (bytes[i] != '\n' && (bytes[i] < 32 || bytes[i] > 126)) return 0;
    char buffer[PUM_MANIFEST_MAX + 1], *lines[7];
    memcpy(buffer, raw, length); buffer[length] = 0;
    char *start = buffer;
    unsigned count = 0;
    for (size_t i = 0; i < length; ++i) {
        if (buffer[i] != '\n') continue;
        if (count == 7) return 0;
        buffer[i] = 0; lines[count++] = start; start = buffer + i + 1;
    }
    if (count != 7 || strcmp(lines[0], "NFS-UPDATE-1")) return 0;
    static const char *const prefixes[6] = {"repo=", "product=", "version=", "tag=", "dmg=", "checksum="};
    for (unsigned i = 0; i < 6; ++i) {
        size_t n = strlen(prefixes[i]);
        if (strncmp(lines[i + 1], prefixes[i], n)) return 0;
        lines[i + 1] += n;
    }
    if (strcmp(lines[1], repo) || strcmp(lines[2], product) || strcmp(lines[4], tag)) return 0;
    const char *fixed_repo, *asset_prefix;
    if (!strcmp(product, "mw")) {
        fixed_repo = "elforeign/nfs-most-wanted-mac"; asset_prefix = "NFS-Most-Wanted-Native-Setup";
    } else if (!strcmp(product, "carbon")) {
        fixed_repo = "elforeign/nfs-carbon-mac"; asset_prefix = "NFS-Carbon-Native-Setup";
    } else return 0;
    uint32_t parts[4];
    if (strcmp(repo, fixed_repo) || !pum_version_parts(lines[3], parts) ||
        lines[4][0] != 'v' || strcmp(lines[4] + 1, lines[3])) return 0;
    PUMManifest parsed = {0};
    char dmg[128], checksum[128];
    int n = snprintf(dmg, sizeof dmg, "%s-v%s.dmg", asset_prefix, lines[3]);
    if (n < 0 || (size_t)n >= sizeof dmg) return 0;
    n = snprintf(checksum, sizeof checksum, "%s.sha256", dmg);
    if (n < 0 || (size_t)n >= sizeof checksum) return 0;
    if (!pum_copy(parsed.repo, sizeof parsed.repo, lines[1]) ||
        !pum_copy(parsed.product, sizeof parsed.product, lines[2]) ||
        !pum_copy(parsed.version, sizeof parsed.version, lines[3]) ||
        !pum_copy(parsed.tag, sizeof parsed.tag, lines[4]) ||
        !pum_parse_asset(lines[5], dmg, &parsed.dmg) ||
        !pum_parse_asset(lines[6], checksum, &parsed.checksum)) return 0;
    *out = parsed;
    return 1;
}

/* Writes exactly the signed wire bytes; length excludes the convenience NUL.
 * cap includes that NUL. Invalid structs and insufficient capacity fail closed. */
static inline int pum_serialize(const PUMManifest *m, char *out, size_t cap, size_t *length) {
    if (length) *length = 0;
    if (!m || !out || !cap || !length) return 0;
    out[0] = 0;
#define PUM_TERMINATED(field) (memchr(m->field, 0, sizeof m->field) != NULL)
    if (!PUM_TERMINATED(repo) || !PUM_TERMINATED(product) || !PUM_TERMINATED(version) ||
        !PUM_TERMINATED(tag) || !PUM_TERMINATED(dmg.name) || !PUM_TERMINATED(dmg.sha256) ||
        !PUM_TERMINATED(checksum.name) || !PUM_TERMINATED(checksum.sha256)) return 0;
#undef PUM_TERMINATED
    char wire[PUM_MANIFEST_MAX + 1];
    int n = snprintf(wire, sizeof wire,
        "NFS-UPDATE-1\nrepo=%s\nproduct=%s\nversion=%s\ntag=%s\ndmg=%s|%" PRIu64 "|%s\nchecksum=%s|%" PRIu64 "|%s\n",
        m->repo, m->product, m->version, m->tag, m->dmg.name, m->dmg.bytes, m->dmg.sha256,
        m->checksum.name, m->checksum.bytes, m->checksum.sha256);
    PUMManifest checked;
    if (n < 0 || (size_t)n > PUM_MANIFEST_MAX || (size_t)n >= cap ||
        !pum_parse(wire, (size_t)n, m->repo, m->product, m->tag, &checked)) return 0;
    memcpy(out, wire, (size_t)n + 1); *length = (size_t)n;
    return 1;
}

/* X9.63 uncompressed P-256 public points. Skip unset/invalid slots; a signature
 * must verify under an actual pinned key. Test fixtures may supply scratch keys. */
static inline int pum_verify_signature_with_pins(const void *raw, size_t length,
        const void *signature, size_t signature_length, const uint8_t *const *pins, size_t pin_count) {
    if (!raw || !length || length > PUM_MANIFEST_MAX || !signature || !signature_length ||
        signature_length > PUM_SIGNATURE_MAX || !pins || !pin_count) return 0;
    CFDataRef message = CFDataCreate(NULL, raw, (CFIndex)length);
    CFDataRef sig = CFDataCreate(NULL, signature, (CFIndex)signature_length);
    if (!message || !sig) {
        if (message) CFRelease(message);
        if (sig) CFRelease(sig);
        return 0;
    }
    const void *keys[] = {kSecAttrKeyType, kSecAttrKeyClass};
    const void *values[] = {kSecAttrKeyTypeECSECPrimeRandom, kSecAttrKeyClassPublic};
    CFDictionaryRef attrs = CFDictionaryCreate(NULL, keys, values, 2,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    int valid = 0;
    for (size_t i = 0; attrs && i < pin_count && !valid; ++i) {
        if (!pins[i] || pins[i][0] != 0x04) continue;  /* includes both all-zero unset pins */
        CFDataRef point = CFDataCreate(NULL, pins[i], 65);
        CFErrorRef error = NULL;
        SecKeyRef key = point ? SecKeyCreateWithData(point, attrs, &error) : NULL;
        if (error) { CFRelease(error); error = NULL; }
        if (key) {
            valid = SecKeyVerifySignature(key, kSecKeyAlgorithmECDSASignatureMessageX962SHA256,
                                          message, sig, &error);
            if (error) CFRelease(error);
            CFRelease(key);
        }
        if (point) CFRelease(point);
    }
    if (attrs) CFRelease(attrs);
    CFRelease(sig); CFRelease(message);
    return valid;
}

static inline int pum_verify_signature(const void *raw, size_t length,
                                        const void *signature, size_t signature_length) {
    return pum_verify_signature_with_pins(raw, length, signature, signature_length, PUM_PINS, 2);
}

static inline int pum_verify_with_pins(const void *raw, size_t length, const void *signature, size_t signature_length,
        const char *repo, const char *product, const char *tag, PUMManifest *out,
        const uint8_t *const *pins, size_t pin_count) {
    if (!out) return 0;
    memset(out, 0, sizeof *out);
    PUMManifest parsed;
    if (!pum_parse(raw, length, repo, product, tag, &parsed) ||
        !pum_verify_signature_with_pins(raw, length, signature, signature_length, pins, pin_count)) return 0;
    *out = parsed;
    return 1;
}

static inline int pum_verify(const void *raw, size_t length, const void *signature, size_t signature_length,
                             const char *repo, const char *product, const char *tag, PUMManifest *out) {
    return pum_verify_with_pins(raw, length, signature, signature_length,
                               repo, product, tag, out, PUM_PINS, 2);
}

/* Also usable with a streaming downloader's independently computed size/hash. */
static inline int pum_asset_matches(const PUMAsset *asset, uint64_t size, const char *sha256) {
    return asset && asset->bytes == size && pum_hex(sha256) &&
        memchr(asset->sha256, 0, sizeof asset->sha256) && !strcmp(asset->sha256, sha256);
}

static inline int pum_verify_file(const PUMAsset *asset, const char *path) {
    if (!asset || !path) return 0;
    FILE *file = fopen(path, "rb");
    if (!file) return 0;
    CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);
    uint64_t size = 0;
    unsigned char buffer[65536], digest[CC_SHA256_DIGEST_LENGTH];
    size_t count;
    int valid = 1;
    while ((count = fread(buffer, 1, sizeof buffer, file))) {
        if (count > UINT64_MAX - size) { valid = 0; break; }
        size += count;
        if (size > asset->bytes) { valid = 0; break; }
        CC_SHA256_Update(&ctx, buffer, (CC_LONG)count);
    }
    if (ferror(file)) valid = 0;
    if (fclose(file)) valid = 0;
    if (!valid) return 0;
    CC_SHA256_Final(digest, &ctx);
    char hex[65];
    for (unsigned i = 0; i < sizeof digest; ++i) snprintf(hex + i * 2, 3, "%02x", digest[i]);
    return pum_asset_matches(asset, size, hex);
}
#endif
