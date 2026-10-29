"""Ordered manifest proof. Good 0/1/2: 200/260/316 checks, 0 failures.
Missing strings: 8/316; borrowed NameId: 2/316; missing logos: 6/298;
lot owned-row guard removed: 2/316 failures (transition plus early cash change).
Missed bound/duplicate ID: exit 2. CoreText raster runs offline; upload is stubbed.
Uses actual PC records, relocated load descriptors and translated lookup/bound code.
Guest construction/FE enumeration is a contract fixture, not native proof.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
import car85_test as base

HARNESS = base.HARNESS[:base.HARNESS.index('int main(int argc, char **argv)')]
# Stub the same guest contracts for multiple explicitly known fixture identities.
KEYS = {name: base.hash32(name.lower()) for name in ("CAR85", "R8", "CCX", "911TURBO", "997S")}
MODEL_KEYS = {name: base.hash32(name) for name in KEYS}
LOOKUPS = "\n".join(f'if (!strcasecmp(s,"{name}")) return 0x{key:08X}u;' for name,key in KEYS.items())
MODELS = "\n".join(f'if (!strcmp(s,"{name}")) return 0x{key:08X}u;' for name,key in MODEL_KEYS.items())
HARNESS = HARNESS.replace('void f_00590D40_orig(Cpu *c) {', 'static uint32_t lookup(const char *s) { '+LOOKUPS+' abort(); }\nstatic uint32_t model(const char *s) { '+MODELS+' abort(); }\nvoid f_00590D40_orig(Cpu *c) {')
a=HARNESS.index('    M32(db)=0;'); b=HARNESS.index('void f_0045C380_orig',a)
HARNESS=HARNESS[:a]+r'''    // A fresh DB contract: 50 stock and 31 preset rows, including intro.
    for (unsigned i=0;i<81;++i) {
        uint32_t row=db+i*20; M32(row)=i; M32(row+8)=0x10000+i;
        M32(row+12)=i<50?0x10001:0xF0008; M8(row+16)=i<50?255:i-50;
    }
    unsigned extra=0;
    for(unsigned j=0;j<nadditions;++j) {
        Source *pv=source(PVEHICLE,additions[j].pv);
        if(pv && has_addition(pv->vault,PVEHICLE,additions[j].key)) {
            uint32_t row=db+(81+extra)*20;
            M32(row)=81+extra; M32(row+4)=M32(row+8)=additions[j].key;
            M32(row+12)=0x10001; M16(row+16)=0xFFFF; ++extra;
        }
    }
    fe_count=81+extra; c->esp+=4;
}
'''+HARNESS[b:]
HARNESS=HARNESS.replace('case 0x004549B0: c->eax=!strcasecmp((char *)G+args[0],"CAR85")?NEWKEY:0xD27D5B8D; break;', 'case 0x004549B0: c->eax=lookup((char *)G+args[0]); break;')
HARNESS=HARNESS.replace('M32(c->ecx+8)=0xDB10DBEA;', 'M32(c->ecx+8)=model((char *)G+args[0]);')
HARNESS=HARNESS.replace('CHECK(M32(ld)==NEWKEY); CHECK(M32(ld+8)!=NEWKEY);', 'CHECK(M32(ld)!=M32(ld+8));')
HARNESS=HARNESS.replace('CHECK(!strcmp((char *)G+M32(M32(ld+0x1C)+0x1C),"CAR85"));', 'CHECK(lookup((char *)G+M32(M32(ld+0x1C)+0x1C))==M32(ld));')
HARNESS=HARNESS.replace('CHECK(M32(ref+4)==NEWKEY);', 'CHECK(M32(ref+4)==M32(ld));')
HARNESS=HARNESS.replace('case 0x007398D0:', 'case 0x0056BB80: f_0056BB80_orig(c); break;\n    case 0x007398D0:')
HARNESS=HARNESS.replace('case 0x007398D0:', 'case 0x00503400: f_00503400(c); break;\n    case 0x007398D0:')
HARNESS=HARNESS.replace('uint32_t call_guest(', 'static unsigned lot_calls;\nuint32_t call_guest(')
HARNESS=HARNESS.replace('case 0x007398D0:', 'case 0x00605250: ++lot_calls; c->eax=123; c->ecx=456; break;\n    case 0x007398D0:')
HARNESS+=r'''
void f_006CB320(Cpu *c) { c->eax=0; c->esp+=4; }
void f_0045CD20(Cpu *c) { (void)c; abort(); }
static void check_names(Cpu *c, unsigned expected) {
    for (unsigned j=0;j<expected;++j) {
        char label[96];
        const char *brand=j==0?"AUDI":"KOENIGSEGG";
        for (unsigned k=0;k<2;++k) {
            if(k==0) snprintf(label,sizeof label,"CARNAME_%s_%s",brand,additions[j].name);
            else snprintf(label,sizeof label,"CAR_NAME_%s",additions[j].name);
            c->edx=bstr(label); uint32_t sp=c->esp;
            uint32_t p=call_guest(c,0x56BB80,0,NULL);
            CHECK(p && !strcmp((char *)G+p,additions[j].name)); CHECK(c->esp==sp);
        }
    }
}
int main(int argc, char **argv) {
    assert(argc==2); unsigned expected=(unsigned)atoi(argv[1]);
    G=calloc(1,0x4000000); assert(G);
    Cpu c={0}; c.esp=0x800000;
    uint32_t chunk=0x200000; M32(chunk)=0x34600; M32(chunk+4)=0x4448;
    load("TABLE_PATH",chunk+16,84*208);
    uint8_t original[84*208]; memcpy(original,G+chunk+16,sizeof original);
    M32(c.esp+4)=chunk; f_00756A90(&c);
    CHECK(car85_count()==84+expected); CHECK(nadditions==expected);
    CHECK(!memcmp(original,G+M32(TABLE),sizeof original));
    for(unsigned i=0;i<84;++i) {
        uint32_t hash=M32(chunk+16+i*208+0x50), type=M32(chunk+16+i*208+0x90);
        CHECK(call_guest(&c,0x7398D0,1,&hash)==type);
        CHECK(call_guest(&c,0x739940,1,&hash)==M32(TABLE)+i*208);
    }
    uint32_t missing=bstr("NOT_A_CAR"); CHECK(call_guest(&c,0x7398D0,1,&missing)==84+expected);
    for(unsigned j=0;j<expected;++j) {
        Addition *a=&additions[j]; uint32_t hash=bstr(a->name);
        CHECK(call_guest(&c,0x7398D0,1,&hash)==84+j);
        CHECK(call_guest(&c,0x739940,1,&hash)==M32(TABLE)+(84+j)*208);
        CHECK(M32(M32(TABLE)+(84+j)*208+0x90)==84+j);
        CHECK(!strcmp((char *)G+M32(TABLE)+(84+j)*208,a->name));
        CHECK(a->type<84); CHECK(a->record==M32(TABLE)+a->type*208);
        uint32_t defaults=gmalloc(139*8); M32(0x9B09EC)=defaults;
        M32(defaults)=~0u; M32(defaults+4)=~0u; M32(0x9B09F4)=0;
        uint32_t slot_args[2]={0,84+j};
        CHECK(M32(call_guest(&c,0x7399F0,2,slot_args))==a->hash);
        char guest[128],host[4096]; snprintf(guest,sizeof guest,"CARS/%s/GEOMETRY.BIN",a->name);
        CHECK(car85_path(guest,host,sizeof host)); CHECK(!strncmp(host,a->directory,strlen(a->directory)));
        CHECK(car85_asset_name(M32(TABLE)+(84+j)*208+16)==a->record+16);
        snprintf(guest,sizeof guest,"CARS/%s/../escape",a->name); CHECK(!car85_path(guest,host,sizeof host));
        uint32_t hashes=gmalloc(4); M32(hashes)=a->hash; M32(0x9B09C4)=hashes; M32(0x9B09C8)=1;
        CHECK((call_guest(&c,0x739870,1,&hash)&255)==0);
    }
    LOAD_FIXTURES
    uint32_t alloc_obj=gmalloc(4),vt=gmalloc(4); M32(0x8F7EA8)=alloc_obj; M32(alloc_obj)=vt; M32(vt)=0x1001000;
    uint32_t dbvault=setup_vault(),fevault=setup_vault();
    REGISTER_SOURCES
    Cpu before=c; car85_vault_loaded(&c,dbvault); CHECK(!memcmp(&before,&c,sizeof c));
    car85_vault_loaded(&c,fevault); CHECK(!memcmp(&before,&c,sizeof c));
    CHECK(constructor_count==3*expected);
    for(unsigned j=0;j<expected;++j) {
        Addition *a=&additions[j];
        uint32_t list=gmalloc(8),node=gmalloc(24),keys=gmalloc(8),bounds=gmalloc(16);
        M32(list)=node; M32(node)=list; M32(node+16)=keys; M32(node+20)=keys+8;
        M32(keys)=a->donor_model; M32(keys+4)=bounds; c.ecx=list;
        CHECK(call_guest(&c,0x681340,1,&a->model_key)==bounds);
    }
    car85_vault_loaded(&c,dbvault); car85_vault_loaded(&c,fevault); CHECK(constructor_count==3*expected);
    for(unsigned j=0;j<expected;++j) {
        CHECK(has_addition(dbvault,PVEHICLE,additions[j].key));
        CHECK(has_addition(dbvault,ECAR,additions[j].key));
        CHECK(has_addition(fevault,FRONTEND,additions[j].key));
        uint32_t exports=M32(fevault+0x2C),keys=M32(fevault+0x30),clone=0;
        for(unsigned i=0;i<M32(fevault+0x34);++i)
            if(M32(keys+4*i)==additions[j].key) clone=M32(M32(exports+16*i)+0x18);
        CHECK(clone!=0);
        uint32_t donor=M32(source(FRONTEND,additions[j].fe)->ld+0x1C);
        CHECK(!strcmp((char *)G+M32(clone+0x18),additions[j].name));
        CHECK(M8(clone+0x40)==(j==0?5:28));
        CHECK(M32(clone+0x38)==M32(donor+0x38));
        CHECK(M32(clone+0x4C)==M32(donor+0x4C));
        CHECK(M8(clone+0x58)==M8(donor+0x58));
        CHECK(M8(clone+0x59)==M8(donor+0x59));
    }
    // Simulate the language resource loader's relocated table, including both
    // ends of the unsigned sort order; search is the actual PC translation.
    uint32_t stock_text=gstr("stock text"),language=gmalloc(16);
    M32(language)=0; M32(language+4)=stock_text;
    M32(language+8)=~0u; M32(language+12)=stock_text;
    M32(0x91CF80)=language; M32(0x91CF78)=2;
    M32(0x9173F8)=0x9173F8; M32(0x9173FC)=0x9173F8;
    M32(0x915FA8)=36; // the real port can fill this before FE Default
    c.ecx=gmalloc(0x8CC8); uint32_t db=c.ecx; f_00590D40(&c); CHECK(fe_count==81+expected);
    CHECK(logo_count==expected+(expected==2));
    CHECK(M32(0x915FA8)==36); // private packs never consume extra-texture slots
    for(unsigned j=0;j<expected;++j) {
        char label[64]; snprintf(label,sizeof label,"SECONDARY_LOGO_%s",additions[j].name);
        uint32_t args[3]={bstr(label),0,0};
        uint32_t ti=call_guest(&c,0x503400,3,args);
        CHECK(ti && ti==additions[j].logo); CHECK(ti && M32(ti+0x70));
        // Normal lookup moves the pack to the list head; subsequent lookup hits
        // the game's real cache. Both paths must return the same nameplate.
        CHECK(call_guest(&c,0x503400,3,args)==ti);
    }
    CHECK(M32(0x91CF78)==2+2*expected); check_names(&c,expected);
    uint32_t registered=M32(0x91CF80); register_names();
    CHECK(M32(0x91CF80)==registered); CHECK(M32(0x91CF78)==2+2*expected);
    c.edx=0; CHECK(call_guest(&c,0x56BB80,0,NULL)==stock_text);
    c.edx=~0u; CHECK(call_guest(&c,0x56BB80,0,NULL)==stock_text);
    // Unload/reload with a different language table: old stock pointers must
    // disappear, while both invariant car names are registered again.
    M32(0x91CF80)=0; M32(0x91CF78)=0; register_names();
    CHECK(M32(0x91CF80)==0);
    uint32_t second_text=gstr("second language"),second=gmalloc(16);
    memcpy(G+second,G+language,16); M32(second+4)=second_text; M32(second+12)=second_text;
    M32(0x91CF80)=second; M32(0x91CF78)=2;
    c.ecx=db; f_005816B0(&c); check_names(&c,expected);
    CHECK(logo_count==expected+(expected==2)); // no duplicate texture slots after refresh
    CHECK(M32(0x91CF78)==2+2*expected);
    c.edx=0; CHECK(call_guest(&c,0x56BB80,0,NULL)==second_text);
    for(unsigned j=0;j<expected;++j) {
        unsigned matches=0; for(unsigned i=0;i<200;++i) matches+=M32(db+i*20+8)==additions[j].key;
        CHECK(matches==1);
    }
    // All eleven actual instruction overrides must agree with the same sentinel.
    INSTRUCTION_CHECKS
    // Vault retirement/reload rebuilds every FE addition once, with no stale source.
    uint32_t templates[512]; unsigned nt=0;
    for(unsigned i=0;i<nsources;++i) if(sources[i].vault==fevault) {
        uint32_t ld=sources[i].ld,n=0x20+4*M32(ld+0x18)+12*M32(ld+0x14);
        uint32_t copy=gmalloc(n); memcpy(G+copy,G+ld,n); templates[nt++]=copy;
    }
    c.ecx=fevault; f_0045C090(&c); memset(G+fevault,0xDD,0x40);
    fevault=setup_vault(); for(unsigned i=0;i<nt;++i) car85_collection_seen(templates[i],fevault);
    car85_vault_loaded(&c,fevault); CHECK(constructor_count==4*expected);
    for(unsigned j=0;j<expected;++j) CHECK(has_addition(fevault,FRONTEND,additions[j].key));
    if(expected) {
        uint32_t fedb=gmalloc(0x200),row=gmalloc(20); M32(0x91CF90)=fedb;
        uint32_t profile=gmalloc(0xA000); M32(fedb+0x10)=profile;
        M32(profile+0xB4)=10000; setenv("NFS_TEST_ADDCARS_PURCHASE","1",1);
        M32(0x925E90)=6; M32(fedb+0x12C)=1; M32(row+0xC)=2;
        unsetenv("NFS_TEST_ADDCARS_CARLOT"); car_lot_probe(&c,row); CHECK(lot_calls==0);
        setenv("NFS_TEST_ADDCARS_CARLOT","1",1);
        diag_on=0; car_lot_probe(&c,row); CHECK(lot_calls==0); diag_on=1;
        M32(0x925E90)=3; car_lot_probe(&c,row); CHECK(lot_calls==0); M32(0x925E90)=6;
        M32(fedb+0x12C)=0; car_lot_probe(&c,row); CHECK(lot_calls==0); M32(fedb+0x12C)=1;
        M32(row+0xC)=1; car_lot_probe(&c,row); CHECK(lot_calls==0); M32(row+0xC)=2;
        CHECK(M32(profile+0xB4)==10000);
        Cpu saved=c; car_lot_probe(&c,row); CHECK(lot_calls==1); CHECK(!memcmp(&saved,&c,sizeof c));
        CHECK(M32(profile+0xB4)==200000);
        car_lot_probe(&c,row); CHECK(lot_calls==1); unsetenv("NFS_TEST_ADDCARS_CARLOT");
        unsetenv("NFS_TEST_ADDCARS_PURCHASE");
    }
    printf("%s additions=%u types=%u checks=%u failures=%u constructors=%u\n", failures?"FAIL":"PASS", expected,car85_count(),checks,failures,constructor_count);
    return failures?1:0;
}
'''


def main(extra_addresses=(), variants=None):
    if not Path(base.recomp.EXE).is_file():
        print("SKIP: private MW assets absent"); return 3
    rc=base.recomp.Recompiler(pe=base.recomp.PE()); rc.calls=True
    starts=sorted(int(line.split("\t")[0],16) for line in (base.ROOT/"recomp/functions_v13.tsv").read_text().splitlines() if re.match(r"^[0-9A-Fa-f]{8}(?:\t|$)",line))
    rc.functions=dict(zip(starts,starts[1:]))
    generated=[]
    for address in [0x7398D0,0x739940,0x7398A0,0x7399F0,0x739870,0x460BF0,0x681340,0x56F090,0x739920,0x56BB80,0x503400,0x502E80,0x4FB210, *extra_addresses]:
        code,error=rc.translate(address,rc.functions.get(address,address+2048)); assert not error,error
        if address in [0x7399F0,0x739870,0x681340,0x56F090,0x739920,0x56BB80,0x5ACB70]:
            code=code.replace(f"void f_{address:08X}(",f"void f_{address:08X}_orig(",1)
        generated.append(code)
    checks=[]; functions=[]
    for i,address in enumerate(base.SITES):
        insn=next(rc.md.disasm(rc.pe.read(address,15),address)); code=rc.insn(insn,0,0)
        assert any("car85_count()" in line for line in code)
        functions.append(f"static uint32_t bound_{i}(Cpu *c) {{ uint8_t cf=0,pf=0,af=0,zf=0,sf=0,of=0; {' '.join(code)} return zf; }}")
        if insn.mnemonic=="cmp":
            reg=insn.op_str.split(",")[0]; scale=208 if "4440" in insn.op_str else 1
            checks.append(f"c.{reg}=(84+expected)*{scale}; CHECK(bound_{i}(&c));")
        elif insn.mnemonic=="mov": checks.append(f"bound_{i}(&c); CHECK(c.eax==84+expected);")
        else: checks.append(f"bound_{i}(&c); CHECK(M32(c.esp)==84+expected); c.esp+=4;")
    with tempfile.TemporaryDirectory(prefix="addcars-test-") as directory:
        tmp=Path(directory); (tmp/"diag").mkdir(); (tmp/"diag.user").mkdir(); packs=tmp/"packs"; packs.mkdir()
        for name in ("GEOMETRY.BIN","TEXTURES.BIN"): (packs/name).write_bytes(b"fixture")
        bases,rows=base.fixtures(tmp); first=rows[0][0]
        source=HARNESS.replace("GENERATED","\n".join(generated+functions)).replace("TABLE_PATH",str(tmp/"table.bin"))
        source=source.replace("LOAD_FIXTURES","\n".join(f'load("{path}",{at},{n});' for at,n,path in bases))
        source=source.replace("REGISTER_SOURCES","\n".join(f'register_source({"dbvault" if group==first else "fevault"},{ld});' for group,ld in rows))
        source=source.replace("INSTRUCTION_CHECKS","\n".join(checks)); (tmp/"test.c").write_text(source)
        env=dict(os.environ,BOOT_DIAG=str(tmp/"diag"),BOOT_USERDATA=str(tmp/"diag.user")); env.pop("NFS_TEST_CAR85",None)
        actual=(base.ROOT/"runtime/car85.c").read_text()
        results=[]
        for variant in (variants or ("good","duplicate_id","missed_bound","missing_names","borrowed_name","missing_logos","lot_guard")):
            changed=actual
            if variant=="duplicate_id": changed=actual.replace('M32(extra + 0x90) = addition_id(j);', 'M32(extra + 0x90) = TYPES;')
            if variant=="missing_names":
                changed=actual.replace('M32(owned + n * 8 + 4) = a->string;', 'M32(owned + n * 8 + 4) = 0;')
                assert changed!=actual
            if variant=="borrowed_name":
                changed=actual.replace('M32(lay + 0x18) = a->string;', '/* deliberately retain donor NameId */')
                assert changed!=actual
            if variant=="missing_logos":
                changed=actual.replace('register_logos(c);', '/* deliberately omit secondary logos */')
                assert changed!=actual
            if variant=="lot_guard":
                changed=actual.replace('!(M32(row + 0xC) & 2)', '0 /* deliberately ignore owned-row guard */')
                assert changed!=actual
            if variant=="double_insert":
                changed=actual.replace('if (found) continue;', '/* deliberately insert again */')
                assert changed!=actual
            if variant=="partial_insert":
                changed=actual.replace('if (free_count < count) return', 'if (free_count < count) { M32(db) = 123; }\n    if (free_count < count) return')
                assert changed!=actual
            if variant=="low_level_only":
                changed=actual.replace('const char *reason = reconcile_stock(db, &inserted);', 'inserted = 0; const char *reason = NULL;')
                assert changed!=actual
            if variant=="missing_parts":
                changed=actual.replace('if (!call_guest(c, 0x00747C00, 1, &index))', 'if (0)')
                assert changed!=actual
            if variant=="seed_guard":
                changed=actual.replace('if (!scratch_profile()) fail("save seeding requires guarded scratch userdata");', '/* deliberately omit scratch seed guard */')
                assert changed!=actual
            if variant=="capacity_boundary":
                changed=actual.replace('if (free_count < count) {', 'if (free_count <= count) {')
                assert changed!=actual
            if variant=="catalog_crc":
                changed=actual.replace('M32(frontend + 8) = stored;', '/* deliberately retain incompatible running catalog CRC */')
                assert changed!=actual
            if variant=="crc_guard":
                changed=actual.replace('if (!frontend || !scratch_profile()) return current;', 'if (!frontend) return current;')
                assert changed!=actual
            if variant=="ordinary_profile_guard":
                changed=actual.replace('if (!on() && !scratch_profile()) return;', '/* deliberately validate ordinary profiles */',1)
                assert changed!=actual
            assert variant in ("good","loader_hook","missed_bound") or changed != actual, variant
            (tmp/"car85.c").write_text(changed)
            code=source
            if variant=="loader_hook":
                code=code.replace('    car85_profile_loaded(c, self);', '    /* deliberately omit the production admission call */',1)
                assert code!=source
            if variant=="missed_bound":
                # Leave exactly one of eleven generated instructions at stock 84.
                code=code.replace('car85_count() * 208u','84u * 208u',1)
                assert code!=source
            (tmp/"test.c").write_text(code)
            binary=tmp/variant
            build=subprocess.run(["clang","-O1","-I",str(tmp),"-I",str(base.ROOT/"runtime"),"-I",str(base.ROOT/"recomp"),"-I",str(base.ROOT/"recomp/out"),str(tmp/"test.c"),"-framework","CoreGraphics","-framework","CoreText","-framework","CoreFoundation","-o",str(binary)],text=True,capture_output=True)
            assert build.returncode==0,build.stderr[-7000:]
            for n in ((0,1,2) if variant=="good" else (2,)):
                manifest=tmp/"manifest.tsv"; manifest.write_text("".join(f"{name}\t{donor}\t{packs}\n" for name,donor in list(zip(("R8","CCX"),("997S","911TURBO")))[:n]))
                result=subprocess.run([str(binary),str(n)],env=dict(env,NFS_TEST_ADDCARS=str(manifest)),text=True,capture_output=True)
                print(variant,result.stdout.strip()); print(result.stderr[-1800:],end="")
                assert (result.returncode==0)==(variant=="good"),(variant,n,result.returncode)
                results.append((variant,n,result.returncode))
        malformed = {
            "duplicate_folder": f"R8\t997S\t{packs}\nr8\t911TURBO\t{packs}\n",
            "original_folder": f"997S\t997S\t{packs}\n",
            "missing_donor": f"R8\tNO_DONOR\t{packs}\n",
            "missing_pack": f"R8\t997S\t{tmp}/missing\n",
            "bad_folder": f"../R8\t997S\t{packs}\n",
            "wrong_fields": "R8 997S /tmp\n",
            "too_many": "".join(f"CAR{i}\t997S\t{packs}\n" for i in range(77)),
        }
        for label,contents in malformed.items():
            manifest=tmp/"invalid.tsv"; manifest.write_text(contents)
            result=subprocess.run([str(tmp/"good"),"2"],env=dict(env,NFS_TEST_ADDCARS=str(manifest)),text=True,capture_output=True)
            assert result.returncode==2,(label,result.returncode,result.stderr[-1000:])
        manifest=tmp/"manifest.tsv"; manifest.write_text(f"R8\t997S\t{packs}\n")
        for label,extra in [("both switches",dict(NFS_TEST_CAR85="911TURBO")),("ordinary profile",dict(BOOT_USERDATA=str(tmp)))]:
            result=subprocess.run([str(tmp/"good"),"1"],env=dict(env,NFS_TEST_ADDCARS=str(manifest),**extra),text=True,capture_output=True)
            assert result.returncode==2,(label,result.returncode,result.stderr[-1000:])
        print(f"PASS: {len(malformed)} invalid manifests, conflicting switches and ordinary profile rejected")
        print("PASS: manifest 0/1/2, original 84 preserved, names/language reload, three classes per entry, vault reload; all mutants rejected",results)
    return 0

if __name__=="__main__": raise SystemExit(main())
