"""Added cars that author no rear brake solid must convert; an authored one stays required.

Two source cars ship front brake solids only. The stock audit asked every car for a rear brake
under the MW slot's name, so both conversions were refused. MW frees a part model whose solid is
absent (CarRenderInfo::UpdateCarParts 00755800) and its own GALLARDO ships no rear brake solid.
One number: stock solids the audit reports missing after a full conversion.
  fixed audit:    CHALLENGER71 0, CUDA 0
  previous audit: CHALLENGER71 1, CUDA 1 (the pinned fragment below: CAMARO_KIT00_REAR_BRAKE_A)
Control: CAMARON authors a rear brake, and both audits still require it.
Offline; private Carbon/MW packs absent: exit 3.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[1] / "tools"))
from setup_kit.input_paths import input_path
import contextlib
import hashlib
import io
import os
import sys
import tempfile

HERE = _InputsPath(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import carbon_batch
import carbon_car as cc
import carbon_namespace

# Reviewed previous audit list; the digest keeps this control from drifting.
OLD_AUDIT = r'''    stock = [f'{slot}_BASE_{lod}' for lod in 'ABCD']
    stock += [f'{slot}_KIT00_BODY_{lod}' for lod in 'ABCD']
    stock += [f'{slot}_KIT00_{end}_BRAKE_A' for end in ('FRONT', 'REAR')]
'''
assert hashlib.sha256(OLD_AUDIT.encode()).hexdigest() == 'd9711dc530bacfefea067e39820d0ca8b9c1dd931c1732ab52e82657d0f10afc', 'pinned baseline changed'

NO_REAR = ('CHALLENGER71', 'CUDA')
DONOR = 'CAMARO'


def previous_stock(slot, source, authored):
    scope = {'slot': slot}
    exec('\n'.join(line[4:] for line in OLD_AUDIT.splitlines()), scope)
    return scope['stock']


def solid_names(path):
    return {cc.model(s)['name'] for _, s in cc.solids(path.read_bytes())}


def main():
    carbon = input_path('NFSC') / 'CARS'
    mw = _InputsPath(os.environ.get('NFS_GAME', HERE.parents[1] / 'Need For Speed Most Wanted Black Edition')) / 'CARS'
    if not all((base / car / 'GEOMETRY.BIN').is_file() for base, cars in
               ((carbon, NO_REAR + ('CAMARON',)), (mw, (DONOR, 'GALLARDO'))) for car in cars):
        print('SKIP: private Carbon/MW packs absent')
        return 3
    os.environ.setdefault('NFS_GAME', str(mw.parent))
    failures = 0
    # Independent premise, read from MW's own pack: a player car without a rear brake solid.
    native = checks_names(mw / 'GALLARDO/GEOMETRY.BIN')
    assert 'GALLARDO_KIT00_FRONT_BRAKE_A' in native and 'GALLARDO_KIT00_REAR_BRAKE_A' not in native
    with tempfile.TemporaryDirectory(prefix='carbon-rear-brake-') as scratch:
        for car in NO_REAR:
            authored = solid_names(carbon / car / 'GEOMETRY.BIN')
            assert f'{car}_KIT00_FRONT_BRAKE_A' in authored and f'{car}_KIT00_REAR_BRAKE_A' not in authored
            out = _InputsPath(scratch) / car
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = carbon_namespace.convert(carbon / car, mw / DONOR, out)
            audit = result[2] if isinstance(result, tuple) else result
            # The added pack carries its own identity; the audit speaks in the slot's names.
            delivered = {n.replace('NFSAD_' + car, DONOR, 1) for n in checks_names(out / 'GEOMETRY.BIN')}
            now = [n for n in carbon_batch.stock_solids(DONOR, car, authored) if n not in delivered]
            was = [n for n in previous_stock(DONOR, car, authored) if n not in delivered]
            print(f'{car}: missing stock solids {len(now)} (previous audit {len(was)}: {", ".join(was)})')
            if now or audit.get('missing_stock') or f'{DONOR}_KIT00_FRONT_BRAKE_A' not in delivered:
                failures += 1
                print(f'FAIL {car}: conversion refused or front brake lost')
            if was != [f'{DONOR}_KIT00_REAR_BRAKE_A']:
                failures += 1
                print(f'FAIL {car}: the previous audit no longer shows the defect')
    authored = solid_names(carbon / 'CAMARON/GEOMETRY.BIN')
    required = f'{DONOR}_KIT00_REAR_BRAKE_A' in carbon_batch.stock_solids(DONOR, 'CAMARON', authored)
    print(f'CAMARON: authored rear brake still required: {required}')
    if not required or previous_stock(DONOR, 'CAMARON', authored) != carbon_batch.stock_solids(DONOR, 'CAMARON', authored):
        failures += 1
        print('FAIL CAMARON: an authored rear brake is no longer audited')
    print('FAIL' if failures else 'PASS')
    return 1 if failures else 0


def checks_names(path):
    """Solid names of an MW-layout pack, read from its headers."""
    data = path.read_bytes()
    return {cc.payload(s, 0x134011, 16)[160:].split(b'\0')[0].decode() for _, s in cc.solids(data)}


if __name__ == '__main__':
    raise SystemExit(main())
