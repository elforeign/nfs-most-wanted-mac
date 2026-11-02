"""Synthetic Python-writer/native-reader proof; no installed assets or approvals.

The frozen original public stub fails the positive sparse activation check.
Deliberately broken readers bypass approval, sidecars, canonical parsing and
snapshot retention; each must fail its matching regression check. Counts are
printed by the runner (good assertions, rejected controls, original and mutants).
Proof: 17 tests, 10 good checks, 153 rejected controls, 1 original-bug failure
and 4 detected mutants. Native builds use address/undefined-behavior sanitizers.
Unicode regression: fixed Setup/native reader reject 16/16; predecessors accept
16/16 malformed approved display cases. Valid surrogate pairs still activate.
"""
from pathlib import Path
import copy
import hashlib
import inspect
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_catalog as cc
from setup_kit.car_convert import hash32, name_hash

SOURCE = ROOT / 'runtime/car_catalog.c'
NATIVE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
static unsigned io_calls;
static int counted_open(const char *p,int flags,...) {
    ++io_calls;
    if (flags&O_CREAT) { va_list a; va_start(a,flags); int mode=va_arg(a,int); va_end(a); return open(p,flags,mode); }
    return open(p,flags);
}
static int counted_openat(int fd,const char *p,int flags,...) {
    ++io_calls;
    if (flags&O_CREAT) { va_list a; va_start(a,flags); int mode=va_arg(a,int); va_end(a); return openat(fd,p,flags,mode); }
    return openat(fd,p,flags);
}
static ssize_t counted_read(int fd,void *p,size_t n) { ++io_calls; return read(fd,p,n); }
static ssize_t counted_pread(int fd,void *p,size_t n,off_t at) { ++io_calls; return pread(fd,p,n,at); }
static int counted_stat(int fd,struct stat *st) { ++io_calls; return fstat(fd,st); }
static int counted_statat(int fd,const char *p,struct stat *st,int flags) { ++io_calls; return fstatat(fd,p,st,flags); }
#define open counted_open
#define openat counted_openat
#define read counted_read
#define pread counted_pread
#define fstat counted_stat
#define fstatat counted_statat
#include "READER_SOURCE"
#undef open
#undef openat
#undef read
#undef pread
#undef fstat
#undef fstatat
static void text(const char *s) {
    putchar('"');
    for (;*s;++s) {
        unsigned char c=(unsigned char)*s;
        if (c=='"' || c=='\\') putchar('\\');
        if (c<32) printf("\\u%04x",c); else putchar(c);
    }
    putchar('"');
}
int main(int argc,char **argv) {
    if (argc<3) return 2;
    int rc=car_catalog_init(argv[1],atoi(argv[2]));
    unsigned initial_io=io_calls;
    if (argc>3 && !strcmp(argv[3],"race")) { puts("READY"); fflush(stdout); if (getchar()!='!') return 4; }
    int again=car_catalog_init("/invalid/repeated-init-must-not-open",!atoi(argv[2]));
    if (rc!=again || io_calls!=initial_io) return 5;
    size_t count=car_catalog_count(),all=car_catalog_inventory_count();
    if (car_catalog_get(count) || car_catalog_inventory_get(all) || car_catalog_output_name((CarCatalogOutputKind)99)) return 6;
    printf("{\"result\":%d,\"count\":%zu,\"inventory\":%zu,\"io\":%u,\"generation\":",rc,count,all,initial_io);
    text(car_catalog_generation()); printf(",\"compatibility\":"); text(car_catalog_compatibility());
    printf(",\"error\":"); text(car_catalog_error()); printf(",\"rows\":[");
    for (size_t i=0;i<all;++i) {
        const CarCatalogSlot *s=car_catalog_inventory_get(i);
        if (!s || s->slot!=i) return 7;
        if (i) putchar(',');
        printf("{\"slot\":%u,\"id\":%u,\"ready\":%d,\"selected\":%d,\"swap\":%d,\"identity\":",s->slot,s->id,s->ready,s->selected,s->swap_index);
        text(s->identity); printf(",\"compatibility\":"); text(s->compatibility);
        printf(",\"name\":"); text(s->display_name); printf(",\"manufacturer\":"); text(s->manufacturer);
        printf(",\"folder\":"); text(s->folder); printf(",\"pvehicle\":"); text(s->pvehicle);
        printf(",\"frontend\":"); text(s->frontend); printf(",\"ecar\":"); text(s->ecar);
        printf(",\"donor\":"); text(s->model_donor); printf(",\"price\":"); text(s->price_donor);
        printf(",\"part_types\":[");
        for (size_t k=0;k<s->part_count;++k) { if(k) putchar(','); printf("[%u,%zu,%zu]",s->parts[k].type,s->parts[k].first,s->parts[k].count); }
        printf("],\"part_keys\":[");
        for (size_t k=0;k<s->part_key_count;++k) { if(k) putchar(','); text(s->part_keys[k]); }
        printf("],\"outputs\":[");
        for (unsigned k=0;k<CAR_CATALOG_OUTPUTS;++k) {
            if (k) putchar(',');
            const CarCatalogOutput *o=&s->outputs[k];
            printf("{\"present\":%d,\"version\":%u,\"hash\":",o->present,o->version); text(o->sha256);
            printf(",\"bytes_hash\":");
            int fd=car_catalog_open_output((unsigned)i,(CarCatalogOutputKind)k);
            char h[65]="";
            if (o->present) {
                if(fd<0) return 8;
                CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx); unsigned char b[65536]; ssize_t got; uint64_t n=0;
                while((got=read(fd,b,sizeof b))>0) { CC_SHA256_Update(&ctx,b,(CC_LONG)got); n+=(uint64_t)got; }
                if(got<0 || n!=o->size) return 9;
                hash_end(&ctx,h);
                if (write(fd,"X",1)!=-1) return 10;
                close(fd);
                /* Existing pathname consumers must see the same anonymous bytes. */
                FILE *f=fopen(o->path,"rb"); if(!f || fseek(f,0,SEEK_SET)) return 11;
                CC_SHA256_Init(&ctx); size_t z;
                while ((z=fread(b,1,sizeof b,f))) CC_SHA256_Update(&ctx,b,(CC_LONG)z);
                if (ferror(f)) return 12;
                fclose(f); char other[65]; hash_end(&ctx,other); if(strcmp(h,other)) return 13;
            } else if (fd!=-1 || o->path[0] || o->size || o->version || o->sha256[0]) return 14;
            text(h); putchar('}');
        }
        printf("]}");
    }
    printf("],\"selected_ids\":[");
    for (size_t i=0;i<count;++i) { if(i) putchar(','); printf("%u",car_catalog_get(i)->id); }
    puts("]}"); return 0;
}
'''
# Frozen source of the original public behavior; independent of future wrapper
# integration and repository branches. No dynamic main-branch baseline.
ORIGINAL = '''#include "private_cars.h"
int private_cars_init(const char *r,int e,const char *const *o,size_t n) {
 (void)r;(void)e;(void)o;(void)n;return 0;
}
size_t private_cars_count(void) {return 0;}
'''


def remove_tree(path):
    if not path.exists():
        return
    for base, dirs, _ in os.walk(path, followlinks=False):
        os.chmod(base, 0o700)
        for d in dirs:
            p = Path(base) / d
            if not p.is_symlink():
                p.chmod(0o700)
    shutil.rmtree(path)


def paint(folder, donor, mode=1):
    values = [0x31545044, 2, name_hash(folder), name_hash(donor), 123 if mode else 0, 123 if mode else 0, mode]
    h = 0xffffffff
    for v in values:
        h = ((h ^ v) * 16777619) & 0xffffffff
    return struct.pack('<8I', *values, h)


def sidecars(folder, donor):
    a, b = hash32(folder.lower()), hash32(donor.lower())
    bounds = bytearray(80)
    struct.pack_into('<4I', bounds, 0, hash32(folder), 1, 0, 0)
    struct.pack_into('<3h', bounds, 32, 500, 1000, 500)
    struct.pack_into('<b', bounds, 39, -1)
    struct.pack_into('<h', bounds, 46, -1)
    return {
        'GEOMETRY.BIN': b'synthetic geometry ' + folder.encode(),
        'TEXTURES.BIN': b'synthetic textures ' + folder.encode(),
        'SOLIDMAP.BIN': struct.pack('<4I4I', 0x31504d53, 2, name_hash(folder), 2, 11, 101, 12, 102),
        'WHEELFIT.BIN': struct.pack('<4I22f12B11fI54f', 0x31544657, 2, a, b,
                                     *([0., 0., 0., .5] * 4 + [0., 0.] + [.5] * 4),
                                     *([0] * 12), *([1.] * 11), 5, *([1.] * 54)),
        'COLLISION.BIN': struct.pack('<5I', 0x31544c43, 1, a, b, len(bounds)) + bounds,
        'PAINT.BIN': paint(folder, donor),
        'PHYSICS.BIN': struct.pack('<4I5f', 0x31534850, 1, a, b, 1000., 1., 2., 3., 0.),
    }


class NativeCatalogTests(unittest.TestCase):
    good = broken = originals = mutants = 0

    @classmethod
    def tearDownClass(cls):
        print(f'native catalog proof: good={cls.good}; rejected={cls.broken}; original-bug={cls.originals}; mutants={cls.mutants}; sparse IDs=84,95,159')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='native-catalog-')
        self.root = Path(self.tmp.name).resolve()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(remove_tree, self.root)
        self.store = self.root / 'catalog'
        self.rows, self.outputs, self.inputs = cc.reservations(), {}, {}
        self.serial = 0
        for s in (0, 11, 75):
            folder, donor = f'MODEL_{s}', 'MODEL_DONOR'
            base = self.root / f'local-{s}'
            base.mkdir()
            groups = {}
            for group in cc.INPUT_GROUPS:
                p = base / group
                p.mkdir()
                # Quotes in logical input keys exercise canonical key ordering.
                for name in ('INPUT.BIN', 'A"Q.BIN', 'A0.BIN'):
                    (p / name).write_bytes(f'synthetic {group} {folder} {name}'.encode())
                groups[group] = p
            out = base / 'converted'
            out.mkdir()
            for name, data in sidecars(folder, donor).items():
                (out / name).write_bytes(data)
            row = dict(self.rows[s], state='ready',
                       origin=dict(game='carbon', platform='pc', folder=folder),
                       display=dict(name=f'Synthetic model {s} é 🚙', manufacturer='Synthetic maker'),
                       bindings=dict(folder=folder, pvehicle=folder + '_PV', frontend=folder + '_FE', ecar=folder + '_EC'),
                       donors=dict(model=donor, price='PRICE_DONOR'),
                       parts=[dict(type=0, keys=['BODY_STOCK', 'BODY_ALT']), dict(type=254, keys=['WHEEL_STOCK'])],
                       converter=dict(revision=1, policy=cc.digest('synthetic converter policy')),
                       inputs={g: {name: cc.fingerprint(p / name) for name in os.listdir(p)} for g, p in groups.items()},
                       outputs={name: cc.fingerprint(out / name) for name in os.listdir(out)})
            row['identity'] = cc.source_identity(**row['origin'])
            self.rows[s], self.outputs[s], self.inputs[s] = row, out, groups
        self.policy_doc = dict(schema=1, revision=1, slots=[dict(slot=s, identity=None, variants=[]) for s in range(90)])
        self.approve_rows()
        self.policy = self.load_policy()
        self.catalog = cc.publish(self.store, self.rows, self.outputs, self.inputs, policy=self.policy)
        self.exe = self.compile()

    def approve_rows(self, rows=None):
        for r in rows or self.rows:
            if r['state'] != 'ready':
                continue
            s = self.policy_doc['slots'][r['slot']]
            s['identity'] = r['identity']
            variant = dict(contract=cc.contract_hash(r), compatibility=cc.slot_compatibility(r))
            if variant not in s['variants']:
                s['variants'].append(variant)

    def load_policy(self):
        path = self.root / 'policy.json'
        path.write_bytes(cc.encoded(self.policy_doc))
        return cc.load_policy(path, cc.digest(self.policy_doc))

    def compile(self, *, source=None, policy=True, policy_data=None, expected=None):
        self.serial += 1
        d = self.root / f'build-{self.serial}'
        d.mkdir()
        reader = d / 'reader.c'
        reader.write_text(SOURCE.read_text() if source is None else source)
        if policy:
            data = cc.encoded(self.policy_doc) if policy_data is None else policy_data
            (d / 'approval.h').write_text('#define CAR_CATALOG_POLICY_JSON ' + json.dumps(data.decode('ascii')) + '\n#define CAR_CATALOG_POLICY_SHA256 "' + (expected or hashlib.sha256(data).hexdigest()) + '"\n')
        c = d / 'harness.c'
        c.write_text(NATIVE.replace('READER_SOURCE', str(reader)))
        exe = d / 'reader'
        args = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined', '-I', str(ROOT / 'runtime')]
        if policy:
            args += ['-DCAR_CATALOG_APPROVAL_HEADER="' + str(d / 'approval.h') + '"']
        p = subprocess.run(args + [str(c), '-o', str(exe)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr[-5000:])
        return exe

    def native(self, *, exe=None, store=None, enabled=True, mutate=None):
        args = [str(exe or self.exe), str(store or self.store), str(int(enabled))]
        if mutate is not None:
            p = subprocess.Popen(args + ['race'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(p.stdout.readline(), 'READY\n')
            mutate()
            out, err = p.communicate('!', timeout=30)
            self.assertEqual(p.returncode, 0, err[-3000:])
        else:
            p = subprocess.run(args, capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr[-3000:])
            out = p.stdout
        return json.loads(out)

    def good_result(self, result=None, selected=(84, 95, 159)):
        r = result or self.native()
        self.assertEqual(r['error'], '')
        self.assertEqual(r['result'], len(selected))
        self.assertEqual(r['count'], len(selected))
        self.assertEqual(r['inventory'], 90)
        self.assertEqual(r['selected_ids'], list(selected))
        for s in (0, 11, 75):
            row, actual = self.rows[s], r['rows'][s]
            self.assertEqual(actual['id'], 84 + s)
            self.assertEqual(actual['identity'], row['identity'])
            self.assertEqual(actual['compatibility'], cc.slot_compatibility(row))
            self.assertEqual(actual['name'], row['display']['name'])
            self.assertEqual(actual['manufacturer'], row['display']['manufacturer'])
            for key in ('folder', 'pvehicle', 'frontend', 'ecar'):
                self.assertEqual(actual[key], row['bindings'][key])
            self.assertEqual(actual['donor'], row['donors']['model'])
            self.assertEqual(actual['price'], row['donors']['price'])
            self.assertEqual(actual['part_types'], [[0, 0, 2], [254, 2, 1]])
            self.assertEqual(actual['part_keys'], ['BODY_STOCK', 'BODY_ALT', 'WHEEL_STOCK'])
            for o in actual['outputs']:
                self.assertEqual(o['bytes_hash'], o['hash'])
        self.assertEqual(r['compatibility'], cc.digest([cc.slot_compatibility(x) if x['state'] == 'ready' else None for x in self.rows]))
        for s in (1, 74, 76, 89):
            self.assertEqual(r['rows'][s]['ready'], 0)
            self.assertEqual(r['rows'][s]['id'], 84+s if s<76 else 0)
            self.assertEqual(r['rows'][s]['swap'], -1 if s<76 else s-76)
        type(self).good += 1
        return r

    def zero(self, result=None, *, error=True):
        r = result or self.native()
        self.assertEqual((r['count'], r['inventory'], r['rows'], r['selected_ids']), (0, 0, [], []))
        self.assertEqual((r['generation'], r['compatibility']), ('', ''))
        if error:
            self.assertLess(r['result'], 0)
            self.assertTrue(r['error'])
        else:
            self.assertEqual((r['result'], r['error']), (0, ''))
        type(self).broken += 1
        return r

    def pointer(self):
        return json.loads((self.store / 'current.json').read_bytes())

    def generation_dir(self):
        return self.store / self.pointer()['generation']

    def write_pointer(self, doc):
        path = self.store / 'current.json'
        path.chmod(0o600)
        path.write_bytes(cc.encoded(doc))

    def rewrite(self, change=None, *, rows=None, raw=None):
        """Forge all public hashes, without changing the compiled trust root."""
        old = self.generation_dir()
        doc = json.loads((old / 'catalog.json').read_bytes())
        if rows is not None:
            doc['cars'] = copy.deepcopy(rows)
        if change:
            change(doc)
        doc['compatibility'] = cc.digest([cc.slot_compatibility(r) if r['state']=='ready' else None for r in doc['cars']])
        data = raw(doc) if raw else cc.encoded(doc)
        digest = hashlib.sha256(data).hexdigest()
        name = 'g-' + digest
        target = self.store / name
        if target != old:
            shutil.copytree(old, target)
        target.chmod(0o700)
        p = target / 'catalog.json'
        p.chmod(0o600)
        p.write_bytes(data)
        ptr = self.pointer()
        ptr.update(generation=name, catalog_sha256=digest, compatibility=doc['compatibility'])
        self.write_pointer(ptr)
        return target

    def test_sparse_writer_reader_agreement(self):
        self.good_result()
        py = cc.verify_current(self.store, policy=self.policy)
        self.assertEqual([r['id'] for r in py.selected], [84, 95, 159])
        self.assertEqual(py.error, '')
        cc.set_selection(self.store, [75], policy=self.policy)
        self.good_result(selected=(159,))
        cc.set_selection(self.store, [], policy=self.policy)
        self.good_result(selected=())

    def test_original_bug(self):
        d = self.root / 'original'
        d.mkdir()
        (d / 'stub.c').write_text(ORIGINAL)
        (d / 'main.c').write_text('#include "private_cars.h"\nint main(int n,char **v){(void)n;private_cars_init(v[1],1,0,0);return private_cars_count()==3?0:1;}\n')
        subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT/'runtime'), str(d/'stub.c'), str(d/'main.c'), '-o', str(d/'stub')], check=True, capture_output=True)
        self.assertEqual(subprocess.run([str(d/'stub'), str(self.store)], capture_output=True).returncode, 1)
        self.assertEqual(len(cc.verify_current(self.store, policy=self.policy).selected), 3)
        type(self).originals += 1

    def test_disabled_absent_and_policy_absent(self):
        r = self.zero(self.native(enabled=False), error=False)
        self.assertEqual(r['io'], 0)
        self.zero(self.native(store=self.root/'missing'), error=False)
        (self.store/'current.json').rename(self.store/'retained-pointer.json')
        self.zero(error=False)
        (self.store/'retained-pointer.json').rename(self.store/'current.json')
        self.zero(self.native(exe=self.compile(policy=False)))
        cc.disable(self.store)
        self.zero(self.native(exe=self.compile(policy=False)), error=False)
        self.zero(error=False)

    def test_forged_approval_and_recomputed_hashes(self):
        self.rewrite(lambda d: d['cars'][0]['display'].update(manufacturer='Forged local approval'))
        self.zero()
        mutant = SOURCE.read_text().replace('NEED(approved(policy,approval,out->identity,contract,out->compatibility),', 'NEED((approved(policy,approval,out->identity,contract,out->compatibility),1),')
        self.assertNotEqual(mutant, SOURCE.read_text())
        # Same fixture admits cars only if the independent approval guard broke.
        self.assertEqual(self.native(exe=self.compile(source=mutant))['count'], 3)
        type(self).mutants += 1

    def test_approval_words_never_authorize(self):
        self.rewrite(lambda d: d['cars'][0].update(approved=True, evidence='local words'))
        self.zero()

    def test_policy_integrity_and_invalid_shapes(self):
        self.zero(self.native(exe=self.compile(expected='0'*64)))
        original = copy.deepcopy(self.policy_doc)
        changes = [lambda p: p.update(schema=2),
                   lambda p: p.update(revision=0),
                   lambda p: p['slots'][1].update(slot=0),
                   lambda p: p['slots'][1].update(identity=p['slots'][0]['identity']),
                   lambda p: p['slots'][1].update(variants=p['slots'][0]['variants']),
                   lambda p: p['slots'][0]['variants'].append(p['slots'][0]['variants'][0]),
                   lambda p: p['slots'][0]['variants'][0].update(approved='yes')]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                self.policy_doc = copy.deepcopy(original)
                change(self.policy_doc)
                self.zero(self.native(exe=self.compile()))
        self.policy_doc = original
        data = cc.encoded(original).replace(b'"schema":1', b'"schema":1,"schema":1')
        self.zero(self.native(exe=self.compile(policy_data=data)))

    def test_pointer_validation(self):
        original = self.pointer()
        changes = [lambda p: p.update(schema=2), lambda p: p.update(approved=True),
                   lambda p: p.update(generation='../outside'), lambda p: p.update(catalog_sha256='0'*64),
                   lambda p: p.update(compatibility='0'*64), lambda p: p.update(selection=[0, 0]),
                   lambda p: p.update(selection=[75, 0]), lambda p: p.update(selection=[1]),
                   lambda p: p.update(selection=[90]), lambda p: p.update(selection=[True]),
                   lambda p: p.update(selection=[0.0]), lambda p: p.update(selection=None),
                   lambda p: p.update(generation=None), lambda p: p.update(selection=[-1])]
        for change in changes:
            p = copy.deepcopy(original)
            change(p)
            self.write_pointer(p)
            self.zero()
        self.write_pointer(original)
        data = cc.encoded(original)
        for bad in (data.replace(b'"schema":1', b'"schema":1,"schema":1'), data+b' ', b' '+data,
                    data.replace(b'"schema"', b'"sch\\u0065ma"'), data[:-1], data.replace(b'"schema":1', b'"schema":01')):
            (self.store/'current.json').write_bytes(bad)
            self.zero()
        (self.store/'current.json').write_bytes(b'x'*(cc.MAX_JSON+1))
        self.zero()

    def test_corruption_and_exact_inventory(self):
        p = self.generation_dir() / 'cars/75/PHYSICS.BIN'
        data = p.read_bytes()
        p.chmod(0o600)
        p.write_bytes(data[:-1] + bytes([data[-1]^1]))
        cc_pointer = self.pointer()
        cc_pointer['selection'] = [0]
        self.write_pointer(cc_pointer)
        self.zero()  # deselected corruption invalidates everything
        p.write_bytes(data)
        base = self.generation_dir()
        for where in ('EXTRA.BIN', 'cars/EXTRA.BIN', 'cars/1/EXTRA.BIN', 'cars/0/EXTRA.BIN', 'empty'):
            p = base / where
            if p.parent.exists():
                p.parent.chmod(0o700)
            if where=='empty':
                p.mkdir()
            else:
                p.parent.mkdir(exist_ok=True)
                p.write_bytes(b'extra')
            self.zero()
            if p.is_dir(): p.rmdir()
            else: p.unlink()
            if where=='cars/1/EXTRA.BIN': p.parent.rmdir()
        self.good_result(selected=(84,))

    def test_catalog_semantics_even_with_compiled_variant(self):
        original_ptr, original_policy = self.pointer(), copy.deepcopy(self.policy_doc)
        changes = [
            ('schema', lambda d: d.update(schema=2)),
            ('catalog field', lambda d: d.update(extra=0)),
            ('slot id', lambda d: d['cars'][11].update(id=84)),
            ('slot order', lambda d: d['cars'][1].update(slot=0)),
            ('absent fields', lambda d: d['cars'][1].update(approved=False)),
            ('state', lambda d: d['cars'][0].update(state='pending')),
            ('swap', lambda d: d['cars'][0].update(role='swap')),
            ('origin', lambda d: d['cars'][0]['origin'].update(platform='other')),
            ('identity', lambda d: d['cars'][0].update(identity='0'*64)),
            ('name empty', lambda d: d['cars'][0]['display'].update(name='')),
            ('manufacturer control', lambda d: d['cars'][0]['display'].update(manufacturer='bad\x7f')),
            ('display length', lambda d: d['cars'][0]['display'].update(name='a'*129)),
            ('binding path', lambda d: d['cars'][0]['bindings'].update(ecar='../OTHER')),
            ('duplicate binding', lambda d: d['cars'][11]['bindings'].update(frontend=d['cars'][0]['bindings']['frontend'])),
            ('donor token', lambda d: d['cars'][0]['donors'].update(model='bad')),
            ('converter revision', lambda d: d['cars'][0]['converter'].update(revision=0)),
            ('converter policy', lambda d: d['cars'][0]['converter'].update(policy='bad')),
            ('empty parts', lambda d: d['cars'][0].update(parts=[])),
            ('duplicate type', lambda d: d['cars'][0]['parts'][1].update(type=0)),
            ('type bound', lambda d: d['cars'][0]['parts'][0].update(type=255)),
            ('duplicate part', lambda d: d['cars'][0]['parts'][1].update(keys=['BODY_STOCK'])),
            ('empty part', lambda d: d['cars'][0]['parts'][0].update(keys=[])),
            ('unknown output', lambda d: d['cars'][0]['outputs'].update(OTHER_BIN=d['cars'][0]['outputs']['GEOMETRY.BIN'])),
            ('output path', lambda d: d['cars'][0]['outputs'].update({'../OTHER.BIN':d['cars'][0]['outputs']['GEOMETRY.BIN']})),
            ('missing sidecar', lambda d: d['cars'][0]['outputs'].pop('SOLIDMAP.BIN')),
            ('bad file size', lambda d: d['cars'][0]['outputs']['GEOMETRY.BIN'].update(size=(2<<30)+1)),
            ('bad pin digest', lambda d: d['cars'][0]['outputs']['GEOMETRY.BIN'].update(sha256='f'*63)),
            ('unknown pin field', lambda d: d['cars'][0]['outputs']['GEOMETRY.BIN'].update(path='elsewhere')),
            ('input group missing', lambda d: d['cars'][0]['inputs'].pop('donor')),
            ('input group empty', lambda d: d['cars'][0]['inputs'].update(donor={})),
            ('input path', lambda d: d['cars'][0]['inputs']['donor'].update({'../INPUT.BIN': d['cars'][0]['inputs']['donor']['INPUT.BIN']})),
            ('input case collision', lambda d: d['cars'][0]['inputs']['donor'].update({'input.bin':d['cars'][0]['inputs']['donor']['INPUT.BIN']})),
        ]
        for label, change in changes:
            with self.subTest(label=label):
                self.write_pointer(original_ptr)
                self.policy_doc = copy.deepcopy(original_policy)
                def forge(d):
                    change(d)
                    for row in d['cars']:
                        if row['state']=='ready':
                            slot=self.policy_doc['slots'][row['slot']]
                            slot['identity']=row['identity']
                            variant=dict(contract=cc.contract_hash(row), compatibility=cc.slot_compatibility(row))
                            if variant not in slot['variants']:
                                slot['variants'].append(variant)
                    d['policy_sha256']=cc.digest(self.policy_doc)
                self.rewrite(forge)
                self.zero(self.native(exe=self.compile()))

    def test_unpaired_surrogates(self):
        # Fixed/predecessor numbers are recorded in the module header.
        original_ptr, original_policy = self.pointer(), copy.deepcopy(self.policy_doc)
        fixed = SOURCE.read_text()
        predecessor = fixed.replace('(c >= 0xd800 && c <= 0xdfff) || ', '', 1)
        self.assertNotEqual(fixed, predecessor)
        old_setup = inspect.getsource(cc._row).replace(
            ' and\n                     not 0xD800 <= ord(c) <= 0xDFFF', '')
        self.assertNotEqual(old_setup, inspect.getsource(cc._row))
        scope = dict(cc.__dict__)
        exec(old_setup, scope)
        exec(inspect.getsource(cc.make_catalog), scope)
        rejected = accepted = 0
        for field in ('name', 'manufacturer'):
            for value in ('\ud800', '\udfff', '\ud800x', '\ud800\ud800'):
                # Values are Python escapes above: decode to actual lone scalars.
                value = json.loads('"' + value + '"')
                self.write_pointer(original_ptr)
                self.policy_doc = copy.deepcopy(original_policy)
                def forge(d):
                    d['cars'][0]['display'][field] = value
                    row = d['cars'][0]
                    self.policy_doc['slots'][0]['variants'].append(dict(
                        contract=cc.contract_hash(row), compatibility=cc.slot_compatibility(row)))
                    d['policy_sha256'] = cc.digest(self.policy_doc)
                self.rewrite(forge)
                rows = json.loads((self.generation_dir() / 'catalog.json').read_bytes())['cars']
                policy = self.load_policy()
                with self.assertRaises(cc.CatalogError):
                    cc.make_catalog(rows, policy)
                self.assertEqual(scope['make_catalog'](rows, policy)['cars'], rows)
                self.zero(self.native(exe=self.compile()))
                exe = self.compile(source=predecessor)
                old = subprocess.run([str(exe), str(self.store), '1'], capture_output=True, timeout=30)
                self.assertEqual(old.returncode, 0, old.stderr[-2000:])
                decoded = json.loads(old.stdout.decode('utf-8', 'surrogatepass'))
                self.assertEqual(decoded['count'], 3)
                self.assertEqual(decoded['rows'][0][field], value)
                rejected += 2
                accepted += 2
        print(f'surrogate proof: fixed rejects={rejected}/16; predecessor accepts={accepted}/16; approved inventory never exposed')

    def test_canonical_duplicate_keys_and_hashes(self):
        original = self.pointer()
        mutations = [
            lambda b: b.replace(b'"id":84', b'"id":84,"id":84', 1),
            lambda b: b.replace(b'"manufacturer":', b'"name":"ignored","manufacturer":', 1),
            lambda b: b.replace(b'"schema":1', b'"schema":1,"schema":1', 1),
            lambda b: b.replace(b'"type":0', b'"type":0,"type":0', 1),
            lambda b: b.replace(b'"size":', b'"sha256":"ignored","size":', 1),
            lambda b: b.replace(b'"slot":0', b'"sl\\u006ft":0', 1),
            lambda b: b.replace(b'"id":84', b'"id":84.0', 1),
        ]
        for mutate in mutations:
            self.write_pointer(original)
            self.rewrite(raw=lambda d: mutate(cc.encoded(d)))
            self.zero()
        self.write_pointer(original)
        base = self.generation_dir()
        p = base / 'catalog.json'
        p.chmod(0o600)
        data=p.read_bytes()
        p.write_bytes(data.replace(b'Synthetic maker', b'Tampered maker!'))
        self.zero()

    def test_links_and_bad_root_paths(self):
        base = self.generation_dir()
        # Regular-looking symlinks at each open boundary fail without following.
        for p in (self.store/'current.json', base/'catalog.json', base/'cars/0/GEOMETRY.BIN', base/'cars/0', base/'cars', base):
            with self.subTest(path=p.name):
                p.parent.chmod(0o700)
                saved=p.with_name(p.name+'.retained')
                p.rename(saved)
                p.symlink_to(saved.name)
                self.zero()
                p.unlink()
                saved.rename(p)
        p=base/'cars/0/GEOMETRY.BIN'
        p.parent.chmod(0o700)
        p.chmod(0o600)
        hard=self.root/'hardlink'
        os.link(p, hard)
        self.zero()
        hard.unlink()
        data=p.read_bytes()
        p.unlink()
        os.mkfifo(p)
        self.zero()  # nonblocking open, no hang
        p.unlink()
        p.write_bytes(data)
        # Root component links, dot traversal, backslashes and controls.
        link=self.root/'catalog-link'
        link.symlink_to(self.store)
        self.zero(self.native(store=link))
        for path in (str(self.store)+'/../catalog', str(self.store)+'/./', str(self.store)+'\\bad', str(self.store)+'\n'):
            self.zero(self.native(store=path))
        self.good_result()

    def test_snapshot_replacement_races(self):
        base=self.generation_dir()
        p=base/'cars/0/GEOMETRY.BIN'
        p.parent.chmod(0o700)
        old=p.read_bytes()
        def replace():
            replacement=p.with_name('replacement')
            replacement.write_bytes(b'forged replacement')
            os.replace(replacement,p)
        self.good_result(self.native(mutate=replace))
        self.zero()  # next process sees corrupt installed data
        p.write_bytes(old)
        def inplace():
            p.chmod(0o600)
            p.write_bytes(b'X'*len(old))
        self.good_result(self.native(mutate=inplace))
        p.write_bytes(old)
        def directory():
            moved=base.with_name('moved-generation')
            base.rename(moved)
            base.mkdir()
            (base/'catalog.json').write_bytes(b'forged')
            cc.disable(self.store)
        self.good_result(self.native(mutate=directory))

    def test_canonical_and_snapshot_mutants(self):
        original=SOURCE.read_text()
        # Pointer fields have no approval hash of their own. Reordered canonical
        # fields catch removal of the parser's ordering/duplicate-key guard.
        ptr=self.pointer()
        path=self.store/'current.json'
        path.chmod(0o600)
        path.write_bytes((json.dumps(dict(reversed(list(ptr.items()))), separators=(',',':'))+'\n').encode())
        self.zero()
        mutant=original.replace('if (key_order(j,&j->t[previous],k)>=0)', 'if (0 && key_order(j,&j->t[previous],k)>=0)')
        self.assertNotEqual(mutant,original)
        self.assertEqual(self.native(exe=self.compile(source=mutant))['count'],3)
        type(self).mutants += 1
        self.write_pointer(ptr)
        # Retaining the installed file descriptor survives renames but still
        # admits later in-place writes. The correct reader copies the bytes.
        mutant=original.replace('    close(source);\n    if (!rc) rc=sidecar', '    /* retain installed descriptor */\n    if (!rc) rc=sidecar')
        mutant=mutant.replace('r->fd=ro;', 'close(ro); r->fd=source;')
        mutant=mutant.replace('snprintf(o->path,sizeof o->path,"/dev/fd/%d",ro);', 'snprintf(o->path,sizeof o->path,"/dev/fd/%d",source);')
        self.assertNotEqual(mutant,original)
        p=self.generation_dir()/'cars/0/GEOMETRY.BIN'
        data=p.read_bytes()
        def overwrite():
            p.chmod(0o600)
            p.write_bytes(b'X'*len(data))
        r=self.native(exe=self.compile(source=mutant),mutate=overwrite)
        self.assertNotEqual(r['rows'][0]['outputs'][0]['bytes_hash'],r['rows'][0]['outputs'][0]['hash'])
        type(self).mutants += 1

    def test_sidecar_size_identity_version_and_values(self):
        original_ptr, original_policy = self.pointer(), copy.deepcopy(self.policy_doc)
        original_base = self.generation_dir()
        blobs = sidecars('MODEL_0', 'MODEL_DONOR')
        cases=[]
        for name in ('SOLIDMAP.BIN','WHEELFIT.BIN','COLLISION.BIN','PAINT.BIN','PHYSICS.BIN'):
            data=blobs[name]
            cases += [(name, 'truncate', data[:-1]), (name, 'append', data+b'X')]
            for offset, label in ((0,'magic'),(4,'version'),(8,'identity')):
                bad=bytearray(data)
                struct.pack_into('<I',bad,offset,0)
                cases.append((name,label,bytes(bad)))
            if name!='SOLIDMAP.BIN':
                bad=bytearray(data)
                struct.pack_into('<I',bad,12,0)
                cases.append((name,'donor',bytes(bad)))
        def field(name, label, off, fmt, value):
            bad=bytearray(blobs[name]); struct.pack_into(fmt,bad,off,value); cases.append((name,label,bytes(bad)))
        field('SOLIDMAP.BIN','order',24,'<I',11)
        field('WHEELFIT.BIN','nan',16,'<f',float('nan'))
        field('WHEELFIT.BIN','radius',28,'<f',0.)
        field('WHEELFIT.BIN','width',88,'<f',0.)
        field('WHEELFIT.BIN','spokes',160,'<I',256)
        field('WHEELFIT.BIN','camera',376,'<f',float('inf'))
        field('COLLISION.BIN','size',16,'<I',0)
        field('COLLISION.BIN','raw identity',20,'<I',0)
        field('COLLISION.BIN','backpointer',28,'<I',1)
        field('COLLISION.BIN','count',24,'<I',129)
        field('COLLISION.BIN','negative dimension',52,'<h',-1)
        field('COLLISION.BIN','cloud index',59,'<b',0)
        field('PHYSICS.BIN','nan',16,'<f',float('nan'))
        field('PHYSICS.BIN','mass',16,'<f',0.)
        field('PHYSICS.BIN','tensor',20,'<f',101.)
        field('PHYSICS.BIN','padding',32,'<f',1.)
        field('PAINT.BIN','checksum',28,'<I',0)
        for name,label,data in cases:
            with self.subTest(name=name,label=label):
                self.write_pointer(original_ptr)
                self.policy_doc=copy.deepcopy(original_policy)
                rows=copy.deepcopy(self.rows)
                rows[0]['outputs'][name]=dict(size=len(data),sha256=hashlib.sha256(data).hexdigest())
                self.approve_rows(rows)
                def forge(d):
                    d['cars']=rows
                    d['policy_sha256']=cc.digest(self.policy_doc)
                target=self.rewrite(forge)
                out=target/'cars/0'/name
                out.parent.chmod(0o700); out.chmod(0o600); out.write_bytes(data)
                self.zero(self.native(exe=self.compile()))
        # A deliberate validator removal must admit a fully pinned malformed
        # sidecar, demonstrating these checks do more than policy/hash checks.
        mutant=SOURCE.read_text().replace('if (!rc) rc=sidecar(&inventory[slot],kind,temp);', 'if (!rc && 0) rc=sidecar(&inventory[slot],kind,temp);')
        self.assertEqual(self.native(exe=self.compile(source=mutant))['count'], 3)
        type(self).mutants += 1
        self.write_pointer(original_ptr)
        self.policy_doc=original_policy
        self.assertEqual(self.generation_dir(),original_base)

    def test_optional_sidecars_absent_and_legacy_versions(self):
        # Explicit absence never causes reads of convenient sibling sidecars.
        base=self.generation_dir()
        for s in (0,11,75):
            for name in ('WHEELFIT.BIN','COLLISION.BIN','PAINT.BIN','PHYSICS.BIN'):
                self.rows[s]['outputs'].pop(name)
                (self.outputs[s]/name).unlink()
            data=bytearray((self.outputs[s]/'SOLIDMAP.BIN').read_bytes())
            struct.pack_into('<I',data,4,1)
            (self.outputs[s]/'SOLIDMAP.BIN').write_bytes(data)
            self.rows[s]['outputs']['SOLIDMAP.BIN']=cc.fingerprint(self.outputs[s]/'SOLIDMAP.BIN')
        self.approve_rows()
        self.policy=self.load_policy()
        cc.publish(self.store,self.rows,self.outputs,self.inputs,policy=self.policy)
        r=self.good_result(self.native(exe=self.compile()))
        self.assertTrue(all(not x['present'] for x in r['rows'][0]['outputs'][3:]))
        self.assertEqual(r['rows'][0]['outputs'][2]['version'],1)
        self.assertTrue(base.exists())

    def test_valid_donor_paint_and_wheel_v1(self):
        for s in (0,11,75):
            out=self.outputs[s]
            folder=self.rows[s]['bindings']['folder']
            donor=self.rows[s]['donors']['model']
            (out/'PAINT.BIN').write_bytes(paint(folder,donor,mode=0))
            data=bytearray((out/'WHEELFIT.BIN').read_bytes()[:116]); struct.pack_into('<I',data,4,1)
            (out/'WHEELFIT.BIN').write_bytes(data)
            for name in ('PAINT.BIN','WHEELFIT.BIN'):
                self.rows[s]['outputs'][name]=cc.fingerprint(out/name)
        self.approve_rows()
        self.policy=self.load_policy()
        cc.publish(self.store,self.rows,self.outputs,self.inputs,policy=self.policy)
        r=self.good_result(self.native(exe=self.compile()))
        self.assertEqual(r['rows'][0]['outputs'][3]['version'],1)


if __name__ == '__main__':
    unittest.main()
