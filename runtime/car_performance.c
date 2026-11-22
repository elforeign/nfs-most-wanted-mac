#include "car_performance.h"
#include <errno.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct { unsigned offset, width, array; } Field;
#define SCALAR(at) {at, 1, 0}
#define PAIR(at) {at, 2, 0}
#define ARRAY(at, width) {at, width, 1}
/* Each row starts at the named MW layout offset. The converter checks the
 * corresponding Carbon definition, then writes values in this order. */
static const Field vehicle[] = {SCALAR(0x38), {0, 4, 0}, ARRAY(0, 2)};
static const Field engine[] = {
    ARRAY(0, 9), ARRAY(0x2c, 2), ARRAY(0x3c, 3), SCALAR(0x50),
    SCALAR(0x54), SCALAR(0x58), SCALAR(0x5c)
};
static const Field transmission[] = {
    ARRAY(0, 9), ARRAY(0x2c, 3), ARRAY(0x40, 9), SCALAR(0x6c),
    SCALAR(0x70), SCALAR(0x74), SCALAR(0x78), SCALAR(0x7c), SCALAR(0x80)
};
static const Field induction[] = {
    SCALAR(0), SCALAR(4), SCALAR(8), SCALAR(0xc), SCALAR(0x10), SCALAR(0x14), SCALAR(0x18)
};
static const Field chassis[] = {SCALAR(0x68), SCALAR(0x70)};
static const Field brakes[] = {PAIR(0), PAIR(8), SCALAR(0x10)};
static const Field tires[] = {SCALAR(0x48)};
static const Field nos[] = {
    SCALAR(4), SCALAR(0x10), /* CPF2 boost and capacity */
    SCALAR(0), SCALAR(8), SCALAR(0xc), SCALAR(0x14), SCALAR(0x18), SCALAR(0x1c)
};

typedef struct { const Field *fields; unsigned count; } Block;
#define BLOCK(fields) {fields, sizeof fields / sizeof fields[0]}
static const Block blocks[] = {
    BLOCK(vehicle), BLOCK(engine), BLOCK(transmission), BLOCK(induction),
    BLOCK(chassis), BLOCK(brakes), BLOCK(tires), BLOCK(nos)
};

static Block performance_block(unsigned index, unsigned version) {
    Block block=blocks[index];
    if (index==CAR_PERFORMANCE_NOS_LINK+1 && version<3)
        block.count=version==2 ? 2 : 0;
    return block;
}

const CarPerformanceLink car_performance_links[CAR_PERFORMANCE_LINKS] = {
    {0xF1F5FBC7, 0x60, 0}, /* engine */
    {0x07A7A3E5, 0x84, 0}, /* transmission */
    {0xC92A0142, 0x1c, 0}, /* induction */
    {0xAFA210F0, 0x74, 1}, /* chassis */
    {0x36350867, 0x14, 0}, /* brakes */
    {0xBD38D1CA, 0x50, 2}, /* tires */
    [CAR_PERFORMANCE_NOS_LINK] = {0xB1669F64, 0x20, 0}, /* nos */
};

int car_performance_enabled(void) {
    const char *value=getenv("NFS_ADDCAR_PERFORMANCE");
    return !value || (strcasecmp(value,"off") && strcmp(value,"0") && strcasecmp(value,"retail"));
}
int car_performance_class(uint32_t key) {
    for (unsigned i=0;i<CAR_PERFORMANCE_LINKS;++i)
        if (car_performance_links[i].key==key) return 1;
    return 0;
}
static float real(const void *bytes) {
    float value;
    memcpy(&value,bytes,sizeof value);
    return value;
}

int car_performance_read(int fd,uint32_t car,uint32_t donor,CarPerformance *out) {
    static const struct { char magic[5]; unsigned size; } formats[] = {
        {"CPF1", 312}, {"CPF2", 320}, {"CPF3", CAR_PERFORMANCE_BYTES}
    };
    uint32_t words[CAR_PERFORMANCE_BYTES / 4];
    struct stat st;
    memset(out,0,sizeof *out);
    if (fstat(fd,&st) || !S_ISREG(st.st_mode)) return -1;
    unsigned format=0;
    while (format<sizeof formats/sizeof formats[0] && st.st_size!=formats[format].size) ++format;
    if (format==sizeof formats/sizeof formats[0]) return -1;
    size_t size=formats[format].size;
    unsigned version=format+1;
    size_t read_bytes=0;
    while (read_bytes<size) {
        ssize_t got=pread(fd,(char *)words+read_bytes,size-read_bytes,(off_t)read_bytes);
        if (got<0 && errno==EINTR) continue;
        if (got<=0) return -1;
        read_bytes+=(size_t)got;
    }
    if (!car || !donor || memcmp(words,formats[format].magic,4) || words[1]!=size ||
        words[2]!=car || words[3]!=donor) return -1;
    const uint32_t *p=words+4;
    for (unsigned b=0;b<=CAR_PERFORMANCE_LINKS;++b) {
        Block block=performance_block(b,version);
        for (unsigned i=0;i<block.count;++i) {
            Field field=block.fields[i];
            unsigned count=field.array ? *p++ : field.width;
            unsigned minimum=b==1 && i==1 ? 0 : b==2 && i==0 ? 3 : field.width;
            if (count<minimum || count>field.width) return -1;
            for (unsigned j=0;j<field.width;++j,++p) {
                float v=real(p);
                if (!isfinite(v) || fabsf(v)>100000 || (j>=count && *p)) return -1;
            }
        }
    }
    if (p!=words+size/sizeof words[0] || real(words+4)<=0) return -1;
    memcpy(out->values,words+4,size-16);
    out->version=version;
    return 0;
}

static unsigned length(const unsigned char *array,unsigned maximum) {
    uint16_t header[4];
    memcpy(header,array,sizeof header);
    return header[1]<=maximum && header[2]==4 ? header[1] : 0;
}

float car_performance_clamp_top_speed(const void *engine_layout,float speed) {
    const unsigned char *limiter=(const unsigned char *)engine_layout+0x2c;
    if (length(limiter,2)==2) {
        float onset=real(limiter+8), width=real(limiter+12);
        if (onset>0 && width>0) return fminf(speed,(onset+width)*0.44703f);
    }
    return speed;
}

static void array_header(void *out,unsigned capacity,unsigned count) {
    uint16_t header[4]={capacity,count,4,0};
    memcpy(out,header,sizeof header);
}

void car_performance_body(const CarPerformance *record,void *layout,void *handling) {
    const uint32_t *v=record->values;
    memcpy((char *)layout+0x38,v,4);
    memcpy(layout,v+1,16);
    array_header(handling,2,2);
    memcpy((char *)handling+8,v+6,8);
}

unsigned car_performance_tiers(const CarPerformance *record,unsigned link,
                               unsigned donor_count,unsigned donor_indices[3]) {
    donor_indices[0]=0; donor_indices[1]=1; donor_indices[2]=2;
    /* With two references MW blends stock into partial kits. Give nitrous a
     * separate low kit so both ends of the blend have equipped refill values. */
    if (link==CAR_PERFORMANCE_NOS_LINK &&
        (record->version>=3 || record->source==CAR_PERFORMANCE_PROSTREET) && donor_count==2) {
        donor_indices[1]=0; donor_indices[2]=1;
        return 3;
    }
    return donor_count;
}

void car_performance_layout(const CarPerformance *record,unsigned link,int installed,
                            const void *stock,const void *endpoint,void *out) {
    const uint32_t *p=record->values;
    for (unsigned b=0;b<=link;++b)
        for (unsigned i=0;i<blocks[b].count;++i)
            p+=blocks[b].fields[i].width+blocks[b].fields[i].array;
    int prostreet=record->source==CAR_PERFORMANCE_PROSTREET;
    int source_nos=link==CAR_PERFORMANCE_NOS_LINK && (record->version>=3 || prostreet);
    Block block=performance_block(link+1,source_nos ? 3 : record->version);
    int stock_has_nos=source_nos && real(p+1)>0;
    for (unsigned i=0;i<block.count;++i) {
        Field field=block.fields[i];
        const unsigned char *base=(const unsigned char *)stock+field.offset;
        const unsigned char *tier=(const unsigned char *)endpoint+field.offset;
        unsigned char *dst=(unsigned char *)out+field.offset;
        unsigned count=field.array ? *p++ : field.width;
        unsigned common=field.width;
        if (field.array) {
            unsigned nbase=length(base,field.width), ntier=length(tier,field.width);
            common=nbase<ntier ? nbase : ntier;
            array_header(dst,field.width,count);
            base+=8; tier+=8; dst+=8;
        }
        for (unsigned j=0;j<field.width;++j) {
            float projected=real(p+j);
            if (prostreet && link==CAR_PERFORMANCE_ENGINE_LINK && field.offset==0x2c && j==1 && count==2)
                projected=fmaxf(0,projected-real(p)); /* Absolute cutoff to ramp width. */
            if (prostreet && link==CAR_PERFORMANCE_CHASSIS_LINK && field.offset==0x68)
                projected=real(base+4*j); /* Retain the MW downforce model's coefficient. */
            if (installed && j<count && j<common) {
                float before=real(base+4*j), after=real(tier+4*j);
                if (source_nos) {
                    /* The donor kits match the source's boost and capacity
                     * endpoints. Refill and flow stay at equipped values. */
                    if (i<2 || !stock_has_nos) projected=after;
                } else if (link==CAR_PERFORMANCE_NOS_LINK && projected==0) projected=after;
                else if (projected==before) projected=after;
                else if (before!=after) projected=before!=0 ? projected*(after/before) : projected+after;
            }
            memcpy(dst+4*j,&projected,4);
        }
        p+=field.width;
    }
}
