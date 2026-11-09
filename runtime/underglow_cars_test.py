"""Full player roster and native default through production store/light/ePoly.
Game PlayerUsable + nonempty MODEL: 48 stock + 76 additions + 14 replacements.
Default Off: all 138 stay dark after colour selection, then explicit Light On
submits 4 strips + 1 pool each, Measured and Stronger: 5659108/5659108 each.
Changed fresh-colour assertion on ORIGINAL On code: 41002/41004 (2 failures,
4 strips/1 pool/3 allocations); fixed: 7/7, zero submissions/allocations.
Pinned original choice_get reproduces that failure, no-colour 5/6 and roster
276 failures; fixed no-colour 6/6. Fragments are SHA-256 checked, no history reads.
Saved v1 colour and restart retain On: 41012/41012 each; explicit Off 8/8,
v2 Off 10/10 and zero Off 9/9 retain Off. Dropped zero fails 1/9; missing v1
migration fails 2/10 in both load/restart. Pinned older core/decode fragments
also fail those v1 and zero-retention checks while accepting the Off default.
"""
from pathlib import Path
import hashlib
import os
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import underglow_light_test as light


def player_fixtures():
    """Enumerate the game and catalog first; geometry cannot shrink either set."""
    sys.path.insert(0, str(ROOT / 'tools_snapshot'))
    sys.path.insert(0, str(ROOT / 'tools'))
    try:
        import carbon_car as cc
        import private_cars
        import uc_car
        from uc_batch import Source
        from wheel_attrib import Wheels
        from setup_kit.input_paths import input_path
    except ModuleNotFoundError:
        return None
    game = ROOT.parent / 'Need For Speed Most Wanted Black Edition'
    source_paths = {'MW': game, 'C': input_path('NFSC'),
                    'P': input_path('Need for Speed ProStreet')}
    uc_path = ROOT.parent / 'ext_untrusted/undercover_x360'
    if not all((p / 'GLOBAL/ATTRIBUTES.BIN').is_file() for p in source_paths.values()) or not uc_path.is_dir():
        return None
    pv = Wheels(game / 'GLOBAL/ATTRIBUTES.BIN', 'mw', 'pvehicle')
    stock = set()
    # FEPlayerCarDB::Default enumerates pvehicle PlayerUsable and nonempty MODEL.
    # MODEL is a relocated string-key tuple at layout+10, string pointer at +1C.
    for key, (vault, off, modern) in pv.collections.items():
        assert not modern
        try:
            usable = pv.raw(key, 'PlayerUsable') != b'\0'
        except ValueError as error:
            assert str(error).startswith('attribute absent:'), error
            usable = False
        if not usable:
            continue
        dep, pos = vault.deref(0, off + 28)
        target = vault.deref(dep, pos + 0x1c)
        model = vault.cstr(*target) if target else ''
        if model:
            stock.add(model)
    assert 'BMWM3GTR' in stock and 'BMWM3GTRE46' in stock
    catalog = private_cars.identities()
    additions = [row for row in catalog if row['role'] == 'addition']
    swaps = [row for row in catalog if row['role'] == 'swap']
    assert len(additions) == private_cars.CENSUS['final_additions'] == 76
    assert len({r['id'] for r in additions}) == len(additions)
    assert all(row['name'] in stock for row in swaps)
    required = ('BMWM3GTRE46', 'CARRERAGT', 'PUNTO', 'ELISE', 'MUSTANGGT', 'CLIO', '997S')
    ordered = list(required) + sorted(stock - set(required))
    roster = [dict(game='MW', folder=name, role='stock') for name in ordered] + additions + swaps
    readers = {tag: Wheels(path / 'GLOBAL/ATTRIBUTES.BIN', kind)
               for (tag, path), kind in zip(source_paths.items(), ('mw', 'carbon', 'prostreet'))}
    readers['U'] = Wheels(uc_path, 'undercover')
    uc_source = Source(uc_path)
    rows = []
    try:
        for row in roster:
            tag, name = row['game'], row['folder']
            data = (uc_source.car_file(name, 'GEOMETRY.BIN') if tag == 'U' else
                    (source_paths[tag] / 'CARS' / name / 'GEOMETRY.BIN').read_bytes())
            # The solid index names stock bodies by their hash. Decode just
            # those entries instead of every compressed part in every pack.
            index = cc.index(data, 0x134004)
            wanted = {cc.name_hash(name + suffix) for suffix in
                      ('_KIT00_BODY_A', '_KIT00_DAMAGE0_BODY_A')}
            selected = [entry for entry in index if entry[0] in wanted]
            assert selected, (tag, name, 'missing stock body index')
            unpack = uc_car.unpack_entry if tag == 'U' else cc.unpack_entry
            solids = [(entry[0], unpack(data, entry)) for entry in selected]
            headers = {}
            for _, solid in solids:
                header = cc.payload(solid, 0x134011, 16)
                version = struct.unpack_from('<H', header, 12)[0]
                start = 200 if tag == 'U' else 184 if version == 25 else 160
                label = header[start:].split(b'\0')[0].decode('ascii')
                headers[label] = header
            candidates = [label for label in headers if label.endswith('_BODY_A')]
            assert candidates, (tag, name, 'missing body; roster entry cannot be skipped')
            # Police stock bodies have DAMAGE0; later packs may name stock KIT00.
            candidates.sort(key=lambda s: (s != name + '_KIT00_BODY_A', 'DAMAGE0' not in s, s))
            header = headers[candidates[0]]
            lo, hi = (struct.unpack_from('<3f', header, at) for at in (32, 48))
            # Converters retain these car-local axes, metres and wheel centres.
            # No donor wheel substitution is used for a pending catalog addition.
            wheel = readers[tag].extract(name)
            fields = (lo, hi, wheel['TireOffsets'], wheel['TireSkidWidth'])
            label = tag + '/' + name.lower()
            rows.append('{"' + label + '",' + ','.join(
                '{' + ','.join(format(v, '.9g') for v in field) + '}' for field in fields) + ',1}')
    finally:
        uc_source.close()
    print(f'roster: stock={len(stock)} additions={len(additions)} swaps={len(swaps)}; '
          f'player models={len(stock) + len(additions)} fixtures={len(rows)}', flush=True)
    return ('struct CarFixture {const char *name;float lo[3],hi[3],wheels[16],widths[4];int supported;};\n'
            'static const struct CarFixture fixtures[]={\n' + ',\n'.join(rows) + '};\n',
            len(stock), len(additions), len(swaps))


NATIVE = r'''
static const char *data_dir;
const char *userdata_dir(void){return data_dir;}
void f_00590BD0_orig(Cpu *c){}
void f_00581370_orig(Cpu *c){}
#define active ug_profile_active
#include "native_core.c"
#undef active
#include "native_store.c"
static void seal(unsigned char *bytes,size_t n) {
 unsigned char digest[32];memset(bytes+16,0,32);CC_SHA256(bytes,(CC_LONG)n,digest);memcpy(bytes+16,digest,32);
}
static void legacy(unsigned version,unsigned colour) {
 unsigned char bytes[108]={0},digest[32];
 CHECK(ug_save_digest(G+0x200000,UG_SAVE_BYTES,digest));
 memcpy(bytes,"UGSNAP\0\0",8);put32(bytes+8,version);put32(bytes+12,1);
 memcpy(bytes+48,digest,32);put32(bytes+80,1);
 uint32_t row=profile+0x414+fixture_handle*20;
 for(unsigned i=0;i<4;++i)put32(bytes+84+4*i,M32(row+4*i));
 put32(bytes+96,G[row+16]);put32(bytes+100,M32(row+12)&6);put32(bytes+104,colour);
 seal(bytes,sizeof bytes);char path[4096];snprintf(path,sizeof path,"%s/underglow.bin",data_dir);
 FILE *f=fopen(path,"wb");CHECK(f!=NULL);if(f){CHECK(fwrite(bytes,1,sizeof bytes,f)==sizeof bytes);CHECK(!fclose(f));}
 underglow_profile_load(profile,0x200000,UG_SAVE_BYTES);
}
static void restart(void) {
 underglow_profile_save(profile,0x200000,UG_SAVE_BYTES);underglow_save_complete(1);
 underglow_profile_default(profile);underglow_profile_load(profile,0x200000,UG_SAVE_BYTES);
}
int main(int argc,char **argv) {
 CHECK(argc==3);if(argc!=3)return 2;data_dir=argv[1];
 saved_strength=!strcmp(argv[2],"all-cars-stronger");const char *mode=saved_strength?"all-cars":argv[2];
 G=calloc(1,16*1024*1024);CHECK(G!=NULL);if(!G)return 2;
 strcpy((char *)G+profile,"UNDERGLOW");underglow_profile_default(profile);
 M32(0x9B09D8)=0x70000;M32(ride)=41;M32(ride+0x48)=0x6200;M32(self+0x84)=ride;
 M32(view+4)=1;M32(0x982C80)=0x3000;M32(0x3004)=4;M32(0x6000)=0x6100;
 static Obj device;device.guest=0x4000;dev.self=&device;M32(0x4000)=0x4100;M32(0x415c)=0x1000;
 Cpu c={0};c.esp=0x90000;c.ecx=self;M32(c.esp+4)=view;M32(c.esp+8)=0x52100;M32(c.esp+12)=0x52000;
 for(unsigned i=0;i<4;++i)MF32(0x52000+20*i)=1;MF32(0x52108)=.25;c.eax=1;
 unsigned count=!strcmp(mode,"all-cars")?sizeof fixtures/sizeof fixtures[0]:1,passed=0,models=0,replacements=0;
 for(unsigned i=0;i<count;++i) {
  load_fixture(i);CHECK(underglow_owned(profile,fixture_handle));
  if(!strcmp(mode,"v1-colour")||!strcmp(mode,"v1-restart"))legacy(1,2);
  else if(!strcmp(mode,"v2-off"))legacy(2,2);
  else if(!strcmp(mode,"zero-off")){CHECK(underglow_light_set(profile,fixture_handle,0));restart();CHECK(live.count==1 && live.entries[0].preset==0);CHECK(underglow_set(profile,fixture_handle,2));}
  else if(strcmp(mode,"no-colour")){CHECK(underglow_set(profile,fixture_handle,2));}
  if(!strcmp(mode,"v1-restart"))restart();
  if(!strcmp(mode,"explicit-off")){CHECK(underglow_light_set(profile,fixture_handle,0));restart();}
  if(!strcmp(mode,"all-cars")) {
   CHECK(!underglow_light_get(profile,fixture_handle));
   unsigned bs=strips,bp=pools,ba=allocs;f_0074E160(&c);
   CHECK(strips==bs&&pools==bp&&allocs==ba);
   CHECK(underglow_light_set(profile,fixture_handle,1));
  }
  int on=!strcmp(mode,"all-cars")||!strcmp(mode,"v1-colour")||!strcmp(mode,"v1-restart");
  CHECK(underglow_light_get(profile,fixture_handle)==(unsigned)on);
  unsigned bs=strips,bp=pools,ba=allocs;Cpu saved=c;f_0074E160(&c);CHECK(!memcmp(&c,&saved,sizeof c));
  if(on) {
   CHECK(strips-bs==4&&pools-bp==1);
   if(strips-bs==4&&pools-bp==1){++passed;if(i<STOCK_COUNT+ADDITION_COUNT)++models;else ++replacements;}
   if(strips-bs==4&&pools-bp==1)verify_pool();
  } else CHECK(strips==bs&&pools==bp&&allocs==ba);
  printf("case=%s car=%s strips=%u pool=%u allocations=%u\n",mode,fixtures[i].name,strips-bs,pools-bp,allocs-ba);
 }
 if(!strcmp(mode,"all-cars"))printf("player coverage=%u/%u swaps=%u/%u fixtures=%u/%u (stock=STOCK_COUNT additions=ADDITION_COUNT)\n",models,STOCK_COUNT+ADDITION_COUNT,replacements,SWAP_COUNT,passed,count);
 printf("%s checks=%u failures=%u strips=%u pool=%u allocations=%u\n",mode,checks,failures,strips,pools,allocs);
 free(G);return failures?1:0;
}
'''


ORIGINAL_DEFAULT = 'static unsigned choice_get(uint32_t profile, uint32_t handle) {\n    UGIdentity car;\n    if (!identity(profile,handle,&car)) return 0;\n    for (unsigned i=0;i<live.count;++i)\n        if (ug_identity_equal(&car,&live.entries[i].car)) return live.entries[i].preset;\n    return UG_LIGHT_ON;\n}'
ORIGINAL_DEFAULT_SHA = 'd2b3bbb041cb3e1015c7923bcb9d4d25a6fd7d72d162deb5ed9539b4f82f1c7c'

# Function fragments from the prior Off default, not a production-file hash.
PINNED_CORE = [('static unsigned choice_get(uint32_t profile, uint32_t handle)', 'static unsigned choice_get(uint32_t profile, uint32_t handle) {\n    UGIdentity car;\n    if (!identity(profile,handle,&car)) return 0;\n    for (unsigned i=0;i<live.count;++i)\n        if (ug_identity_equal(&car,&live.entries[i].car)) return live.entries[i].preset;\n    return 0;\n}', '10af92fcc8b09301506f094f51f1844b8b3ebf7bcd09f57434d12e355513f15b'), ('static int choice_set(uint32_t profile, uint32_t handle, unsigned choice)', 'static int choice_set(uint32_t profile, uint32_t handle, unsigned choice) {\n    UGIdentity car;\n    if (!identity(profile,handle,&car)) return 0;\n    forget(handle);\n    if (choice) live.entries[live.count++]=(UGEntry){car,choice};\n    return 1;\n}', '5b831d8e0793adc6220747ca6e599904e7b0de5819de735e75a1a6fe02cc0a63')]
OLD_DECODE = '            if (get32(bytes+8)==1 && e->preset>7) return 0;\n'
OLD_DECODE_SHA = 'f06c096927b28898b8f37729a2159b96f91236ad966aef087e204a18d73d6803'

def main():
    result = player_fixtures()
    if result is None:
        print('SKIP: complete player car inputs required')
        return 3
    fixtures, stock, additions, swaps = result
    code = (ROOT / 'runtime/underglow_light.m').read_text()
    core = (ROOT / 'runtime/underglow.c').read_text()
    store = (ROOT / 'runtime/underglow_store.c').read_text()
    diag = (ROOT / 'runtime/diag.c').read_text()
    wrapper = diag[diag.index('\nunsigned diag_car1[4];') + 1:diag.index('void f_007387E0_orig')]
    harness = light.HARNESS.split('int main(')[0]
    start = harness.index('unsigned underglow_light_get(')
    end = harness.index('void diag_event(', start)
    harness = harness[:start] + harness[end:]
    # The old harness uses tiny guest addresses; give all catalog cars unique,
    # legal owned/customization handles without overlapping its render scratch.
    harness = harness.replace('fixture_handle=index?10+index:82;', 'fixture_handle=index;')
    harness = harness.replace('G[row+16]=index?index:7;', 'G[row+16]=index%76;')
    # Keep the actual per-car name at the retail table entry (not the label).
    harness = harness.replace('f->name);', 'strchr(f->name,\'/\')+1);')
    body = NATIVE.replace('STOCK_COUNT', str(stock)).replace('ADDITION_COUNT', str(additions)).replace('SWAP_COUNT', str(swaps))
    cases = ['fresh-colour', 'v1-colour', 'v1-restart', 'no-colour', 'explicit-off', 'v2-off', 'zero-off', 'all-cars']
    old_core = core
    for signature, old, digest in PINNED_CORE:
        assert hashlib.sha256(old.encode()).hexdigest() == digest
        old_core = old_core.replace(light.function_fragment(old_core, signature), old)
    assert hashlib.sha256(OLD_DECODE.encode()).hexdigest() == OLD_DECODE_SHA
    current_decode = OLD_DECODE + '            /* Colour-only snapshots predate the independent Light choice. */\n            if (get32(bytes+8)==1) e->preset|=UG_LIGHT_ON;\n'
    assert current_decode in store
    old_store = store.replace(current_decode, OLD_DECODE)
    old_set = next(old for signature, old, _ in PINNED_CORE if signature.startswith('static int choice_set'))
    dropped_zero = core.replace(light.function_fragment(core, 'static int choice_set'), old_set)
    assert hashlib.sha256(ORIGINAL_DEFAULT.encode()).hexdigest() == ORIGINAL_DEFAULT_SHA
    default_on = core.replace(light.function_fragment(core, 'static unsigned choice_get'), ORIGINAL_DEFAULT)
    variants = [('good', core, store, code), ('original-default-on', default_on, store, code), ('1cd7efb7', old_core, old_store, code),
                ('drop-zero-choice', dropped_zero, store, code), ('v1-migration-omitted', core, old_store, code)]
    with tempfile.TemporaryDirectory(prefix='ug-cars-') as td:
        tmp = Path(td)
        (tmp / 'diag_car_core.c').write_text((ROOT / 'runtime/diag_car.c').read_text().split('// BOOT_DIAG cartex:')[0])
        (tmp / 'harness.m').write_text(harness.replace('FIXTURES', fixtures).replace('PRODUCTION_WRAPPER', wrapper) + body)
        render = tmp / 'render.o'
        cp = subprocess.run(['clang', '-O1', '-Iruntime', '-Irecomp', '-DUG_RENDER_CORE_ONLY', '-c',
                             'runtime/underglow_render.m', '-o', str(render)], cwd=ROOT, capture_output=True, text=True)
        assert cp.returncode == 0, cp.stderr[-3000:]
        for name, native_core, native_store, native_light in variants:
            (tmp / 'native_core.c').write_text(native_core)
            (tmp / 'native_store.c').write_text(native_store)
            (tmp / 'light.m').write_text(native_light)
            exe = tmp / name
            cp = subprocess.run(['clang', '-O1', '-g', '-fsanitize=address,undefined', '-Wno-deprecated-declarations',
                                 '-Iruntime', '-Irecomp', str(tmp / 'harness.m'), str(tmp / 'light.m'), str(render),
                                 'runtime/underglow_photometry.c', '-framework', 'Metal', '-framework', 'Foundation', '-o', str(exe)],
                                cwd=ROOT, capture_output=True, text=True)
            assert cp.returncode == 0, cp.stderr[-4000:]
            selected = ['fresh-colour', 'no-colour', 'all-cars'] if name == 'original-default-on' else ['zero-off'] if name == 'drop-zero-choice' else ['v1-colour', 'v1-restart'] if name == 'v1-migration-omitted' else cases+(['all-cars-stronger'] if name=='good' else [])
            for case in selected:
                directory = tmp / (name + '-' + case)
                directory.mkdir()
                env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
                env.pop('NFS_UNDERGLOW', None)
                cp = subprocess.run([str(exe), str(directory), case], env=env, capture_output=True, text=True)
                print(name, cp.stdout.strip(), flush=True)
                expected_fail = name in ('original-default-on', 'drop-zero-choice', 'v1-migration-omitted') or name == '1cd7efb7' and case in ('v1-colour', 'v1-restart', 'zero-off')
                assert cp.returncode == int(expected_fail), cp.stderr[-5000:]
    print('underglow_cars_test PASS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
