#!/usr/bin/env python3
"""Rebuild the shipped independent libraries from their accompanying source archive.

The fresh rebuild uses the pinned tarball, the distributed scripts and no game
objects. A link map must name the rebuilt host audio, media, translator and all
five FFmpeg archives used by the app. Removing libavcodec must fail the link.
Good: rebuilt link map 8/8 archives. Broken: missing codec link rejected 1/1.
The executable is a synthetic link probe; no game is launched.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('prebuilt', ROOT / 'tools/setup_kit/prebuilt.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


def copy_sources(destination):
    destination.mkdir()
    for name in (*p.recipe(ROOT), p.FFMPEG):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)


def link(root, expect=0):
    source = root / 'probe.cpp'
    source.write_text('''#include "host/audio/dsound_host.h"
#include "host/shaders/sm_translate.h"
#include "media/movie_api.h"
extern "C" {
#include <libavutil/avutil.h>
}
int main() {
    DsConfig config; ds_config_default(&config);
    SmOptions options; sm_default_options(&options);
    media_movie_close(nullptr);
    return av_version_info()[0] == 0;
}
''')
    ff = root / 'third_party/ffmpeg/lib'
    libraries = [root / 'host/audio/build/libhostaudio.a', root / 'host/audio/build/libmedia.a',
                 root / 'host/shaders/build/libsmtranslate.a',
                 *(ff / ('lib' + name + '.a') for name in ('avformat', 'avcodec', 'swscale', 'swresample', 'avutil'))]
    command = ['clang++', '-arch', 'arm64', '-mmacosx-version-min=14.5', '-I' + str(root),
               '-I' + str(root / 'third_party/ffmpeg/include'), str(source), *map(str, libraries),
               '-lz', '-framework', 'AudioToolbox', '-framework', 'CoreAudio', '-framework', 'CoreFoundation',
               '-Wl,-map,' + str(root / 'link.map'), '-o', str(root / 'probe')]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    assert (result.returncode == 0) == (expect == 0), result.stderr[-3000:]
    if expect == 0:
        text = (root / 'link.map').read_text()
        for archive in libraries:
            assert str(archive) + '(' in text, 'rebuilt archive not linked: ' + archive.name
        # Executing a probe is unnecessary: this check concerns static linking.
    return result.returncode


def main():
    if sys.platform != 'darwin' or not (ROOT / p.FFMPEG).is_file():
        print('SKIP: needs macOS and the pinned accompanying FFmpeg source archive')
        return 3
    with tempfile.TemporaryDirectory(prefix='prebuilt-rebuild-') as folder:
        work = Path(folder)
        rebuilt = work / 'rebuilt'
        copy_sources(rebuilt)
        started = time.monotonic()
        p.build(rebuilt, rebuilt / 'prebuilt')
        release_seconds = time.monotonic() - started
        assert p.digest(rebuilt / p.FFMPEG) == p.SHA
        assert not (rebuilt / 'recomp').exists(), 'game-derived inputs must be unnecessary'
        link(rebuilt)
        missing = rebuilt / 'third_party/ffmpeg/lib/libavcodec.a'
        missing.rename(missing.with_suffix('.hidden'))
        assert link(rebuilt, expect=1) != 0
        missing.with_suffix('.hidden').rename(missing)
        installed = work / 'installed'
        installed.mkdir()
        started = time.monotonic()
        assert p.install(rebuilt, installed, print, expected_sha256=p.digest(rebuilt / 'prebuilt/manifest.json'))
        prebuilt_seconds = time.monotonic() - started
        # Every archive is copied exactly from the audited set.
        for name in p.load(ROOT / 'media/check_libraries.py').manifest():
            assert p.digest(installed / name) == p.digest(rebuilt / 'prebuilt/files' / name)
        print(f'prebuilt_rebuild_test: PASS; fresh pinned rebuild; rebuilt link map 8/8 archives; '
              f'missing codec link rejected 1/1; source release {release_seconds:.3f}s, '
              f'verified copy {prebuilt_seconds:.3f}s; load {os.getloadavg()}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
