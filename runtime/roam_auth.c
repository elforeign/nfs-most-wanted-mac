#include "roam_auth.h"
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <unistd.h>
#ifdef __APPLE__
#include <Security/Security.h>
#include <CommonCrypto/CommonDigest.h>
#include <sys/xattr.h>
#include <sys/acl.h>
#else
#include <openssl/ec.h>
#include <openssl/ecdsa.h>
#include <openssl/obj_mac.h>
#include <openssl/sha.h>
#include <sys/prctl.h>
// OpenSSL 1.1 and 3 provide the same audited P-256 external representation.
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#endif

void roam_auth_clear(RoamAuth *a) {
    volatile uint8_t *p = (volatile uint8_t *)a;
    for (size_t i = 0; i < sizeof *a; ++i) p[i] = 0;
}
void roam_auth_digest(const void *p, size_t n, uint8_t out[32]) {
#ifdef __APPLE__
    CC_SHA256(p, (CC_LONG)n, out);
#else
    SHA256(p, n, out);
#endif
}
int roam_auth_identifier(const uint8_t pub[65], uint8_t out[32]) {
    if (pub[0] != 0x04) { memset(out, 0, 32); return 0; }
    roam_auth_digest(pub, 65, out); return 1;
}
void roam_auth_hex(const uint8_t id[32], char out[65]) {
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 32; ++i) { out[i*2] = hex[id[i] >> 4]; out[i*2+1] = hex[id[i] & 15]; }
    out[64] = 0;
}
int roam_auth_unhex(const char *s, uint8_t id[32]) {
    if (strlen(s) != 64) return 0;
    for (int i = 0; i < 32; ++i) {
        unsigned v = 0;
        for (int j = 0; j < 2; ++j) {
            unsigned char c = (unsigned char)s[i*2+j];
            if (c >= '0' && c <= '9') v = v*16 + c-'0';
            else if (c >= 'a' && c <= 'f') v = v*16 + c-'a'+10;
            else return 0;
        }
        id[i] = (uint8_t)v;
    }
    return 1;
}
int roam_auth_no_dumps(void) {
    struct rlimit limit = {0, 0};
    if (setrlimit(RLIMIT_CORE, &limit)) return 0;
#ifndef __APPLE__
    if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)) return 0;
#endif
    return 1;
}
static size_t transcript(uint8_t out[160], uint8_t version, const uint8_t hello[ROAM_HELLO_BASE], const uint8_t cookie[20]) {
    static const char domain[] = "NFSMW relay installation proof";
    size_t n = sizeof domain;
    memcpy(out, domain, n); out[n++] = version;
    memcpy(out+n, hello, ROAM_HELLO_BASE); n += ROAM_HELLO_BASE;
    memcpy(out+n, cookie, 20); return n+20;
}
#ifdef __APPLE__
static CFDictionaryRef attributes(int private_key) {
    int bits = 256;
    CFNumberRef size = CFNumberCreate(NULL, kCFNumberIntType, &bits);
    const void *keys[] = {kSecAttrKeyType, kSecAttrKeyClass, kSecAttrKeySizeInBits};
    const void *values[] = {kSecAttrKeyTypeECSECPrimeRandom, private_key ? kSecAttrKeyClassPrivate : kSecAttrKeyClassPublic, size};
    CFDictionaryRef d = CFDictionaryCreate(NULL, keys, values, 3, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFRelease(size); return d;
}
static SecKeyRef import_key(const uint8_t *p, int private_key) {
    if (p[0] != 0x04) return NULL;
    CFDataRef data = CFDataCreate(NULL, p, private_key ? 97 : 65);
    CFDictionaryRef attr = attributes(private_key);
    SecKeyRef key = SecKeyCreateWithData(data, attr, NULL);
    CFRelease(attr); CFRelease(data); return key;
}
int roam_auth_generate(RoamAuth *a) {
    roam_auth_clear(a);
    CFDictionaryRef attr = attributes(1);
    SecKeyRef key = SecKeyCreateRandomKey(attr, NULL); CFRelease(attr);
    if (!key) return 0;
    CFDataRef data = SecKeyCopyExternalRepresentation(key, NULL); CFRelease(key);
    int ok = data && CFDataGetLength(data) == 97;
    if (ok) { memcpy(a->secret, CFDataGetBytePtr(data), 97); a->ready = 1; }
    if (data) CFRelease(data);
    return ok;
}
int roam_auth_sign(const RoamAuth *a, uint8_t v, const uint8_t h[ROAM_HELLO_BASE], const uint8_t c[20], uint8_t sig[72], uint8_t *len) {
    if (!a->ready) return 0;
    uint8_t msg[160]; size_t n = transcript(msg, v, h, c);
    SecKeyRef key = import_key(a->secret, 1); if (!key) return 0;
    CFDataRef data = CFDataCreate(NULL, msg, (CFIndex)n);
    CFDataRef signed_data = SecKeyCreateSignature(key, kSecKeyAlgorithmECDSASignatureMessageX962SHA256, data, NULL);
    CFRelease(data); CFRelease(key);
    int ok = signed_data && CFDataGetLength(signed_data) <= 72;
    memset(sig, 0, 72);
    if (ok) { *len = (uint8_t)CFDataGetLength(signed_data); memcpy(sig, CFDataGetBytePtr(signed_data), *len); }
    if (signed_data) CFRelease(signed_data);
    return ok;
}
int roam_auth_verify(uint8_t v, const uint8_t h[ROAM_HELLO_BASE], const uint8_t c[20], const uint8_t sig[72], uint8_t len) {
    if (!len || len > 72) return 0;
    for (int i = len; i < 72; ++i) if (sig[i]) return 0;
    uint8_t msg[160]; size_t n = transcript(msg, v, h, c);
    SecKeyRef key = import_key(h+20, 0); if (!key) return 0;
    CFDataRef data = CFDataCreate(NULL, msg, (CFIndex)n), signature = CFDataCreate(NULL, sig, len);
    int ok = SecKeyVerifySignature(key, kSecKeyAlgorithmECDSASignatureMessageX962SHA256, data, signature, NULL);
    CFRelease(signature); CFRelease(data); CFRelease(key); return ok;
}
#else
static EC_KEY *import_key(const uint8_t *p, int private_key) {
    if (p[0] != 0x04) return NULL;
    EC_KEY *key = EC_KEY_new_by_curve_name(NID_X9_62_prime256v1);
    if (!key) return NULL;
    const EC_GROUP *group = EC_KEY_get0_group(key);
    EC_POINT *point = EC_POINT_new(group);
    int ok = point && EC_POINT_oct2point(group, point, p, 65, NULL) && EC_KEY_set_public_key(key, point);
    EC_POINT_free(point);
    if (ok && private_key) {
        BIGNUM *bn = BN_bin2bn(p+65, 32, NULL);
        ok = bn && EC_KEY_set_private_key(key, bn); BN_clear_free(bn);
    }
    if (!ok || !EC_KEY_check_key(key)) { EC_KEY_free(key); return NULL; }
    return key;
}
int roam_auth_generate(RoamAuth *a) {
    roam_auth_clear(a);
    EC_KEY *key = EC_KEY_new_by_curve_name(NID_X9_62_prime256v1);
    int ok = key && EC_KEY_generate_key(key) &&
        EC_POINT_point2oct(EC_KEY_get0_group(key), EC_KEY_get0_public_key(key), POINT_CONVERSION_UNCOMPRESSED, a->secret, 65, NULL) == 65 &&
        BN_bn2binpad(EC_KEY_get0_private_key(key), a->secret+65, 32) == 32;
    EC_KEY_free(key); a->ready = ok; return ok;
}
int roam_auth_sign(const RoamAuth *a, uint8_t v, const uint8_t h[ROAM_HELLO_BASE], const uint8_t c[20], uint8_t sig[72], uint8_t *len) {
    if (!a->ready) return 0;
    EC_KEY *key = import_key(a->secret, 1); if (!key) return 0;
    uint8_t msg[160], digest[32]; size_t n = transcript(msg, v, h, c); roam_auth_digest(msg, n, digest);
    unsigned length = 72; memset(sig, 0, 72);
    int ok = ECDSA_sign(0, digest, 32, sig, &length, key) && length <= 72;
    EC_KEY_free(key); *len = (uint8_t)length; return ok;
}
int roam_auth_verify(uint8_t v, const uint8_t h[ROAM_HELLO_BASE], const uint8_t c[20], const uint8_t sig[72], uint8_t len) {
    if (!len || len > 72) return 0;
    for (int i = len; i < 72; ++i) if (sig[i]) return 0;
    EC_KEY *key = import_key(h+20, 0); if (!key) return 0;
    uint8_t msg[160], digest[32]; size_t n = transcript(msg, v, h, c); roam_auth_digest(msg, n, digest);
    int ok = ECDSA_verify(0, digest, 32, sig, len, key) == 1; EC_KEY_free(key); return ok;
}
#endif

static int private_acl(int fd, int clear) {
#ifdef __APPLE__
    // Existing credentials fail closed on any extended ACL. Newly created storage
    // loses inherited entries before it can receive private material.
    acl_t acl = clear ? acl_init(0) : acl_get_fd_np(fd, ACL_TYPE_EXTENDED);
    if (!acl) return !clear && errno == ENOENT;
    int ok;
    if (clear) ok = !acl_set_fd_np(fd, acl, ACL_TYPE_EXTENDED);
    else {
        acl_entry_t entry;
        ok = !acl_valid(acl) && acl_get_entry(acl, ACL_FIRST_ENTRY, &entry) == -1 && errno == EINVAL;
    }
    int saved = errno; acl_free(acl); errno = saved; return ok;
#else
    (void)fd; (void)clear; return 1;
#endif
}
static int safe_file(int fd, int directory) {
    struct stat st;
    return !fstat(fd, &st) && st.st_uid == geteuid() && !(st.st_mode & 077) &&
           (directory ? S_ISDIR(st.st_mode) : S_ISREG(st.st_mode) && st.st_nlink == 1) && private_acl(fd, 0);
}
int roam_auth_load(RoamAuth *a, const char *userdata) {
    roam_auth_clear(a);
    if (!userdata || !*userdata) { errno = EINVAL; return 0; }
    int root = open(userdata, O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC);
    if (root < 0) return 0;
    int created = !mkdirat(root, "online-identity", 0700);
    if (!created && errno != EEXIST) { close(root); return 0; }
    int dir = openat(root, "online-identity", O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC); close(root);
    if (dir < 0) return 0;
    int fd = -1, ok = 0;
    if (created && !private_acl(dir, 1)) goto end;
    if (!safe_file(dir, 1)) { errno = EACCES; goto end; }
#ifdef __APPLE__
    // Exclude credentials from Time Machine as well as dump(8). No key is created on failure.
    CFDataRef excluded = CFPropertyListCreateData(NULL, CFSTR("com.apple.backupd"), kCFPropertyListBinaryFormat_v1_0, 0, NULL);
    int marked = excluded && !fsetxattr(dir, "com.apple.metadata:com_apple_backup_excludeItem", CFDataGetBytePtr(excluded), (size_t)CFDataGetLength(excluded), 0, 0) && !fchflags(dir, UF_NODUMP);
    if (excluded) CFRelease(excluded);
    if (!marked) goto end;
#endif
    fd = openat(dir, "key", O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC);
    if (fd >= 0) {
        uint8_t extra;
        if (!safe_file(fd, 0)) { errno = EACCES; goto end; }
        struct stat st;
        if (fstat(fd, &st) || st.st_size != 97 || read(fd, a->secret, 97) != 97 || read(fd, &extra, 1) != 0) { errno = EINVAL; goto end; }
#ifdef __APPLE__
        SecKeyRef key = import_key(a->secret, 1);
        if (!key) { errno = EINVAL; goto end; }
        // A private import must reproduce the saved public point.
        SecKeyRef pub = SecKeyCopyPublicKey(key); CFRelease(key);
        CFDataRef data = pub ? SecKeyCopyExternalRepresentation(pub, NULL) : NULL;
        if (pub) CFRelease(pub);
        int valid = data && CFDataGetLength(data) == 65 && !memcmp(CFDataGetBytePtr(data), a->secret, 65);
        if (data) CFRelease(data);
        if (!valid) { errno = EINVAL; goto end; }
#else
        EC_KEY *key = import_key(a->secret, 1);
        if (!key) { errno = EINVAL; goto end; } EC_KEY_free(key);
#endif
        a->ready = 1;
        uint8_t hello[ROAM_HELLO_BASE] = {0}, cookie[20] = {0}, sig[72], length;
        memcpy(hello+20, a->secret, 65);
        if (!roam_auth_sign(a, 0, hello, cookie, sig, &length) || !roam_auth_verify(0, hello, cookie, sig, length)) { errno = EINVAL; goto end; }
        ok = 1;
    } else if (errno == ENOENT) {
        if (!roam_auth_generate(a)) { errno = EIO; goto end; }
        // linkat publishes a fully flushed credential without replacing a concurrent writer.
        char temporary[64]; uint8_t digest[32]; char hex[65];
        roam_auth_digest(a->secret, 97, digest); roam_auth_hex(digest, hex);
        memcpy(temporary, hex, 32); memcpy(temporary+32, ".tmp", 5);
        fd = openat(dir, temporary, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC, 0600);
        if (fd < 0) goto end;
        ok = private_acl(fd, 1) && safe_file(fd, 0) && write(fd, a->secret, 97) == 97 && !fsync(fd);
        int collision = 0;
        if (ok && linkat(dir, temporary, dir, "key", 0)) { ok = 0; collision = errno == EEXIST; }
        int saved = ok ? 0 : errno;
        if (unlinkat(dir, temporary, 0)) { saved = errno; ok = collision = 0; }
        if (ok && fsync(dir)) { saved = errno; ok = 0; }
        if (collision) { close(fd); close(dir); roam_auth_clear(a); return roam_auth_load(a, userdata); }
        errno = saved ? saved : ok ? 0 : EIO;
    }
end: {
    int saved = errno; if (fd >= 0) close(fd); close(dir);
    if (!ok) roam_auth_clear(a);
    errno = saved; return ok;
    }
}
