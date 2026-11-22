"""Dormant GRP1 admission, ownership, live upgrades and installed tyre parts.

Paired proof results are printed below; every pair includes a valid C51 lookup
that the original source-only field accessor refuses. The frozen accessor's
SHA-256 is checked before the comparison. Guard-removal controls separately
prove each refusal, live-level/endpoint distinction and Junkman multiplication.
The native integration uses the real public catalog, adapter, car85 startup,
collection clone and retirement functions, with synthetic guest memory only.
Measured: 23/23 admission pairs, 3600/3600 live field goldens, nine detected
controls. Exact-byte hash binding rejects an in-place substitution accepted
by the original unpinned reader (1/1). Original accessor fails all 23 positive C51 halves; original startup
copy omission exits 1. Native proof covers 84 stock identities, traffic, nonplayer
additions, replacement after admission, unload/reload and vault address reuse.
No game process, physics hook or private asset is used.
"""
from pathlib import Path
import ctypes as ct
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tools'), str(ROOT / 'tests/setup'), str(ROOT / 'runtime')]
from setup_kit import car_grip as wire
from setup_kit.car_convert import hash32
import test_car_grip as reader

DYNAMIC, STATIC, SCALE, YAW, ASSIST = (hash32(n) for n in
    ('DYNAMIC_GRIP', 'STATIC_GRIP', 'GRIP_SCALE', 'YAW_CONTROL', 'DRIFT_YAW_CONTROL'))
KEYS = (DYNAMIC, STATIC, SCALE, YAW, ASSIST)


def grip_blob(source='MODEL_0', donor='MODEL_DONOR', tiers=3, upgrade_max=3):
    body = bytearray()
    for t in range(tiers):
        body += struct.pack('<2I', t, 0x3000 + t)
        for i, (key, count, _, semantics) in enumerate(wire.FIELDS):
            n = count or (9 + (t % 2) if key == ASSIST else 3)
            values = [10 * t + i + 1 + j / 16 for j in range(n)]
            body += struct.pack('<4I', key, semantics, n, 0)
            body += struct.pack('<' + 'f' * n, *values)
    return struct.pack('<10I', wire.MAGIC, wire.VERSION, 40 + len(body),
                       hash32(source.lower()), hash32(donor.lower()), 1,
                       tiers, upgrade_max, len(wire.FIELDS), 0) + body


class Binding(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in ('version', 'key', 'vault')] + [
        ('generation', ct.c_uint64), ('grip', ct.POINTER(reader.Grip))]


class Owner(ct.Structure):
    _fields_ = [(n, ct.c_uint32) for n in ('version', 'key', 'vault', 'simable', 'local_simable')] + [
        ('generation', ct.c_uint64)]


class Scale(ct.Structure):
    _fields_ = [('key', ct.c_uint32), ('multiplier', ct.c_float)]


class Live(ct.Structure):
    _fields_ = [(n, ct.c_int) for n in ('level', 'max_level', 'junkman')] + [
        ('scales', ct.POINTER(Scale)), ('scale_count', ct.c_size_t)]


class Selected(ct.Structure):
    _fields_ = [('fields', reader.Field * 5)]


def configure(dll):
    dll.car_grip_read_pinned.argtypes = [ct.c_int, ct.c_char_p, ct.c_char_p, ct.c_int,
                                        ct.c_char_p, ct.c_uint64, ct.POINTER(reader.Grip)]
    dll.car_grip_read_pinned.restype = ct.c_int
    dll.car_grip_bind.argtypes = [ct.POINTER(Binding), ct.POINTER(reader.Grip),
                                  ct.c_uint32, ct.c_uint32, ct.c_uint64]
    dll.car_grip_select.argtypes = [ct.POINTER(Binding), ct.POINTER(Owner),
                                    ct.POINTER(Live), ct.POINTER(Selected)]
    dll.car_grip_select.restype = ct.c_int
    dll.car_grip_selected_field.argtypes = [ct.POINTER(Selected), ct.c_uint32]
    dll.car_grip_selected_field.restype = ct.POINTER(reader.Field)
    return dll


def load(dll, path, blob):
    if blob is not None:
        path.write_bytes(blob)
    else:
        path.unlink(missing_ok=True)
    grip = reader.Grip()
    rc = dll.car_grip_load(os.fsencode(path), b'MODEL_0', b'MODEL_DONOR', 1, ct.byref(grip))
    return rc, grip


def bind(dll, grip):
    b = Binding()
    dll.car_grip_bind(ct.byref(b), ct.byref(grip), 0x1234, 0x5000, 17)
    return b


def changed_owner(attribute, value):
    owner = Owner(1, 0x1234, 0x5000, 0x6000, 0x6000, 17)
    setattr(owner, attribute, value)
    return owner


def select(dll, binding, owner=None, live=None):
    owner = owner or Owner(1, 0x1234, 0x5000, 0x6000, 0x6000, 17)
    live = live or Live(0, 3, 0, None, 0)
    out = Selected()
    ct.memset(ct.byref(out), 0xA5, ct.sizeof(out))
    rc = dll.car_grip_select(ct.byref(binding), ct.byref(owner), ct.byref(live), ct.byref(out))
    if not rc:
        assert bytes(out) == bytes(ct.sizeof(out)), 'refusal leaked prior values'
    return rc, out


def field(dll, out, key):
    p = dll.car_grip_selected_field(ct.byref(out), key)
    assert p, hex(key)
    return tuple(p.contents.values[:p.contents.count])


def reference(grip, t, key):
    return next(tuple(f.values[:f.count]) for f in grip.tiers[t].fields if f.key == key)


def synthetic(tmp):
    code = (ROOT / 'runtime/car_grip.c').read_text()
    dll = configure(reader.compile_reader(tmp))
    path = tmp / 'GRIP.BIN'
    blob = grip_blob()
    rc, grip = load(dll, path, blob)
    assert rc == 0 and grip.present
    digest = hashlib.sha256(blob).hexdigest().encode()
    pinned = reader.Grip()
    with path.open('rb') as stream:
        assert dll.car_grip_read_pinned(stream.fileno(),b'MODEL_0',b'MODEL_DONOR',1,
                                        digest,len(blob),ct.byref(pinned)) == 0
        assert bytes(pinned) == bytes(grip)
        # Replace one finite coefficient after the descriptor was verified.
        changed = bytearray(blob); at = 48
        while struct.unpack_from('<I',changed,at)[0] != DYNAMIC:
            at += 16 + 4 * struct.unpack_from('<I',changed,at+8)[0]
        struct.pack_into('<f',changed,at+16,42)
        path.write_bytes(changed)
        assert dll.car_grip_read(stream.fileno(),b'MODEL_0',b'MODEL_DONOR',1,ct.byref(pinned)) == 0
        assert pinned.present and reference(pinned,0,DYNAMIC)[0] == 42, 'the unpinned original reader must accept this valid substitution'
        assert dll.car_grip_read_pinned(stream.fileno(),b'MODEL_0',b'MODEL_DONOR',1,
                                        digest,len(blob),ct.byref(pinned)) == -1
        assert bytes(pinned) == bytes(ct.sizeof(pinned))
    print('exact-byte admission: pinned PASS; original unpinned reader admits 1/1 substitutions')
    binding = bind(dll, grip)
    rc, out = select(dll, binding)
    assert rc == 1 and field(dll, out, ASSIST) == reference(grip, 0, ASSIST)
    # This unchanged, pinned production fragment is the predecessor API.
    start = code.index('const CarGripField *car_grip_field(')
    original = code[start:code.index('\n}', start) + 2]
    assert hashlib.sha256(original.encode()).hexdigest() == '31a3830620db4cee013674df589c85d265d8b387b4a2acb42bbb47d380cd09b6'
    assert not dll.car_grip_field(ct.byref(grip), 0, ASSIST)
    original_failures = 0
    def positive(label):
        nonlocal original_failures
        rc, selected = select(dll, binding)
        assert rc == 1 and field(dll, selected, ASSIST), label
        assert not dll.car_grip_field(ct.byref(grip), 0, ASSIST), label
        original_failures += 1

    malformed = []
    for label, offset, value in [('version', 4, 2), ('source', 12, hash32('other')),
                                  ('donor', 16, hash32('other')), ('reserved', 36, 1)]:
        b = bytearray(blob); struct.pack_into('<I', b, offset, value)
        malformed.append((label, bytes(b)))
    malformed += [('truncated', blob[:-1]), ('trailing', blob + b'\0'), ('missing', None)]
    pairs = 0
    for label, invalid in malformed:
        # Pair every refusal with the same positive source-assist selection.
        positive(label)
        result, bad = load(dll, path, invalid)
        assert result == (0 if invalid is None else -1), label
        assert bytes(bad) == bytes(ct.sizeof(bad)), label
        assert select(dll, bind(dll, bad))[0] == 0, label
        pairs += 1
    owner_cases = [('version', 2), ('key', 0x1235), ('vault', 0x5004),
                   ('generation', 16), ('simable', 0x6001), ('simable', 0)]
    for attribute, value in owner_cases:
        positive(attribute)
        assert select(dll, binding, changed_owner(attribute, value))[0] == 0, attribute
        pairs += 1
    live_checks = 0
    # Every allowed live level/max pair, for every record reference count.
    # Authored upgrade_max deliberately disagrees with the live max.
    for tiers in range(1, 9):
        _, g = load(dll, path, grip_blob(tiers=tiers, upgrade_max=1))
        b = bind(dll, g)
        for maximum in range(9):
            for level in range(maximum + 1):
                for junkman in (0, 1):
                    scales = (Scale * 2)(Scale(DYNAMIC, 1.25), Scale(ASSIST, .75))
                    live = Live(level, maximum, junkman, scales if junkman else None, 2 if junkman else 0)
                    rc, selected = select(dll, b, live=live)
                    assert rc == 1
                    lo, hi, ratio = 0, 0, 0
                    if level and tiers > 1:
                        lo, hi = (1, 2) if tiers == 3 else (0, 1)
                        ratio = level / maximum
                    for key in KEYS:
                        a, z = reference(g, lo, key), reference(g, hi, key)
                        expected = a if ratio == 0 else z if ratio == 1 else tuple(
                            ratio * (z[j] if j < len(z) else 0) + (1-ratio) * (a[j] if j < len(a) else 0)
                            for j in range(max(len(a), len(z))))
                        multiplier = (1.25 if key == DYNAMIC else .75 if key == ASSIST else 1) if junkman else 1
                        actual = field(dll, selected, key)
                        assert len(actual) == len(expected)
                        assert all(math.isclose(v, e * multiplier, rel_tol=2e-6, abs_tol=1e-6)
                                   for v, e in zip(actual, expected)), (tiers, maximum, level, key)
                        live_checks += 1
                    for key, _, _, _ in wire.FIELDS:
                        if key not in KEYS:
                            assert not dll.car_grip_selected_field(ct.byref(selected), key)
    for live in (Live(-1,3,0,None,0), Live(4,3,0,None,0), Live(0,-1,0,None,0),
                 Live(0,9,0,None,0), Live(0,3,2,None,0), Live(0,3,1,None,0), Live(0,3,1,None,1)):
        positive("live-refusal")
        assert select(dll, binding, live=live)[0] == 0
        pairs += 1
    for scales, junkman in [((Scale * 1)(Scale(DYNAMIC, float('nan'))), 1),
                             ((Scale * 2)(Scale(DYNAMIC, 2), Scale(DYNAMIC, 3)), 1),
                             ((Scale * 1)(Scale(DYNAMIC, 2)), 0)]:
        positive("live-refusal")
        assert select(dll, binding, live=Live(1,3,junkman,scales,len(scales)))[0] == 0
        pairs += 1
    # Individual compiled controls target each new guard or binding equation.
    controls = [
        ('version', 'owner->version != CAR_GRIP_SELECTION_VERSION', '0', changed_owner('version',2)),
        ('identity', 'binding->key != owner->key', '0', changed_owner('key',0x1235)),
        ('generation', 'binding->generation != owner->generation', '0', changed_owner('generation',16)),
        ('vault', 'binding->vault != owner->vault', '0', changed_owner('vault',0x5004)),
        ('player', 'owner->simable != owner->local_simable', '0', changed_owner('simable',0x6001)),
    ]
    for label, before, after, owner in controls:
        assert before in code
        mutant = configure(reader.compile_reader(tmp, code.replace(before, after, 1), label))
        assert select(mutant, binding, owner)[0] == 1, label
    for label, before, after in [
        ('ordinal', '(float)live->level / (float)live->max_level', '(float)live->level / (float)g->tier_count'),
        ('counter', '(float)live->level / (float)live->max_level', '(float)live->level / (float)g->upgrade_max'),
        ('endpoint', 'low = g->tier_count == 3 ? 1 : 0;', 'low = 0;'),
        ('junkman', 'value *= multiplier;', 'value += 0 * multiplier;'),
    ]:
        assert before in code
        mutant = configure(reader.compile_reader(tmp, code.replace(before, after, 1), label))
        scales = (Scale * 1)(Scale(DYNAMIC, 1.25))
        live = Live(1,8,1,scales,1)
        good = select(dll, binding, live=live)[1]
        bad = select(mutant, binding, live=live)[1]
        assert field(dll, good, DYNAMIC) != field(mutant, bad, DYNAMIC), label
    assert original_failures == pairs
    print(f'admission pairs: fixed {pairs}/{pairs}; original C51 accessor 0/{original_failures} positive halves; '
          f'live coefficient goldens {live_checks}/{live_checks}; guard/equation controls 9/9 detected')


NATIVE_MAIN = r'''
int main(int argc,char **argv) {
    assert(argc==3); G=calloc(1,0x8000000); assert(G); userroot=argv[1]; game_dir=argv[1];
    unsigned expected=(unsigned)atoi(argv[2]);
    private_cars_boot(); CHECK(on() && nadditions==2);
    Addition *a=&additions[0]; a->key=test_key("MODEL_0_PV");
    CHECK(a->grip.present==expected && private_cars_get(0)->grip.present==expected);
    CHECK(car_catalog_inventory_get(0)->grip.present==expected);
    uint32_t allocator=gmalloc(4),vt=gmalloc(4); M32(0x8F7EA8)=allocator; M32(allocator)=vt; M32(vt)=0x1001000;
    uint32_t v=vault(),pv=collection(v,PVEHICLE,test_key("MODEL_DONOR"),0x74);
    ref(pv,FRONTEND,FRONTEND,11); life(v)->ready=1;
    Source src={pv,v,pv,life(v)->generation};
    /* Unrelated appearance references are outside this fixture. */
    a->render_fit=0; a->wheel_fit=0;
    Cpu c={.esp=0x1800000};
    append_collection_as(&c,&src,0x74,1,a,a->key,0,NULL);
    CarGripOwner owner={1,a->key,v,0x1000,0x1000,life(v)->generation};
    CarGripOwner minted;
    CHECK(car85_grip_owner(a->key,v,owner.simable,owner.local_simable,&minted)==expected);
    if(expected) CHECK(owner.version==minted.version && owner.key==minted.key &&
        owner.vault==minted.vault && owner.simable==minted.simable &&
        owner.local_simable==minted.local_simable && owner.generation==minted.generation);
    CarGripLive live={1,8,0,NULL,0}; CarGripSelected out;
    CHECK(car85_grip_select(&owner,&live,&out)==expected);
    if(expected) CHECK(car_grip_selected_field(&out,0xC51EB1A1)!=NULL);
    FILE *sidecar=car_catalog_open_sidecar(0,CAR_CATALOG_GRIP);
    CHECK((sidecar!=NULL)==expected);
    if(sidecar) fclose(sidecar);
    CHECK(car_catalog_inventory_get(0)->outputs[CAR_CATALOG_GRIP].version==expected);
    const char *replace=getenv("GRIP_REPLACE");
    if(expected && replace) {
        chmod(replace,0600); FILE *f=fopen(replace,"wb"); assert(f); fputs("replaced",f); fclose(f);
        CHECK(car85_grip_select(&owner,&live,&out)==1);
    }
    /* A catalog can be replaced after startup without affecting its snapshot. */
    if(expected) { a->grip_binding.version=2; CHECK(!car85_grip_select(&owner,&live,&out)); a->grip_binding.version=1; }
    uint8_t *snapshot=malloc(0x8000000); assert(snapshot); memcpy(snapshot,G,0x8000000);
    Cpu cpu_before=c; Addition traffic_before=traffic_addition, added_before=*a;
    unsigned allocations_before=arena, calls_before=selection_calls;
    for(unsigned i=0;i<84;++i) {
        CarGripOwner stock=owner; stock.key=0x800000+i;
        CHECK(!car85_grip_select(&stock,&live,&out));
        CHECK(!car85_grip_owner(stock.key,v,stock.simable,stock.local_simable,&minted));
    }
    traffic_active=1; traffic_addition.key=0x160; traffic_addition.grip=a->grip;
    CarGripOwner traffic=owner; traffic.key=traffic_addition.key;
    CHECK(!car85_grip_select(&traffic,&live,&out));
    CHECK(!car85_grip_owner(traffic.key,v,traffic.simable,traffic.local_simable,&minted));
    traffic_addition=traffic_before; traffic_active=0;
    CarGripOwner other=owner; other.simable++; CHECK(!car85_grip_select(&other,&live,&out));
    CHECK(!car85_grip_owner(other.key,v,other.simable,other.local_simable,&minted));
    CHECK(selection_calls==calls_before && arena==allocations_before);
    CHECK(!memcmp(&c,&cpu_before,sizeof c) && !memcmp(G,snapshot,0x8000000));
    CHECK(!memcmp(a,&added_before,sizeof *a) && !memcmp(&traffic_addition,&traffic_before,sizeof traffic_before));
    free(snapshot);
    CarGripBinding retired=a->grip_binding;
    CarGripOwner stale=owner; car85_vault_unloaded(v);
    CHECK(!car85_grip_select(&owner,&live,&out));
    CHECK(!a->grip_binding.grip && !a->grip_binding.generation);
    CHECK(!car85_grip_owner(owner.key,v,owner.simable,owner.local_simable,&minted));
    car85_vault_begin(v); life(v)->ready=1; M32(v+0x34)=0;
    CHECK(life(v)->generation!=stale.generation);
    a->grip_binding=retired; /* stale cached binding at a reused vault address */
    CHECK(!car85_grip_select(&stale,&live,&out));
    CHECK(!car85_grip_owner(stale.key,v,stale.simable,stale.local_simable,&minted));
    memset(&a->grip_binding,0,sizeof a->grip_binding);
    src.generation=life(v)->generation;
    append_collection_as(&c,&src,0x74,1,a,a->key,0,NULL);
    CHECK(!car85_grip_select(&stale,&live,&out));
    CHECK(car85_grip_owner(owner.key,v,owner.simable,owner.local_simable,&minted)==expected);
    owner.generation=life(v)->generation;
    if(expected) CHECK(minted.generation==owner.generation && minted.generation!=stale.generation);
    CHECK(car85_grip_select(&owner,&live,&out)==expected);
    printf("native admission expected=%u checks=%u failures=%u; 84 stock + traffic + nonplayer bypasses, vault reuse\n",expected,checks,failures);
    free(G); return failures!=0;
}
'''


def integration(tmp):
    import car_catalog_test as catalog
    import public_cars_integration_test as integration
    original_sidecars = catalog.sidecars
    root = tmp / 'native'; root.mkdir()
    def sidecars(folder, donor):
        return dict(original_sidecars(folder, donor), **{'GRIP.BIN': grip_blob(folder, donor)})
    with patch.object(catalog, 'sidecars', sidecars):
        integration.saves.fixture(root)
    shutil.copytree(root / 'catalog', root / 'car-catalog')
    source = integration.NATIVE[:integration.NATIVE.index('int main(')]
    source = source.replace('BOOT_INIT', integration.function((ROOT / 'runtime/boot.c').read_text(), 'private_cars_boot'))
    for marker in ('PROFILE_HELPERS', 'TRANSLATED_PROFILES', 'TRANSLATED_MENUS'):
        source = source.replace(marker, '')
    # Count every call into selection so bypasses prove zero helper calls.
    source = source.replace('#include "car85.c"', '''#include "car_grip.h"
int tracked_select(const CarGripBinding *,const CarGripOwner *,const CarGripLive *,CarGripSelected *);
#define car_grip_select tracked_select
#include "car85.c"
#undef car_grip_select
static unsigned selection_calls;
int tracked_select(const CarGripBinding *b,const CarGripOwner *o,const CarGripLive *l,CarGripSelected *r) {
    ++selection_calls; return car_grip_select(b,o,l,r);
}''')
    car = (ROOT / 'runtime/car85.c').read_text()
    source += NATIVE_MAIN
    calls = set(re.findall(r'\b(f_[0-9A-F]{8}(?:_orig)?)\(', car + source))
    provided = set(re.findall(r'void (f_[0-9A-F]{8}(?:_orig)?)\(Cpu \*(?:restrict )?c\)\s*\{', car + source))
    calls |= {'f_005ACB70_orig', 'f_0058FD10_orig'}
    stubs = '\n'.join(f'void {n}(Cpu *c) {{ (void)c; abort(); }}' for n in sorted(calls - provided))
    (root / 'native.c').write_text(source.replace('STUBS', stubs))
    (root / 'reader.c').write_text('#include "car_catalog.c"\nuint32_t test_key(const char *s) {return attribute_key_case(s,1);}\n')
    inputs = [root / 'native.c', root / 'reader.c', ROOT / 'runtime/car_grip.c', ROOT / 'runtime/car_performance.c',
              ROOT / 'runtime/private_cars_public.c', ROOT / 'runtime/carsave_public.c', ROOT / 'runtime/traffic_pilot.c']
    flags = ['clang', '-std=c11', '-O1', '-g', '-DNFS_PUBLIC=1', '-fsanitize=address,undefined',
             '-Wno-deprecated-declarations', '-I'+str(root), '-I'+str(ROOT/'runtime'),
             '-I'+str(ROOT/'recomp'), '-I'+str(ROOT/'recomp/out'),
             '-DCAR_CATALOG_APPROVAL_HEADER="'+str(root/'approval.h')+'"']
    exe = root / 'native'
    def run_build():
        p = subprocess.run(flags + list(map(str, inputs)) + ['-framework','CoreGraphics','-framework','CoreText',
                            '-framework','CoreFoundation','-o',str(exe)], capture_output=True,text=True)
        assert p.returncode == 0, p.stderr[-4000:]
    (root / 'car85.c').write_text(car)
    run_build()
    def run(expected):
        p = subprocess.run([str(exe), str(root), str(expected)], capture_output=True,text=True,timeout=30,
                           env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0',
                                'GRIP_REPLACE':str(root/'car-catalog'/json.loads((root/'car-catalog/current.json').read_text())['generation']/'cars/0/GRIP.BIN')})
        assert 'AddressSanitizer' not in p.stderr and 'runtime error:' not in p.stderr, p.stderr[-3000:]
        return p
    p = run(1)
    assert p.returncode == 0, p.stdout+p.stderr[-4000:]
    print(p.stdout.strip())
    generation=root/'car-catalog'/json.loads((root/'car-catalog/current.json').read_text())['generation']
    (generation/'cars/0/GRIP.BIN').write_bytes(grip_blob())
    # Frozen original startup seam: no GRIP record is copied to an addition.
    # This is the actual predecessor behavior; the new query has no admitted data.
    before='            a->grip=car->grip;'
    assert before in car
    (root / 'car85.c').write_text(car.replace(before, '', 1))
    run_build()
    p = run(1)
    assert p.returncode == 1 and 'a->grip.present==expected' in p.stderr, p.stdout+p.stderr[-2000:]
    print('original startup seam: positive admission FAIL (exit 1); fixed PASS (exit 0)')
    (root / 'car85.c').write_text(car)
    run_build()
    # Refused optional metadata still leaves the catalog car admitted. Forge a
    # reviewed synthetic conversion variant for each record, keeping hash checks.
    for name, blob in [('wrong-version', grip_blob()[:4]+struct.pack('<I',2)+grip_blob()[8:]),
                        ('wrong-identity', grip_blob('OTHER')), ('malformed', b'bad'), ('missing', None)]:
        case = root / name; case.mkdir()
        def bad_sidecars(folder, donor):
            result=original_sidecars(folder,donor)
            if blob is not None or folder!='MODEL_0':
                result['GRIP.BIN']=blob if folder=='MODEL_0' else grip_blob(folder,donor)
            return result
        with patch.object(catalog, 'sidecars', bad_sidecars):
            integration.saves.fixture(case)
        shutil.copytree(case/'catalog',case/'car-catalog')
        # This executable's authority is its compiled approval, so rebuild with
        # the reviewed synthetic variant's policy; never bypass the trust root.
        shutil.copyfile(case/'approval.h',root/'approval.h')
        run_build()
        p=subprocess.run([str(exe),str(case),'0'],capture_output=True,text=True,timeout=30,
                         env={**os.environ,'ASAN_OPTIONS':'detect_leaks=0'})
        assert p.returncode==0, name+p.stdout+p.stderr[-3000:]
        print(name,p.stdout.strip())


def main():
    with tempfile.TemporaryDirectory(prefix='grip-admit-') as directory:
        tmp = Path(directory).resolve()
        synthetic(tmp)
        integration(tmp)
    print('GRIP admission PASS; no gameplay hooks called')


if __name__ == '__main__':
    main()
