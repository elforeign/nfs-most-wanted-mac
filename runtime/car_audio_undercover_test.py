#!/usr/bin/env python3
"""Stock Undercover player layers: original 0/28 through both constructors.

Fixed: 28/28 per constructor, 14 cars / 20 source TMXs; parser controls
uncorrected Charger and rejected Scirocco rate each admit 19/20 (fixed 20/20).
Wrong-car pairing and removed admission each give 0/28 (exit 1); the original
guest frame word fails the native lifecycle proof. All 220 malformed layouts
refuse. The roster and typed Audio[0] selectors come from independent catalog/Attrib
reads. Original BE TMX identities must survive packaging and native opens;
only a guest GIN's frame word may change to terminal cycle sample + 1.
Missing private sources exit 3 after portable admission checks.
"""
import hashlib
import ctypes as ct
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio, car_audio_pack, car_audio_undercover as uc, uc_attrib
from setup_kit.car_convert import FitAttributes, hash32
from setup_kit.input_paths import input_path


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def expectations(attributes):
    native = FitAttributes(attributes, vaults=uc_attrib.vaults(attributes))
    attrib = object.__new__(car_audio.AudioAttributes)
    attrib.defs, attrib.collections = native.defs, native.collections
    rows = json.loads((ROOT / 'tools/setup_kit/car_selections.json').read_text())
    cars = [r['origin']['folder'] for r in rows if r['origin']['game'] == 'undercover']
    assert len(cars) == len(set(cars)) == 14
    result = {}
    for car in cars:
        own = hash32(uc_attrib.STOCK_ALIASES.get(car, car).lower())
        audio = attrib.field(hash32('pvehicle'), own, hash32('Audio'))['values'][0]
        assert int(audio['class'], 16) == car_audio.CAR
        paths = []
        for field in (0x9aed4ffe, 0xb3186761):
            ng = attrib.field(int(audio['class'], 16), int(audio['key'], 16), field)['values'][0]
            assert int(ng['class'], 16) == 0x6d483f8f
            name = attrib.field(int(ng['class'], 16), int(ng['key'], 16), 0x0eb1c9d2)['values'][0]['text']
            paths.append('SOUND/ENGINE/' + name.upper())
        result[car] = paths
    assert len({p for paths in result.values() for p in paths}) == 20
    assert result['LAM_GAL_SUP_08'] == result['LAM_GAL_560_09']
    return result


def reference_package(attributes, car, paths, resources):
    """Independent v6 wire fixture; production must emit the same bytes."""
    from setup_kit.car_audio_codec import decode_sound
    rows = sorted(set(paths))
    plans = {p: uc.tmx(resources[p]) for p in rows}
    payload = bytearray(struct.pack('<2I4f', *(rows.index(p) for p in paths),
                                   *(v for p in paths for v in plans[p]['rpm'])))
    payload += hashlib.sha256(attributes).digest()
    for path in rows:
        data = resources[path]
        sounds = [t['sound'] for t in plans[path]['tracks'] if t['sound']]
        payload += struct.pack('<4I', 3, len(data), 0, len(sounds)) + hashlib.sha256(data).digest() + data
        for sound in sounds:
            pcm = decode_sound(sound).pcm
            payload += struct.pack('<4I', sound.frames, sound.rate,
                                   0 if sound.loop_start is None else sound.loop_start + 1, len(pcm)) + pcm
    return car_audio_pack.HEADER.pack(b'A7CA', 6, 68 + len(payload), len(rows), 1,
        car.lower().encode().ljust(16, b'\0'), hashlib.sha256(payload).digest()) + payload


def harness(ps):
    main = ps.ALL_MAIN.replace('/62', '/28').replace('==62', '==28').replace('cars==31', 'cars==14')
    main = main.replace('p->version==5', 'p->version==6').replace('e->package->version==5', 'e->package->version==6')
    main = main.replace('assert(car_audio_load(77,1,car,path)==1);',
                        'if(car_audio_load(77,1,car,path)!=1) continue;')
    main = main.replace('unsigned bounds=rd(r->source+0x4c);', 'unsigned bounds=uc_be(r->source+0x4c);')
    main = main.replace('rf(r->source+bounds)', 'uc_float(r->source+bounds)')
    main = main.replace('rf(r->source+bounds+4)', 'uc_float(r->source+bounds+4)')
    main = main.replace('assert(!memcmp(G+e->ginsu[voice].gin,r->source+t->offset,t->size));', r'''
                        const uint8_t *raw=r->source+t->offset,*actual=G+e->ginsu[voice].gin;
                        unsigned tables=40+4*(rd(raw+16)+rd(raw+20));
                        assert(!memcmp(actual,raw,24) && !memcmp(actual+28,raw+28,t->size-28));
                        assert(rd(actual+24)==rd(raw+tables-4)+1);
                        assert(t->rate==rd(actual+28));
''')
    return r'''
static uint32_t uc_be(const uint8_t *p) { return (uint32_t)p[0]<<24|(uint32_t)p[1]<<16|(uint32_t)p[2]<<8|p[3]; }
static float uc_float(const uint8_t *p) { uint32_t u=uc_be(p);float f;memcpy(&f,&u,4);return f; }
''' + main


def parser_proof(folder, resources):
    source = (ROOT / 'runtime/car_audio_tmx.c').read_text()
    controls = {
        'fixed': source,
        'uncorrected': source.replace('s->frames=terminal+1;', '/* original frame count retained */'),
        'rate': source.replace('!(be && s->rate==34000)', '1'),
    }
    assert all(text != source for key, text in controls.items() if key != 'fixed')
    for label, text in controls.items():
        path = folder / (label + '.c'); path.write_text(text)
        library = folder / (label + '.dylib')
        result = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-dynamiclib',
            '-I' + str(ROOT / 'runtime'), str(path), '-o', str(library)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-3000:]
        lib = ct.CDLL(str(library)); parse = lib.car_audio_tmx_parse
        parse.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_size_t]
        accepted = []
        for name, data in resources.items():
            status = parse(ct.create_string_buffer(4096), data, len(data))
            if status == 0: accepted.append(name)
        if label == 'fixed':
            assert len(accepted) == 20
            bad = 0
            for data in resources.values():
                for at in (12, 16, 20, 24, 40, 48, 56, 60, 64, 88, 92):
                    changed = bytearray(data); changed[at] ^= 0x80
                    assert parse(ct.create_string_buffer(4096), bytes(changed), len(changed)) < 0
                    bad += 1
            print('malformed BE topology refusals:', bad)
        elif label == 'uncorrected':
            assert 'SOUND/ENGINE/CAR_073-ENGINE-PLAYER.TMX' not in accepted
            assert len(accepted) < 20
        else:
            assert 'SOUND/ENGINE/CAR_029-EXHAUST-PLAYER.TMX' not in accepted
        print(f'native parser {label}: {len(accepted)}/20 original stock TMXs')


def importer_proof(folder, resources):
    """Real source snapshots, conversion, publication and changed-input refusal."""
    from unittest import mock
    from setup_kit import car_catalog as catalog, car_import, car_selection
    original = input_path('Need for Speed Undercover').resolve()
    source = folder / 'source'
    source.mkdir()
    # Only the selected car and shared packs are required for recognition.
    (source / 'CARS').mkdir(); (source / 'GLOBAL').mkdir()
    car = 'DOD_CHR_BEE_07'
    shutil.copytree(original / 'CARS' / car, source / 'CARS' / car)
    for name in ('ATTRIBUTES.BIN', 'GLOBALB.LZC'):
        shutil.copy2(original / 'GLOBAL' / name, source / 'GLOBAL' / name)
    for name, data in resources.items():
        target = source / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    before = next(r for r in car_selection.load() if r['origin']['folder'] == car)
    engine = car_import.CarImporter(folder / 'catalog', folder / 'work')
    mw = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')).resolve()
    plan = engine.plan(source, mw_pc=mw, cars=[car_selection.spec(before)], measure=True)
    after = plan['cars'][0]['contract']
    assert after['outputs']['AUDIO.BIN']['sha256'] == '0d5cf10886953aefc44fbdab33c74a360fe842e46abf94c139d5ce469455c945'
    assert {k:v for k,v in after['outputs'].items() if k!='AUDIO.BIN'} == {k:v for k,v in before['outputs'].items() if k!='AUDIO.BIN'}
    names = {n for n in after['inputs']['source'] if n.startswith('SOUND/ENGINE/')}
    assert names == {'SOUND/ENGINE/CAR_073-ENGINE-PLAYER.TMX','SOUND/ENGINE/CAR_073-EXHAUST-PLAYER.TMX'}
    assert all(after['inputs']['source'][n] == dict(size=len(resources[n]),sha256=hashlib.sha256(resources[n]).hexdigest()) for n in names)
    rows = [after if r['slot']==before['slot'] and r['origin']==before['origin'] else r for r in car_selection.load()]
    doc = json.loads(catalog.packaged_policy().data)
    variants = doc['slots'][after['slot']]['variants']
    variant = dict(contract=catalog.contract_hash(after),compatibility=catalog.slot_compatibility(after))
    if variant not in variants: variants.append(variant)
    wire=catalog.encoded(doc);policy=catalog.Policy(wire,hashlib.sha256(wire).hexdigest())
    with mock.patch.object(catalog,'packaged_policy',return_value=policy), mock.patch.object(car_selection,'load',return_value=rows):
        engine.import_cars(source,mw_pc=mw,cars=[car_selection.spec(after)])
        verified=catalog.verify_current(engine.root,policy=policy)
        assert not verified.error and len(verified.selected)==1,verified.error
        assert verified.selected[0]['outputs']['AUDIO.BIN']==after['outputs']['AUDIO.BIN']
        pointer=(engine.root/'current.json').read_bytes()
        path=source/next(iter(names));original_bytes=path.read_bytes()
        changed=bytearray(original_bytes);changed[-1]^=1;path.write_bytes(changed)
        with unittest.TestCase().assertRaises((ValueError,OSError)):
            engine.import_cars(source,mw_pc=mw,cars=[car_selection.spec(after)])
        assert (engine.root/'current.json').read_bytes()==pointer
        path.write_bytes(original_bytes)
        path.unlink()
        with unittest.TestCase().assertRaisesRegex(ValueError,'missing reachable resources'):
            engine.import_cars(source,mw_pc=mw,cars=[car_selection.spec(after)])
        assert (engine.root/'current.json').read_bytes()==pointer
    print('Undercover importer: 1/1 pinned sidecars, 2/2 tracked TMXs; altered/missing controls 2/2 refused without activation')


class Boundaries(unittest.TestCase):
    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            car_audio_pack.build(b'unknown', 'DOD_CHR_BEE_07', {}, game='undercover')
        with self.assertRaises(ValueError):
            car_audio_pack.resource_paths(b'unknown', 'DOD_CHR_BEE_07', (), game='undercover')

    def test_converter_emits_supplied_audio(self):
        from unittest import mock
        from setup_kit import ucpc_car as converter
        raw=bytearray(176);struct.pack_into('<I',raw,16,30)
        source={'CARS/DOD_CHR_BEE_07/GEOMETRY.BIN':bytes(raw), 'CARS/DOD_CHR_BEE_07/TEXTURES.BIN':b'',
                'GLOBAL/GLOBALB.LZC':b'', 'GLOBAL/ATTRIBUTES.BIN':b'attrs', 'SOUND/ENGINE/SOURCE.TMX':b'sound'}
        native={p:b'' for p in ('CARS/MUSTANGGT/GEOMETRY.BIN','CARS/MUSTANGGT/TEXTURES.BIN',
                'CARS/'+converter.ps_namespace.TEMPLATE+'/GEOMETRY.BIN',
                'CARS/'+converter.ps_namespace.TEMPLATE+'/TEXTURES.BIN','GLOBAL/ATTRIBUTES.BIN')}
        with mock.patch.object(converter.mw,'pack_header',return_value=(0,168)), \
             mock.patch.object(converter,'find',return_value=(0,144)), \
             mock.patch.object(converter,'v8_textures',return_value=b''), \
             mock.patch.object(converter.mw,'texture_blobs',return_value=[]), \
             mock.patch.object(converter.uc_batch,'refpack',return_value=b''), \
             mock.patch.object(converter,'opaque_tyres',return_value=b''), \
             mock.patch.object(converter.ps_namespace,'stock_packs',return_value={}), \
             mock.patch.object(converter.mw,'wheel_sidecar',return_value=b'fit'), \
             mock.patch.object(converter.uc_attrib,'performance',return_value=b'performance'), \
             mock.patch.object(uc,'admitted',return_value=True), \
             mock.patch.object(car_audio_pack,'resource_paths',return_value=['SOUND/ENGINE/SOURCE.TMX']), \
             mock.patch.object(car_audio_pack,'build',return_value=b'audio') as build:
            result=converter.convert('DOD_CHR_BEE_07','MUSTANGGT',source,native)
        self.assertEqual(result['AUDIO.BIN'],b'audio')
        build.assert_called_once_with(b'attrs','DOD_CHR_BEE_07',{'SOUND/ENGINE/SOURCE.TMX':b'sound'},game='undercover')


def private(original=False):
    attr = input_path('Need for Speed Undercover/GLOBAL/ATTRIBUTES.BIN')
    override = os.environ.get('NFS_UC_X360_ISO')
    images = [Path(override)] if override else sorted((input_path().parent / 'ext_untrusted/undercover_x360').glob('*.iso'))
    if not attr.is_file() or len(images) != 1 or not images[0].is_file():
        print('SKIP private Undercover attributes/console audio absent'); return False
    attributes = attr.read_bytes()
    expected = expectations(attributes)
    with uc.Disc(images[0]) as disc:
        resources = {p: disc.read(p) for paths in expected.values() for p in paths}
    assert len(resources) == 20
    corrections, rates = 0, set()
    for path, data in resources.items():
        for track in uc.tmx(data)['tracks']:
            if track['kind'] != 'gin': continue
            raw = data[track['offset']:track['offset'] + track['size']]
            corrections += raw[:24] + raw[28:] == track['data'][:24] + track['data'][28:] and raw != track['data']
            rates.add(struct.unpack_from('<I', raw, 28)[0])
    assert corrections and 34000 in rates
    print('stock corpus: 14 cars, 28 ordered layers, 20 original TMXs;', corrections, 'GIN frame corrections; rates', sorted(rates))
    ps = module(ROOT / 'runtime/car_audio_prostreet_test.py', 'undercover_native_proof')
    with tempfile.TemporaryDirectory(prefix='undercover-audio-') as work:
        folder = Path(work).resolve(); manifest = []; pins = {}
        for car, paths in expected.items():
            package = reference_package(attributes, car, paths, resources)
            if not original:
                selected = car_audio_pack.resource_paths(attributes, car, resources, game='undercover')
                assert set(selected) == set(paths)
                assert car_audio_pack.build(attributes, car, {p: resources[p] for p in selected}, game='undercover') == package
            path = folder / (car + '.bin'); path.write_bytes(package)
            pins[car.lower()] = hashlib.sha256(package).hexdigest()
            manifest.append(f'{car.lower()} {path} ' + ' '.join(hashlib.sha256(resources[p]).hexdigest() for p in paths) + '\n')
        manifest_path = folder / 'stock.txt'; manifest_path.write_text(''.join(manifest))
        print('package pins:', json.dumps(pins, sort_keys=True))
        implementation = (ROOT / 'runtime/car_audio.c').read_text()
        guard = 'if(version==6) for(size_t i=0;i<sizeof undercover_packages/sizeof *undercover_packages;i++)'
        assert implementation.count(guard) == 1
        def check(result):
            if original:
                assert result.returncode == 1 and '0/28, 0/28; cars=14' in result.stdout, result.stderr[-3000:] + result.stdout[-1000:]
            else:
                assert result.returncode == 0 and '28/28, 28/28; cars=14' in result.stdout, result.stderr[-3000:] + result.stdout[-1000:]
        if original:
            implementation = implementation.replace(guard, guard.replace('version==6', '0'))
        ps.live(folder, manifest_path, harness_main=harness(ps), implementation=implementation, check=check)
        if not original:
            parser_proof(folder, resources)
            def no_admission(result):
                assert result.returncode == 1 and '0/28, 0/28; cars=14' in result.stdout, result.stderr[-3000:]
            ps.live(folder, manifest_path, harness_main=harness(ps),
                    implementation=implementation.replace(guard, guard.replace('version==6', '0')), check=no_admission)
            wrong = []
            changed = implementation
            for car, paths in expected.items():
                other = next(p for p in expected.values() if not set(p) & set(paths))
                package = reference_package(attributes, car, other, resources)
                path = folder / (car + '-wrong.bin'); path.write_bytes(package)
                digest = hashlib.sha256(package).hexdigest()
                old = '{"' + car.lower() + '","' + pins[car.lower()] + '"}'
                assert changed.count(old) == 1
                changed = changed.replace(old, '{"' + car.lower() + '","' + digest + '"}')
                wrong.append(f'{car.lower()} {path} ' + ' '.join(hashlib.sha256(resources[p]).hexdigest() for p in paths) + '\n')
            wrong_path = folder / 'wrong.txt'; wrong_path.write_text(''.join(wrong))
            ps.live(folder, wrong_path, harness_main=harness(ps), implementation=changed, check=no_admission)
            print('wrong-car pairing control: 0/28 on both constructors (exit 1)')
            normalization = 'if(track && e->package->version==6) M32(gin+24)=track->frames;'
            assert implementation.count(normalization) == 1
            def uncorrected(result):
                assert result.returncode != 0 and ('rd(actual+24)' in result.stderr or 'e && e->replaced' in result.stderr), result.stderr[-3000:]
            ps.live(folder, manifest_path, harness_main=harness(ps),
                    implementation=implementation.replace(normalization, ''), check=uncorrected)
            print('uncorrected guest Charger GIN frame count rejected (exit nonzero)')
            importer_proof(folder, resources)
    return True


if __name__ == '__main__':
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(Boundaries))
    if not result.wasSuccessful(): sys.exit(1)
    sys.exit(0 if private('--original' in sys.argv) else 3)
