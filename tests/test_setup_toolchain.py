"""tools/setup_kit/setup.py check_toolchain (3.0.4, D-203a): Setup logs whether the player's compiler is the tested
one, stops below the kit's minimum, and builds with /usr/bin/clang; the release version reaches the build. Needs no
game files and no compiler (the probe is replaced)."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('setup_kit_setup_tc', ROOT / 'tools/setup_kit/setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def run_check(monkeypatch, clang_line, sdk):
    said = []
    monkeypatch.setattr(setup, 'say', said.append)
    tc = setup.toolchain_module()
    monkeypatch.setattr(setup, 'toolchain_module', lambda: tc)
    monkeypatch.setattr(tc, 'probe', lambda: (clang_line, sdk))
    monkeypatch.setattr(setup, 'ENV', {})
    return said


def test_the_tested_compiler_is_logged_and_used(monkeypatch):
    tested = setup.toolchain_module().read_spec()
    major_minor = tested['tested_clang'].split()[2]
    build = tested['tested_clang'].split('(')[1].rstrip(')')
    said = run_check(monkeypatch, f'Apple clang version {major_minor} ({build})', tested['tested_sdk'])
    setup.check_toolchain()
    assert said[-1].endswith(': the tested compiler')
    assert setup.ENV['CC'] == '/usr/bin/clang' and setup.ENV['CXX'] == '/usr/bin/clang++'


def test_an_old_compiler_stops_setup_with_the_update_hint(monkeypatch):
    said = run_check(monkeypatch, 'Apple clang version 14.0.3 (clang-1403.0.22.14.1)', '13.3')
    with pytest.raises(SystemExit):
        setup.check_toolchain()
    assert any('SETUP STOPPED' in s and 'xcode-select --install' in s for s in said)
    assert 'CC' not in setup.ENV


def test_the_release_version_is_the_changelog_top():
    top = next(l for l in (ROOT / 'CHANGELOG.md').read_text().splitlines() if l.startswith('## '))
    assert setup.release_version() == top.split()[1]
    assert setup.release_version()[0].isdigit()
