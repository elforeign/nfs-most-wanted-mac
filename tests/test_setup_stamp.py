"""tools/setup_kit/setup.py fresh_build_tree: a new source version clears the earlier compiled tree (the Setup app keeps
build/ across updates, and unpacked sources carry their commit time, so Ninja could link old objects into a new app);
the same version keeps it. Needs no game files."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_kit_setup', ROOT / 'tools/setup_kit/setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
setup.say = lambda msg: None   # it appends to the real build/setup.log


def tree(root, version, api=b'struct PopModApi { int a; };'):
    (root / 'kit/mods').mkdir(parents=True, exist_ok=True)
    (root / 'kit/mods/pop_mod_api.h').write_bytes(api)
    (root / 'CHANGELOG.md').write_text(f'# Changelog\n\n## {version} - test\n\n## 1.0.0\n')
    for d in ('build/cmake/macos', 'build/SpeedRecomp.app', 'build/recomp/mods/core'):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / 'build/recomp/gen').mkdir(parents=True, exist_ok=True)


def built(root):
    stamp, ident = setup.fresh_build_tree(root)
    for d in ('build/cmake/macos', 'build/SpeedRecomp.app', 'build/recomp/mods/core'):
        (root / d).mkdir(parents=True, exist_ok=True)   # the build makes them again
    stamp.write_text(ident)
    return ident


def test_a_tree_without_a_stamp_is_rebuilt(tmp_path):
    tree(tmp_path, '2.0.0')
    setup.fresh_build_tree(tmp_path)
    assert not (tmp_path / 'build/cmake').exists() and not (tmp_path / 'build/SpeedRecomp.app').exists()
    assert (tmp_path / 'build/recomp/gen').exists()   # only the compiled tree goes


def test_the_same_version_keeps_its_build(tmp_path):
    tree(tmp_path, '2.0.1')
    built(tmp_path)
    setup.fresh_build_tree(tmp_path)
    assert (tmp_path / 'build/cmake/macos').exists() and (tmp_path / 'build/recomp/mods/core').exists()


def test_a_new_version_or_mod_api_is_rebuilt(tmp_path):
    tree(tmp_path, '2.0.0')
    built(tmp_path)
    tree(tmp_path, '2.0.1')
    setup.fresh_build_tree(tmp_path)
    assert not (tmp_path / 'build/cmake').exists()
    tree(tmp_path, '2.0.1')
    built(tmp_path)
    tree(tmp_path, '2.0.1', api=b'struct PopModApi { int a; int b; };')
    setup.fresh_build_tree(tmp_path)
    assert not (tmp_path / 'build/cmake').exists()


def test_a_new_version_keeps_the_prepared_shaders(tmp_path):
    """3.0.4 (run 38, D-202): the warm-up's drives and seed stay for warmseed.py to check; its runner goes."""
    tree(tmp_path, '3.0.3')
    built(tmp_path)
    for d in ('build/warm/seed', 'build/warm/drives/default'):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / 'build/warm/seed/order.txt').write_text('x\n')
    (tmp_path / 'build/recomp/pop_smoke').write_text('runner')
    tree(tmp_path, '3.0.4')
    setup.fresh_build_tree(tmp_path)
    assert not (tmp_path / 'build/cmake').exists() and not (tmp_path / 'build/recomp/pop_smoke').exists()
    assert (tmp_path / 'build/warm/seed/order.txt').is_file() and (tmp_path / 'build/warm/drives/default').is_dir()
