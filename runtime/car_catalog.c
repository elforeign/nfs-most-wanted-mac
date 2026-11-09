/* Public catalog reader: local bytes are evidence, compiled policy is authority. */
#include "car_catalog.h"
#define COMMON_DIGEST_FOR_OPENSSL 0
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#include <CommonCrypto/CommonDigest.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#ifdef CAR_CATALOG_APPROVAL_HEADER
#include CAR_CATALOG_APPROVAL_HEADER
#elif defined(NFS_PUBLIC)
#include "car_approvals_public.h"
#endif
#ifndef CAR_CATALOG_POLICY_JSON
#define CAR_CATALOG_POLICY_JSON ""
#define CAR_CATALOG_POLICY_SHA256 ""
#endif

#define JSON_LIMIT (4u << 20)
#define FILE_LIMIT (UINT64_C(2) << 30)
#define DIR_FLAGS (O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
#define FILE_FLAGS (O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
#define NEED(test, why) do { if (!(test)) return bad(why); } while (0)

typedef struct { size_t begin, end; int next, count; char type; } Token;
typedef struct { char *s; size_t size, pos, capacity; Token *t; int n; } Json;
typedef struct { int fd; uint64_t size; char hash[65]; } Retained;
static CarCatalogSlot inventory[CAR_CATALOG_SLOTS];
static Retained retained[CAR_CATALOG_SLOTS][CAR_CATALOG_OUTPUTS];
static size_t nslots, nselected;
static int initialized, result;
static char generation[67], compatibility[65], problem[512];
static const char *const output_names[CAR_CATALOG_OUTPUTS] = {
    "GEOMETRY.BIN", "TEXTURES.BIN", "SOLIDMAP.BIN", "WHEELFIT.BIN",
    "COLLISION.BIN", "PAINT.BIN", "PHYSICS.BIN"
};
static int bad(const char *why) {
    if (!problem[0]) snprintf(problem, sizeof problem, "%s", why);
    return -1;
}
static int io_bad(const char *what) {
    char message[400]; snprintf(message, sizeof message, "%s: %s", what, strerror(errno));
    return bad(message);
}
static void slot_error(unsigned slot, const char *area) {
    char message[512];
    snprintf(message,sizeof message,"slot %u %s: %.400s",slot,area,problem);
    snprintf(problem,sizeof problem,"%s",message);
}
static int hex(const char *s) {
    if (strlen(s) != 64) return 0;
    for (; *s; ++s) if (!(*s >= '0' && *s <= '9') && !(*s >= 'a' && *s <= 'f')) return 0;
    return 1;
}
static int token_word(const char *s) {
    size_t n = strlen(s);
    if (!n || n > 31) return 0;
    for (; *s; ++s) if (!(*s >= 'A' && *s <= 'Z') && !(*s >= '0' && *s <= '9') && *s != '_') return 0;
    return 1;
}
static void hash_end(CC_SHA256_CTX *ctx, char out[65]) {
    unsigned char h[32]; CC_SHA256_Final(h, ctx);
    for (size_t i = 0; i < 32; ++i) snprintf(out + 2*i, 3, "%02x", h[i]);
}
static void hash_bytes(const void *bytes, size_t n, char out[65]) {
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx); CC_SHA256_Update(&ctx, bytes, (CC_LONG)n); hash_end(&ctx, out);
}
static void hash_text(CC_SHA256_CTX *ctx, const char *s) { CC_SHA256_Update(ctx, s, (CC_LONG)strlen(s)); }
static int digit(unsigned char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}
static unsigned key_char(Json *j, size_t *i) {
    unsigned c=(unsigned char)j->s[(*i)++];
    if (c=='\\') c=(unsigned char)j->s[(*i)++];
    return c;
}
static int key_order(Json *j, Token *a, Token *b) {
    size_t i=a->begin+1,k=b->begin+1;
    while (i+1<a->end && k+1<b->end) {
        unsigned x=key_char(j,&i),y=key_char(j,&k);
        if (x!=y) return x<y ? -1:1;
    }
    return i+1==a->end ? (k+1==b->end ? 0:-1):1;
}
/* Canonical ASCII JSON, with Python ensure_ascii spelling. No field in v1
 * permits floating point or booleans. Keys must be printable ASCII and sorted;
 * this rejects duplicate keys before any semantic lookup or hashing. */
static int parse(Json *j, unsigned depth) {
    if (depth > 32 || j->pos >= j->size || (size_t)j->n >= j->capacity) return -1;
    int at = j->n++;
    Token *t = &j->t[at]; t->begin = j->pos; t->type = j->s[j->pos++];
    if (t->type == '"') {
        int closed = 0;
        while (j->pos < j->size) {
            unsigned char c = j->s[j->pos++];
            if (c == '"') { closed = 1; break; }
            if (c < 32 || c >= 127) return -1;
            if (c != '\\') continue;
            if (j->pos >= j->size) return -1;
            c = j->s[j->pos++];
            if (c == 'u') {
                unsigned value = 0;
                for (int i = 0; i < 4; ++i) {
                    if (j->pos >= j->size) return -1;
                    int d = digit((unsigned char)j->s[j->pos++]); if (d < 0) return -1;
                    value = value * 16 + (unsigned)d;
                }
                if ((value >= 32 && value < 127) || value == 8 || value == 9 || value == 10 || value == 12 || value == 13) return -1;
            } else if (!strchr("\"\\bfnrt", c)) return -1;
        }
        if (!closed) return -1;
    } else if (t->type == '{' || t->type == '[') {
        char close = t->type == '{' ? '}' : ']';
        if (j->pos < j->size && j->s[j->pos] == close) ++j->pos;
        else {
            int previous = -1;
            for (;;) {
                if (t->type == '{') {
                    int key = j->n;
                    if (j->pos >= j->size || j->s[j->pos] != '"' || parse(j, depth + 1) < 0) return -1;
                    Token *k = &j->t[key];
                    for (size_t i = k->begin + 1; i + 1 < k->end; ++i) if (j->s[i] == '\\') {
                        if (j->s[i+1]!='"' && j->s[i+1]!='\\') return -1;
                        ++i;
                    }
                    if (previous >= 0) {
                        if (key_order(j,&j->t[previous],k)>=0) return -1;
                    }
                    previous = key;
                    if (j->pos >= j->size || j->s[j->pos++] != ':') return -1;
                }
                if (parse(j, depth + 1) < 0) return -1;
                ++t->count;
                if (j->pos >= j->size) return -1;
                char c = j->s[j->pos++]; if (c == close) break;
                if (c != ',') return -1;
            }
        }
    } else if (t->type == 'n') {
        if (j->pos + 3 > j->size || memcmp(j->s + j->pos, "ull", 3)) return -1;
        j->pos += 3;
    } else if (t->type >= '0' && t->type <= '9') {
        if (t->type != '0') while (j->pos < j->size && j->s[j->pos] >= '0' && j->s[j->pos] <= '9') ++j->pos;
    } else return -1;
    t->end = j->pos; t->next = j->n; return at;
}
static void json_free(Json *j) { free(j->s); free(j->t); memset(j, 0, sizeof *j); }
static int json_decode(Json *j, const void *s, size_t n) {
    NEED(n && n <= JSON_LIMIT, "JSON size exceeds 4 MiB or is empty");
    j->s = malloc(n + 1); j->capacity = n / 2 + 2; j->t = calloc(j->capacity, sizeof *j->t);
    NEED(j->s && j->t, "cannot allocate bounded JSON parser");
    memcpy(j->s, s, n); j->s[n] = 0; j->size = n;
    NEED(parse(j, 0) == 0 && j->t[0].type == '{' && j->pos + 1 == n && j->s[j->pos] == '\n',
         "invalid canonical JSON (ordering, duplicate key, type or spelling)");
    return 0;
}
static int equal(Json *j, int at, const char *s) {
    return at >= 0 && j->t[at].type == '"' && j->t[at].end-j->t[at].begin == strlen(s)+2 &&
        !memcmp(j->s+j->t[at].begin+1, s, strlen(s));
}
static int field(Json *j, int at, const char *key) {
    if (at < 0 || j->t[at].type != '{') return -1;
    for (int k = at+1; k < j->t[at].next; k = j->t[k+1].next) if (equal(j, k, key)) return k+1;
    return -1;
}
static int fields(Json *j, int at, const char *const *names, size_t count) {
    if (at < 0 || j->t[at].type != '{' || j->t[at].count != (int)count) return 0;
    for (size_t i = 0; i < count; ++i) if (field(j, at, names[i]) < 0) return 0;
    return 1;
}
#define FIELDS(j, at, ...) fields((j), (at), (const char *const[]){__VA_ARGS__}, sizeof((const char *const[]){__VA_ARGS__})/sizeof(char *))
static int number(Json *j, int at, uint64_t max, uint64_t *out) {
    if (at < 0 || j->t[at].type < '0' || j->t[at].type > '9') return -1;
    uint64_t v = 0;
    for (size_t i = j->t[at].begin; i < j->t[at].end; ++i) {
        unsigned d = (unsigned)(j->s[i]-'0');
        if (d > max || v > (max-d)/10) return -1;
        v = v*10+d;
    }
    *out = v; return 0;
}
static int integer(Json *j, int at, uint64_t expected) {
    uint64_t v; return !number(j, at, expected, &v) && v == expected;
}
static int null(Json *j, int at) { return at >= 0 && j->t[at].type == 'n'; }
/* Decode canonical strings to UTF-8; validate text by Unicode code point. */
static int string(Json *j, int at, char *out, size_t cap, int display) {
    if (at < 0 || j->t[at].type != '"') return -1;
    size_t used = 0, count = 0;
    for (size_t i = j->t[at].begin+1; i+1 < j->t[at].end; ++i) {
        unsigned c = (unsigned char)j->s[i];
        if (c == '\\') {
            c = (unsigned char)j->s[++i];
            if (c == 'u') {
                c = 0; for (int k = 0; k < 4; ++k) c = c*16+(unsigned)digit((unsigned char)j->s[++i]);
                if (c >= 0xd800 && c <= 0xdbff && i+6 < j->t[at].end && j->s[i+1] == '\\' && j->s[i+2] == 'u') {
                    unsigned low = 0;
                    for (int k = 3; k <= 6; ++k) low = low*16+(unsigned)digit((unsigned char)j->s[i+k]);
                    if (low >= 0xdc00 && low <= 0xdfff) { c = 0x10000+(c-0xd800)*1024+low-0xdc00; i += 6; }
                }
            } else {
                switch (c) { case 'b': c=8; break; case 'f': c=12; break; case 'n': c=10; break; case 'r': c=13; break; case 't': c=9; break; default: break; }
            }
        }
        if ((c >= 0xd800 && c <= 0xdfff) || !c || (display ? (c < 32 || (c >= 127 && c <= 159)) : c >= 127)) return -1;
        unsigned char bytes[4]; size_t n;
        if (c < 128) { bytes[0]=(unsigned char)c; n=1; }
        else if (c < 2048) { bytes[0]=0xc0|(c>>6); bytes[1]=0x80|(c&63); n=2; }
        else if (c < 65536) { bytes[0]=0xe0|(c>>12); bytes[1]=0x80|((c>>6)&63); bytes[2]=0x80|(c&63); n=3; }
        else { bytes[0]=0xf0|(c>>18); bytes[1]=0x80|((c>>12)&63); bytes[2]=0x80|((c>>6)&63); bytes[3]=0x80|(c&63); n=4; }
        if (used+n >= cap) return -1;
        memcpy(out+used, bytes, n); used += n; ++count;
    }
    out[used]=0;
    return display && (!count || count > 128) ? -1 : 0;
}
static int hash_field(Json *j, int at, const char *key, char out[65]) {
    return string(j, field(j, at, key), out, 65, 0) || !hex(out) ? -1 : 0;
}
static void hash_token(CC_SHA256_CTX *ctx, Json *j, int at) {
    CC_SHA256_Update(ctx, j->s+j->t[at].begin, (CC_LONG)(j->t[at].end-j->t[at].begin));
}
static void object_hash(Json *j, int obj, const char *const *keys, size_t count, char out[65]) {
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx); hash_text(&ctx, "{");
    for (size_t i=0; i<count; ++i) {
        if (i) hash_text(&ctx, ",");
        hash_text(&ctx, "\""); hash_text(&ctx, keys[i]); hash_text(&ctx, "\":"); hash_token(&ctx, j, field(j,obj,keys[i]));
    }
    hash_text(&ctx, "}\n"); hash_end(&ctx, out);
}
static void row_hash(Json *j, int row, char out[65]) {
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx); hash_token(&ctx, j, row); hash_text(&ctx, "\n"); hash_end(&ctx, out);
}
static int relative(const char *s) {
    size_t n = strlen(s); if (!n || n > 512) return 0;
    const char *part = s;
    for (const char *p=s;;++p) {
        unsigned char c = (unsigned char)*p;
        if (c == '/' || !c) {
            size_t z = (size_t)(p-part);
            if (!z || (z == 1 && part[0] == '.') || (z == 2 && part[0] == '.' && part[1] == '.')) return 0;
            if (!c) break;
            part=p+1;
        } else if (c < 32 || c >= 127 || c == '\\') return 0;
    }
    return 1;
}
static int open_relative(int base, const char *path, int directory) {
    if (!relative(path)) { errno=EINVAL; return -1; }
    char text[513]; memcpy(text,path,strlen(path)+1);
    int fd=dup(base); if (fd<0) return -1;
    char *part=text;
    for (;;) {
        char *slash=strchr(part,'/'); if (slash) *slash=0;
        int next=openat(fd,part,(slash || directory) ? DIR_FLAGS : FILE_FLAGS);
        if (next<0) { int error=errno; close(fd); errno=error; return -1; }
        close(fd);
        fd=next; if (!slash) return fd;
        part=slash+1;
    }
}
static int root_open(const char *path) {
    if (!path || !*path || strlen(path) > 4095) { errno=EINVAL; return -1; }
    int fd=open(path[0]=='/' ? "/" : ".",DIR_FLAGS); if (fd<0) return -1;
    const char *part=path+(path[0]=='/');
    while (*part) {
        const char *slash=strchr(part,'/'); size_t n=slash ? (size_t)(slash-part) : strlen(part);
        if (!n || n>255 || (n==1 && part[0]=='.') || (n==2 && !memcmp(part,"..",2))) { close(fd); errno=EINVAL; return -1; }
        char name[256]; memcpy(name,part,n); name[n]=0;
        for (size_t i=0;i<n;++i) if ((unsigned char)name[i]<32 || name[i]=='\\') { close(fd); errno=EINVAL; return -1; }
        int next=openat(fd,name,DIR_FLAGS);
        if (next<0) { int error=errno; close(fd); errno=error; return -1; }
        close(fd);
        fd=next; if (!slash) return fd; part=slash+1;
        if (!*part) { close(fd); errno=EINVAL; return -1; }
    }
    return fd;
}
static int safe_stat(int fd, struct stat *st, uint64_t limit) {
    return fstat(fd,st) || !S_ISREG(st->st_mode) || st->st_nlink!=1 || st->st_size<0 || (uint64_t)st->st_size>limit ? -1:0;
}
static int unchanged(const struct stat *a, const struct stat *b) {
    return a->st_dev==b->st_dev && a->st_ino==b->st_ino && a->st_size==b->st_size && a->st_nlink==b->st_nlink &&
        a->st_mtimespec.tv_sec==b->st_mtimespec.tv_sec && a->st_mtimespec.tv_nsec==b->st_mtimespec.tv_nsec &&
        a->st_ctimespec.tv_sec==b->st_ctimespec.tv_sec && a->st_ctimespec.tv_nsec==b->st_ctimespec.tv_nsec;
}
static int json_read(int base, const char *name, Json *j) {
    int fd=open_relative(base,name,0); if (fd<0) return io_bad("cannot open catalog JSON");
    struct stat before,after;
    if (safe_stat(fd,&before,JSON_LIMIT)) { close(fd); return bad("unsafe or oversized catalog JSON"); }
    size_t n=(size_t)before.st_size, used=0; char *bytes=malloc(n+1);
    if (!bytes) { close(fd); return bad("cannot allocate JSON bytes"); }
    while (used<n) {
        ssize_t got=read(fd,bytes+used,n-used);
        if (got<0 && errno==EINTR) continue;
        if (got<=0) break;
        used+=(size_t)got;
    }
    int valid=used==n && !fstat(fd,&after) && unchanged(&before,&after); close(fd);
    int rc=valid ? json_decode(j,bytes,n) : bad("catalog JSON changed during read"); free(bytes); return rc;
}

static int policy_load(Json *p, int slots[CAR_CATALOG_SLOTS], char digest[65]) {
    const char *bytes=CAR_CATALOG_POLICY_JSON, *expected=CAR_CATALOG_POLICY_SHA256;
    NEED(*bytes && hex(expected), "no packaged approvals; install a package with a reviewed approval policy");
    size_t size=strnlen(bytes,JSON_LIMIT+1); NEED(size<=JSON_LIMIT,"compiled approval policy exceeds JSON limit");
    hash_bytes(bytes,size,digest); NEED(!strcmp(digest,expected),"compiled approval policy digest mismatch");
    if (json_decode(p,bytes,size)) return -1;
    NEED(FIELDS(p,0,"schema","revision","slots") && integer(p,field(p,0,"schema"),1),"unsupported approval policy schema");
    uint64_t revision;
    NEED(!number(p,field(p,0,"revision"),2147483647,&revision) && revision,"invalid approval policy revision");
    int array=field(p,0,"slots");
    NEED(p->t[array].type=='[' && p->t[array].count==CAR_CATALOG_SLOTS,"approval policy requires 90 reservations");
    char identities[CAR_CATALOG_SLOTS][65]={{0}};
    int at=array+1;
    for (unsigned s=0;s<CAR_CATALOG_SLOTS;++s) {
        slots[s]=at;
        NEED(FIELDS(p,at,"slot","identity","variants") && integer(p,field(p,at,"slot"),s),"reordered approval policy slot");
        int id=field(p,at,"identity"), variants=field(p,at,"variants");
        NEED(p->t[variants].type=='[' && p->t[variants].count<=256,"invalid approval variant list");
        if (null(p,id)) NEED(!p->t[variants].count,"approval without identity reservation");
        else {
            NEED(!hash_field(p,at,"identity",identities[s]),"invalid approval identity");
            for (unsigned k=0;k<s;++k) NEED(strcmp(identities[k],identities[s]),"duplicate approval identity");
        }
        char contracts[256][65]; unsigned used=0;
        for (int v=variants+1;v<p->t[variants].next;v=p->t[v].next) {
            char c[65];
            NEED(FIELDS(p,v,"contract","compatibility") && !hash_field(p,v,"contract",contracts[used]) &&
                 !hash_field(p,v,"compatibility",c),"invalid approval variant");
            for (unsigned k=0;k<used;++k) NEED(strcmp(contracts[k],contracts[used]),"duplicate approval variant");
            ++used;
        }
        at=p->t[at].next;
    }
    return 0;
}
static int approved(Json *p, int slot, const char *identity, const char *contract, const char *signature) {
    char id[65];
    if (hash_field(p,slot,"identity",id) || strcmp(id,identity)) return 0;
    int variants=field(p,slot,"variants");
    for (int v=variants+1;v<p->t[variants].next;v=p->t[v].next) {
        char a[65],b[65];
        if (!hash_field(p,v,"contract",a) && !hash_field(p,v,"compatibility",b) && !strcmp(a,contract) && !strcmp(b,signature)) return 1;
    }
    return 0;
}
static int compare_words(const void *a, const void *b) { return strcmp(a,b); }
static int compare_folded(const void *a, const void *b) { return strcasecmp(*(const char *const *)a,*(const char *const *)b); }
static int pin(Json *j, int at, uint64_t *size, char digest[65]) {
    return FIELDS(j,at,"size","sha256") && !number(j,field(j,at,"size"),FILE_LIMIT,size) &&
        !hash_field(j,at,"sha256",digest) ? 0 : -1;
}
static int pins(Json *j, int at) {
    NEED(at>=0 && j->t[at].type=='{' && j->t[at].count>0 && j->t[at].count<=1024,"missing or excessive file pins");
    size_t n=(size_t)j->t[at].count;
    char (*names)[513]=calloc(n,sizeof *names); const char **sorted=calloc(n,sizeof *sorted);
    if (!names || !sorted) { free(names); free(sorted); return bad("cannot allocate file pin validation"); }
    size_t used=0; int rc=0;
    for (int k=at+1;k<j->t[at].next;k=j->t[k+1].next) {
        uint64_t size; char hash[65];
        if (string(j,k,names[used],513,0) || !relative(names[used]) || pin(j,k+1,&size,hash)) { rc=bad("unsafe input path or invalid file pin"); break; }
        sorted[used]=names[used]; ++used;
    }
    if (!rc) {
        qsort(sorted,n,sizeof *sorted,compare_folded);
        for (size_t k=1;k<n;++k) if (!strcasecmp(sorted[k-1],sorted[k])) { rc=bad("case-colliding file pins"); break; }
    }
    free(names); free(sorted); return rc;
}
static int binding(Json *j, int at, const char *key, char out[32]) {
    return string(j,field(j,at,key),out,32,0) || !token_word(out) ? -1 : 0;
}
static int parts_load(Json *j, int at, CarCatalogSlot *slot) {
    NEED(at>=0 && j->t[at].type=='[' && j->t[at].count>0 && j->t[at].count<=255,"missing or excessive part types");
    size_t count=0;
    for (int p=at+1;p<j->t[at].next;p=j->t[p].next) {
        NEED(FIELDS(j,p,"type","keys"),"invalid part fields");
        int k=field(j,p,"keys");
        NEED(j->t[k].type=='[' && j->t[k].count>0 && (size_t)j->t[k].count<=32768-count,"flattened part index exceeds 32767 or empty part type");
        count+=(size_t)j->t[k].count;
    }
    CarCatalogPart *parts=calloc((size_t)j->t[at].count,sizeof *parts);
    char (*keys)[32]=calloc(count,sizeof *keys), (*sorted)[32]=calloc(count,sizeof *sorted);
    if (!parts || !keys || !sorted) { free(parts); free(keys); free(sorted); return bad("cannot allocate part semantics"); }
    slot->parts=parts; slot->part_keys=(const char (*)[32])keys;
    unsigned types[255]={0}; size_t index=0,flat=0; int rc=0;
    for (int p=at+1;p<j->t[at].next;p=j->t[p].next) {
        uint64_t type;
        if (number(j,field(j,p,"type"),254,&type) || types[type]++) { rc=bad("invalid or duplicate part type"); break; }
        int k=field(j,p,"keys");
        parts[index].type=(unsigned)type; parts[index].first=flat; parts[index].count=(size_t)j->t[k].count; ++index;
        for (int v=k+1;v<j->t[k].next;v=j->t[v].next) {
            if (string(j,v,keys[flat],32,0) || !token_word(keys[flat])) { rc=bad("invalid part key"); break; }
            ++flat;
        }
        if (rc) break;
    }
    if (!rc) {
        memcpy(sorted,keys,count*sizeof *keys); qsort(sorted,count,sizeof *sorted,compare_words);
        for (size_t i=1;i<count;++i) if (!strcmp(sorted[i-1],sorted[i])) { rc=bad("duplicate flattened part key"); break; }
    }
    free(sorted); slot->part_count=index; slot->part_key_count=flat; return rc;
}
static int row_load(Json *j, int at, unsigned s, Json *policy, int approval) {
    CarCatalogSlot *out=&inventory[s]; out->slot=s; out->id=s<76 ? 84+s : 0; out->swap_index=s<76 ? -1 : (int)s-76;
    NEED(integer(j,field(j,at,"slot"),s) && integer(j,field(j,at,"id"),out->id) &&
         equal(j,field(j,at,"role"),s<76 ? "addition":"swap") &&
         (s<76 ? null(j,field(j,at,"swap_index")) : integer(j,field(j,at,"swap_index"),s-76)),"duplicate, reordered or reassigned catalog slot/ID");
    if (equal(j,field(j,at,"state"),"absent")) {
        NEED(FIELDS(j,at,"slot","id","role","swap_index","state"),"unexpected fields in absent reservation"); return 0;
    }
    NEED(s<76 && equal(j,field(j,at,"state"),"ready") && FIELDS(j,at,"slot","id","role","swap_index","state",
        "identity","origin","display","bindings","donors","parts","converter","inputs","outputs"),"unsupported state, swap import or unexpected ready fields");
    int origin=field(j,at,"origin"), display=field(j,at,"display"), bindings=field(j,at,"bindings"), donors=field(j,at,"donors");
    NEED(FIELDS(j,origin,"game","platform","folder") && equal(j,field(j,origin,"game"),"carbon") &&
         (equal(j,field(j,origin,"platform"),"pc") || equal(j,field(j,origin,"platform"),"x360")) &&
         !binding(j,origin,"folder",out->source_folder),"unsupported source game/platform/folder");
    strcpy(out->source_game,"carbon");
    NEED(!string(j,field(j,origin,"platform"),out->source_platform,sizeof out->source_platform,0),"invalid source platform");
    static const char *const identity_keys[]={"folder","game"}; char identity[65];
    object_hash(j,origin,identity_keys,2,identity);
    NEED(!hash_field(j,at,"identity",out->identity) && !strcmp(identity,out->identity),"source identity mismatch");
    NEED(FIELDS(j,display,"name","manufacturer") && !string(j,field(j,display,"name"),out->display_name,sizeof out->display_name,1) &&
         !string(j,field(j,display,"manufacturer"),out->manufacturer,sizeof out->manufacturer,1),"invalid display name or manufacturer");
    NEED(FIELDS(j,bindings,"folder","pvehicle","frontend","ecar") && !binding(j,bindings,"folder",out->folder) &&
         !binding(j,bindings,"pvehicle",out->pvehicle) && !binding(j,bindings,"frontend",out->frontend) && !binding(j,bindings,"ecar",out->ecar),"invalid runtime bindings");
    NEED(FIELDS(j,donors,"model","price") && !binding(j,donors,"model",out->model_donor) && !binding(j,donors,"price",out->price_donor),"invalid donor bindings");
    if (parts_load(j,field(j,at,"parts"),out)) return -1;
    int converter=field(j,at,"converter"); uint64_t revision;
    NEED(FIELDS(j,converter,"revision","policy") && !number(j,field(j,converter,"revision"),2147483647,&revision) && revision &&
         !hash_field(j,converter,"policy",out->converter_policy),"invalid converter revision or policy");
    out->converter_revision=(unsigned)revision;
    int inputs=field(j,at,"inputs");
    NEED(FIELDS(j,inputs,"source","dependencies","donor","converter"),"incomplete input groups");
    const char *const groups[]={"source","dependencies","donor","converter"};
    for (unsigned i=0;i<4;++i) if (pins(j,field(j,inputs,groups[i]))) return -1;
    int outputs=field(j,at,"outputs");
    NEED(outputs>=0 && j->t[outputs].type=='{' && j->t[outputs].count>=3 && j->t[outputs].count<=CAR_CATALOG_OUTPUTS,"invalid output inventory");
    for (int k=outputs+1;k<j->t[outputs].next;k=j->t[k+1].next) {
        unsigned kind;
        for (kind=0;kind<CAR_CATALOG_OUTPUTS;++kind) if (equal(j,k,output_names[kind])) break;
        NEED(kind<CAR_CATALOG_OUTPUTS,"unknown output type; a supported sidecar validator is required");
        CarCatalogOutput *o=&out->outputs[kind];
        NEED(!pin(j,k+1,&o->size,o->sha256),"invalid output size/hash pin"); o->present=1;
    }
    NEED(out->outputs[0].present && out->outputs[1].present && out->outputs[2].present,"missing required geometry, textures or solid map");
    static const char *const signature_keys[]={"bindings","donors","id","identity","parts","role","slot","swap_index"};
    object_hash(j,at,signature_keys,8,out->compatibility);
    char contract[65]; row_hash(j,at,contract);
    NEED(approved(policy,approval,out->identity,contract,out->compatibility),"conversion contract is not approved by packaged policy; reimport an approved variant");
    for (unsigned i=0;i<s;++i) if (inventory[i].ready) {
        CarCatalogSlot *other=&inventory[i];
        NEED(strcmp(other->identity,out->identity),"duplicate source identity");
        NEED(strcmp(other->folder,out->folder) && strcmp(other->pvehicle,out->pvehicle) &&
             strcmp(other->frontend,out->frontend) && strcmp(other->ecar,out->ecar),"duplicate runtime binding");
    }
    out->ready=1; return 0;
}

static uint32_t word32(const unsigned char *p) {
    return (uint32_t)p[0] | (uint32_t)p[1]<<8 | (uint32_t)p[2]<<16 | (uint32_t)p[3]<<24;
}
static int16_t signed16(const unsigned char *p) { return (int16_t)((unsigned)p[0] | (unsigned)p[1]<<8); }
static float real32(const unsigned char *p) { uint32_t u=word32(p); float f; memcpy(&f,&u,4); return f; }
static uint32_t name_key(const char *s) {
    uint32_t h=UINT32_MAX; for (;*s;++s) h=h*33+(unsigned char)*s; return h;
}
#define MIX(a,b,c) do { \
    a-=b; a-=c; a^=c>>13; b-=c; b-=a; b^=a<<8; c-=a; c-=b; c^=b>>13; \
    a-=b; a-=c; a^=c>>12; b-=c; b-=a; b^=a<<16; c-=a; c-=b; c^=b>>5; \
    a-=b; a-=c; a^=c>>3; b-=c; b-=a; b^=a<<10; c-=a; c-=b; c^=b>>15; \
} while (0)
static uint32_t attribute_key_case(const char *s, int lower) {
    unsigned char text[32]; size_t n=strlen(s),i=0;
    for (size_t k=0;k<n;++k) text[k]=(unsigned char)(lower && s[k]>='A' && s[k]<='Z' ? s[k]+32:s[k]);
    uint32_t a=0x9e3779b9,b=a,c=0xabcdef00;
    for (;n-i>=12;i+=12) { a+=word32(text+i); b+=word32(text+i+4); c+=word32(text+i+8); MIX(a,b,c); }
    c+=(uint32_t)n;
    for (size_t k=0;k<n-i;++k) {
        uint32_t v=text[i+k];
        if (k>=8) c+=v<<(8*(k-7)); else if (k>=4) b+=v<<(8*(k-4)); else a+=v<<(8*k);
    }
    MIX(a,b,c); return c;
}
#undef MIX
static int floats(const unsigned char *p, unsigned n, float maximum) {
    for (unsigned i=0;i<n;++i) { float f=real32(p+4*i); if (!isfinite(f) || fabsf(f)>maximum) return 0; }
    return 1;
}
static int sidecar(CarCatalogSlot *car, unsigned kind, int fd) {
    CarCatalogOutput *o=&car->outputs[kind];
    if (kind<2) { NEED(o->size>0,"empty geometry or texture pack"); return 0; }
    NEED(o->size>=16 && o->size<=1048596,"invalid sidecar size");
    size_t size=(size_t)o->size; unsigned char *b=malloc(size);
    NEED(b,"cannot allocate bounded sidecar validation");
    ssize_t got=pread(fd,b,size,0); int good=got==(ssize_t)size;
    uint32_t magic=good ? word32(b):0, version=good ? word32(b+4):0;
    uint32_t own=name_key(car->folder),donor=name_key(car->model_donor);
    uint32_t own_attrib=attribute_key_case(car->folder,1), donor_attrib=attribute_key_case(car->model_donor,1);
    if (kind==CAR_CATALOG_SOLID_MAP) {
        uint32_t count=good ? word32(b+12):0;
        good=good && magic==0x31504d53 && (version==1 || version==2) && word32(b+8)==own && count>0 && count<=8192 && size==16+8u*count;
        for (uint32_t i=1;good && i<count;++i) if (word32(b+16+8*i)<=word32(b+8+8*i)) good=0;
    } else if (kind==CAR_CATALOG_WHEEL_FIT) {
        good=good && magic==0x31544657 && (version==1 || version==2) && word32(b+8)==own_attrib && word32(b+12)==donor_attrib &&
            size==(version==1 ? 116u:380u);
        if (good) {
            good=floats(b+16,18,20) && floats(b+88,4,1);
            for (unsigned i=0;good && i<4;++i) {
                float radius=real32(b+28+16*i),width=real32(b+88+4*i);
                if (radius<.1f || radius>1 || width<.1f || width>1) good=0;
            }
            if (version==2) good=good && floats(b+116,11,10000) && word32(b+160)<=255 && floats(b+164,54,10000);
        }
    } else if (kind==CAR_CATALOG_PAINT) {
        good=good && size==32 && magic==0x31545044 && version==2 && word32(b+8)==own && word32(b+12)==donor;
        if (good) {
            uint32_t source=word32(b+16),key=word32(b+20),mode=word32(b+24),sum=UINT32_MAX;
            for (unsigned i=0;i<7;++i) sum=(sum^word32(b+4*i))*16777619u;
            good=sum==word32(b+28) && ((mode==0 && key==0) ||
                (source && source!=UINT32_MAX && key && key!=UINT32_MAX && (mode==1 || mode==2) && (mode!=1 || source==key)));
        }
    } else if (kind==CAR_CATALOG_PHYSICS) {
        good=good && size==36 && magic==0x31534850 && version==1 && word32(b+8)==own_attrib && word32(b+12)==donor_attrib;
        if (good) {
            good=floats(b+16,5,100000) && real32(b+16)>0 && real32(b+32)==0;
            for (unsigned i=1;good && i<4;++i) { float f=real32(b+16+4*i); if (f<=0 || f>100) good=0; }
        }
    } else if (kind==CAR_CATALOG_COLLISION) {
        good=good && size>=100 && magic==0x31544c43 && version==1 && word32(b+8)==own_attrib && word32(b+12)==donor_attrib &&
            word32(b+16)==size-20;
        if (good) {
            const unsigned char *p=b+20; size_t extent=size-20; uint32_t count=word32(p+4),at=16;
            good=word32(p)==attribute_key_case(car->folder,0) && word32(p+8)==0 &&
                count>0 && count<=128 && 16+48u*count+16<=extent;
            if (good) {
                at+=48*count; uint32_t clouds=word32(p+at); good=clouds<=128;
                for (unsigned i=0;good && i<count;++i) {
                    const unsigned char *bound=p+16+48*i; int first=signed16(bound+30),cloud=(int8_t)bound[23];
                    int x=signed16(bound+16),y=signed16(bound+18),z=signed16(bound+20);
                    good=x>=0 && y>=0 && z>=0 && (x || y || z) &&
                        (first==-1 ? bound[22]==0 : first>=0 && (unsigned)(first+bound[22])<=count) &&
                        cloud>=-1 && cloud<(int)clouds && word32(bound+44)==0;
                }
                at+=16;
                for (unsigned i=0;good && i<clouds;++i) {
                    if (at+16>extent) { good=0; break; }
                    uint32_t points=word32(p+at);
                    if (points<4 || points>4096 || 16+16*points>extent-at || word32(p+at+12)!=0) { good=0; break; }
                    for (unsigned k=0;good && k<points*4;++k) {
                        float f=real32(p+at+16+4*k); if (!isfinite(f) || fabsf(f)>=1000) good=0;
                    }
                    at+=16+points*16;
                }
                good=good && at==extent;
            }
        }
    } else good=0;
    free(b);
    if (!good) { char message[200]; snprintf(message,sizeof message,"slot %u: invalid %s identity, size, version or values; reimport",car->slot,output_names[kind]); return bad(message); }
    o->version=version; return 0;
}
/* Snapshot is anonymous before the first copied byte. Retain only a read-only
 * descriptor, never a named temporary output. Later consumers cannot race an
 * installed rename or in-place write. No runtime caller can change trust pins.
 */
static int anonymous_file(int *readonly) {
    char name[]="/private/tmp/nfs-catalog-XXXXXXXX";
    int fd=mkstemp(name); if (fd<0) return io_bad("cannot create verified output snapshot");
    /* On this platform /dev/fd duplicates the original access mode. Acquire a
     * genuinely read-only handle by name, check its inode, then unlink before
     * writing any bytes. Only the writer descriptor can mutate the snapshot.
     */
    int ro=open(name,FILE_FLAGS); struct stat a,b;
    int good=ro>=0 && !safe_stat(fd,&a,FILE_LIMIT) && !safe_stat(ro,&b,FILE_LIMIT) &&
        a.st_dev==b.st_dev && a.st_ino==b.st_ino && (fcntl(ro,F_GETFL)&O_ACCMODE)==O_RDONLY;
    int removed=unlink(name);
    if (!good || removed || fcntl(fd,F_SETFD,FD_CLOEXEC)<0) {
        if (ro>=0) close(ro);
        close(fd); return bad("cannot retain anonymous read-only output snapshot");
    }
    *readonly=ro;
    return fd;
}
static int readonly_snapshot(int fd) {
    char path[64]; snprintf(path,sizeof path,"/dev/fd/%d",fd);
    int ro=open(path,O_RDONLY|O_CLOEXEC); if (ro<0) return io_bad("cannot retain read-only verified snapshot");
    if (lseek(ro,0,SEEK_SET)<0) { close(ro); return io_bad("cannot rewind verified snapshot"); }
    return ro;
}
static int output_verify(int base, unsigned slot, unsigned kind) {
    CarCatalogOutput *o=&inventory[slot].outputs[kind]; char path[100];
    snprintf(path,sizeof path,"cars/%u/%s",slot,output_names[kind]);
    int source=open_relative(base,path,0); if (source<0) return io_bad("cannot open pinned output (missing or unsafe path)");
    struct stat before,after;
    if (safe_stat(source,&before,FILE_LIMIT) || (uint64_t)before.st_size!=o->size) { close(source); return bad("unsafe output or output size mismatch; reimport"); }
    int ro=-1,temp=anonymous_file(&ro); if (temp<0) { close(source); return -1; }
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx); uint64_t n=0; int rc=0;
    unsigned char block[65536];
    while (n<o->size) {
        size_t want=o->size-n<sizeof block ? (size_t)(o->size-n):sizeof block;
        ssize_t got=read(source,block,want);
        if (got<0 && errno==EINTR) continue;
        if (got<=0) { rc=bad("output changed or failed while reading"); break; }
        CC_SHA256_Update(&ctx,block,(CC_LONG)got); n+=(uint64_t)got;
        size_t used=0;
        while (used<(size_t)got) {
            ssize_t written=write(temp,block+used,(size_t)got-used);
            if (written<0 && errno==EINTR) continue;
            if (written<=0) { rc=io_bad("cannot copy verified output snapshot"); break; }
            used+=(size_t)written;
        }
        if (rc) break;
    }
    char actual[65]; hash_end(&ctx,actual);
    if (!rc && (fstat(source,&after) || !unchanged(&before,&after) || n!=o->size || strcmp(actual,o->sha256))) rc=bad("output changed or SHA-256 mismatch; reimport");
    close(source);
    if (!rc) rc=sidecar(&inventory[slot],kind,temp);
    if (!rc && lseek(ro,0,SEEK_SET)<0) rc=io_bad("cannot rewind verified snapshot");
    close(temp);
    if (rc) { if (ro>=0) close(ro); return -1; }
    Retained *r=&retained[slot][kind]; r->fd=ro; r->size=o->size; strcpy(r->hash,o->sha256);
    snprintf(o->path,sizeof o->path,"/dev/fd/%d",ro); return 0;
}
/* Exact inventory at each level; no recursive traversal of untrusted trees.
 * Extra empty directories are rejected too. Every leaf has already been read
 * through no-follow descriptors; compare inode snapshots during enumeration.
 */
static int inventory_check(int fd, int level, int slot) {
    int copy=openat(fd,".",DIR_FLAGS); if (copy<0) return io_bad("cannot enumerate generation");
    DIR *dir=fdopendir(copy); if (!dir) { close(copy); return io_bad("cannot enumerate generation"); }
    unsigned seen=0; int rc=0;
    for (;;) {
        errno=0; struct dirent *entry=readdir(dir);
        if (!entry) { if (errno) rc=io_bad("cannot read generation directory"); break; }
        const char *name=entry->d_name;
        if (!strcmp(name,".") || !strcmp(name,"..")) continue;
        struct stat st;
        if (fstatat(fd,name,&st,AT_SYMLINK_NOFOLLOW)) { rc=io_bad("generation inventory changed"); break; }
        int directory=0,child_slot=-1,accepted=0;
        if (level==0) {
            if (!strcmp(name,"catalog.json")) accepted=S_ISREG(st.st_mode) && st.st_nlink==1;
            else if (!strcmp(name,"cars")) { accepted=1; directory=1; }
        } else if (level==1) {
            for (unsigned i=0;i<76;++i) if (inventory[i].ready) {
                char expected[8]; snprintf(expected,sizeof expected,"%u",i);
                if (!strcmp(name,expected)) { accepted=1; directory=1; child_slot=(int)i; break; }
            }
        } else {
            for (unsigned i=0;i<CAR_CATALOG_OUTPUTS;++i) if (inventory[slot].outputs[i].present && !strcmp(name,output_names[i])) {
                accepted=S_ISREG(st.st_mode) && st.st_nlink==1; break;
            }
        }
        if (!accepted || (directory && !S_ISDIR(st.st_mode))) { rc=bad("unexpected, linked or special generation output"); break; }
        ++seen;
        if (directory) {
            int child=openat(fd,name,DIR_FLAGS); struct stat opened;
            if (child<0) { rc=io_bad("cannot open generation directory without following links"); break; }
            if (fstat(child,&opened) || opened.st_dev!=st.st_dev || opened.st_ino!=st.st_ino) rc=bad("generation directory replaced during inventory");
            if (!rc) rc=inventory_check(child,level+1,child_slot);
            close(child); if (rc) break;
        }
    }
    closedir(dir);
    unsigned expected=0;
    if (level==0) {
        expected=1;
        for (unsigned i=0;i<76;++i) if (inventory[i].ready) { expected=2; break; }
        /* Writer may emit an empty cars directory for an empty catalog. */
        if (!rc && expected==1 && seen==2) return 0;
    } else if (level==1) { for (unsigned i=0;i<76;++i) if (inventory[i].ready) ++expected; }
    else for (unsigned i=0;i<CAR_CATALOG_OUTPUTS;++i) if (inventory[slot].outputs[i].present) ++expected;
    if (!rc && seen!=expected) rc=bad("missing generation outputs");
    return rc;
}
static void clear_inventory(void) {
    for (unsigned s=0;s<CAR_CATALOG_SLOTS;++s) {
        free((void *)inventory[s].parts); free((void *)inventory[s].part_keys);
        for (unsigned k=0;k<CAR_CATALOG_OUTPUTS;++k) if (retained[s][k].fd>=0) { close(retained[s][k].fd); retained[s][k].fd=-1; }
    }
    memset(inventory,0,sizeof inventory); generation[0]=compatibility[0]=0; nslots=nselected=0;
}
static int load(const char *root) {
    Json pointer={0},catalog={0},policy={0}; int base=-1,gen=-1,rc=-1;
    int policy_slots[CAR_CATALOG_SLOTS]; char policy_digest[65],catalog_digest[65],claimed[65];
    errno=0;
    base=root_open(root);
    if (base<0) {
        if (errno==ENOENT) return 0;
        return io_bad("cannot open catalog root safely");
    }
    /* Absence is ordinary MW operation, including packages without approvals. */
    struct stat pointer_stat;
    if (fstatat(base,"current.json",&pointer_stat,AT_SYMLINK_NOFOLLOW) && errno==ENOENT) { rc=0; goto done; }
    if (json_read(base,"current.json",&pointer)) goto done;
    if (!FIELDS(&pointer,0,"schema","generation","catalog_sha256","compatibility","selection") || !integer(&pointer,field(&pointer,0,"schema"),1)) { bad("unsupported pointer schema or unexpected pointer fields"); goto done; }
    int selection=field(&pointer,0,"selection"),name=field(&pointer,0,"generation");
    if (pointer.t[selection].type!='[' || pointer.t[selection].count>CAR_CATALOG_SLOTS) { bad("invalid selection array"); goto done; }
    if (null(&pointer,name)) {
        if (!null(&pointer,field(&pointer,0,"catalog_sha256")) || !null(&pointer,field(&pointer,0,"compatibility")) || pointer.t[selection].count) bad("invalid disabled pointer");
        else rc=0;
        goto done;
    }
    if (string(&pointer,name,generation,sizeof generation,0) || hash_field(&pointer,0,"catalog_sha256",catalog_digest) ||
        strlen(generation)!=66 || memcmp(generation,"g-",2) || strcmp(generation+2,catalog_digest) ||
        hash_field(&pointer,0,"compatibility",compatibility)) { bad("invalid generation name, digest or pointer compatibility"); goto done; }
    if (policy_load(&policy,policy_slots,policy_digest)) goto done;
    gen=openat(base,generation,DIR_FLAGS); if (gen<0) { io_bad("cannot open retained generation safely"); goto done; }
    if (json_read(gen,"catalog.json",&catalog)) goto done;
    hash_bytes(catalog.s,catalog.size,claimed);
    if (strcmp(claimed,catalog_digest)) { bad("catalog SHA-256 mismatch"); goto done; }
    if (!FIELDS(&catalog,0,"schema","policy_sha256","compatibility","cars") || !integer(&catalog,field(&catalog,0,"schema"),1) ||
        hash_field(&catalog,0,"policy_sha256",claimed) || strcmp(claimed,policy_digest)) { bad("catalog schema or packaged approval policy mismatch"); goto done; }
    int cars=field(&catalog,0,"cars");
    if (catalog.t[cars].type!='[' || catalog.t[cars].count!=CAR_CATALOG_SLOTS) { bad("catalog requires 90 ordered reservations"); goto done; }
    int at=cars+1;
    for (unsigned s=0;s<CAR_CATALOG_SLOTS;++s) {
        if (row_load(&catalog,at,s,&policy,policy_slots[s])) { slot_error(s,"contract"); goto done; }
        at=catalog.t[at].next;
    }
    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx); hash_text(&ctx,"[");
    for (unsigned s=0;s<CAR_CATALOG_SLOTS;++s) {
        if (s) hash_text(&ctx,",");
        if (inventory[s].ready) { hash_text(&ctx,"\""); hash_text(&ctx,inventory[s].compatibility); hash_text(&ctx,"\""); }
        else hash_text(&ctx,"null");
    }
    hash_text(&ctx,"]\n"); hash_end(&ctx,claimed);
    char catalog_compat[65];
    if (hash_field(&catalog,0,"compatibility",catalog_compat) || strcmp(catalog_compat,claimed) || strcmp(compatibility,claimed)) { bad("catalog or pointer compatibility mismatch"); goto done; }
    int previous=-1;
    for (int v=selection+1;v<pointer.t[selection].next;v=pointer.t[v].next) {
        uint64_t s;
        if (number(&pointer,v,CAR_CATALOG_SLOTS-1,&s) || (int)s<=previous || !inventory[s].ready) { bad("selection must contain sorted unique ready slots"); goto done; }
        inventory[s].selected=1; previous=(int)s;
    }
    for (unsigned s=0;s<CAR_CATALOG_SLOTS;++s) if (inventory[s].ready) {
        for (unsigned k=0;k<CAR_CATALOG_OUTPUTS;++k) if (inventory[s].outputs[k].present && output_verify(gen,s,k)) {
            slot_error(s,output_names[k]); goto done;
        }
    }
    if (inventory_check(gen,0,-1)) goto done;
    /* Only now publish any inventory/selected entries. */
    nslots=CAR_CATALOG_SLOTS; nselected=(size_t)pointer.t[selection].count; rc=(int)nselected;
 done:
    if (gen>=0) close(gen);
    if (base>=0) close(base);
    json_free(&pointer); json_free(&catalog); json_free(&policy);
    return rc;
}
int car_catalog_init(const char *root, int enabled) {
    if (initialized) return result;
    initialized=1;
    for (unsigned s=0;s<CAR_CATALOG_SLOTS;++s) for (unsigned k=0;k<CAR_CATALOG_OUTPUTS;++k) retained[s][k].fd=-1;
    result=enabled ? load(root):0;
    if (result<0) clear_inventory();
    return result;
}
size_t car_catalog_count(void) { return nselected; }
const CarCatalogSlot *car_catalog_get(size_t index) {
    if (index>=nselected) return NULL;
    for (unsigned s=0;s<nslots;++s) if (inventory[s].selected && index--==0) return &inventory[s];
    return NULL;
}
size_t car_catalog_inventory_count(void) { return nslots; }
const CarCatalogSlot *car_catalog_inventory_get(size_t slot) { return slot<nslots ? &inventory[slot]:NULL; }
const char *car_catalog_generation(void) { return generation; }
const char *car_catalog_compatibility(void) { return compatibility; }
const char *car_catalog_error(void) { return problem; }
const char *car_catalog_output_name(CarCatalogOutputKind kind) { return (unsigned)kind<CAR_CATALOG_OUTPUTS ? output_names[kind]:NULL; }
int car_catalog_open_output(unsigned slot, CarCatalogOutputKind kind) {
    if (slot>=nslots || (unsigned)kind>=CAR_CATALOG_OUTPUTS || retained[slot][kind].fd<0) { errno=ENOENT; return -1; }
    return readonly_snapshot(retained[slot][kind].fd);
}
