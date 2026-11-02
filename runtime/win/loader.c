#include "host.h"
static uint16_t u16(const uint8_t *p) { return (uint16_t)(p[0]|p[1]<<8); }
static uint32_t u32(const uint8_t *p) { return (uint32_t)p[0]|(uint32_t)p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24; }
static int fits(uint64_t at,uint64_t size,uint64_t end) { return at<=end && size<=end-at; }
int win_load_pe(const uint8_t *data,size_t size) {
    if (size<64 || u16(data)!=0x5a4d) return 0;
    uint32_t pe=u32(data+0x3c);
    if (!fits(pe,24,size) || u32(data+pe)!=0x4550 || u16(data+pe+4)!=0x14c) return 0;
    uint32_t opt=pe+24, n=u16(data+pe+6), optsize=u16(data+pe+20);
    if (!n || n>96 || optsize<96 || !fits(opt,optsize,size) || u16(data+opt)!=0x10b) return 0;
    uint32_t image=u32(data+opt+56), headers=u32(data+opt+60), sections=opt+optsize, align=u32(data+opt+32);
    // The Windows loader maps SizeOfImage rounded up to SectionAlignment; repacks with the
    // unnamed relocation section store it unaligned (0x678e4e) while that section's raw data
    // ends on the aligned boundary.
    if (align<0x200 || (align&(align-1)) || image>0x10000000) return 0;
    image=(image+align-1)&~(align-1);
    if (u32(data+opt+28)!=0x400000 || (uint64_t)u32(data+opt+16)+0x400000!=speed_entry ||
        image<headers || image>0x10000000 || headers<sections+40*n ||
        !fits(0,headers,size) || !fits(sections,40*n,size) || speed_entry-0x400000>=image) return 0;
    // Validate every source/destination before changing guest memory.
    for (uint32_t i=0; i<n; ++i) {
        const uint8_t *s=data+sections+40*i;
        uint32_t vs=u32(s+8), va=u32(s+12), rs=u32(s+16), raw=u32(s+20);
        if (va<headers || !fits(va,vs>rs?vs:rs,image) || !fits(raw,rs,size)) return 0;
    }
    if (!win_vm_commit(0x400000,image)) return 0;
    memset(G+0x400000,0,image); memcpy(G+0x400000,data,headers);
    for (uint32_t i=0; i<n; ++i) {
        const uint8_t *s=data+sections+40*i;
        memcpy(G+0x400000+u32(s+12),data+u32(s+20),u32(s+16));
    }
    if (!win_import_table_valid(speed_imports,speed_import_count)) return 0;
    for (unsigned i=0; i<speed_import_count; ++i)
        if (speed_imports[i].slot<0x400000 || !fits(speed_imports[i].slot-0x400000,4,image)) return 0;
    return 1;
}
