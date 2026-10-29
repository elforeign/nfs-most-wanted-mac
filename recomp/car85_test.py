"""Private offline car85 proof. Good OFF 220 / ON 248 checks: 0 failures; count=84: 19; asset aliases off: 7.
Collision alias off: 1; borrowed metadata: 1; unrelated-vault leak exits 2.
Reviewed 8409557: OFF 15 failures, ON 14 failures; accepts four unsafe profiles.

Uses the actual 84-record GLOBALB chunk and relocated PC donor load descriptors.
Executes the translator's real scans, slot mapper and eleven instruction overrides
against the actual car85.c. Guest allocator/collection-constructor/FE calls are
contract stubs; native game runs remain the proof of those integrations.
No Unicorn, sockets, graphics or game launch.
"""
from pathlib import Path
import os
import re
import struct
import subprocess
import sys
import tempfile
import importlib.util

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "recomp"))
sys.path.insert(0, str(ROOT / "tools_snapshot/vaults"))
import recomp
from vaultlib import Vault, read_vpak, hash32

SITES = [0x6690BB, 0x734EE1, 0x7398A6, 0x7398FE, 0x739908, 0x73995B,
         0x7515FB, 0x759913, 0x75E6F4, 0x75E7B4, 0x7B3877]
ASSET_SITES = [0x75187D, 0x739DE8, 0x747FDE, 0x7493F3, 0x7494C8, 0x756F72, 0x7570E4]
REVIEWED_REVISION = "840955712b681f4ad3efcdc11d43f75cf55fbae7"


def fixtures(tmp):
    game = Path(recomp.EXE).parent
    bundle = (game / "GLOBAL/GLOBALB.BUN").read_bytes()
    needle = struct.pack("<II", 0x34600, 0x4448)
    chunk = bundle.index(needle)
    start = (chunk + 23) & ~15
    (tmp / "table.bin").write_bytes(bundle[start:start + 84 * 208])
    rows, bases, cursor = [], [], 0x1000000
    for filename, wanted in [("ATTRIBUTES.BIN", "db"), ("FE_ATTRIB.BIN", "frontend")]:
        for name, b, v, bo, vo in read_vpak(game / "GLOBAL" / filename):
            if name != wanted:
                continue
            vault = Vault(name, b, v, "<", bo, vo)
            blobs = [bytearray(v), bytearray(b)]
            addresses = []
            for blob in blobs:
                addresses.append(cursor)
                cursor = (cursor + len(blob) + 4095) & ~4095
            for (dep, off), fix in vault.fix.items():
                value = addresses[fix[1]] + fix[2] if fix[0] == "ptr" else 0
                struct.pack_into("<I", blobs[dep], off, value)
            for dep, blob in enumerate(blobs):
                path = tmp / f"vault{len(bases)}.bin"
                path.write_bytes(blob)
                bases.append((addresses[dep], len(blob), str(path)))
            group = len(rows)
            for key, typ, size, off in vault.exports:
                if typ == 0x8E112EB7 and vault.u32(v, off + 4) in (0x4A97EC8F, 0x85885722, 0xA5B543B7):
                    rows.append((group, addresses[0] + off))
    return bases, rows


HARNESS = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdlib.h>
#include <stdio.h>
#include "car85.c"
#define donor_record additions[0].record
#define donor_hash additions[0].hash
#define pv_key additions[0].pv
static int has_clone(uint32_t vault, uint32_t cls) { return has_addition(vault, cls, NEWKEY); }
uint8_t *G;
int diag_on = 1;
const char *game_dir = "/tmp/car85-fixture-root";
static unsigned failures, checks, redirects, fe_count, constructor_count, logo_count;
void untranslated(Cpu *c, uint32_t pc) { (void)c; fprintf(stderr,"untranslated %08X\n",pc); exit(3); }
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static uint32_t cursor = 0x3000000;
uint32_t gmalloc(uint32_t n) { uint32_t p=cursor; cursor=(cursor+n+63)&~63u; assert(cursor<0x4000000); return p; }
void gfree(uint32_t p) { (void)p; }
uint32_t gstr(const char *s) { uint32_t p=gmalloc(strlen(s)+1); strcpy((char *)G+p,s); return p; }
uint32_t extra_texture_add(Cpu *c, const uint8_t rec[124], const uint8_t pl[32], uint32_t fmt, uint32_t data, uint32_t size, int add) {
    (void)c; CHECK(fmt==21 && add==0); CHECK(pl[8]==1);
    CHECK((rec[0x44] | rec[0x45]<<8)==512); CHECK((rec[0x46] | rec[0x47]<<8)==128);
    CHECK(size==512*128*4);
    unsigned visible=0; for(unsigned i=0;i<size/4;++i) visible+=M8(data+4*i+3)!=0;
    CHECK(visible>100); ++logo_count;
    uint32_t ti=gmalloc(0xA0); memcpy(G+ti,rec,124); M32(ti+0x70)=data; return ti;
}
void diag_event(const char *fmt, ...) {
    char s[2048]; va_list ap; va_start(ap,fmt); vsnprintf(s,sizeof s,fmt,ap); va_end(ap);
    if (strstr(s,"redirect")) ++redirects;
    if (strstr(s,"FEPlayerCarDB")) puts(s);
}
static uint32_t bstr(const char *s) { uint32_t h=~0u; while(*s) h=h*33+(unsigned char)*s++; return h; }
GENERATED
void f_00756A90_orig(Cpu *c) { uint32_t a=M32(c->esp+4); if(M32(a)==0x34600) M32(TABLE)=(a+23)&~15u; c->eax=1; c->esp+=4; }
void f_00590D40_orig(Cpu *c) {
    uint32_t db=c->ecx;
    for (unsigned i=0;i<200;++i) M32(db+i*20)=~0u;
    M32(db)=0; M32(db+8)=0xD27D5B8D; // donor record
    unsigned extra=0;
    for(unsigned i=0;i<nsources;++i) if(M32(sources[i].ld+4)==PVEHICLE && has_clone(sources[i].vault,PVEHICLE)) { extra=1; break; }
    if(extra) { M32(db+20)=1; M32(db+28)=NEWKEY; }
    fe_count=1+extra; c->esp+=4;
}
void f_0045C380_orig(Cpu *c) { c->esp += 4; }
void f_0045C090_orig(Cpu *c) { c->esp += 4; }
void f_005816B0_orig(Cpu *c) { c->eax=84; c->esp+=4; }
void f_00581790_orig(Cpu *c) { c->eax=0; c->esp+=4; }
static uint32_t allocate(Cpu *c) { return gmalloc(M32(c->esp+4)); }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) {
    uint32_t sp=c->esp;
    for(int i=n-1;i>=0;--i) { c->esp-=4; M32(c->esp)=args[i]; }
    c->esp-=4; M32(c->esp)=0xDEADC0DE;
    switch(fn) {
    case 0x007398D0: f_007398D0(c); break;
    case 0x00739940: f_00739940(c); break;
    case 0x007398A0: f_007398A0(c); break;
    case 0x007399F0: f_007399F0(c); break;
    case 0x00739870: f_00739870(c); break;
    case 0x00681340: f_00681340(c); break;
    case 0x00739920: f_00739920(c); break;
    case 0x0056F090: f_0056F090(c); break;
    case 0x005816B0: f_005816B0(c); break;
    case 0x004549B0: c->eax=!strcasecmp((char *)G+args[0],"CAR85")?NEWKEY:0xD27D5B8D; break;
    case 0x00453760: M32(c->ecx+8)=0xDB10DBEA; M32(c->ecx+12)=args[0]; c->eax=c->ecx; break;
    case 0x01001000: c->eax=allocate(c); break;
    case 0x00457F90: {
        uint32_t ld=args[0], col=c->ecx, cls=gmalloc(4);
        M32(cls)=M32(ld+4); M32(col+0x14)=cls; M32(col+0x18)=M32(ld+0x1C);
        M32(col+0x20)=M32(ld); M32(col+0x24)=args[1];
        CHECK(M32(ld)==NEWKEY); CHECK(M32(ld+8)!=NEWKEY);
        if(M32(ld+4)==PVEHICLE) {
            CHECK(!strcmp((char *)G+M32(M32(ld+0x1C)+0x1C),"CAR85"));
            uint32_t e=entry(ld,FRONTEND), ref=M32(e+4);
            CHECK(M32(ref)==FRONTEND); CHECK(M32(ref+4)==NEWKEY); CHECK(M32(ref+8)==0);
        }
        ++constructor_count; c->eax=col; break;
    }
    default: fprintf(stderr,"unexpected guest call %08X\n",fn); abort();
    }
    c->esp=sp; return c->eax;
}
static void load(const char *name, uint32_t at, size_t n) {
    FILE *f=fopen(name,"rb"); assert(f); assert(fread(G+at,1,n,f)==n); fclose(f);
}
static uint32_t setup_vault(void) {
    uint32_t v=gmalloc(0x40), tt=gmalloc(12), types=gmalloc(8), block=gmalloc(16), keys=gmalloc(4);
    M32(v+4)=tt; M32(tt)=types; M32(tt+8)=1; M32(types)=0x8E112EB7;
    M32(v+0xC)=block; M32(v+0x2C)=block; M32(v+0x10)=keys; M32(v+0x30)=keys;
    car85_vault_begin(v); return v;
}
static void register_source(uint32_t v, uint32_t ld) { car85_collection_seen(ld,v); }
int main(int argc, char **argv) {
    assert(argc==2); G=calloc(1,0x4000000); assert(G);
    if(!strcmp(argv[1],"activation")) {
        setenv("NFS_TEST_CAR85","911TURBO",1);
        printf("activated types=%u\n",car85_count()); return 0;
    }
    if(!strcmp(argv[1],"on")) setenv("NFS_TEST_CAR85","911TURBO",1); else unsetenv("NFS_TEST_CAR85");
    int active=!strcmp(argv[1],"on");
    Cpu c={0}; c.esp=0x800000; c.ebx=0xBEEF; c.esi=0xCAFE; c.edi=0xFACE;
    uint32_t chunk=0x200000; M32(chunk)=0x34600; M32(chunk+4)=0x4448;
    load("TABLE_PATH",chunk+16,84*208);
    uint8_t original[84*208]; memcpy(original,G+chunk+16,sizeof original);
    M32(c.esp+4)=chunk; f_00756A90(&c);
    CHECK(car85_count()==(active?85:84)); CHECK(!memcmp(original,G+M32(TABLE),sizeof original));
    CHECK(c.eax==1 && c.ebx==0xBEEF && c.esi==0xCAFE && c.edi==0xFACE);
    for(unsigned i=0;i<84;++i) {
        uint32_t h=M32(chunk+16+i*208+0x50), expected=M32(chunk+16+i*208+0x90);
        CHECK(call_guest(&c,0x7398D0,1,&h)==expected);
        CHECK(call_guest(&c,0x739940,1,&h)==M32(TABLE)+i*208);
    }
    uint32_t h=bstr("CAR85"), type=call_guest(&c,0x7398D0,1,&h);
    CHECK(type==84); // on: real type 84; off: invalid sentinel 84 (pointer check below distinguishes them)
    CHECK(call_guest(&c,0x739940,1,&h)==(active?M32(TABLE)+84*208:0));
    h=bstr("NO_SUCH_CAR"); CHECK(call_guest(&c,0x7398D0,1,&h)==(active?85:84));
    if(active) {
        uint8_t expected[208]; memcpy(expected,original,sizeof expected);
        memset(expected,0,32); memcpy(expected,"CAR85",6); memcpy(expected+16,"CAR85",6);
        uint32_t newhash=bstr("CAR85"), newid=84;
        memcpy(expected+0x50,&newhash,4); memcpy(expected+0x90,&newid,4);
        CHECK(!memcmp(expected,G+M32(TABLE)+84*208,sizeof expected));
        CHECK(!strcmp((char *)G+M32(TABLE)+84*208,"CAR85"));
        CHECK(!strcmp((char *)G+M32(TABLE)+84*208+0x10,"CAR85"));
        CHECK(!memcmp(G+M32(TABLE)+84*208+0x20,original+0x20,0x30));
        uint32_t args[2]={0,84};
        // GetTypesFromSlot's default car-name substitution must resolve donor.
        uint32_t defaults=gmalloc(139*8);
        SLOT_SETUP
        uint32_t p=call_guest(&c,0x7399F0,2,args); CHECK(M32(p)==donor_hash);
    }
    uint32_t part_hashes=gmalloc(8); M32(part_hashes)=bstr("911TURBO"); M32(part_hashes+4)=bstr("OTHER");
    M32(0x9B09C4)=part_hashes; M32(0x9B09C8)=2;
    uint32_t part_key=bstr("CAR85"); CHECK((call_guest(&c,0x739870,1,&part_key)&255)==(active?0:255));
    part_key=bstr("911TURBO"); CHECK((call_guest(&c,0x739870,1,&part_key)&255)==0);
    // Real PC donor descriptors, with all pointer relocations applied offline.
    LOAD_FIXTURES
    uint32_t alloc_obj=gmalloc(4), vt=gmalloc(4); M32(0x8F7EA8)=alloc_obj; M32(alloc_obj)=vt; M32(vt)=0x1001000;
    uint32_t dbvault=setup_vault(), fevault=setup_vault();
    for(unsigned i=0;i<600;++i) { uint32_t unrelated=setup_vault(); car85_vault_loaded(&c,unrelated); }
    REGISTER_SOURCES
    // The real vault frees these temporary descriptors before our post-load hook.
    for(unsigned i=0;i<nsources;++i) {
        uint32_t ld=sources[i].original, bytes=0x20+4*M32(ld+0x18)+12*M32(ld+0x14);
        memset(G+ld,0xDD,bytes);
    }
    if(nsources && M32(sources[0].ld)==0xDDDDDDDD) {
        CHECK(M32(sources[0].ld)!=0xDDDDDDDD);
        printf("FAIL: borrowed metadata checks=%u failures=%u\n",checks,failures);
        return 1; // report the control safely before it dereferences poisoned metadata
    }
    Cpu before=c;
    car85_vault_loaded(&c,dbvault); CHECK(!memcmp(&before,&c,sizeof c));
    car85_vault_loaded(&c,fevault); CHECK(!memcmp(&before,&c,sizeof c));
    CHECK(constructor_count==(active?3:0));
    if(active) {
        Source *pv_source=source(PVEHICLE,pv_key); uint32_t captured=pv_source->ld;
        car85_collection_seen(captured,dbvault); CHECK(source(PVEHICLE,pv_key)->ld==captured);
    }
    uint32_t list=gmalloc(8), node=gmalloc(24), keys=gmalloc(8), bounds=gmalloc(16);
    M32(list)=node; M32(node)=list; M32(node+16)=keys; M32(node+20)=keys+8;
    M32(keys)=0x8069A83D; M32(keys+4)=bounds; c.ecx=list;
    uint32_t collision_key=0xDB10DBEA;
    CHECK(call_guest(&c,0x681340,1,&collision_key)==(active?bounds:0));
    c.ecx=list; collision_key=0x8069A83D;
    CHECK(call_guest(&c,0x681340,1,&collision_key)==bounds);
    unsigned built=constructor_count;
    car85_vault_loaded(&c,dbvault); car85_vault_loaded(&c,fevault); CHECK(constructor_count==built);
    c.ecx=gmalloc(0x8CC8); f_00590D40(&c); CHECK(fe_count==(active?2:1));
    if(active) {
        // FE -> race unload -> moved FE reload, followed by same-address reuse.
        uint32_t templates[512]; unsigned count=0;
        for(unsigned i=0;i<nsources;++i) if(sources[i].vault==fevault) {
            uint32_t ld=sources[i].ld, bytes=0x20+4*M32(ld+0x18)+12*M32(ld+0x14);
            uint32_t copy=gmalloc(bytes), layout=gmalloc(0x5B);
            memcpy(G+copy,G+ld,bytes); memcpy(G+layout,G+M32(ld+0x1C),0x5B); M32(copy+0x1C)=layout;
            assert(count<512); templates[count++]=copy;
        }
        uint32_t retired=fevault;
        c.ecx=retired; f_0045C090(&c);
        unsigned stale=0; for(unsigned i=0;i<nsources;++i) stale+=sources[i].vault==retired;
        CHECK(stale==0); memset(G+retired,0xDD,0x40);
        fevault=setup_vault(); CHECK(fevault!=retired);
        for(unsigned i=0;i<count;++i) car85_collection_seen(templates[i],fevault);
        unsigned was=constructor_count; car85_vault_loaded(&c,fevault);
        CHECK(constructor_count==was+1); CHECK(has_clone(fevault,FRONTEND));
        if(!stale) { c.ecx=gmalloc(0x8CC8); f_00590D40(&c); CHECK(fe_count==2); }
        // Reinitialize the same numeric address while an old generation exists.
        uint32_t fresh=setup_vault();
        car85_vault_begin(fevault); memcpy(G+fevault,G+fresh,0x40); car85_vault_unloaded(fresh);
        for(unsigned i=0;i<count;++i) car85_collection_seen(templates[i],fevault);
        was=constructor_count; car85_vault_loaded(&c,fevault);
        CHECK(constructor_count==was+1); CHECK(has_clone(fevault,FRONTEND));
        c.ecx=fevault; f_0045C380(&c);
        stale=0; for(unsigned i=0;i<nsources;++i) stale+=sources[i].vault==fevault;
        CHECK(stale==0);
    }
    // Low-level loads preserve every byte; unavailable profiles are rejected
    // at the real profile loader, never by orphaning linked records here.
    uint32_t saved=gmalloc(0x8CC8), loaded=gmalloc(0x8CC8); memset(G+saved,0xA5,0x8CC8);
    M32(saved)=0; M32(saved+8)=0xD27D5B8D; M32(saved+20)=1; M32(saved+28)=NEWKEY;
    c.ecx=loaded; uint32_t load_args[2]={saved,0x8CC8};
    CHECK(call_guest(&c,0x56F090,2,load_args)==saved+0x8CC8);
    CHECK(M32(loaded+20)==1); CHECK(M32(saved+20)==1);
    CHECK(!memcmp(G+loaded,G+saved,20)); CHECK(!memcmp(G+loaded+40,G+saved+40,0x8CC8-40));
    c.ecx=saved+20; CHECK(call_guest(&c,0x5816B0,0,NULL)==(active?84:0xFFFFFFFFu));
    uint32_t invalid=active?85:84;
    CHECK(call_guest(&c,0x739920,1,&invalid)==0);
    // Poison names just beyond the live table: a wrong helper reads this canary.
    strcpy((char *)G+M32(TABLE)+(invalid+1)*208+16,"OOB_CANARY");
    uint32_t invalid_hash_type=invalid+1; CHECK(call_guest(&c,0x7398A0,1,&invalid_hash_type)==0);
    uint32_t invalid_slot[2]={0,invalid}; uint32_t slots=call_guest(&c,0x7399F0,2,invalid_slot);
    CHECK(M32(slots)==0 && M32(slots+4)==0);
    char path[4096]="unchanged";
    CHECK(car85_path("C:\\NFSMW\\CARS\\CAR85\\TEXTURES.BIN",path,sizeof path)==active);
    CHECK(!strcmp(path,active?"/tmp/car85-fixture-root/CARS/911TURBO/TEXTURES.BIN":"unchanged"));
    CHECK(!car85_path("CARS/CAR850/TEXTURES.BIN",path,sizeof path));
    CHECK(!car85_path("CARS/CAR85/../foo",path,sizeof path));
    CHECK(!car85_path("CARS/911TURBO/TEXTURES.BIN",path,sizeof path));
    CHECK(redirects==(unsigned)active);
    INSTRUCTION_CHECKS
    if(!active) {
        strcpy((char *)G+M32(TABLE)+84*208,"OOB_NAME");
        strcpy((char *)G+M32(TABLE)+84*208+16,"OOB_MODEL");
    }
    ASSET_CHECKS
    printf("%s: switch=%s checks=%u failures=%u constructor_stubs=%u originals=84\n",failures?"FAIL":"PASS",argv[1],checks,failures,constructor_count);
    free(G); return failures?1:0;
}
'''


def main():
    if not Path(recomp.EXE).is_file():
        print("SKIP: private MW assets absent"); raise SystemExit(3)
    old = "--review-old" in sys.argv
    old_tmp = tempfile.TemporaryDirectory(prefix="car85-old-") if old else None
    compiler = recomp
    if old:
        old_path = Path(old_tmp.name) / "old_recomp.py"
        old_path.write_text(subprocess.run(["git", "show", REVIEWED_REVISION+":recomp/recomp.py"], cwd=ROOT, text=True, capture_output=True, check=True).stdout)
        spec = importlib.util.spec_from_file_location("car85_old_recompiler", old_path)
        compiler = importlib.util.module_from_spec(spec); spec.loader.exec_module(compiler)
    rc = compiler.Recompiler(pe=recomp.PE())
    rc.calls = True
    generated = []
    # Read the existing manifest; no import of the emulation test runner.
    starts = []
    for line in (ROOT / "recomp/functions_v13.tsv").read_text().splitlines():
        fields = line.split("\t")
        if fields and re.fullmatch(r"[0-9A-Fa-f]{8}", fields[0]):
            starts.append(int(fields[0], 16))
    starts.sort()
    manifest = {a: b for a, b in zip(starts, starts[1:])}
    rc.functions = manifest
    for address in [0x7398D0, 0x739940, 0x7398A0, 0x7399F0, 0x739870, 0x460BF0, 0x681340, 0x56F090, 0x739920]:
        code, error = rc.translate(address, manifest.get(address, address + 2048))
        assert not error, error
        if address in [0x7399F0, 0x739870, 0x681340, 0x56F090, 0x739920]:
            code = code.replace(f"void f_{address:08X}(", f"void f_{address:08X}_orig(", 1)
        generated.append(code)
    slot_code = generated[3]
    # These globals are read directly by the compiled GetTypesFromSlot body.
    print("slot globals:", sorted(set(re.findall(r"0x9B[0-9A-F]+u", slot_code))))
    instructions, instruction_checks = [], []
    for index, address in enumerate(SITES):
        insn = next(rc.md.disasm(rc.pe.read(address, 15), address))
        code = rc.insn(insn, 0, 0)
        assert any("car85_count()" in line for line in code)
        rc.calls = False
        original = rc.insn(insn, 0, 0)
        rc.calls = True
        adjacent = next(rc.md.disasm(bytes(insn.bytes), address + 0x1000000))
        assert rc.insn(adjacent, 0, 0) == original
        body = "\n".join(code)
        instructions.append(f"static uint32_t insn_{index}(Cpu *c) {{ uint8_t cf=0,pf=0,af=0,zf=0,sf=0,of=0; {body}\n return cf | (zf<<1) | (sf<<2) | (of<<3) | (af<<4) | (pf<<5); }}")
        if insn.mnemonic == "cmp":
            reg = insn.op_str.split(",")[0]
            multiplier = 208 if "4440" in insn.op_str else 1
            instruction_checks.append(f"c.{reg}=(active?85:84)*{multiplier}; CHECK(insn_{index}(&c)&2);")
            if address in [0x6690BB,0x7398A6,0x75E6F4,0x75E7B4]:
                instruction_checks.append(f"c.{reg}=0xFFFFFFFFu; CHECK(insn_{index}(&c)&2);")
        elif insn.mnemonic == "mov":
            instruction_checks.append(f"insn_{index}(&c); CHECK(c.eax==(active?85:84));")
        else:
            instruction_checks.append(f"insn_{index}(&c); CHECK(M32(c.esp)==(active?85:84)); c.esp+=4;")
    asset_checks = []
    for index, address in enumerate(ASSET_SITES):
        insn = next(rc.md.disasm(rc.pe.read(address, 15), address))
        code = rc.insn(insn, 0, 0)
        assert any("car85_asset_name" in line for line in code)
        rc.calls = False
        original = rc.insn(insn, 0, 0)
        rc.calls = True
        adjacent = next(rc.md.disasm(bytes(insn.bytes), address + 0x1000000))
        assert rc.insn(adjacent, 0, 0) == original
        instructions.append(f"static void asset_{index}(Cpu *c) {{ {' '.join(code)} }}")
        if insn.mnemonic == "lea":
            asset_checks.append(f"c.eax=84*208; c.ecx=M32(TABLE); asset_{index}(&c); CHECK(active?c.edx==donor_record+16:M8(c.edx)==0);")
        else:
            reg = insn.op_str
            offset = 0 if address in [0x756F72, 0x7570E4] else 16
            asset_checks.append(f"c.{reg}=M32(TABLE)+84*208+{offset}; asset_{index}(&c); CHECK(active?M32(c.esp)==donor_record+{offset}:M8(M32(c.esp))==0); c.esp+=4;")
    with tempfile.TemporaryDirectory(prefix="car85-test-") as directory:
        tmp = Path(directory)
        (tmp / "diag").mkdir(); (tmp / "diag.user").mkdir()
        test_env = dict(os.environ, BOOT_DIAG=str(tmp / "diag"), BOOT_USERDATA=str(tmp / "diag.user"))
        bases, rows = fixtures(tmp)
        first_group = rows[0][0]
        harness = HARNESS
        if old:
            harness = re.sub(r"#define donor_record.*?static int has_clone[^\n]*\n", "", harness, flags=re.S)
        source = harness.replace("GENERATED", "\n".join(generated + instructions))
        source = source.replace("TABLE_PATH", str(tmp / "table.bin"))
        source = source.replace("LOAD_FIXTURES", "\n".join(f'load("{path}",{at},{n});' for at, n, path in bases))
        source = source.replace("REGISTER_SOURCES", "\n".join(f'register_source({"dbvault" if group == first_group else "fevault"},{ld});' for group, ld in rows))
        source = source.replace("INSTRUCTION_CHECKS", "\n".join(instruction_checks))
        source = source.replace("ASSET_CHECKS", "\n".join(asset_checks))
        # Filled from the reread PC globals in GetTypesFromSlot.
        source = source.replace("SLOT_SETUP", "M32(0x9B09EC)=defaults; M32(defaults)=~0u; M32(defaults+4)=~0u; M32(0x9B09F4)=0;")
        (tmp / "test.c").write_text(source)
        actual = (ROOT / "runtime/car85.c").read_text()
        if old:
            actual = subprocess.run(["git", "show", REVIEWED_REVISION+":runtime/car85.c"], cwd=ROOT, text=True, capture_output=True, check=True).stdout
            # Old code has no lifecycle/load/bounds hooks: model its original
            # delegation, so the same tests can exercise the reviewed version.
            actual += r'''
void car85_vault_begin(uint32_t v) { (void)v; }
void car85_vault_unloaded(uint32_t v) { (void)v; }
void f_0045C380(Cpu *c) { f_0045C380_orig(c); }
void f_0045C090(Cpu *c) { f_0045C090_orig(c); }
void f_0056F090(Cpu *c) { f_0056F090_orig(c); }
void f_005816B0(Cpu *c) { f_005816B0_orig(c); }
void f_00739920(Cpu *c) { f_00739920_orig(c); }
'''
        results = []
        for variant in (["good"] if old else ["good", "count84", "asset_names", "collision", "metadata", "unrelated_vaults"]):
            modified = actual
            if variant == "count84":
                modified = actual.replace("return TYPES + nadditions;", "return TYPES;")
            elif variant == "asset_names":
                modified = actual.replace("if (off == 0 || off == 0x10)", "if (0)")
            elif variant == "collision":
                modified = actual.replace("M32(at) = additions[i].donor_model;", "M32(at) = key;")
            elif variant == "metadata":
                modified = actual.replace("uint32_t copy = gmalloc(bytes);", "uint32_t copy = ld;")
            elif variant == "unrelated_vaults":
                modified = actual.replace("if (!relevant) { car85_vault_unloaded(vault); return; }", "if (!relevant) return;")
            (tmp / "car85.c").write_text(modified)
            binary = tmp / variant
            command = ["clang", "-O1", "-g", "-I", str(tmp), "-I", str(ROOT / "runtime"),
                       "-I", str(ROOT / "recomp"), "-I", str(ROOT / "recomp/out"), str(tmp / "test.c"),
                       "-framework", "CoreGraphics", "-framework", "CoreText", "-framework", "CoreFoundation", "-o", str(binary)]
            build = subprocess.run(command, text=True, capture_output=True)
            if build.returncode:
                print(build.stderr[-7000:]); raise SystemExit(build.returncode)
            if variant == "good":
                ordinary = tmp / "ordinary-profile"; ordinary.mkdir(exist_ok=True)
                unsafe = [dict(test_env), dict(test_env, BOOT_USERDATA=str(ordinary))]
                unsafe[0].pop("BOOT_USERDATA", None)
                unsafe.append(dict(test_env, BOOT_USERDATA=str(Path.home() / "Library/Application Support/NFSMW-port")))
                for index, env in enumerate(unsafe):
                    guard = subprocess.run([str(binary), "activation"], env=env, text=True, capture_output=True)
                    expected = 0 if old else 2
                    assert guard.returncode == expected, (old, index, guard.returncode, guard.stderr)
                scratch = tmp / "diag.user"; held = tmp / "held-scratch"
                scratch.rename(held); scratch.symlink_to(ordinary, target_is_directory=True)
                guard = subprocess.run([str(binary), "activation"], env=test_env, text=True, capture_output=True)
                assert guard.returncode == (0 if old else 2), guard.stderr
                scratch.unlink(); held.rename(scratch)
                guard = subprocess.run([str(binary), "activation"], env=test_env, text=True, capture_output=True)
                assert guard.returncode == 0, guard.stderr
                print(f"scratch guard: {'OLD ACCEPTS' if old else 'rejects'} {len(unsafe)+1} unsafe profiles; valid scratch accepted")
            for option in (["off", "on"] if variant == "good" else ["on"]):
                result = subprocess.run([str(binary), option], text=True, capture_output=True, env=test_env)
                print(variant + " " + result.stdout.strip())
                print(result.stderr[-3500:], end="")
                results.append((variant, option, result.returncode))
        if old:
            assert all(row[2] != 0 for row in results), "reviewed code escaped the regressions"
            print("PASS: reviewed revision code rejected in both OFF and ON modes; unsafe activation reproduced")
            return
        assert results[0][2] == 0 and results[1][2] == 0, results
        assert results[2][2] != 0, "broken count variant escaped detection"
        assert results[3][2] != 0, "broken asset-name variant escaped detection"
        assert results[4][2] != 0 and results[5][2] != 0, "collision/metadata control escaped detection"
        assert results[6][2] != 0, "unrelated vault capacity control escaped detection"
        print("PASS: good off/on exit=0/0; five broken controls rejected; 11 count + 7 asset overrides and adjacent-address exclusions")


if __name__ == "__main__":
    main()
