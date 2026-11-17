"""Factory brakes on all 45 ProStreet/Undercover additions, every stored LOD/draw.

One number: rotor draws without exact source pixels/state, normal-map/texture loader requests,
and a requested brake solid. Fixed: 0/251 (166 P + 85 U); mounts checked separately.
Original converter control: 257/257 bad draws and 92/92 unreadable LOD-A mounts
across the original 46 cars; the excluded GNX contributes 6 draws and 2 mounts.
Current 45-car broken control: 251/251 bad draws, 90/90 unreadable mounts.
The original builder is a SHA256-pinned fragment, never read from git history.
--broken-images and --broken-mounts each fail (exit 1); bare absent assets: exit 3.
Caliper MISC pixels, shading, UVs/colour and all source triangles stay intact.
"""
import contextlib
import hashlib
import io
import os
from pathlib import Path
import struct
import sys
import tempfile
from unittest import mock

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'tools')]
from setup_kit.input_paths import input_path
import carbon_car as c
import v25_batch as vb
import v25_car as v25
import private_cars as pc
import ps_namespace
import ucpc_car

# Loader names decoded from GetUsedCarTextureInfo, not the alias builder.
REQUESTS = ('MISC MISC_N INTERIOR INTERIOR_N ENGINE DRIVER NEON BADGING BADGING_N '
            'BADGING_EU LOGO LICENSE_PLATE DOOR_HANDLE SIDELIGHT TIRE TIRE_N RIM RIM_BLUR TREAD TREAD_N').split()


# Original model builder; independent of checkout history at test time.
PIN_MAKE_MODELS = '''def make_models(selected, car, refs, texture_hashes, body_roles=()):
    table = v25.templates(refs)
    models, manifest = {}, {}
    aliases = {value: key for key, value in v25.ALIASES.items()}
    # MW names rear side glass REAR_LEFT/RIGHT_WINDOW (FORDGT has none to alias).
    aliases.update(BASE='BASE', WHEEL_TIRE_REAR='REAR_TIRE', SPOILER='SPOILER',
                   WINDOW_REAR_LEFT='REAR_LEFT_WINDOW', WINDOW_REAR_RIGHT='REAR_RIGHT_WINDOW', **ROTOR_ROLES)
    groups = {}
    for srcname, src in sorted(selected.items()):
        role, lod = srcname[len(car + '_KIT00_'):].rsplit('_', 1)
        body = role in v25.BODY_PARTS or role in ('DOOR_REAR_LEFT', 'DOOR_REAR_RIGHT', 'BUMPER_FRONT_EXTRA') or role in body_roles
        target_role = 'BODY' if body else 'INTERIOR' if role in ('ENGINE', 'SEAT_LEFT', 'SEAT_RIGHT') else aliases.get(role, role)
        name = f'{car}_BASE_{lod}' if role == 'BASE' else f'{car}_KIT00_{target_role}_{lod}'
        # No reference-bound partition: every selected triangle occurs once.
        normalized = car_local_source(src)
        m = v25.target_model(normalized, srcname, range(len(src['draws'])), table, texture_hashes)
        # Static upload flags come from a known MW stock slot, never PS flags.
        refname = f'FORDGT_BASE_{lod}' if role == 'BASE' else f'FORDGT_KIT00_{target_role}_{lod}'
        ref = refs.get(refname, refs['FORDGT_KIT00_BODY_A'])
        mesh = bytearray(m['mesh']); mesh[8:16] = ref['mesh'][8:16]; m['mesh'] = bytes(mesh)
        groups.setdefault(name, []).append(m)
        manifest.setdefault(name, []).append(srcname)
    for name, parts in groups.items(): models[name] = c.assemble(name, parts)
    return models, manifest
'''


def original_builder():
    assert hashlib.sha256(PIN_MAKE_MODELS.encode()).hexdigest() == \
        '94ce68efa19b48640df408c5983aedafe92d584ec334bd67e7789d2493b703de'
    env = dict(vars(vb))
    exec(PIN_MAKE_MODELS, env)
    builder = env['make_models']
    # The mount control predates the independent global wheel-palette import.
    def without_mounts(*args, wheel_images=(), **kwargs):
        return builder(*args, **kwargs)
    return without_mounts


def source_models(path, game):
    tex = (path / 'TEXTURES.BIN').read_bytes()
    if game == 'U': tex = ucpc_car.v8_textures(tex)
    images = dict(c.texture_blobs(tex))
    raw = (path / 'GEOMETRY.BIN').read_bytes()
    car = path.name.upper()
    sources = vb.decode_sources(raw, car) if game == 'P' else ucpc_car.decode_sources(raw, car, set(images))
    return vb.stock_sources(sources, car), images


def source_atlases(root, game):
    wanted = {0xC387F4D4 + i for i in (0, 1, 2, 5)}
    if game == 'P':
        path = next(p for p in (root / 'GLOBAL/InGameA.bun', root / 'GLOBAL/INGAMEA.BUN') if p.is_file())
        return c.carbon_global_extras(path, wanted | {0xE010D1C9}) | c.carbon_global_extras(
            next(p for p in (root / 'GLOBAL/GlobalB.lzc', root / 'GLOBAL/GLOBALB.LZC') if p.is_file()), {0xE010D1C9})
    path = next(p for p in (root / 'GLOBAL/GlobalB.lzc', root / 'GLOBAL/GLOBALB.LZC') if p.is_file())
    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / 'GlobalB.lzc'
        staged.write_bytes(ucpc_car.uc.refpack(path.read_bytes()))
        return c.carbon_global_extras(staged, wanted)


def inspect_pack(pack, row, selected, local, atlases, *, broken_mounts=False):
    """Compare every source brake draw with its serialized output and loader requests."""
    car, donor = row['name'], row['donor']
    models = vb.output_models((pack / 'GEOMETRY.BIN').read_bytes())
    images = dict(c.texture_blobs((pack / 'TEXTURES.BIN').read_bytes()))
    requests = dict(struct.iter_unpack('<II', (pack / 'SOLIDMAP.BIN').read_bytes()[16:]))
    reached = {requests.get(c.name_hash(donor + '_' + role)) for role in REQUESTS}
    grouped = {}
    source_rotors = 0
    for name, src in sorted(selected.items()):
        role, lod = name[len(car + '_KIT00_'):].rsplit('_', 1)
        discs = sum(d['light'] == c.name_hash('BRAKEDISC') for d in src['draws'])
        source_rotors += discs
        if not role.startswith(('BRAKE_', 'BRAKEROTOR_')):
            assert not discs, f'unclassified embedded rotor: {name}'
            continue
        end = role.rsplit('_', 1)[1]
        target = f'NFSAD_{car}_KIT00_{end}_BRAKE_{lod}'
        grouped.setdefault(target, []).extend(src['draws'])
    wrong = total = calipers = 0; styles = {}
    for name, draws in grouped.items():
        model = models.get(name)
        assert model is not None and len(model['records']) == len(draws), name
        assert requests.get(c.name_hash(donor + name[len('NFSAD_' + car):])) == c.name_hash(name), name
        cursor = 0
        for draw, record, label in zip(draws, model['records'], model['names']):
            diffuse, normal = (model['textures'][record[i]] for i in (24, 25))
            blob = images.get(diffuse)
            vertices = model['vertices'][cursor:cursor + len(draw['vertices'])]; cursor += len(vertices)
            assert np.array_equal(vertices[:, 24:], draw['vertices'][:, 24:]), f'brake UV/colour changed: {name}'
            if draw['light'] == c.name_hash('BRAKEDISC'):
                source = atlases[draw['texture']]
                good = (diffuse in reached and blob is not None and blob[:-156] == source[2]
                        and blob[-156:-32][79:88] == source[0][51:60] and label.split(b'\0')[0] == b'BRAKE_STYLE01_ROTOR')
                if draw['normal'] is not None:
                    nblob = images.get(normal)
                    good &= (normal == requests.get(c.name_hash(donor + '_NEON')) and nblob is not None
                             and nblob[:-156] == atlases[draw['normal']][2])
                wrong += not good; total += 1
                styles[draw['texture']] = styles.get(draw['texture'], 0) + 1
            else:
                assert draw['texture'] == c.name_hash(car + '_MISC'), f'caliper not car MISC: {name}'
                assert diffuse in reached and blob is not None and blob[:-156] == local[draw['texture']][:-148], name
                assert label.split(b'\0')[0] == (b'BODY_ALUMINUM' if draw['light'] == c.name_hash('ALUMINUM') else b'BRAKE_KIT00_CALIPER'), name
                calipers += 1
    assert total == source_rotors and total, f'incomplete rotor census: {car}'
    missing = mounts = all_mounts = 0
    for name, src in selected.items():
        role, lod = name[len(car + '_KIT00_'):].rsplit('_', 1)
        if role not in ('WHEEL_TIRE_FRONT', 'WHEEL_TIRE_REAR'): continue
        end = role.rsplit('_', 1)[1]
        model = models[f'NFSAD_{car}_KIT00_{end}_TIRE_{lod}']
        rows = c.marker_records(model)
        source = next(r for r in src['markers'] if c.u32(r) in (c.name_hash('BRAKE_FRONT'), c.name_hash('BRAKE_REAR')))
        matrix = np.frombuffer(src['header'][64:128], '<f4').reshape(4, 4).astype(np.float64).copy()
        matrix[3, :3] = 0
        expected = (np.frombuffer(source[16:], '<f4').reshape(4, 4).astype(np.float64) @ matrix).astype('<f4').tobytes()[52:56]
        marker = next((r for r in rows if c.u32(r) == c.name_hash(end + '_BRAKE')), None)
        bad = marker is None or marker[68:72] != expected
        all_mounts += 1
        if lod == 'A': missing += bad; mounts += 1
        elif not broken_mounts: assert not bad, f'unreadable {name} mount'
    if car == 'GTO65':
        assert not any('BRAKEROTOR_FRONT' in n for n in selected), 'GTO65 front rotor premise changed'
    return wrong, total, missing, mounts, calipers, all_mounts, styles


def audit(rows, roots, mw, out, *, broken=False):
    atlases = {g: source_atlases(root, g) for g, root in roots.items()}
    counts = [0] * 6; mixed = {}; per_game = {'P': 0, 'U': 0}
    with contextlib.ExitStack() as stack:
        if broken or '--broken-images' in sys.argv:
            # Original no-atlas converter; bypass the NEW validation pin only in
            # the deliberately broken pipeline, so serialized output is inspected.
            stack.enter_context(mock.patch.object(vb, 'rotor_extras', return_value={}))
            stack.enter_context(mock.patch.object(vb, 'validate_output', return_value={}))
        if broken or '--broken-mounts' in sys.argv:
            stack.enter_context(mock.patch.object(vb, 'make_models', original_builder()))
            stack.enter_context(mock.patch.object(vb, 'validate_output', return_value={}))
        for row in rows:
            source = roots[row['game']] / 'CARS' / row['name']; dest = out / row['name']
            selected, local = source_models(source, row['game'])
            convert = ps_namespace.convert if row['game'] == 'P' else ucpc_car.convert
            with contextlib.redirect_stdout(io.StringIO()): convert(source, mw / row['donor'], dest)
            result = inspect_pack(dest, row, selected, local, atlases[row['game']], broken_mounts=broken or '--broken-mounts' in sys.argv)
            for i, value in enumerate(result[:6]): counts[i] += value
            per_game[row['game']] += result[1]
            if len(result[6]) > 1: mixed[row['name']] = result[6]
            print(f'{row["name"]}: {result[0]}/{result[1]} bad rotor draws, {result[2]}/{result[3]} unreadable A mounts', flush=True)
    assert per_game == {'P': 166, 'U': 85}, per_game
    assert set(mixed) == {'MER_SLR_722_07', 'REN_MEG_COU_08', 'VOL_SCI_STK_08'}, mixed
    assert mixed['MER_SLR_722_07'] == {0xC387F4D4: 2, 0xC387F4D5: 2}
    assert all(mixed[n] == {0xC387F4D4: 6, 0xC387F4D5: 4} for n in ('REN_MEG_COU_08', 'VOL_SCI_STK_08'))
    return counts


def main():
    roots = {'P': input_path('Need for Speed ProStreet'), 'U': input_path('Need for Speed Undercover')}
    mw = Path(os.environ.get('NFS_GAME', HERE.parents[1] / 'Need For Speed Most Wanted Black Edition')) / 'CARS'
    rows = [r for r in pc.identities() if r['role'] == 'addition' and r['game'] in roots]
    assert len(rows) == 45 and sum(r['game'] == 'U' for r in rows) == 14
    if not all((roots[r['game']] / 'CARS' / r['name'] / f).is_file() for r in rows for f in pc.PACKS) or not (mw / 'FORDGT/GEOMETRY.BIN').is_file():
        print('SKIP: private ProStreet / Undercover PC / MW packs absent'); return 3
    with tempfile.TemporaryDirectory(prefix='factory-brakes-') as scratch:
        root = Path(scratch)
        fixed = audit(rows, roots, mw, root / 'fixed')
        old = audit(rows, roots, mw, root / 'original', broken=True)
        # Retain the historical 46-car control without returning the excluded
        # pack to the catalogue. Its source is only a broken-control fixture.
        dropped = dict(name='BUI_GNX_STK_87', donor='SUPRA', game='U')
        path = roots['U'] / 'CARS' / dropped['name']
        with mock.patch.object(vb, 'rotor_extras', return_value={}), mock.patch.object(vb, 'validate_output', return_value={}), mock.patch.object(vb, 'make_models', original_builder()):
            with contextlib.redirect_stdout(io.StringIO()): ucpc_car.convert(path, mw / 'SUPRA', root / 'dropped')
            selected, local = source_models(path, 'U')
            excluded = inspect_pack(root / 'dropped', dropped, selected, local, source_atlases(roots['U'], 'U'), broken_mounts=True)
        assert excluded[:4] == (6, 6, 2, 2), excluded[:4]
        assert old[:4] == [251, 251, 90, 90], old
        assert old[0] + excluded[0] == 257 and old[2] + excluded[2] == 92
        print(f'45 cars: {fixed[0]}/{fixed[1]} bad rotor draws; {fixed[2]}/{fixed[3]} unreadable A mounts; {fixed[4]} exact caliper draws; {fixed[5]} mounts checked')
        print('original control: 257/257 bad draws / 92/92 unreadable mounts (includes excluded GNX)')
        failed = bool(fixed[0] or fixed[2])
        print('FAIL' if failed else 'PASS')
        return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
