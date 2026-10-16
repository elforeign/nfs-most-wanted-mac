"""tools/warm_seed/warmseed.py (2.0.4): the shader warm-up Setup makes on the player's Mac. No game run here: the
drive list's settings exist, the menu script's grammar, the merge (first-use order, each source kept only with its
file, pipelines deduplicated), the stamp, and the plumbing (setup flag, package option, Launch export only when the
seed folder is there)."""
import importlib.util, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('warmseed', ROOT / 'tools/warm_seed/warmseed.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
K = lambda c: c * 64


def test_drive_settings_are_real_settings():
    keys = {l.split(' =')[0] for l in (ROOT / 'mods/core/nfsmw/mod.toml').read_text().splitlines() if ' = {' in l}
    for name, track, settings, extra in m.DRIVES:
        assert set(settings) <= keys, name
        assert track is None or 0 <= track < 30
        assert not (settings.get('antialiasing') == 2 and extra.get('RECOMP_TEST_HDR_FLOAT')), 'HDR never with 4x'
    assert len({n for n, *_ in m.DRIVES}) == len(m.DRIVES)


def test_script_grammar():
    s = m.script(3, 1000)
    for line in s.splitlines():
        assert re.fullmatch(r'wait \d+|key (RETURN|RIGHT|UP) (down|up)|quit'
                            r'|keyuntil RETURN fe:[A-Za-z_]+\.fng>0 every \d+ within \d+'
                            r'|await fe:[A-Za-z_]+\.fng>0 for \d+ within \d+', line), line
    assert s.rstrip().endswith('quit')
    # run 33 (D-183, census frames viewed): each screen change waits for its screen; the keys in order
    keys = [l.split()[1] + (':' + l.split()[2] if l.startswith('keyuntil') else '') for l in s.splitlines()
            if l.endswith(' down') or l.startswith('keyuntil')]
    assert keys[:8] == ['RETURN:fe:MC_Main.fng>0', 'RETURN:fe:MainMenu.fng>0', 'RIGHT', 'RIGHT',
                        'RETURN:fe:MainMenu_Sub.fng>0', 'RIGHT', 'RETURN', 'RETURN:fe:Track_Select.fng>0']
    assert keys[8:11] == ['RIGHT'] * 3
    assert keys[11:15] == ['RETURN:fe:Track_Options.fng>0', 'RETURN:fe:Car_Select.fng>0', 'RETURN:fe:Dialog.fng>0',
                           'RETURN:fe:Loading.fng>0'] and keys[15:] == ['UP']
    # every keyuntil presses again only after its screen change had time to show (census: under 1.5 s), so a second
    # press cannot land on the next screen; none waits forever
    for l in s.splitlines():
        if l.startswith('keyuntil'):
            w = l.split()
            assert int(w[4]) >= 3000 and int(w[4]) < int(w[6]) <= 90000, l
    # 3.0.4 (run 39): before every timed press the screen it lands on has held the input for 120 presented frames (a
    # cold shader cache stalls the main menu for seconds); never on a screen the game leaves by itself (Loading)
    lines = s.splitlines()
    for i, l in enumerate(lines):
        if l == 'key RIGHT down':   # the menu's timed presses (UP is the accelerator, in the race)
            before = [x for x in lines[:i] if x.startswith(('keyuntil', 'key '))][-1:]
            if before and before[0].startswith('keyuntil'):
                screen = before[0].split()[2]
                assert f'await {screen} for 6000 within 60000' in lines[:i], (l, screen)
    awaited = [l.split()[1] for l in lines if l.startswith('await')]
    assert awaited == ['fe:MainMenu.fng>0', 'fe:MainMenu_Sub.fng>0', 'fe:Track_Select.fng>0']


def test_stuff_pack_front_end():
    """2.0.4 (run 28): the race drives' menu path is the PC front end's, so they run with x360_frontend 0 (with the
    Stuff Pack front end on, the same keys stayed in its main menu: no race shaders were recorded); one menus-only
    drive records the Xbox 360 front end; a drive's log must show the world for it to count as a race."""
    assert sum(1 for _, t, st, _ in m.DRIVES if t is None and st.get('x360_frontend') == 1) == 1
    assert all(st.get('x360_frontend', 0) == 0 for _, t, st, _ in m.DRIVES if t is not None)
    menus = m.script(None, 1000)
    assert 'RIGHT' not in menus and menus.rstrip().endswith('quit')
    for line in menus.splitlines():
        assert re.fullmatch(r'wait \d+|key RETURN (down|up)|quit', line), line
    assert m.WORLD.search('[mod core.nfsmw] core.nfsmw: Xbox scenery lights: 1 draw items so far (near 0/0), 0 v')
    assert m.WORLD.search('[mod core.nfsmw] core.nfsmw: Weather: clear (cycle 0.12)')
    assert not m.WORLD.search('[mod core.nfsmw] core.nfsmw: Xbox scenery lights: 4185 verified instances, 2 USA contexts')
    assert not m.WORLD.search('[mod core.nfsmw] core.nfsmw: Xbox 360 front end on (Stuff Pack: FrontB, GlobalB, garages)')
    assert not m.WORLD.search('[mod core.nfsmw] core.nfsmw: Xbox light pools: 0 terrain-piece items, 0 lit items')   # printed in menus too
    assert m.WORLD.search('[mod core.nfsmw] core.nfsmw: Xbox light pools: 412 terrain-piece items, 3 lit items')
    assert m.WORLD.search('[mod core.nfsmw] core.nfsmw: Time of day: day cycle from 0.382 (a day lasts 60 min)')
    assert m.WORLD.search('[mod core.nfsmw] core.nfsmw: Time of day: 0.393')


def test_merge(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir(); b.mkdir()
    (a / 'order.txt').write_text(f'{K("1")}\n{K("2")}\nnot-a-key\n')
    (a / f'{K("1")}.msl').write_text('one')
    (a / f'{K("2")}.msl').write_text('two')
    (a / 'pipelines.txt').write_text('p1 x\np1 y\n')
    (b / 'order.txt').write_text(f'{K("2")}\n{K("3")}\n{K("4")}\n')
    (b / f'{K("2")}.msl').write_text('two again')
    (b / f'{K("3")}.msl').write_text('three')          # K4 has no file: left out
    (b / 'pipelines.txt').write_text('p1 y\np1 z\n')
    libs, pipes = m.merge([a, b], tmp_path / 'seed')
    seed = tmp_path / 'seed'
    assert (libs, pipes) == (3, 3)
    assert (seed / 'order.txt').read_text().split() == [K('1'), K('2'), K('3')]
    assert (seed / f'{K("2")}.msl').read_text() == 'two'
    assert (seed / 'pipelines.txt').read_text().split('\n')[:3] == ['p1 x', 'p1 y', 'p1 z']
    stamp = (seed / 'stamp.txt').read_text().splitlines()
    assert stamp[0] == 'fingerprint ' + m.fingerprint() and stamp[3] == 'libraries 3 pipelines 3'
    assert not (tmp_path / 'seed.staging').exists()


def test_plumbing():
    setup = (ROOT / 'tools/setup_kit/setup.py').read_text()
    assert '--no-shader-seed' in setup and 'tools/warm_seed/warmseed.py' in setup and '"--warm-seed", seed' in setup
    assert '"--shader-seed"' in setup and 'WARM_DEFAULT_ON = False' in setup   # 3.0, owner D-185: opt-in
    assert '--warm-seed' in (ROOT / 'tools/setup_kit/package.py').read_text()
    launch = (ROOT / 'tools/setup_kit/defaults/Launch').read_text()
    assert re.search(r'if \[ -f "\$app_resources/ShaderWarmSeed/order.txt" \]; then\n\s+export RECOMP_D3D9_SHADER_WARM_SEED=', launch)
    assert 'unset RECOMP_D3D9_SHADER_WARM_SEED' in launch
    swift = (ROOT / 'tools/installer/SetupApp.swift').read_text()
    assert '"--no-shader-seed"' in swift and '"--shader-seed"' in swift and 'shader warm-up: drive ' in swift
    assert '@Published var shaderSeed = false' in swift


def test_setup_warm_flags_parse():
    """3.0 (owner D-185): --shader-seed / --no-shader-seed with WARM_DEFAULT_ON (off) as the default."""
    import argparse, pytest
    src = (ROOT / 'tools/setup_kit/setup.py').read_text()
    block = src[src.index('    warm = ap.add_mutually_exclusive_group()'):src.index('    hd = ap.add_mutually_exclusive_group()')]
    for default in (False, True):
        ap = argparse.ArgumentParser()
        exec(re.sub(r'^    ', '', block, flags=re.M), {'ap': ap, 'WARM_DEFAULT_ON': default})
        assert ap.parse_args([]).shader_seed is default
        assert ap.parse_args(['--shader-seed']).shader_seed is True
        assert ap.parse_args(['--no-shader-seed']).shader_seed is False
        with pytest.raises(SystemExit):
            ap.parse_args(['--shader-seed', '--no-shader-seed'])


def fake_tree(root):
    """The files the fingerprint reads, in a scratch source tree."""
    for f in ('kit/dx/d3d9_shadergen.cpp', 'kit/dx/d3d9_msl.h', 'kit/dx/d3d9_shader.cpp', 'kit/dx/d3d9_shader.h',
              'kit/host/gpu/metal/surface.h', 'kit/host/gpu/metal/pipeline_warm_spec.h', 'build/generated/clean-chain.fx', 'build/generated/clean-chain.txt',
              'build/generated/clean-pools.bin'):
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f)


def made_before(root, monkeypatch, capsys):
    """A finished warm-up as an earlier Setup left it: every drive's mark and the merged seed with its stamp."""
    def fake_drive(exe, core, run, track, settings, extra, race_s):
        (run / 'ShaderWarm').mkdir(parents=True, exist_ok=True)   # the real drive empties the run folder first
        (run / 'ShaderWarm/order.txt').write_text(K(run.name[0]) + '\n')
        (run / 'ShaderWarm' / f'{K(run.name[0])}.msl').write_text(run.name)
        return 1, track is not None
    monkeypatch.setattr(m, 'drive', fake_drive)
    monkeypatch.setattr(m, 'smoke', lambda: 'pop_smoke')
    monkeypatch.setattr(m, 'core_dir', lambda cache: cache / 'core')
    monkeypatch.setattr('sys.argv', ['warmseed.py'])
    m.main()
    assert 'seed ready' in capsys.readouterr().out


def test_fingerprint_parts(tmp_path, monkeypatch):
    """Run 38 (D-202): the generator, the pipeline line format, the effects Setup made and the key namespace; not the
    renderer, not the light-pool data."""
    fake_tree(tmp_path)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    names = [f for f, _ in m.parts()]
    assert 'kit/host/gpu/metal/pipeline_warm_spec.h' in names and 'build/generated/clean-chain.fx' in names
    assert 'build/generated/clean-chain.txt' in names and 'build/generated/clean-pools.bin' not in names
    assert names[-1] in ('format msl-v1/p1', 'format msl-v1-safe/p1')
    fp = m.fingerprint()
    (tmp_path / 'build/generated/clean-chain.fx').write_text('changed')
    assert m.fingerprint() != fp
    fp = m.fingerprint()
    (tmp_path / 'build/generated/clean-pools.bin').write_text('changed')
    assert m.fingerprint() == fp


def test_fingerprint_covers_the_generators_includes(tmp_path, monkeypatch):
    """3.0.4 (run 39, challenge review): every header the real shader generator includes, and the headers those
    include, is in the fingerprint, except the GLSL and WGSL writers (no Metal shader comes from them); surface.h
    (surf_ps_patch edits the pixel shaders) was missing. Control: a change to it now moves the fingerprint."""
    other_languages = {'kit/dx/d3d9_glsl.h', 'kit/dx/d3d9_wgsl.h'}
    names = {f for f, _ in m.parts()}
    todo, seen = ['kit/dx/d3d9_shadergen.cpp'], set()
    while todo:
        f = todo.pop()
        if f in seen or f in other_languages:
            continue
        seen.add(f)
        for inc in re.findall(r'^#include "([^"]+)"', (ROOT / f).read_text(), flags=re.M):
            p = (ROOT / f).parent.joinpath(inc).resolve().relative_to(ROOT).as_posix()
            if (ROOT / p).is_file():
                todo.append(p)
    assert 'kit/host/gpu/metal/surface.h' in seen
    assert seen - {'kit/dx/d3d9_shadergen.cpp'} <= names, sorted(seen - names)
    fake_tree(tmp_path)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    fp = m.fingerprint()
    (tmp_path / 'kit/host/gpu/metal/surface.h').write_text('changed')
    assert m.fingerprint() != fp


def test_kept_seed_is_reused_without_driving(tmp_path, monkeypatch, capsys):
    """An update that draws its shaders the same way: the reuse line, no runner build, no drive, the seed untouched."""
    fake_tree(tmp_path)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    made_before(tmp_path, monkeypatch, capsys)
    seed = tmp_path / 'build/warm/seed'
    before = (seed / 'stamp.txt').read_text()
    def no(*_):
        raise AssertionError('drove or built the runner')
    monkeypatch.setattr(m, 'drive', no)
    monkeypatch.setattr(m, 'smoke', no)
    m.main()
    out = capsys.readouterr().out
    assert f'shader warm-up: reused the prepared shaders (fingerprint {m.fingerprint()[:12]}, ' in out
    assert 'libraries, ' in out and 'drive ' not in out and (seed / 'stamp.txt').read_text() == before


def test_changed_shaders_drive_again(tmp_path, monkeypatch, capsys):
    """Control: one generator file changed: the changed line, every drive again, a new stamp; a failed re-drive leaves
    no stale seed behind for the packager."""
    fake_tree(tmp_path)
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    made_before(tmp_path, monkeypatch, capsys)
    (tmp_path / 'kit/dx/d3d9_msl.h').write_text('changed')
    m.main()
    out = capsys.readouterr().out
    assert 'the shaders changed in this version: preparing them again' in out
    assert out.count('shader warm-up: drive ') == len(m.DRIVES) and 'seed ready' in out
    seed = tmp_path / 'build/warm/seed'
    assert m.stamp_of(seed)['fingerprint'] == m.fingerprint()
    (tmp_path / 'kit/dx/d3d9_shader.h').write_text('changed again')
    def fails(*_):
        raise SystemExit('shader warm-up: the first drive did not reach a race')
    monkeypatch.setattr(m, 'drive', fails)
    import pytest
    with pytest.raises(SystemExit):
        m.main()
    assert not seed.exists()


def test_stamp_of(tmp_path):
    assert m.stamp_of(tmp_path) == {}
    (tmp_path / 'stamp.txt').write_text('fingerprint ' + K('a') + '\ngenerated 2026-10-15 10:00:00 from 8 drives on '
                                        'this Mac (setup)\nsource setup\nlibraries 378 pipelines 779\nabc  kit/dx/x\n')
    assert m.stamp_of(tmp_path) == {'fingerprint': K('a'), 'generated': '2026-10-15 10:00:00', 'libraries': '378',
                                    'pipelines': '779'}


def test_package_refuses_a_mismatched_seed(tmp_path, monkeypatch):
    """3.0.4 (run 38): Setup's packager leaves out a kept seed that this version draws differently."""
    spec = importlib.util.spec_from_file_location('setup_kit_package', ROOT / 'tools/setup_kit/package.py')
    pkg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pkg)
    (tmp_path / 'stamp.txt').write_text('fingerprint ' + m.fingerprint() + '\n')
    assert pkg.seed_matches(tmp_path)
    (tmp_path / 'stamp.txt').write_text('fingerprint ' + K('0') + '\n')
    assert not pkg.seed_matches(tmp_path)
    src = (ROOT / 'tools/setup_kit/package.py').read_text()
    assert 'if seed_matches(a.warm_seed):' in src and 'the kept seed does not match this version; left out' in src
    swift = (ROOT / 'tools/installer/SetupApp.swift').read_text()
    assert 'shader warm-up: reused the prepared shaders' in swift
