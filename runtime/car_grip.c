#include "car_grip.h"
#include "car_physics.h"
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

_Static_assert(sizeof(float) == 4, "GRP1 requires 32-bit float storage");

enum { HEADER = 40, MAX_BYTES = HEADER + CAR_GRIP_MAX_TIERS *
       (8 + CAR_GRIP_FIELDS * (16 + CAR_GRIP_MAX_VALUES * 4)) };
static int shape(uint32_t key, uint32_t count, uint32_t semantics) {
    uint32_t expected = 0, fixed = 0, limit = 1;
    switch (key) {
        case 0x5161D2EE: case 0xAAFC01B3: case 0xB698BE9F:
        case 0xDEFC0B82: case 0xEC096166: case 0xF177BE1B:
            expected = CAR_GRIP_SHARED; fixed = 2; limit = 2; break;
        case 0x64C43C4B:
            expected = CAR_GRIP_SHARED; fixed = 4; limit = 4; break;
        case 0xC2094707: case 0xFEF5CC35:
            expected = CAR_GRIP_SHARED; fixed = 1; break;
        case 0x0D255C03: case 0x7DB55397: case 0xC141848A: case 0xECEFA5B3:
            fixed = 1; break;
        case 0x7CAFC471: case 0xC51EB1A1: case 0xF4BBE085:
            limit = CAR_GRIP_MAX_VALUES; break;
        case 0xD15B2BDE:
            limit = 10; break;
        default: return 0;
    }
    return semantics == expected && count && count <= limit && (!fixed || count == fixed);
}

static uint32_t word(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static int identity(const char *s) {
    if (!s || !*s) return 0;
    size_t n = 0;
    for (; s[n]; ++n) if (n >= 63 || (unsigned char)s[n] > 127) return 0;
    return car_physics_key(s) != 0;
}

int car_grip_read(int fd, const char *source, const char *donor, int mode, CarGrip *out) {
    if (!out) return -1;
    memset(out, 0, sizeof *out);
    if (mode == CAR_GRIP_DONOR) return 0;
    if (mode != CAR_GRIP_SHARED || !identity(source) || !identity(donor)) return -1;
    struct stat st;
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < HEADER ||
        st.st_size > MAX_BYTES || lseek(fd, 0, SEEK_SET) < 0) return -1;
    unsigned char bytes[MAX_BYTES + 1]; size_t n = 0;
    while (n < sizeof bytes) {
        ssize_t got = read(fd, bytes + n, sizeof bytes - n);
        if (got < 0 && errno == EINTR) continue;
        if (got < 0) return -1;
        if (!got) break;
        n += (size_t)got;
    }
    if (n != (size_t)st.st_size || n < HEADER || word(bytes) != 0x31505247 ||
        word(bytes+4) != 1 || word(bytes+8) != n ||
        word(bytes+12) != car_physics_key(source) || word(bytes+16) != car_physics_key(donor) ||
        word(bytes+20) != CAR_GRIP_SHARED || !word(bytes+24) ||
        word(bytes+24) > CAR_GRIP_MAX_TIERS || word(bytes+28) > 8 ||
        word(bytes+32) != CAR_GRIP_FIELDS || word(bytes+36)) return -1;
    CarGrip candidate = {0};
    candidate.source = word(bytes+12); candidate.donor = word(bytes+16);
    candidate.tier_count = word(bytes+24); candidate.upgrade_max = word(bytes+28);
    size_t pos = HEADER;
    for (unsigned t = 0; t < candidate.tier_count; ++t) {
        if (n-pos < 8 || word(bytes+pos) != t || !word(bytes+pos+4)) return -1;
        candidate.tiers[t].key = word(bytes+pos+4); pos += 8;
        for (unsigned f = 0; f < CAR_GRIP_FIELDS; ++f) {
            if (n-pos < 16) return -1;
            uint32_t key = word(bytes+pos), semantics = word(bytes+pos+4), count = word(bytes+pos+8);
            if (word(bytes+pos+12)) return -1;
            if (!shape(key, count, semantics)) return -1;
            for (unsigned i = 0; i < f; ++i)
                if (candidate.tiers[t].fields[i].key == key) return -1;
            pos += 16;
            if (count > (n-pos)/4) return -1;
            CarGripField *field = &candidate.tiers[t].fields[f];
            field->key = key; field->semantics = semantics; field->count = count;
            for (unsigned j = 0; j < count; ++j) {
                uint32_t bits = word(bytes+pos); float value; memcpy(&value, &bits, 4); pos += 4;
                if (!isfinite(value) || fabsf(value) > 1000000) return -1;
                field->values[j] = value;
            }
        }
    }
    if (pos != n) return -1;
    candidate.present = 1; *out = candidate; return 0;
}

int car_grip_load(const char *path, const char *source, const char *donor, int mode, CarGrip *out) {
    if (!out) return -1;
    memset(out, 0, sizeof *out);
    if (mode == CAR_GRIP_DONOR) return 0;
    if (mode != CAR_GRIP_SHARED || !identity(source) || !identity(donor)) return -1;
    if (!path) return 0;
    int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return errno == ENOENT ? 0 : -1;
    int rc = car_grip_read(fd, source, donor, mode, out); close(fd); return rc;
}

const CarGripField *car_grip_field(const CarGrip *grip, unsigned tier, uint32_t key) {
    if (!grip || !grip->present || tier >= grip->tier_count || tier >= CAR_GRIP_MAX_TIERS) return NULL;
    for (unsigned i = 0; i < CAR_GRIP_FIELDS; ++i) {
        const CarGripField *f = &grip->tiers[tier].fields[i];
        if (f->key == key && f->semantics == CAR_GRIP_SHARED) return f;
    }
    return NULL;
}
