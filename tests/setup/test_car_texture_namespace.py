"""Setup texture ownership and runtime role-label regression.

Run the generated PC/decoded writer checks even without installed inputs, then
exit 3 if NFS_INPUTS_ROOT/NFSC/CARS or NFS_GAME is absent.
The installed check audits serialized public convert_textures() output, lookup
rows, exact pixels and sampler state against the writer without a namespace.
Original writer: 36 generated failures, 4 full-entry failures and 12 installed
output failures (shared hashes/cut roles), exit 1. Shared writer: 0/0/0 failures
over 24/4/39 images, exit 0. Missing-input control: exit 3 after generated checks.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[2] / "tools"))
from setup_kit.input_paths import input_path
import contextlib
import io
import os
from pathlib import Path
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_kit import car_convert as cc
import test_car_convert as fixture

ROLES = ('_BADGING_N', '_TIRE_N', '_BADGING', '_TIRE')


def label(blob):
    return blob[-144:-120].split(b'\0')[0].decode('ascii')


def audit(before, after, mapping):
    errors = []
    if not before.keys() <= mapping.keys() or {mapping.get(h) for h in before} != after.keys():
        errors.append('incomplete texture lookup mapping')
    for old, blob in before.items():
        target_hash = mapping.get(old, old)
        if target_hash not in after:
            errors.append('carried texture missing from output')
            continue
        new = after[target_hash]
        # Labels, hashes and logical pixel offsets may change; image/state may not.
        if (blob[:-156], blob[-156:-144], blob[-112:-108], blob[-100:]) != (
                new[:-156], new[-156:-144], new[-112:-108], new[-100:]):
            errors.append('pixels/platform/sampler state changed')
        name = label(blob)
        role = next((r for r in ROLES if name.endswith(r)), None)
        if role is None or fixture.nh(name) != old:
            continue
        target = label(new)
        if not target.endswith(role) or fixture.nh(target) != target_hash:
            errors.append(f'cut role: {name} -> {target}')
        partner = fixture.nh(name + '_N')
        if not role.endswith('_N') and partner in before and fixture.nh(target + '_N') != mapping.get(partner, partner):
            errors.append(f'normal lookup lost: {name}')
    return errors


def generated():
    errors = []
    donor = fixture.texture('DONOR', 5)
    source = fixture.texture('DONOR')
    extras = {}
    for role in ('TIRE', 'TIRE_N', 'BADGING', 'BADGING_N'):
        extras.update(cc.pc_texture_entries(fixture.texture('DONOR', role=role)))
    # A global source image and a donor-only fallback both need private hashes.
    extras.update(cc.pc_texture_entries(fixture.texture('GLOBAL')))
    packs = []
    for decoded in (False, True):
        carried = {}
        for identity in ('LONGCARIDENTITY', 'OTHERCARIDENTITY'):
            options = dict(extras=extras, source_sampler_state=True)
            if decoded:
                options['decoded'] = cc.pc_texture_entries(source)
            data = None if decoded else source
            with contextlib.redirect_stdout(io.StringIO()):
                baseline, _ = cc.convert_textures(data, donor, **options)
                mapping = []
                output, _ = cc.convert_textures(data, donor, namespace=('DONOR', 'NFSAD_' + identity),
                                               renames=mapping, **options)
            before = dict(cc.texture_blobs(baseline))
            after = dict(cc.texture_blobs(output))
            errors.extend(audit(before, after, dict(mapping)))
            if before.keys() & after.keys():
                errors.append('stock/global hash retained')
            if carried.keys() & after.keys():
                errors.append('added packs share hashes')
            carried.update(after)
            packs.append(len(after))
        # Empty source: the entire donor pack must still be isolated.
        with contextlib.redirect_stdout(io.StringIO()):
            output, _ = cc.convert_textures(None, donor, decoded={}, namespace=('DONOR', 'NFSAD_EMPTY'))
        if set(cc.validate_textures(output, 5)) & {fixture.nh('DONOR_DIFFUSE')}:
            errors.append('donor-only fallback hash retained')
    print(f'generated: {len(packs)} packs, {sum(packs)} images; {len(errors)} failures')
    return errors


def full_entry():
    errors = []
    for role in ('TIRE', 'BADGING'):
        case = fixture.CarConversion()
        case.setUp()
        try:
            identity = 'LONGCARIDENTITY'
            renamed = case.car.with_name(identity)
            case.car.rename(renamed)
            case.car = renamed
            source = fixture.texture(identity)
            donor = fixture.texture('DONOR', 5, role=role)
            (case.car / 'GEOMETRY.BIN').write_bytes(fixture.geometry(identity, True))
            (case.car / 'TEXTURES.BIN').write_bytes(source)
            (case.donor / 'TEXTURES.BIN').write_bytes(donor)
            (case.shared / 'attributes.bin').write_bytes(fixture.attributes(identity))
            with contextlib.redirect_stdout(io.StringIO()):
                case.convert()
                baseline, _ = cc.convert_textures(source, donor, source_sampler_state=True,
                    aliases=cc.slot_texture_aliases(source, identity, 'DONOR'))
            after = dict(cc.texture_blobs((case.out / 'TEXTURES.BIN').read_bytes()))
            mapping = dict(struct.iter_unpack('<II', (case.out / 'SOLIDMAP.BIN').read_bytes()[16:]))
            before = dict(cc.texture_blobs(baseline))
            errors.extend(audit(before, after, mapping))
            if after.keys() & before.keys():
                errors.append('full entry retained stock hashes')
            models = cc.validate_geometry((case.out / 'GEOMETRY.BIN').read_bytes())
            if any(set(m['textures']) - after.keys() for m in models.values()):
                errors.append('full entry geometry is not bound to private output')
        finally:
            case.tearDown()
    print(f'generated full entry: 2 conversions, 4 images; {len(errors)} failures')
    return errors


def installed(source, game):
    # Short and long identities exercise donor fallback ownership and both
    # diffuse/normal suffix budgets through the public texture writer.
    choices = (('G35', 'IS300'), ('MURCIELAGO640', 'MURCIELAGO'), ('MUSTANGSHLBYN', 'MUSTANGGT'))
    needed = [base / name / 'TEXTURES.BIN' for car, donor in choices
              for base, name in ((source, car), (game / 'CARS', donor))]
    if not all(p.is_file() for p in needed):
        return None
    stock = set()
    for path in sorted((game / 'CARS').glob('*/TEXTURES.BIN')):
        data = path.read_bytes()
        if data:
            stock.update(h for h, _ in cc.texture_blobs(data))
    errors, carried = [], set()
    images = 0
    with tempfile.TemporaryDirectory(prefix='setup-texture-namespace-') as scratch:
        for car, donor in choices:
            source_tex = (source / car / 'TEXTURES.BIN').read_bytes()
            donor_tex = (game / 'CARS' / donor / 'TEXTURES.BIN').read_bytes()
            aliases = cc.slot_texture_aliases(source_tex, car, donor)
            aliases.update(cc.normal_resource_aliases(donor))
            mapping = []
            with contextlib.redirect_stdout(io.StringIO()):
                baseline, _ = cc.convert_textures(source_tex, donor_tex, aliases=aliases, source_sampler_state=True)
                output, _ = cc.convert_textures(source_tex, donor_tex, aliases=aliases, source_sampler_state=True,
                    namespace=(donor, 'NFSAD_' + car), renames=mapping)
            out = Path(scratch) / (car + '.BIN')
            out.write_bytes(output)
            written = out.read_bytes()
            cc.validate_textures(written, 5)
            after = dict(cc.texture_blobs(written))
            errors.extend(car + ': ' + e for e in audit(dict(cc.texture_blobs(baseline)), after, dict(mapping)))
            overlap = after.keys() & stock
            if overlap:
                errors.append(f'{car}: {len(overlap)} stock-shared hashes')
            if after.keys() & carried:
                errors.append(f'{car}: added packs share hashes')
            carried.update(after)
            images += len(after)
    print(f'installed Setup output: {len(choices)} cars, {images} images; {len(errors)} failures')
    return errors


def main():
    errors = generated() + full_entry()
    source = input_path('NFSC/CARS')
    game = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition'))
    actual = installed(source, game)
    errors.extend(actual or [])
    for error in errors[:12]:
        print('FAIL:', error)
    if errors:
        return 1
    if actual is None:
        print('SKIP: needs source and stock car inputs; generated writer checks passed')
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
