#include "car_grip.h"
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#include <CommonCrypto/CommonDigest.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

_Static_assert(sizeof(float) == 4, "GRP1 requires 32-bit float storage");

#define MIX(a,b,c) do { \
    a-=b; a-=c; a^=c>>13; b-=c; b-=a; b^=a<<8; c-=a; c-=b; c^=b>>13; \
    a-=b; a-=c; a^=c>>12; b-=c; b-=a; b^=a<<16; c-=a; c-=b; c^=b>>5; \
    a-=b; a-=c; a^=c>>3; b-=c; b-=a; b^=a<<10; c-=a; c-=b; c^=b>>15; \
} while (0)
static uint32_t grip_key(const char *name) {
    unsigned char text[64]; size_t n=strlen(name);
    if (!n || n>=sizeof text) return 0;
    for (size_t i=0;i<n;++i) {
        unsigned char ch=(unsigned char)name[i];
        text[i]=ch>='A' && ch<='Z'?ch+32:ch;
    }
    uint32_t a=0x9E3779B9u,b=a,c=0xABCDEF00u;
    size_t i=0;
    for (;n-i>=12;i+=12) {
        uint32_t v[3]; memcpy(v,text+i,12); a+=v[0]; b+=v[1]; c+=v[2]; MIX(a,b,c);
    }
    c+=(uint32_t)n;
    for (size_t j=0;j<n-i;++j) {
        uint32_t v=text[i+j];
        if (j>=8) c+=v<<(8*(j-7));
        else if (j>=4) b+=v<<(8*(j-4));
        else a+=v<<(8*j);
    }
    MIX(a,b,c); return c;
}
#undef MIX

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
    return grip_key(s) != 0;
}

static int read_record(int fd, const char *source, const char *donor, int mode,
                       const char *sha256, uint64_t size, CarGrip *out) {
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
        word(bytes+12) != grip_key(source) || word(bytes+16) != grip_key(donor) ||
        word(bytes+20) != CAR_GRIP_SHARED || !word(bytes+24) ||
        word(bytes+24) > CAR_GRIP_MAX_TIERS || word(bytes+28) > 8 ||
        word(bytes+32) != CAR_GRIP_FIELDS || word(bytes+36)) return -1;
    if (sha256) {
        if (size != n) return -1;
        unsigned char digest[CC_SHA256_DIGEST_LENGTH]; char actual[65];
        CC_SHA256(bytes, (CC_LONG)n, digest);
        for (unsigned i=0;i<sizeof digest;++i) snprintf(actual+2*i,3,"%02x",digest[i]);
        if (strcmp(actual,sha256)) return -1;
    }
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

int car_grip_read(int fd, const char *source, const char *donor, int mode, CarGrip *out) {
    return read_record(fd,source,donor,mode,NULL,0,out);
}

int car_grip_read_pinned(int fd, const char *source, const char *donor, int mode,
                          const char *sha256, uint64_t size, CarGrip *out) {
    if (!sha256) {
        if (out) memset(out,0,sizeof *out);
        return -1;
    }
    return read_record(fd,source,donor,mode,sha256,size,out);
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

static int selected_key(uint32_t key) {
    return key == 0xAAFC01B3 || key == 0xF177BE1B || key == 0xEC096166 ||
           key == 0x64C43C4B || key == 0xC51EB1A1;
}

static const CarGripField *reference(const CarGrip *grip, unsigned tier, uint32_t key) {
    if (tier >= grip->tier_count || tier >= CAR_GRIP_MAX_TIERS) return NULL;
    for (unsigned i = 0; i < CAR_GRIP_FIELDS; ++i) {
        const CarGripField *f = &grip->tiers[tier].fields[i];
        if (f->key == key && shape(key, f->count, f->semantics)) return f;
    }
    return NULL;
}

void car_grip_bind(CarGripBinding *binding, const CarGrip *grip,
                   uint32_t key, uint32_t vault, uint64_t generation) {
    if (!binding) return;
    memset(binding, 0, sizeof *binding);
    if (!grip || !grip->present || !grip->source || !grip->donor ||
        !grip->tier_count || grip->tier_count > CAR_GRIP_MAX_TIERS ||
        !key || !vault || !generation) return;
    *binding = (CarGripBinding){CAR_GRIP_SELECTION_VERSION, key, vault, generation, grip};
}

int car_grip_select(const CarGripBinding *binding, const CarGripOwner *owner,
                    const CarGripLive *live, CarGripSelected *out) {
    if (!out) return 0;
    memset(out, 0, sizeof *out);
    if (!binding || !owner || !live || binding->version != CAR_GRIP_SELECTION_VERSION ||
        owner->version != CAR_GRIP_SELECTION_VERSION || !owner->simable ||
        owner->simable != owner->local_simable || !owner->key || !owner->vault ||
        !owner->generation || binding->key != owner->key || binding->vault != owner->vault ||
        binding->generation != owner->generation || !binding->grip) return 0;
    const CarGrip *g = binding->grip;
    if (!g->present || !g->tier_count || g->tier_count > CAR_GRIP_MAX_TIERS ||
        live->max_level < 0 || live->max_level > 8 || live->level < 0 ||
        live->level > live->max_level || (live->junkman != 0 && live->junkman != 1) ||
        live->scale_count > CAR_GRIP_FIELDS || ((live->junkman || live->scale_count) && !live->scales) ||
        (!live->junkman && live->scale_count)) return 0;
    for (size_t i = 0; i < live->scale_count; ++i) {
        if (!isfinite(live->scales[i].multiplier) || fabsf(live->scales[i].multiplier) > 1000000)
            return 0;
        for (size_t j = 0; j < i; ++j)
            if (live->scales[i].key == live->scales[j].key) return 0;
    }
    unsigned low = 0, high = 0;
    float blend = 0;
    if (live->level && g->tier_count > 1) {
        low = g->tier_count == 3 ? 1 : 0;
        high = g->tier_count == 3 ? 2 : 1;
        blend = (float)live->level / (float)live->max_level;
    }
    static const uint32_t keys[CAR_GRIP_SELECTED_FIELDS] = {
        0xAAFC01B3, 0xEC096166, 0xF177BE1B, 0x64C43C4B, 0xC51EB1A1
    };
    CarGripSelected candidate = {0};
    for (unsigned i = 0; i < CAR_GRIP_SELECTED_FIELDS; ++i) {
        const CarGripField *a = reference(g, low, keys[i]), *b = reference(g, high, keys[i]);
        if (!a || !b) return 0;
        CarGripField *f = &candidate.fields[i];
        f->key = keys[i]; f->semantics = a->semantics;
        /* Endpoints retain their actual counts; only an interior blend takes
         * the larger count. Missing elements are native default float zero. */
        f->count = blend == 0 ? a->count : blend == 1 ? b->count :
                   a->count > b->count ? a->count : b->count;
        float multiplier = 1;
        for (size_t j = 0; j < live->scale_count; ++j)
            if (live->scales[j].key == f->key) multiplier = live->scales[j].multiplier;
        for (unsigned j = 0; j < f->count; ++j) {
            float av = j < a->count ? a->values[j] : 0;
            float bv = j < b->count ? b->values[j] : 0;
            float value = blend == 0 ? av : blend == 1 ? bv : bv * blend + (1 - blend) * av;
            value *= multiplier;
            if (!isfinite(value)) return 0;
            f->values[j] = value;
        }
    }
    *out = candidate;
    return 1;
}

const CarGripField *car_grip_selected_field(const CarGripSelected *selected, uint32_t key) {
    if (!selected || !selected_key(key)) return NULL;
    for (unsigned i = 0; i < CAR_GRIP_SELECTED_FIELDS; ++i)
        if (selected->fields[i].key == key && selected->fields[i].count &&
            selected->fields[i].count <= CAR_GRIP_MAX_VALUES) return &selected->fields[i];
    return NULL;
}
