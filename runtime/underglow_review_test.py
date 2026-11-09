"""Five review regressions, with SHA-256-pinned 2cc2b719 function fragments.
1: 120 unchanged frames rebuild 120 -> 0; part/car changes each rebuild 1 -> 1
   (131 checks: original fails 2, fixed passes all).
2: 81,920 pool pixels compute duties 81,922 -> 1, turn 40,961 -> 0
   (81,998 checks: original fails 2, fixed passes all).
3: Y +/-20 m admits 1 -> 0, rejection diagnostics 0 -> 1
   (original fails 9; fixed passes 11/11, including X/Z and disjoint bounds).
4: 35 v1/v2/v3 choices: wrong colours 9 -> 0; wrong Light bits 0 -> 0
   (73 checks: original fails 9, fixed passes all).
5: old reader sees 0 -> 2 snapshots; its next save retains 0/2 -> 2/2 careers
   (original fails 4/13; fixed passes 77/77, 3 snapshots after old save).
Production inputs: underglow_light.m, underglow_store.c, underglow_photometry.h,
and stock wheel/body data.
The downgrade check executes the v2 reader AND publisher from 1cd7efb7;
its version guard at runtime/underglow_store.c:65 rejects v3.
No repository history, game launches or emulation are used by this test.
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import underglow_light_test as light
import underglow_store_test as store


LIGHT_MAIN = r'''
extern unsigned mount_rebuilds;
static unsigned duty_calls, rejected_logs;
int counted_duties(unsigned preset,double duties[3]) {
    ++duty_calls;return ug_preset_duties(preset,duties);
}
void review_diag(const char *fmt,...) {
    if(strstr(fmt,"underglow mounts rejected:")) ++rejected_logs;
}
int main(int argc,char **argv) {
    SETUP
    CHECK(argc==2);
    if(!strcmp(argv[1],"mounts")) {
        underglow_light_begin(&c,&capture);CHECK(capture.valid);
        unsigned before=mount_rebuilds;
        for(unsigned i=0;i<120;++i) { ++dev.frames;++frame_now;underglow_light_begin(&c,&capture);CHECK(capture.valid); }
        printf("same-car 120 frames: rebuilds=%u\n",mount_rebuilds-before);CHECK(mount_rebuilds==before);
        uint32_t live_ride=M32(self+0x84);
        M32(live_ride+0x48+23*4)^=4;
        before=mount_rebuilds;underglow_light_begin(&c,&capture);CHECK(capture.valid);
        printf("part-change: rebuilds=%u\n",mount_rebuilds-before);CHECK(mount_rebuilds==before+1);
        before=mount_rebuilds;
        for(unsigned i=0;i<120;++i) { ++dev.frames;++frame_now;underglow_light_begin(&c,&capture); }
        CHECK(mount_rebuilds==before);
        /* Reuse the render/ride addresses with another owned-car identity. */
        uint32_t row=profile+0x414+fixture_handle*20;M32(row+4)^=0x80;
        Cpu bind=c;bind.ecx=profile+0x414;M32(bind.esp+4)=fixture_handle;M32(bind.esp+12)=live_ride;f_005910E0(&bind);
        M32(c.esp+4)=view;M32(c.esp+12)=0x52000;
        before=mount_rebuilds;underglow_light_begin(&c,&capture);CHECK(capture.valid);CHECK(mount_rebuilds==before+1);
        printf("car-change: rebuilds=%u\n",mount_rebuilds-before);
        /* A layout edit at the same address must also invalidate. */
        MF32(self+0xa4)+=.01f;before=mount_rebuilds;
        underglow_light_begin(&c,&capture);CHECK(capture.valid);CHECK(mount_rebuilds==before+1);
    } else if(!strcmp(argv[1],"duties")) {
        f_0074E160(&c);CHECK(strips==4 && pools==1);
        unsigned first=duty_calls;
        MF32(0x52000)=MF32(0x52014)=cosf(.25f);MF32(0x52004)=sinf(.25f);MF32(0x52010)=-sinf(.25f);
        ++dev.frames;++frame_now;
        f_0074E160(&c);CHECK(strips==8 && pools==2);
        printf("preset duty computations: initial=%u turn=%u total=%u pixels=81920\n",first,duty_calls-first,duty_calls);
        CHECK(first==1);CHECK(duty_calls==1);
    } else {
        diag_on=1;MF32(self+0x94)=-20;MF32(self+0xa4)=20;
        unsigned before=draws;underglow_light_begin(&c,&capture);
        printf("malformed Y +/-20: admitted=%d diagnostic=%u\n",capture.valid,rejected_logs);
        CHECK(!capture.valid);CHECK(rejected_logs==1);
        underglow_light_end(&c,&capture);CHECK(draws==before);CHECK(allocs==0);
        unsigned builds=mount_rebuilds;
        for(unsigned i=0;i<120;++i) { ++dev.frames;++frame_now;underglow_light_begin(&c,&capture); }
        CHECK(mount_rebuilds==builds);CHECK(rejected_logs==1);
        /* Oversized longitudinal/vertical and disjoint bounds also fail. */
        for(unsigned axis=0;axis<3;axis+=2) {
            load_fixture(0);MF32(self+0x90+4*axis)=-20;MF32(self+0xa0+4*axis)=20;
            underglow_light_begin(&c,&capture);CHECK(!capture.valid);
        }
        load_fixture(0);MF32(self+0x98)=10;MF32(self+0xa8)=11;
        underglow_light_begin(&c,&capture);CHECK(!capture.valid);
    }
    printf("%s checks=%u failures=%u\n",argv[1],checks,failures);free(G);return failures?1:0;
}
'''

STORE_MAIN = r'''
LEGACY_FUNCTIONS
static void legacy_colours(const char *dir) {
    static const unsigned expected[8]={0,1,2,4,6,5,3,7};
    unsigned colour_failures=0,light_failures=0,entries=0;
    char path[4096];snprintf(path,sizeof path,"%s/underglow.bin",dir);
    int fd=open(path,O_WRONLY|O_CREAT,0600);assert(fd>=0);close(fd);
    for(unsigned version=1;version<=3;++version) {
        unsigned char bytes[CAPACITY]={0},digest[32]={0};digest[0]=(unsigned char)version;
        unsigned count=version==1?7:14;
        memcpy(bytes,magic,8);put32(bytes+8,version);put32(bytes+12,1);
        memcpy(bytes+HEADER,digest,32);put32(bytes+HEADER+32,count);
        for(unsigned i=0;i<count;++i) {
            unsigned char *at=bytes+HEADER+36+24*i;
            put32(at,i);put32(at+4,0x123);put32(at+8,0x456);put32(at+12,i);put32(at+16,2);
            put32(at+20,1+i%7+(i>=7?UG_LIGHT_ON:0));
        }
        size_t size=HEADER+36+24*count;reseal(bytes,size);putfile(path,bytes,size);
        CHECK(ug_store_load(dir,digest,&out));
        for(unsigned i=0;i<out.count;++i) {
            unsigned colour=version<3?expected[1+i%7]:1+i%7;
            unsigned on=version==1||i>=7;
            colour_failures+=(out.entries[i].preset&7u)!=colour;
            light_failures+=!!(out.entries[i].preset&UG_LIGHT_ON)!=on;
            CHECK((out.entries[i].preset&7u)==colour);CHECK(!!(out.entries[i].preset&UG_LIGHT_ON)==on);++entries;
        }
    }
    printf("legacy entries=%u colour failures=%u Light failures=%u\n",entries,colour_failures,light_failures);
}
static void downgrade(const char *dir) {
    UGSnapshot first={.digest={1},.count=16},second,third,all[UG_MAX_SNAPSHOTS];
    for(unsigned i=0;i<16;++i) first.entries[i]=(UGEntry){{i,0xABC+i,0xDEF+i,i,2},(i&7u)|(i>=8?UG_LIGHT_ON:0)};
    second=first;second.digest[0]=2;third=first;third.digest[0]=3;
    CHECK(ug_store_publish(dir,&first));CHECK(ug_store_publish(dir,&second));
    CHECK(ug_store_load(dir,first.digest,&out));CHECK(out.entries[0].preset==0);
    CHECK(ug_store_load(dir,second.digest,&out));CHECK(out.entries[0].preset==0);
    char path[4096];snprintf(path,sizeof path,"%s/underglow.bin",dir);
    unsigned count=legacy_read_all(path,all);printf("old reader before save: snapshots=%u\n",count);CHECK(count==2);
    if(count==2) for(unsigned s=0;s<count;++s) {
        CHECK(all[s].count==16);
        for(unsigned i=1;i<16;++i) {
            unsigned colour=i&7u;
            unsigned legacy=colour==3?6:colour==4?3:colour==6?4:colour;
            CHECK(all[s].entries[i].preset==((i>=8?UG_LIGHT_ON:0)|legacy));
        }
    }
    /* The old publisher must retain BOTH other careers when saving a third. */
    third.count=1;third.entries[0].preset=UG_LIGHT_ON|3;
    CHECK(legacy_publish(dir,&third));count=legacy_read_all(path,all);
    printf("old reader after save: snapshots=%u\n",count);CHECK(count==3);
    unsigned retained=0;
    for(unsigned s=0;s<2;++s) {
        UGSnapshot *want=s?&second:&first;int found=ug_store_load(dir,want->digest,&out);
        CHECK(found);if(!found) continue;++retained;CHECK(out.count==16);
        /* An old rewrite drops the extension: only the unrepresentable zero
         * becomes None/Light On. Every named colour and its Light bit remain. */
        for(unsigned i=1;i<16;++i) CHECK(out.entries[i].preset==want->entries[i].preset);
    }
    printf("careers retained after old save=%u/2\n",retained);
    CHECK(ug_store_load(dir,third.digest,&out));CHECK(out.entries[0].preset==(UG_LIGHT_ON|4));
}
int main(int argc,char **argv) {
    assert(argc==3);
    if(!strcmp(argv[2],"legacy")) legacy_colours(argv[1]);else downgrade(argv[1]);
    printf("%s checks=%u failures=%u\n",argv[2],checks,failures);return failures?1:0;
}
'''


def checked_run(command, *, broken=False, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    print(result.stdout.strip(), flush=True)
    assert result.returncode == int(broken), result.stderr[-3500:]
    if broken:
        assert re.search(r'failures=[1-9]\d*', result.stdout), result.stderr[-3500:]


def light_checks(tmp, fixture_data):
    source = (ROOT / 'runtime/underglow_light.m').read_text()
    wrapper = (ROOT / 'runtime/diag.c').read_text()
    wrapper = wrapper[wrapper.index('\nunsigned diag_car1[4];')+1:wrapper.index('void f_007387E0_orig')]
    harness = light.HARNESS[:light.HARNESS.index('int main(int argc,char **argv)')]
    harness = harness.replace('void diag_event(const char *fmt,...){}', 'void review_diag(const char *fmt,...);\nvoid diag_event(const char *fmt,...){review_diag(fmt);}')
    setup = light.HARNESS.split(' G=calloc(1,16*1024*1024);', 1)[1].split(' f_0074E160(&c);CHECK(original_calls==1);', 1)[0]
    setup = ' G=calloc(1,16*1024*1024);' + setup
    driver = harness.replace('FIXTURES', fixture_data).replace('PRODUCTION_WRAPPER', wrapper) + LIGHT_MAIN.replace('SETUP', setup)
    (tmp / 'harness.m').write_text(driver)
    (tmp / 'diag_car_core.c').write_text((ROOT / 'runtime/diag_car.c').read_text().split('// BOOT_DIAG cartex:')[0])
    render = tmp / 'render.o'
    checked_run(['clang', '-O1', '-Iruntime', '-Irecomp', '-DUG_RENDER_CORE_ONLY', '-c', 'runtime/underglow_render.m', '-o', str(render)], cwd=ROOT)
    variants = [('fixed', source, ['mounts', 'duties', 'bounds'])]
    variants.append(('mounts-2cc2b719', source.replace(light.function_fragment(source, 'void underglow_light_begin('), PINS['begin'][0]), ['mounts']))
    old_duties = source.replace(light.function_fragment(source, 'static void rgb_lux('), PINS['lux'][0])
    old_duties = old_duties.replace('rgb_lux(duties,strips,p,rgb);', 'rgb_lux(s->preset,strips,p,rgb);')
    emitter = next(line for line in source.splitlines() if 'ug_mix_xyz(duties,xyz);double error;' in line)
    old_duties = old_duties.replace(emitter, PINS['emitter'][0])
    variants.append(('duties-2cc2b719', old_duties, ['duties']))
    variants.append(('bounds-2cc2b719', source.replace(light.function_fragment(source, 'static int car_mounts('), PINS['mounts'][0]), ['bounds']))
    for name, code, modes in variants:
        code = code.replace('static int car_mounts(uint32_t self,UGStrip out[UG_STRIPS]) {', 'unsigned mount_rebuilds;\nstatic int car_mounts(uint32_t self,UGStrip out[UG_STRIPS]) {\n    ++mount_rebuilds;')
        unit = tmp / (name + '.m');unit.write_text('#define ug_preset_duties counted_duties\n' + code)
        exe = tmp / name
        checked_run(['clang', '-O1', '-g', '-fsanitize=address,undefined', '-Iruntime', '-Irecomp', str(tmp / 'harness.m'), str(unit), str(render), 'runtime/underglow_photometry.c', '-framework', 'Metal', '-framework', 'Foundation', '-o', str(exe)], cwd=ROOT)
        for mode in modes:
            print(name, mode, flush=True)
            checked_run([str(exe), mode], broken=name != 'fixed', env=dict(os.environ, NFS_UNDERGLOW='1', ASAN_OPTIONS='detect_leaks=0'))


def store_checks(tmp):
    source = (ROOT / 'runtime/underglow_store.c').read_text()
    legacy = '\n'.join(PINS[key][0] for key in ('v2valid', 'v2read', 'v2publish'))
    legacy = legacy.replace('valid(', 'legacy_valid(').replace('read_all(', 'legacy_read_all(').replace('ug_store_publish(', 'legacy_publish(')
    prefix = store.MAIN[:store.MAIN.index('int main(int argc,char **argv)')]
    variants = [('fixed-store', source, ['legacy', 'downgrade']),
                ('legacy-2cc2b719', source.replace(light.function_fragment(source, 'static unsigned read_all('), PINS['read'][0]), ['legacy']),
                ('downgrade-2cc2b719', source.replace(light.function_fragment(source, 'int ug_store_publish('), PINS['publish'][0]), ['downgrade'])]
    for name, code, modes in variants:
        unit = tmp / (name + '-store.c');unit.write_text(code)
        driver = tmp / (name + '.c');driver.write_text(prefix.replace('STORE_SOURCE', str(unit)) + STORE_MAIN.replace('LEGACY_FUNCTIONS', legacy))
        exe = tmp / name
        checked_run(['clang', '-O1', '-g', '-fsanitize=address,undefined', '-Wno-deprecated-declarations', '-I', str(ROOT / 'runtime'), str(driver), '-o', str(exe)])
        for mode in modes:
            directory = tmp / (name + '-' + mode);directory.mkdir()
            print(name, mode, flush=True)
            checked_run([str(exe), str(directory), mode], broken=name != 'fixed-store')


def main():
    for fragment, digest in PINS.values():
        assert hashlib.sha256(fragment.encode()).hexdigest() == digest
    fixture_data = light.fixtures()
    if fixture_data is None:
        print('SKIP: stock geometry and wheel attributes required');return 3
    with tempfile.TemporaryDirectory(prefix='ug-review-') as td:
        tmp = Path(td)
        light_checks(tmp, fixture_data)
        store_checks(tmp)
    print('underglow_review_test PASS')
    return 0


# Pinned original function fragments follow; no whole-file or history dependency.
PINS = {
    'begin': ('void underglow_light_begin(const Cpu *c, UGCarCapture *s) {\n    memset(s,0,sizeof *s);\n    if(!underglow_light_enabled()) return;\n    if(diag_on && (postfx_look_variant("ugoff") || postfx_look_variant("ugrepeat"))) return;\n    if(ug_arg(c,8)) return;\n    uint32_t self=c->ecx,ride=M32(self+0x84),view=ug_arg(c,0);\n    if(!ride || !view || M32(view+4)!=1) return;\n    UGOwner o=owner(ride);\n    if(!o.profile || !underglow_owned(o.profile,o.handle)) return;\n    unsigned preset=underglow_get(o.profile,o.handle);\n    if(!preset) return;\n    if(!light_on(o.profile,o.handle)) return;\n    if(M32(0x00925E90u)==6 && !primary_car(c,self,ride)) return;\n    static unsigned preset_logs;\n    if(diag_on && preset_logs++<6) diag_event("underglow colour admitted: self=%08X ride=%08X handle=%u preset=%u lod=%d",self,ride,o.handle,preset,(int)M32(self+0x1628));\n    if(!car_mounts(self,s->mounts)) return;\n    uint32_t basis=ug_arg(c,2),position=ug_arg(c,1);\n    if(!basis||!position) return;\n    memcpy(s->basis,G+basis,64);memcpy(s->position,G+position,12);\n    UGStrip strips[UG_STRIPS];\n    if(!ug_strips_transform(s->mounts,s->basis,s->position,strips)) return;\n    s->self=self;s->view=view;s->view_id=1;s->profile=o.profile;s->handle=o.handle;s->preset=preset;s->valid=1;\n}', '47aa1057e6205afe4d7f1132307ee1d95c2b7e668872349e37ade2a4bd06aba7'),
    'mounts': ('static int car_mounts(uint32_t self,UGStrip out[UG_STRIPS]) {\n    uint32_t layout=M32(self+0x17b4);\n    if(!layout || M16(layout+2)<4 || M16(layout+0xd6)<4) return 0;\n    double lo[3],hi[3];\n    for(unsigned k=0;k<3;++k) {\n        lo[k]=MF32(self+0x90+4*k);hi[k]=MF32(self+0xa0+4*k);\n        if(!isfinite(lo[k])||!isfinite(hi[k])||hi[k]<=lo[k]||fabs(lo[k])>20||fabs(hi[k])>20) return 0;\n    }\n    double rear=-INFINITY,front=INFINITY,cross=INFINITY,wheel_z=0;\n    for(unsigned i=0;i<4;++i) {\n        double x=MF32(layout+0x10+16*i),y=MF32(self+0x110+4*i);\n        double z=MF32(layout+0x18+16*i),radius=MF32(layout+0x1c+16*i),width=MF32(layout+0xdc+4*i);\n        if(!isfinite(x)||!isfinite(y)||!isfinite(z)||!isfinite(radius)||!isfinite(width)||\n           fabs(x)>20||fabs(y)>20||fabs(z)>20||radius<.1||radius>2||width<.05||width>2) return 0;\n        wheel_z+=z*.25;\n        if(i<2) front=fmin(front,x-radius-.05); else rear=fmax(rear,x+radius+.05);\n        cross=fmin(cross,fabs(y)-width*.5-.05);\n    }\n    rear=fmax(rear,lo[0]+.1);front=fmin(front,hi[0]-.1);\n    /* A global minimum can be an exhaust/bumper vertex below the sill.\n     * Use the wheel-centre plane as the underside datum, bounded by this\n     * chassis. This remains chassis-local, independent of the road plane. */\n    double left=hi[1]-.05,right=lo[1]+.05;\n    double z=fmax(lo[2]-.005,fmin(hi[2]-.005,wheel_z-.01));\n    cross=fmin(cross,fmin(left,-right));\n    if(front-rear<.2 || left-right<.2 || cross<.1 || hi[0]-lo[0]<1) return 0;\n    UGVec3 a[4]={{rear,right,z},{rear,left,z},{hi[0]-.1,-cross,z},{lo[0]+.1,-cross,z}};\n    UGVec3 b[4]={{front,right,z},{front,left,z},{hi[0]-.1,cross,z},{lo[0]+.1,cross,z}};\n    for(unsigned i=0;i<UG_STRIPS;++i)\n        out[i]=(UGStrip){a[i],b[i],{0,0,-1},hypot(b[i].x-a[i].x,b[i].y-a[i].y),.01};\n    return 1;\n}', 'e44fc3ac2287faa0dd75c27788a5f031d1d587b054ea76b4448c8a9cb375d331'),
    'lux': ('static void rgb_lux(unsigned preset,const UGStrip *strips,UGVec3 p,double rgb[3]) {\n    double duties[3],xyz[3]={0};ug_preset_duties(preset,duties);\n    for(int c=0;c<3;++c) if(duties[c]) {\n        double e=0;\n        for(int i=0;i<UG_STRIPS;++i) { double v=0;ug_segment_lux(ug_primaries[c].flux*duties[c],strips[i].a,strips[i].b,strips[i].normal,p,(UGVec3){0,0,1},&v);e+=v; }\n        double x=ug_primaries[c].x,y=ug_primaries[c].y;\n        xyz[0]+=e*x/y;xyz[1]+=e;xyz[2]+=e*(1-x-y)/y;\n    }\n    double error;ug_xyz_rec709(xyz,rgb,&error);\n}', 'c94158c2f8656970752090432d361c1f09395c8c3c66bd6c082bb5b96dd80569'),
    'read': ('static unsigned read_all(const char *path, UGSnapshot *snapshots) {\n    unsigned char bytes[CAPACITY+1], expected[32], actual[32];\n    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);\n    if (fd<0) return 0;\n    struct stat st;\n    if (fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_size<HEADER || st.st_size>CAPACITY) { close(fd); return 0; }\n    size_t size=(size_t)st.st_size, got=0;\n    while (got<size) {\n        ssize_t r=read(fd,bytes+got,size-got);\n        if (r<=0) { close(fd); return 0; }\n        got+=(size_t)r;\n    }\n    unsigned char extra;\n    int clean=read(fd,&extra,1)==0;\n    close(fd);\n    if (!clean || memcmp(bytes,magic,8) || get32(bytes+8)<1 || get32(bytes+8)>3) return 0;\n    unsigned count=get32(bytes+12);\n    if (!count || count>UG_MAX_SNAPSHOTS) return 0;\n    memcpy(expected,bytes+16,32); checksum(bytes,size,actual);\n    if (memcmp(expected,actual,32)) return 0;\n    size_t at=HEADER;\n    for (unsigned i=0;i<count;++i) {\n        UGSnapshot *s=&snapshots[i];\n        memset(s,0,sizeof *s);\n        if (at+36>size) return 0;\n        memcpy(s->digest,bytes+at,32); s->count=get32(bytes+at+32); at+=36;\n        if (s->count>UG_MAX_CARS || at+s->count*24u>size) return 0;\n        for (unsigned j=0;j<s->count;++j) {\n            UGEntry *e=&s->entries[j];\n            e->car.handle=get32(bytes+at); e->car.frontend=get32(bytes+at+4);\n            e->car.vehicle=get32(bytes+at+8); e->car.customization=get32(bytes+at+12);\n            e->car.kind=get32(bytes+at+16); e->preset=get32(bytes+at+20); at+=24;\n            if (get32(bytes+8)<3 && !e->preset) return 0;\n            if (get32(bytes+8)==1 && e->preset>7) return 0;\n            /* Colour-only snapshots predate the independent Light choice. */\n            if (get32(bytes+8)==1) e->preset|=UG_LIGHT_ON;\n        }\n        if (!valid(s)) return 0;\n        for (unsigned j=0;j<i;++j) if (!memcmp(snapshots[j].digest,s->digest,32)) return 0;\n    }\n    return at==size ? count : 0;\n}', '99e9612fd7344ab97c99e3d211a026af35912b51e5f4e20b74d685521fb53828'),
    'publish': ('int ug_store_publish(const char *dir, const UGSnapshot *s) {\n    char path[4096], temp[4096];\n    if (!valid(s) || !path_for(dir,path,sizeof path)) return 0;\n    UGSnapshot *all=calloc(UG_MAX_SNAPSHOTS,sizeof *all);\n    unsigned char *bytes=calloc(1,CAPACITY);\n    if (!all || !bytes) { free(all); free(bytes); return 0; }\n    unsigned count=read_all(path,all), keep=0;\n    /* Oldest first; replacing a digest moves it to the newest position. */\n    for (unsigned i=0;i<count;++i) if (memcmp(all[i].digest,s->digest,32)) all[keep++]=all[i];\n    if (keep==UG_MAX_SNAPSHOTS) { memmove(all,all+1,(--keep)*sizeof *all); }\n    all[keep++]=*s;\n    memcpy(bytes,magic,8); put32(bytes+8,3); put32(bytes+12,keep);\n    size_t at=HEADER;\n    for (unsigned i=0;i<keep;++i) {\n        const UGSnapshot *snap=&all[i];\n        memcpy(bytes+at,snap->digest,32); put32(bytes+at+32,snap->count); at+=36;\n        for (unsigned j=0;j<snap->count;++j) {\n            const UGEntry *e=&snap->entries[j];\n            put32(bytes+at,e->car.handle); put32(bytes+at+4,e->car.frontend);\n            put32(bytes+at+8,e->car.vehicle); put32(bytes+at+12,e->car.customization);\n            put32(bytes+at+16,e->car.kind); put32(bytes+at+20,e->preset); at+=24;\n        }\n    }\n    unsigned char hash[32]; checksum(bytes,at,hash); memcpy(bytes+16,hash,32);\n    int n=snprintf(temp,sizeof temp,"%s.tmp.XXXXXX",path), fd=-1, ok=0;\n    if (n>0 && (size_t)n<sizeof temp) fd=mkstemp(temp);\n    if (fd>=0) {\n        /* A short write is an optional failure, never a partial publication. */\n        ok=SAVE_TIME("write", write(fd,bytes,at))==(ssize_t)at && SAVE_TIME("fsync", fsync(fd))==0;\n        if (SAVE_TIME("close", close(fd))) ok=0;\n        if (ok) ok=SAVE_TIME("rename", rename(temp,path))==0;\n        if (!ok) unlink(temp);\n        else {\n            int parent=open(dir,O_RDONLY);\n            if (parent>=0) { (void)SAVE_TIME("directory.fsync", fsync(parent)); SAVE_TIME("directory.close", close(parent)); }\n        }\n    }\n    free(bytes); free(all);\n    return ok;\n}', '86e5f760a3a2481f7d7af32c17947086867408471483847505c8e25a3594e0b8'),
    'v2valid': ('static int valid(const UGSnapshot *s) {\n    if (!s || s->count>UG_MAX_CARS) return 0;\n    for (unsigned i=0;i<s->count;++i) {\n        const UGEntry *e=&s->entries[i];\n        if (e->car.handle>=UG_MAX_CARS || e->car.customization>=76 ||\n            (e->car.kind!=2 && e->car.kind!=4 && e->car.kind!=6) || e->preset==0 || (e->preset&~UG_CHOICE_MASK)) return 0;\n        for (unsigned j=0;j<i;++j) if (s->entries[j].car.handle==e->car.handle) return 0;\n    }\n    return 1;\n}', 'b4e01ebcad071c1b6f8140fd3bcc9ed1295299f0cb7a3dd092d4496ace1e85a6'),
    'v2read': ('static unsigned read_all(const char *path, UGSnapshot *snapshots) {\n    unsigned char bytes[CAPACITY+1], expected[32], actual[32];\n    int fd=open(path,O_RDONLY|O_NOFOLLOW|O_NONBLOCK);\n    if (fd<0) return 0;\n    struct stat st;\n    if (fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_size<HEADER || st.st_size>CAPACITY) { close(fd); return 0; }\n    size_t size=(size_t)st.st_size, got=0;\n    while (got<size) {\n        ssize_t r=read(fd,bytes+got,size-got);\n        if (r<=0) { close(fd); return 0; }\n        got+=(size_t)r;\n    }\n    unsigned char extra;\n    int clean=read(fd,&extra,1)==0;\n    close(fd);\n    if (!clean || memcmp(bytes,magic,8) || (get32(bytes+8)!=1 && get32(bytes+8)!=2)) return 0;\n    unsigned count=get32(bytes+12);\n    if (!count || count>UG_MAX_SNAPSHOTS) return 0;\n    memcpy(expected,bytes+16,32); checksum(bytes,size,actual);\n    if (memcmp(expected,actual,32)) return 0;\n    size_t at=HEADER;\n    for (unsigned i=0;i<count;++i) {\n        UGSnapshot *s=&snapshots[i];\n        memset(s,0,sizeof *s);\n        if (at+36>size) return 0;\n        memcpy(s->digest,bytes+at,32); s->count=get32(bytes+at+32); at+=36;\n        if (s->count>UG_MAX_CARS || at+s->count*24u>size) return 0;\n        for (unsigned j=0;j<s->count;++j) {\n            UGEntry *e=&s->entries[j];\n            e->car.handle=get32(bytes+at); e->car.frontend=get32(bytes+at+4);\n            e->car.vehicle=get32(bytes+at+8); e->car.customization=get32(bytes+at+12);\n            e->car.kind=get32(bytes+at+16); e->preset=get32(bytes+at+20); at+=24;\n            if (get32(bytes+8)==1 && e->preset>7) return 0;\n        }\n        if (!valid(s)) return 0;\n        for (unsigned j=0;j<i;++j) if (!memcmp(snapshots[j].digest,s->digest,32)) return 0;\n    }\n    return at==size ? count : 0;\n}', 'd3088df04fe60dec774595ed9c9bd306ec3b3fa62bc32151be83bee760d4fb0b'),
    'v2publish': ('int ug_store_publish(const char *dir, const UGSnapshot *s) {\n    char path[4096], temp[4096];\n    if (!valid(s) || !path_for(dir,path,sizeof path)) return 0;\n    UGSnapshot *all=calloc(UG_MAX_SNAPSHOTS,sizeof *all);\n    unsigned char *bytes=calloc(1,CAPACITY);\n    if (!all || !bytes) { free(all); free(bytes); return 0; }\n    unsigned count=read_all(path,all), keep=0;\n    /* Oldest first; replacing a digest moves it to the newest position. */\n    for (unsigned i=0;i<count;++i) if (memcmp(all[i].digest,s->digest,32)) all[keep++]=all[i];\n    if (keep==UG_MAX_SNAPSHOTS) { memmove(all,all+1,(--keep)*sizeof *all); }\n    all[keep++]=*s;\n    memcpy(bytes,magic,8); put32(bytes+8,2); put32(bytes+12,keep);\n    size_t at=HEADER;\n    for (unsigned i=0;i<keep;++i) {\n        const UGSnapshot *snap=&all[i];\n        memcpy(bytes+at,snap->digest,32); put32(bytes+at+32,snap->count); at+=36;\n        for (unsigned j=0;j<snap->count;++j) {\n            const UGEntry *e=&snap->entries[j];\n            put32(bytes+at,e->car.handle); put32(bytes+at+4,e->car.frontend);\n            put32(bytes+at+8,e->car.vehicle); put32(bytes+at+12,e->car.customization);\n            put32(bytes+at+16,e->car.kind); put32(bytes+at+20,e->preset); at+=24;\n        }\n    }\n    unsigned char hash[32]; checksum(bytes,at,hash); memcpy(bytes+16,hash,32);\n    int n=snprintf(temp,sizeof temp,"%s.tmp.XXXXXX",path), fd=-1, ok=0;\n    if (n>0 && (size_t)n<sizeof temp) fd=mkstemp(temp);\n    if (fd>=0) {\n        /* A short write is an optional failure, never a partial publication. */\n        ok=SAVE_TIME("write", write(fd,bytes,at))==(ssize_t)at && SAVE_TIME("fsync", fsync(fd))==0;\n        if (SAVE_TIME("close", close(fd))) ok=0;\n        if (ok) ok=SAVE_TIME("rename", rename(temp,path))==0;\n        if (!ok) unlink(temp);\n        else {\n            int parent=open(dir,O_RDONLY);\n            if (parent>=0) { (void)SAVE_TIME("directory.fsync", fsync(parent)); SAVE_TIME("directory.close", close(parent)); }\n        }\n    }\n    free(bytes); free(all);\n    return ok;\n}', '5c7a3355b9587b6a469420691434f51ac2a48b97f14d422ac48e6456fbf9d29f'),
    'emitter': ('    double duties[3],xyz[3],rgb[3];ug_preset_duties(s->preset,duties);ug_mix_xyz(duties,xyz);double error;ug_xyz_rec709(xyz,rgb,&error);', 'bd42ab7087bb58535a0d9f0fdfa8896fc8c72b0b3a61bf9da2b8c855a6f6f8af'),
}


if __name__ == '__main__':
    raise SystemExit(main())
