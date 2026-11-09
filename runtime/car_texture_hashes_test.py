#!/usr/bin/env python3
"""Every car texture pack must own its texture hashes alone.

StreamingTexturePackLoader (0091A098) hands a requested hash to the first loaded pack, in MRU order, that holds it
(LoadStreamingEntry 00508560), and deleting a pack frees all of its loaded entries whatever their reference count
(UnloadAllStreamingEntries 00501050 sets refs to 1). A hash held by two car packs is therefore owned by whichever was
used last, and dies with it: a cached added car evicted at race load takes its donor's shared texture with it, and the
donor car on track draws without it. The 92 stock car packs share no hash at all.

Converts source cars (NFS_INPUTS_ROOT, source <inputs>/NFSC/CARS) into temporary output and checks against
the stock CARS folder (NFS_GAME) and each other. Never reads or writes an installed private-car generation.
Exit 3 without source assets.
Proof of failure: the self test's overlapping pair is reported (1 shared hash) and its disjoint pair is not (0).
Fresh conversion before the fix: FAIL 406 pairs / 460 pair-hashes (92 stock, 28 added packs), exit 1.
Includes 41 stock-shared hashes, e.g. G35 holds IS300_TIRE 5CE624C1 and 997GT3RS holds 997S_TIRE D898A0EE.
After: PASS 0 pairs / 0 pair-hashes; 539 exact images/state and 289560 material bindings preserved, exit 0.
Omitting the geometry remap is rejected at G35/IS300_BASE_A (1/1 broken binding control).
Role labels: the 23-character label must keep each renamed image's _TIRE/_BADGING ending (materials.m keys the X360
normal map by it, label + "_N" through bStringHash) and _TIRE_N ending (x360_textures.m later_tire).
World packs: no added hash may equal a texture hash in the stock GLOBAL, FRONTEND or TRACKS packs (4437 hashes, 0
shared; overlapping-pack control in the self test).
Before the label fix: FAIL 13 cars, exit 1: MURCIELAGO640, MUSTANGSHLBYN, MUSTANGSHLBYO lose _TIRE, 8 cars _BADGING,
5 more only a _BADGING_N label. After: PASS, exit 0.
Names only: image state must equal the conversion that renamed only <slot>_* images. The un-renamed comparison
conversion picks a different same-format default record for 32 images without an exact donor record (tail +132 is 1
there, 0 in that conversion); PRE_NAMESPACE_DEFAULTS pins them, decoded from it. Before the default-order fix: FAIL
32 textures in 9 cars, exit 1. After: PASS, 0 textures, exit 0.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[1] / "tools"))
from setup_kit.input_paths import input_path
import contextlib, io, os, struct, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Label endings the runtime matches on: materials.m carnm_find (_TIRE, _BADGING, then + "_N"),
# x360_textures.m / later_textures_private.h later_tire (_TIRE_N); _BADGING_N for the same full-name rule.
ROLES = ('_TIRE', '_BADGING', '_TIRE_N', '_BADGING_N')
# Images whose v5 defaults come from a no-exact-donor fallback record (texture tail +132 = 0 where the un-renamed
# conversion writes 1), per car, by source hash.
PRE_NAMESPACE_DEFAULTS = {
    '300C': '2571A11A 4A60BDEC 69C032BD 69C032BE 91050E14 931085BE',
    '997GT3RS': '21978F9E 3C904906 7811C14A',
    '997TT': '1A975569 A009F4B1 A2156C5B',
    'CCX': '74BC59A5 76C7D14F 83A8FED0 87DE5552 917F6DDD',
    'COROLLA': 'B6AF56F6 D2AE1424',
    'G35': '0A4E0D99 0C598543 46367551',
    'JAGUARXK': '65A00E49 67AB85F3 69C032BE A07F2D74 ED973F76 F7385801',
    'MURCIELAGO640': '0E848CAA',
    'SKYLINE': '31284063 3333B80D E8C9FFDB',
}


def chunk(cid, body):
    return struct.pack('<II', cid, len(body)) + body


def table_hashes(data):
    """Hashes of every 0x33310003 streaming table (24-byte entries, hash first) inside nested chunks."""
    out = []

    def walk(o, end):
        while o + 8 <= end:
            cid, size = struct.unpack_from('<II', data, o)
            if o + 8 + size > end:
                raise ValueError(f'chunk {cid:08X} at {o} overruns its parent')
            if cid == 0x33310003:
                if size % 24:
                    raise ValueError(f'streaming table at {o} is {size} bytes, not a multiple of 24')
                out.extend(struct.unpack_from('<I', data, o + 8 + i * 24)[0] for i in range(size // 24))
            elif cid & 0x80000000:
                walk(o + 8, o + 8 + size)
            o += 8 + size

    walk(0, len(data))
    return out


def texture_hashes(data):
    """Every texture hash of a non-car file: 0x33310002 lists (8-byte entries) and streaming tables; {} if not chunked."""
    out = set()

    def walk(o, end):
        while o + 8 <= end:
            cid, size = struct.unpack_from('<II', data, o)
            if o + 8 + size > end:
                raise ValueError(f'chunk {cid:08X} at {o} overruns its parent')
            if cid == 0x33310002:
                out.update(struct.unpack_from('<I', data, o + 8 + i * 8)[0] for i in range(size // 8))
            elif cid & 0x80000000:
                walk(o + 8, o + 8 + size)
            o += 8 + size

    try:
        walk(0, len(data))
    except ValueError:
        return set()   # attribute and memory files are not chunk trees
    return out | set(table_hashes(data))


def world_hashes(game):
    out = set()
    for folder in ('GLOBAL', 'FRONTEND', 'TRACKS'):
        for f in sorted((game.parent / folder).rglob('*')):
            if f.is_file() and f.suffix.upper() in ('.BUN', '.BIN'):
                out |= texture_hashes(f.read_bytes())
    return out


def shared(packs):
    """{(pack a, pack b): sorted shared hashes} over every pair, plus hashes repeated inside one pack."""
    owners = {}
    for name, hashes in packs.items():
        for h in hashes:
            owners.setdefault(h, []).append(name)
    pairs = {}
    for h, names in owners.items():
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pairs.setdefault((a, b), []).append(h)
    return {k: sorted(v) for k, v in pairs.items()}


def self_test():
    def pack(*hashes):
        table = b''.join(struct.pack('<6I', h, 0, 0, 0, 0, 0) for h in hashes)
        return chunk(0xB3300000, chunk(0, bytes(48)) + chunk(0xB3310000, chunk(0x33310003, table)))
    a, b, c = table_hashes(pack(1, 2, 0x5CE624C1)), table_hashes(pack(3, 0x5CE624C1)), table_hashes(pack(4, 5))
    assert a == [1, 2, 0x5CE624C1] and b == [3, 0x5CE624C1], (a, b)
    overlap, disjoint = shared({'X': a, 'Y': b}), shared({'X': a, 'Z': c})
    assert overlap == {('X', 'Y'): [0x5CE624C1]}, overlap
    assert disjoint == {}, disjoint
    world = chunk(0xB3300000, chunk(0xB3310000, chunk(0x33310002, struct.pack('<4I', 7, 0, 0x5CE624C1, 0))))
    assert texture_hashes(world) == {7, 0x5CE624C1} and texture_hashes(world) & set(b) == {0x5CE624C1}
    assert texture_hashes(world) & set(c) == set()
    assert texture_hashes(chunk(0x7F000000, bytes(4))[:-1]) == set()
    try:
        table_hashes(chunk(0xB3300000, chunk(0x33310003, bytes(23))))
    except ValueError:
        pass
    else:
        raise AssertionError('a malformed table must be refused')


def main():
    self_test()
    game = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')) / 'CARS'
    carbon = input_path('NFSC/CARS')
    sys.path.insert(0, str(ROOT / 'tools_snapshot'))
    import carbon_namespace as ns
    import private_cars
    rows = [r for r in private_cars.identities() if r['game'] == 'C']
    if not game.is_dir() or not all((folder / r[key] / f).is_file()
                                  for r in rows for folder, key in ((carbon, 'folder'), (game, 'donor'))
                                  for f in private_cars.PACKS):
        print('SKIP: needs stock and source car packs')
        return 3
    with tempfile.TemporaryDirectory(prefix='car-texture-hashes-') as td:
        added = Path(td) / 'added'
        converted = []
        incomplete = set()
        for row in rows:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = ns.convert(carbon / row['folder'], game / row['donor'], added / row['name'])
            if result.get('missing_stock'):
                incomplete.add(row['name'])
                print(f"SKIP candidate {row['name']}: missing stock solids {result['missing_stock']}")
                (added / row['name'] / 'TEXTURES.BIN').unlink()
            else:
                converted.append(row)
        assert incomplete == {'CHALLENGER71', 'CUDA'}, f'unexpected incomplete conversions: {incomplete}'
        assert len(converted) == 28, f'expected 28 complete conversions, got {len(converted)}'
        status = check_packs(game, added)
        if status:
            return status
        return pixel_bindings(carbon, game, added, Path(td) / 'baseline', converted)


def check_packs(game, added):
    packs = {}
    for folder, label in ((game, 'stock'), (added, 'added')):
        for car in sorted(p.name for p in folder.iterdir() if (p / 'TEXTURES.BIN').is_file()):
            packs[f'{label}:{car}'] = table_hashes((folder / car / 'TEXTURES.BIN').read_bytes())
    stock = sum(k.startswith('stock:') for k in packs)
    additions = len(packs) - stock
    if not additions:
        print('SKIP: no converted TEXTURES.BIN')
        return 3
    pairs = shared(packs)
    for (a, b), hashes in sorted(pairs.items()):
        print(f'FAIL: {a} and {b} share {len(hashes)} texture hash(es): {" ".join(f"{h:08X}" for h in hashes)}')
    total = sum(len(v) for v in pairs.values())
    if pairs:
        print(f'car_texture_hashes_test: FAIL {len(pairs)} pack pair(s), {total} shared hash(es) '
              f'({stock} stock, {additions} added packs)')
        return 1
    world = world_hashes(game)
    hits = {k: sorted(world & set(v)) for k, v in packs.items() if k.startswith('added:') and world & set(v)}
    for k, hashes in sorted(hits.items()):
        print(f'FAIL: {k} shares {" ".join(f"{h:08X}" for h in hashes)} with a GLOBAL/FRONTEND/TRACKS pack')
    if hits or len(world) < 1000:
        print(f'car_texture_hashes_test: FAIL {len(hits)} added pack(s) share a world texture hash ({len(world)} read)')
        return 1
    print(f'car_texture_hashes_test: PASS {stock} stock and {additions} added car packs share no texture hash '
          f'with each other or {len(world)} world texture hashes')
    return 0


def label(blob):
    return blob[-156:][12:36].split(b'\0')[0].decode('ascii')


def role_labels(old_tex, new_tex, mapping):
    """Problems with role endings: each full old label ending in a role must stay a full, same-ending private label,
    and a _TIRE/_BADGING label's "_N" partner must be found from the new label as the runtime builds it."""
    import carbon_car as cc
    bad = []
    for h, blob in old_tex.items():
        old = label(blob)
        role = next((r for r in sorted(ROLES, key=len, reverse=True) if old.endswith(r)), None)
        if role is None or cc.name_hash(old) != h:
            continue
        new = label(new_tex[mapping[h]])
        if not new.endswith(role) or cc.name_hash(new) != mapping[h]:
            bad.append(f'{old} -> {new!r}')
            continue
        partner = cc.name_hash(old + '_N')
        if not role.endswith('_N') and partner in old_tex and cc.name_hash(new + '_N') != mapping[partner]:
            bad.append(f'{old}_N not found from {new!r}')
    return bad


def pixel_bindings(carbon, game, added, baseline, rows):
    """Compare each material's image and sampler bytes with the un-renamed conversion (plus the pinned defaults)."""
    import carbon_batch
    import carbon_car as cc
    import test_carbon_car as checks
    textures = bindings = 0
    roles, state = {}, {}
    pinned = {(car, int(h, 16)) for car, hashes in PRE_NAMESPACE_DEFAULTS.items() for h in hashes.split()}
    for row in rows:
        name, donor = row['name'], row['donor']
        before, after = baseline / name, added / name
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            carbon_batch.convert_one(carbon / row['folder'], game / donor, before, added=True)
        old_tex = dict(cc.texture_blobs((before / 'TEXTURES.BIN').read_bytes()))
        new_tex = dict(cc.texture_blobs((after / 'TEXTURES.BIN').read_bytes()))
        mapping = dict(struct.iter_unpack('<II', (after / 'SOLIDMAP.BIN').read_bytes()[16:]))
        assert old_tex.keys() <= mapping.keys(), (name, 'every carried texture needs a lookup row')
        assert {mapping[h] for h in old_tex} == new_tex.keys(), (name, 'texture coverage')
        for h, blob in old_tex.items():
            new = new_tex[mapping[h]]
            # Only the debug label/hash and logical placement may change.
            assert blob[:-156] == new[:-156], (name, f'{h:08X}', 'pixel bytes')
            a, b = bytearray(blob[-156:]), new[-156:]
            if (name, h) in pinned:
                assert a[132] == 1, (name, f'{h:08X}', 'stale pre-namespace default pin')
                a[132] = 0
                pinned.discard((name, h))
            if not (a[:12] == b[:12] and a[40:48] == b[40:48] and a[52:] == b[52:]):
                state.setdefault(name, []).append(f'{h:08X}')
            textures += 1
        bad = role_labels(old_tex, new_tex, mapping)
        if bad:
            roles[name] = bad
        old_models = checks.mw_models((before / 'GEOMETRY.BIN').read_bytes())
        new_models = checks.mw_models((after / 'GEOMETRY.BIN').read_bytes())
        assert len(old_models) == len(new_models), name
        for solid, model in old_models.items():
            other = new_models['NFSAD_' + name + solid[len(donor):]]
            assert other['textures'] == [mapping.get(h, h) for h in model['textures']], (name, solid, 'image bindings')
            assert model['records'] == other['records'] and model['lights'] == other['lights'], (name, solid, 'materials')
            assert model['vertices'].tobytes() == other['vertices'].tobytes() and model['indices'].tobytes() == other['indices'].tobytes(), (name, solid, 'mesh')
            bindings += sum(5 for _ in model['records'])
    assert not pinned, ('pre-namespace default pins not converted', sorted(pinned))
    for name, hashes in sorted(state.items()):
        print(f'FAIL: {name} image state differs from the pre-namespace conversion: {" ".join(hashes)}')
    if state:
        print(f'car_texture_hashes_test: FAIL {sum(map(len, state.values()))} textures in {len(state)} cars '
              'change more than their names')
        return 1
    print(f'car_texture_hashes_test: exact pixels/state for {textures} textures; {bindings} material bindings preserved')
    for name, bad in sorted(roles.items()):
        print(f'FAIL: {name} role labels: {"; ".join(bad)}')
    if roles:
        print(f'car_texture_hashes_test: FAIL {len(roles)} car(s) lose a role label ending')
        return 1
    print('car_texture_hashes_test: PASS every role label keeps its full private name')
    return 0


if __name__ == '__main__':
    sys.exit(main())
