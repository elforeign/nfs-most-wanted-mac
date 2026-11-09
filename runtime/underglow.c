#include "underglow.h"
#include "underglow_store.h"
#include "host.h"

/* UserProfile+414 is FEPlayerCarDB: 200 20-byte rows. A row's +4/+8
 * attribute keys identify its type; +10 is the customization handle (0..75).
 * Low ownership bits 2/4 are career/custom, as in DeleteCareer/CustomCar. */
static uint32_t active_profile;
static unsigned char active_name[32];
static UGSnapshot live, pending;
static int pending_valid;
static int profile_valid(uint32_t profile) {
    return profile && (uint64_t)profile+0x9CB0u<=(1ull<<32);
}
static int active(uint32_t profile) {
    return profile_valid(profile) && profile==active_profile &&
        !memcmp(active_name,G+profile,32);
}
static int identity(uint32_t profile, uint32_t handle, UGIdentity *out) {
    if (!active(profile) || handle>=UG_MAX_CARS) return 0;
    /* Retail assigns a row's handle to its index on successful creation. */
    uint32_t row=profile+0x414+handle*20;
    uint32_t kind=M32(row+12)&6, custom=G[row+16];
    if (M32(row)!=handle || !kind || custom>=76) return 0;
    *out=(UGIdentity){handle,M32(row+4),M32(row+8),custom,kind};
    return 1;
}
static void forget(uint32_t handle) {
    for (unsigned i=0;i<live.count;) {
        if (live.entries[i].car.handle==handle) live.entries[i]=live.entries[--live.count];
        else ++i;
    }
}
void underglow_save_begin(void) {
    pending_valid=0;
    memset(&pending,0,sizeof pending);
}
void underglow_profile_default(uint32_t profile) {
    underglow_save_begin();
    memset(&live,0,sizeof live); active_profile=0;
    memset(active_name,0,sizeof active_name);
    if (!profile_valid(profile)) return;
    active_profile=profile; memcpy(active_name,G+profile,32);
}
void underglow_profile_load(uint32_t profile, uint32_t buffer, uint32_t size) {
    underglow_profile_default(profile);
    unsigned char digest[32];
    if (!active(profile) || (uint64_t)buffer+size>(1ull<<32) ||
        !ug_save_digest(G+buffer,size,digest)) return;
    UGSnapshot loaded;
    if (!ug_store_load(userdata_dir(),digest,&loaded)) return;
    /* Validate against the admitted live table, including identical-model cars. */
    for (unsigned i=0;i<loaded.count;++i) {
        UGIdentity car;
        if (identity(profile,loaded.entries[i].car.handle,&car) &&
            ug_identity_equal(&car,&loaded.entries[i].car)) live.entries[live.count++]=loaded.entries[i];
    }
}
static unsigned choice_get(uint32_t profile, uint32_t handle) {
    UGIdentity car;
    if (!identity(profile,handle,&car)) return 0;
    for (unsigned i=0;i<live.count;++i)
        if (ug_identity_equal(&car,&live.entries[i].car)) return live.entries[i].preset;
    return 0;
}
/* A career created this session is named after UserProfile::Default ran, so its name no longer matches the one
 * recorded there; the customise menu re-binds the profile it is working on (store entries of the same profile are
 * kept, another profile starts empty). */
void underglow_adopt(uint32_t profile) {
    if (!profile_valid(profile) || active(profile)) return;
    if (profile!=active_profile) underglow_profile_default(profile);
    else memcpy(active_name,G+profile,32);
}
int underglow_owned(uint32_t profile, uint32_t handle) {
    UGIdentity car;
    return identity(profile,handle,&car);
}
unsigned underglow_get(uint32_t profile, uint32_t handle) {
    return choice_get(profile,handle)&7u;
}
unsigned underglow_light_get(uint32_t profile, uint32_t handle) {
    return !!(choice_get(profile,handle)&UG_LIGHT_ON);
}
static int choice_set(uint32_t profile, uint32_t handle, unsigned choice) {
    UGIdentity car;
    if (!identity(profile,handle,&car)) return 0;
    forget(handle);
    /* Zero is an explicit Light Off with no colour, distinct from no choice. */
    live.entries[live.count++]=(UGEntry){car,choice};
    return 1;
}
int underglow_set(uint32_t profile, uint32_t handle, unsigned preset) {
    if (preset>7) return 0;
    return choice_set(profile,handle,(choice_get(profile,handle)&UG_LIGHT_ON)|preset);
}
int underglow_light_set(uint32_t profile, uint32_t handle, unsigned on) {
    if (on>1) return 0;
    return choice_set(profile,handle,(choice_get(profile,handle)&7u)|(on?UG_LIGHT_ON:0));
}
void underglow_profile_save(uint32_t profile, uint32_t buffer, uint32_t size) {
    underglow_save_begin();
    if (!active(profile) || (uint64_t)buffer+size>(1ull<<32) ||
        !ug_save_digest(G+buffer,size,pending.digest)) return;
    for (unsigned i=0;i<live.count;++i) {
        UGIdentity car;
        if (identity(profile,live.entries[i].car.handle,&car) &&
            ug_identity_equal(&car,&live.entries[i].car)) pending.entries[pending.count++]=live.entries[i];
    }
    pending_valid=1;
}
void underglow_save_complete(int committed) {
    if (committed && pending_valid) (void)ug_store_publish(userdata_dir(),&pending);
    underglow_save_begin();
}
void f_00590BD0_orig(Cpu *c);
void f_00590BD0(Cpu *c) {
    uint32_t db=c->ecx;
    f_00590BD0_orig(c);
    uint32_t row=c->eax;
    if (active(active_profile) && db==active_profile+0x414 && row>=db &&
        (uint64_t)row+20<=(uint64_t)db+UG_MAX_CARS*20 && (row-db)%20==0 &&
        M32(row)==(row-db)/20) forget(M32(row));
}
void f_00581370_orig(Cpu *c);
void f_00581370(Cpu *c) {
    uint32_t db=c->ecx, handle=M32(c->esp+4);
    f_00581370_orig(c);
    if ((c->eax&255) && active(active_profile) && db==active_profile+0x414) forget(handle);
}
