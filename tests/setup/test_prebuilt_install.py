#!/usr/bin/env python3
"""Two clean, real Setup installs, back-to-back, without launching either app.

Run with --resources Setup.app/Contents/Resources. By default use the built Setup
or the local release-material fixture. The existing artifact audit uses only a
scratch profile/destination and an empty process snapshot, retaining the normal
running-game matcher. Requires the pinned bundled Python and personal PC input.
Records unpack, libraries, runtime and bundle for both paths in one JSON,
including machine load. Timings alone do not establish an installation gain.
Both app link maps must contain all eight consumed independent archives. The
source-only install must link its rebuilt archives; the prebuilt install must
link byte-identical copies of the verified shipped set and build no FFmpeg tree.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('release_material', Path(__file__).with_name('test_release_artifact.py'))
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)


def check_link(result, prebuilt):
    source, cached, resources = (result[k] for k in ('source', 'cached', 'resources'))
    # Guest symbol names can contain non-UTF-8 bytes. Archive paths are ASCII;
    # compare their exact bytes without decoding unrelated guest symbols.
    link_map = (source / 'libraries-link.map').read_bytes()
    libraries = ['host/audio/build/libhostaudio.a', 'host/audio/build/libmedia.a',
                 'host/shaders/build/libsmtranslate.a',
                 *('third_party/ffmpeg/lib/lib' + n + '.a' for n in
                   ('avformat', 'avcodec', 'swscale', 'swresample', 'avutil'))]
    for name in libraries:
        # The linker records paths relative to runtime/, exactly as build.sh.
        r.require(('../' + name + '(').encode() in link_map, 'app did not link library: ' + name)
        if prebuilt:
            r.require((cached / name).read_bytes() == (resources / 'prebuilt/files' / name).read_bytes(),
                      'app archive differs from verified prebuilt: ' + name)
    built = cached / 'third_party/src/ffmpeg-7.1.1'
    r.require(built.exists() != prebuilt, 'wrong library construction path')
    if not prebuilt:
        r.require((built / 'libavcodec/ac3dsp.o').is_file(), 'source-only install did not rebuild the codec')
    print(('prebuilt' if prebuilt else 'source-only') + ': actual app link map 8/8 archives verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    built = ROOT / 'build/installer/dmg' / r.APP_NAME / 'Contents/Resources'
    fixture = ROOT / '.metal-cache/prebuilt-proof/Contents/Resources'
    if not fixture.is_dir():
        fixture = ROOT / 'build/installer/prebuilt-proof/Contents/Resources'
    parser.add_argument('--resources', type=Path, default=built if (built / 'prebuilt').is_dir() else fixture)
    parser.add_argument('--pc', type=Path, default=Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')))
    args = parser.parse_args()
    if not all((args.resources / n).exists() for n in ('source.tar.gz', 'prebuilt', 'python/bin/python3')) or not (args.pc / 'speed.exe').is_file():
        print('SKIP: needs Setup release materials (including prebuilt/Python) and personal PC v1.3 input')
        return 3
    # Version comes from the same backend source that creates the installed app.
    import re
    expected = re.search(r'^PUBLIC_VERSION = "([^"]+)"', (ROOT / 'tools/setup_kit/backend.py').read_text(), re.M)[1]
    (ROOT / '.metal-cache').mkdir(exist_ok=True)
    metrics_path = ROOT / '.metal-cache/prebuilt-install-metrics.json'
    metrics_path.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='prebuilt-install-', dir=ROOT / '.metal-cache') as folder:
        work = Path(folder)
        before_resources = work / 'source-resources'
        shutil.copytree(args.resources, before_resources, symlinks=True)
        shutil.rmtree(before_resources / 'prebuilt')
        load_start = os.getloadavg()
        before = r.audit(before_resources, args.pc.resolve(), work / 'before', expected)
        print(f'clean source-only install: {before["seconds"]:.3f}s; load {os.getloadavg()}', flush=True)
        check_link(before, False)
        between = os.getloadavg()
        after = r.audit(args.resources, args.pc.resolve(), work / 'after', expected)
        print(f'clean prebuilt install: {after["seconds"]:.3f}s; load {os.getloadavg()}', flush=True)
        check_link(after, True)
        def measurement(result):
            details = result['stage_seconds']
            stages = {name: details[name] for name in ('unpack', 'runtime', 'bundle')}
            stages['libraries'] = sum(details.get(name, 0) for name in ('audio', 'shaders', 'ffmpeg'))
            return dict(total_seconds=result['seconds'], backend_seconds=result['backend_seconds'],
                        stage_seconds=stages, all_stage_seconds=details, command_seconds=result['command_seconds'])
        metrics = dict(schema=2, source_unpack_included=True, unpack_method='Setup tar command and dependency copies',
                       gain_established=False,
                       source_sha256=r.hashlib.sha256((args.resources / 'source.tar.gz').read_bytes()).hexdigest(),
                       prebuilt_manifest_sha256=(args.resources / 'prebuilt-manifest.sha256').read_text().strip(),
                       load_before=load_start, load_between=between, load_after=os.getloadavg(),
                       logical_cpus=os.cpu_count(), paths={'source': measurement(before), 'prebuilt': measurement(after)})
        # The suite deletes successful per-test stdout logs; retain the measured
        # pair independently so release reports can cite the exact seconds.
        metrics_path.write_text(json.dumps(metrics, indent=2) + '\n')
        print('clean install measurements: ' + json.dumps(metrics, sort_keys=True))
        print('prebuilt_install: PASS; clean source rebuild and verified prebuilt both produced signed apps; no app launched')
    return 0


if __name__ == '__main__':
    sys.exit(main())
