"""Added cars whose paint is lit but whose lamps are not: stock lamps must not convert unlit.

MW's car vertex shader multiplies diffuse, specular and environment by saturate(2*COLOR.x), so a
lamp solid exported with zero vertex colour draws black whatever its material and images are.
One number: stock KIT00 lamp vertices left with zero RGB after the converter's lighting policy.
  fixed policy:    CHEVELLE 0/3094, MUSTANGSHLBYO 0/1996, ZONDA 0/5921
  previous policy: CHEVELLE 3094/3094, MUSTANGSHLBYO 1996/1996, ZONDA 5921/5921 (the pinned
                   fragment below; the same check fails on it for all three cars)
The affected set is derived from the source vertices, not from the policy.

Second class, same export: unlit pieces or stray vertices inside a lit draw (the lit vertices are
all 255). One number: stock KIT00/BASE vertices left at zero colour inside such a mixed draw.
  fixed policy:    COROLLA 0/1324, MUSTANGSHLBYO 0/1162, CHALLENGER71 0/126
  previous policy: COROLLA 1324/1324, MUSTANGSHLBYO 1162/1162, CHALLENGER71 126/126 (the same pinned
                   fragment: it treats a mixed draw exactly as the policy that followed it did)
COROLLA's front corner lamps (BODY_DULL_PLASTIC of KIT00_BODY) are the visible case: a black block.

Third class: whole draws and kit pieces exported 00 00 00 00 on a car with lit lamps (SKYLINE and R8
exhausts, CORVETTEZ06 roof LODs B-D, body-kit pieces). One number: non-rim vertices left at
00 00 00 00 after the policy, all 30 player cars.
  fixed policy:    0
  previous policy: 68,592 (the second pinned fragment, the policy before this one)
Authored dark vertices (RGB 0 with alpha > 0) stay as authored: 17,627 before and after. Controls:
CCX, ECLIPSE and JAGUARXK convert to the same bytes under both policies.
Offline; private Carbon packs absent: exit 3.
"""
import sys as _inputs_sys
from pathlib import Path as _InputsPath
_inputs_sys.path.insert(0, str(_InputsPath(__file__).resolve().parents[1] / "tools"))
from setup_kit.input_paths import input_path
import hashlib
import sys
import types

HERE = _InputsPath(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import carbon_car as cc

# Reviewed previous policy; the digest keeps this control from drifting.
OLD_POLICY = r'''def map_vertex_lighting(models):
    """Repair classified rim RGB and conditionally supply neutral body illumination.

    MW car VS multiplies diffuse/specular/environment by saturate(2*COLOR.x).
    CCX exports zero for its whole painted body (and most exterior draws), so
    copying that field blacks out even correctly bound paint. Detect this from
    stock BODY_A paint, not a car-name list. The paint gate protects authored
    non-rim shading; classified zero-RGB rim vertices need repair independently.
    This is a compatibility default, not a reconstruction of Carbon's lighting.
    """
    stock = next((m for n, m in models.items() if n.endswith('_KIT00_BODY_A')), None)
    paint, base = [], 0
    for r, name in zip(stock['records'], stock['names']) if stock is not None else ():
        count = u32(r, 60)
        if bytes(name).split(b'\0')[0] in PAINT_NAMES:
            paint.append(stock['vertices'][base:base + count, 24:28])
        base += count
    neutral_body = bool(paint) and not any(v.any() for v in paint)
    changed = 0
    for m in models.values():
        base = 0
        for r, name in zip(m['records'], m['names']):
            count = u32(r, 60)
            colours = m['vertices'][base:base + count, 24:28]
            if bytes(name).startswith(b'RIM_'):
                # Lower LODs merge black lip vertices into lit spoke draws.
                # Alpha is opacity, not evidence of vertex illumination.
                dark = ~colours[:, :3].any(axis=1)
                colours[dark, :3] = 255
                changed += int(dark.sum())
            elif neutral_body and not colours.any():
                colours[:] = 255
                changed += count
            base += count
    return changed

'''
assert hashlib.sha256(OLD_POLICY.encode()).hexdigest() == 'ab82422122a74ee209fd3be149f9d425330560e3b41e33b429efa64476a5292f', 'pinned baseline changed'

# Reviewed policy that followed it (lamp and mixed-draw gates); the digest keeps it fixed.
GATED_POLICY = r'''def map_vertex_lighting(models):
    """Repair classified rim RGB and conditionally supply neutral body illumination.

    MW car VS multiplies diffuse/specular/environment by saturate(2*COLOR.x).
    CCX exports zero for its whole painted body (and most exterior draws), so
    copying that field blacks out even correctly bound paint. Detect this from
    stock BODY_A paint, not a car-name list. The paint gate protects authored
    non-rim shading; classified zero-RGB rim vertices need repair independently.
    Other cars light their paint but export zero for every lamp, mirror and
    window; their stock lamp draws detect that class the same way.
    This is a compatibility default, not a reconstruction of Carbon's lighting.
    """
    stock = next((m for n, m in models.items() if n.endswith('_KIT00_BODY_A')), None)
    paint, base = [], 0
    for r, name in zip(stock['records'], stock['names']) if stock is not None else ():
        count = u32(r, 60)
        if bytes(name).split(b'\0')[0] in PAINT_NAMES:
            paint.append(stock['vertices'][base:base + count, 24:28])
        base += count
    neutral_body = bool(paint) and not any(v.any() for v in paint)
    # A second export class lights its paint but leaves every detail part
    # (lamps, mirrors, glass) at zero. A lamp is never authored unlit, so
    # uniformly zero stock lamp draws identify it; exhaust-only zeros do not.
    lamps = [m['vertices'][:, 24:28] for n, m in models.items()
             if '_KIT00_' in n and ('HEADLIGHT' in n or 'BRAKELIGHT' in n)]
    neutral_body = neutral_body or (bool(lamps) and not any(v.any() for v in lamps))
    # The same export also leaves unlit pieces or stray vertices inside lit
    # draws (the lit vertices are all 255, never a gradient). Stock solids
    # identify that class as well; a car whose stock solids have no such draw
    # keeps its other draws as authored.
    merged = False
    for n, m in models.items():
        base = 0
        for r, name in zip(m['records'], m['names']):
            count = u32(r, 60)
            dark = ~m['vertices'][base:base + count, 24:28].any(axis=1)
            base += count
            if ('_KIT00_' in n or '_BASE_' in n) and not bytes(name).startswith(b'RIM_'):
                merged = merged or bool(dark.any() and not dark.all())
    changed = 0
    for m in models.values():
        base = 0
        for r, name in zip(m['records'], m['names']):
            count = u32(r, 60)
            colours = m['vertices'][base:base + count, 24:28]
            if bytes(name).startswith(b'RIM_'):
                # Lower LODs merge black lip vertices into lit spoke draws.
                # Alpha is opacity, not evidence of vertex illumination.
                dark = ~colours[:, :3].any(axis=1)
                colours[dark, :3] = 255
                changed += int(dark.sum())
            else:
                dark = ~colours.any(axis=1)
                if neutral_body and dark.all() or merged and dark.any() and not dark.all():
                    colours[dark] = 255
                    changed += int(dark.sum())
            base += count
    return changed

'''
assert hashlib.sha256(GATED_POLICY.encode()).hexdigest() == '4bb309b28e0ba58f75165dac028ab96086b272ec3ccbda06ab66f2b9eba52a64', 'pinned baseline changed'

DARK_LAMPS = ('CHEVELLE', 'MUSTANGSHLBYO', 'ZONDA')
MERGED = ('COROLLA', 'MUSTANGSHLBYO', 'CHALLENGER71')
UNCHANGED = ('CCX', 'ECLIPSE', 'JAGUARXK')
PLAYER_CARS = ('240SX', '300C', '997GT3RS', '997TT', 'BRERA', 'CAMARON', 'CCX', 'CHALLENGER71', 'CHALLENGERN',
               'CHARGER06', 'CHARGER69', 'CHEVELLE', 'COROLLA', 'CORVETTEZ06', 'CUDA', 'ECLIPSE', 'EUROPA',
               'G35', 'JAGUARXK', 'LANCEREVO9', 'MAZDASPEED3', 'MR2', 'MURCIELAGO640', 'MUSTANGSHLBYN',
               'MUSTANGSHLBYO', 'R32', 'R8', 'ROADRUNNER', 'SKYLINE', 'ZONDA')


def lamp(name):
    return '_KIT00_' in name and ('HEADLIGHT' in name or 'BRAKELIGHT' in name)


def unlit(models):
    """(lamp vertices with zero RGB, lamp vertices), read from the vertex bytes."""
    dark = total = 0
    for name, m in models.items():
        if lamp(name):
            dark += int((~m['vertices'][:, 24:27].any(axis=1)).sum())
            total += len(m['vertices'])
    return dark, total


def mixed_draws(models):
    """[(solid, first vertex, count)] of stock non-rim draws that hold both zero and lit vertices."""
    found = []
    for name, m in models.items():
        base = 0
        for r, draw in zip(m['records'], m['names']):
            count = cc.u32(r, 60)
            dark = ~m['vertices'][base:base + count, 24:28].any(axis=1)
            if (('_KIT00_' in name or '_BASE_' in name) and not bytes(draw).startswith(b'RIM_')
                    and dark.any() and not dark.all()):
                found.append((name, base, count))
            base += count
    return found


def dark_in(models, draws):
    return sum(int((~models[n]['vertices'][b:b + c, 24:28].any(axis=1)).sum()) for n, b, c in draws)


def colour_classes(models):
    """(non-rim vertices at 00 00 00 00, non-rim vertices with RGB 0 and alpha > 0)."""
    gap = authored = 0
    for m in models.values():
        base = 0
        for r, draw in zip(m['records'], m['names']):
            count = cc.u32(r, 60)
            c = m['vertices'][base:base + count, 24:28]
            base += count
            if bytes(draw).startswith(b'RIM_'): continue
            rgb = c[:, :3].any(axis=1)
            gap += int((~rgb & (c[:, 3] == 0)).sum())
            authored += int((~rgb & (c[:, 3] > 0)).sum())
    return gap, authored


def load(carbon, car):
    data = (carbon / car / 'GEOMETRY.BIN').read_bytes()
    return {m['name']: m for _, s in cc.solids(data) for m in [cc.model(s)]}


def main():
    carbon = input_path('NFSC') / 'CARS'
    if not all((carbon / car / 'GEOMETRY.BIN').is_file() for car in PLAYER_CARS):
        print('SKIP: private Carbon packs absent')
        return 3
    before = types.ModuleType('carbon_car_previous_lighting')
    before.__dict__.update(cc.__dict__)
    exec(compile(OLD_POLICY, cc.__file__, 'exec'), before.__dict__)
    gated = types.ModuleType('carbon_car_gated_lighting')
    gated.__dict__.update(cc.__dict__)
    exec(compile(GATED_POLICY, cc.__file__, 'exec'), gated.__dict__)
    failures = 0
    for car in DARK_LAMPS:
        raw = load(carbon, car)
        source = unlit(raw)
        # Independent premise: every stock lamp vertex of this source is unlit.
        assert source[0] == source[1] > 0, f'{car}: source lamps are not uniformly unlit {source}'
        new, old = load(carbon, car), load(carbon, car)
        cc.map_vertex_lighting(new)
        before.map_vertex_lighting(old)
        now, was = unlit(new), unlit(old)
        print(f'{car}: unlit stock lamp vertices {now[0]}/{now[1]} (previous policy {was[0]}/{was[1]})')
        if now[0] != 0:
            failures += 1
            print(f'FAIL {car}: stock lamps convert unlit')
        if was[0] != was[1]:
            failures += 1
            print(f'FAIL {car}: the previous policy no longer shows the defect')
        for name, m in new.items():
            # Only colour bytes may differ from the source.
            v = raw[name]['vertices']
            if not ((v[:, :24] == m['vertices'][:, :24]).all() and (v[:, 28:] == m['vertices'][:, 28:]).all()):
                failures += 1
                print(f'FAIL {car}: {name} positions/normals/UVs changed')
    for car in MERGED:
        raw = load(carbon, car)
        draws = mixed_draws(raw)
        source = dark_in(raw, draws)
        # Independent premise: the lit part of every such draw is plain 255, not authored shading.
        assert source > 0 and all(set(raw[n]['vertices'][b:b + c, 24:28].ravel().tolist()) <= {0, 255}
                                  for n, b, c in draws), f'{car}: no merged unlit pieces in the source'
        new, old = load(carbon, car), load(carbon, car)
        cc.map_vertex_lighting(new)
        before.map_vertex_lighting(old)
        now, was = dark_in(new, draws), dark_in(old, draws)
        print(f'{car}: unlit vertices in stock mixed draws {now}/{source} (previous policy {was}/{source})')
        if now != 0:
            failures += 1
            print(f'FAIL {car}: merged unlit pieces convert black')
        if was != source:
            failures += 1
            print(f'FAIL {car}: the previous policy no longer shows the defect')
    totals = dict(source=[0, 0], fixed=[0, 0], gated=[0, 0])
    for car in PLAYER_CARS:
        raw, new, old = load(carbon, car), load(carbon, car), load(carbon, car)
        cc.map_vertex_lighting(new)
        gated.map_vertex_lighting(old)
        for key, models in (('source', raw), ('fixed', new), ('gated', old)):
            totals[key] = [a + b for a, b in zip(totals[key], colour_classes(models))]
        if car in UNCHANGED:
            same = all((new[n]['vertices'] == old[n]['vertices']).all() for n in new)
            print(f'{car}: identical to the previous policy: {same}')
            if not same:
                failures += 1
                print(f'FAIL {car}: a car without export gaps changed')
    print(f"unexported vertices left, {len(PLAYER_CARS)} cars: {totals['fixed'][0]} "
          f"(previous policy {totals['gated'][0]}; source {totals['source'][0]})")
    print(f"authored dark vertices: source {totals['source'][1]}, fixed {totals['fixed'][1]}")
    if totals['fixed'][0] != 0:
        failures += 1
        print('FAIL: unexported whole draws or kit pieces convert black')
    if totals['gated'][0] == 0:
        failures += 1
        print('FAIL: the previous policy no longer shows the defect')
    if totals['fixed'][1] != totals['source'][1]:
        failures += 1
        print('FAIL: authored dark vertices changed')
    print('FAIL' if failures else 'PASS')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
