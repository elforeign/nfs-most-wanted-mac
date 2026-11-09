#!/usr/bin/env python3
"""Probe six 2-second in-memory TS segments from 12 seconds of H.264/AAC.
Ingest original/fixed: six-second GOP emits 1/0 segments (540000/0 ticks);
first PIDs [17,0,4096]/[0,4096,17] (SDT/PAT/PMT vs PAT/PMT/SDT).
Measured: 720 video / 565 input AAC packets, six durations 2.000000..2.009333 s,
120 video packets per segment, SPS/PPS before every first IDR, zero DTS faults.
Non-IDR cut emits a 2.016667-s GOP then fails; reset DTS gives 90360 <= 268440.
Removed mpegts fails the build contract and the unreconfigured shipping archive
also failed actual muxing (unknown output format). Off: 0 allocations/0 sinks;
default-On fault: 15 allocations/2 sinks. Held outputs stop at 2, pending peak
157997..158102 bytes; byte caps/callback rejection, 200-ms skew and duplicate DTS tested.
The driver compiles actual cut/DTS/off-path faults and requires their rejection.
Results are printed numerically and retained under .metal-cache/live-seg.
Stop at 4.016667 s: original emits 2 segments, finish=0/failed=1; fixed emits
3 segments with all 241 video packets, finish=1/failed=0, no final AAC required.
"""
from pathlib import Path
import hashlib
import os
import sys
import json
import re
import shutil
import subprocess
import tempfile
import time

RT = Path(__file__).resolve().parent
ROOT = RT.parent
CACHE = ROOT / '.metal-cache/live-seg'
FF = ROOT / 'third_party/ffmpeg'


def checked(command, logpath, cwd=ROOT, timeout=240):
    with logpath.open('w') as log:
        result = subprocess.run(command, cwd=cwd, stdout=log, stderr=log, timeout=timeout)
    assert result.returncode == 0, logpath.read_text()[-3000:]


def probe_binary():
    # The shipping FFmpeg deliberately has no TS demuxer or H.264 decoder.
    # Build an independent, offline-only inspector from the checksum-pinned tar.
    build = CACHE / 'probe'
    build.mkdir(parents=True, exist_ok=True)
    exe = build / 'ffprobe'
    if exe.exists() and (build / 'fixture-v1').exists():
        return exe
    archive = ROOT / 'third_party/dl/ffmpeg-7.1.1.tar.xz'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == \
        '733984395e0dbbe5c046abda2dc49a5544e7e0e1e2366bba849222ae9e3a03b1'
    source = build / 'ffmpeg-7.1.1'
    if not (source / 'configure').exists():
        checked(['tar', '-xJf', str(archive), '-C', str(build)], CACHE/'probe-extract.log')
    flags = ['--disable-everything', '--disable-autodetect', '--disable-network', '--disable-doc',
             '--disable-x86asm', '--enable-ffprobe', '--disable-ffmpeg', '--enable-demuxer=mpegts',
             '--enable-parser=h264,aac', '--enable-decoder=h264,hevc,aac', '--enable-protocol=file']
    checked([str(source/'configure'), *flags], CACHE/'probe-configure.log', build)
    checked(['make', '-j4', 'ffprobe'], CACHE/'probe-build.log', build)
    (build/'fixture-v1').touch()
    return exe


def production_ffmpeg():
    # Test the exact production recipe in scratch. Worktrees may share a
    # read-only dependency installation; tests must never rebuild that copy.
    build = CACHE / 'dependency'
    recipes = [ROOT / 'media' / name for name in
               ('build_ffmpeg.sh', 'check_ffmpeg_sources.py', 'check_libraries.py')]
    digest = hashlib.sha256(b''.join(p.read_bytes() for p in recipes)).hexdigest()
    stamp = build / 'recipe-sha256'
    prefix = build / 'third_party/ffmpeg'
    if (prefix/'lib/libavformat.a').exists() and stamp.exists() and stamp.read_text() == digest:
        return prefix
    (build/'media').mkdir(parents=True, exist_ok=True)
    (build/'third_party/dl').mkdir(parents=True, exist_ok=True)
    for recipe in recipes:
        shutil.copy2(recipe, build/'media'/recipe.name)
    shutil.copy2(ROOT/'third_party/dl/ffmpeg-7.1.1.tar.xz', build/'third_party/dl/ffmpeg-7.1.1.tar.xz')
    start = time.monotonic()
    # The copied recipe resolves the project Python relative to its own tree, which the scratch copy lacks.
    os.environ.setdefault('TOOLS_PY', sys.executable)
    checked([str(build/'media/build_ffmpeg.sh')], CACHE/'production-build.log')
    stamp.write_text(digest)
    print(f'production FFmpeg scratch rebuild: {time.monotonic()-start:.1f}s', flush=True)
    return prefix


def build(directory, name, source):
    # Count actual sink construction in the production factory, without adding
    # production diagnostics or allocations to the inactive path.
    source = source.replace('std::make_shared<clip::Sink>', 'countedSink')
    path = directory / (name + '.mm')
    path.write_text(source)
    exe = directory / name
    command = ['clang++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-function', '-Wno-deprecated-declarations', '-fobjc-arc',
               '-I'+str(RT), '-I'+str(FF/'include'), '-DLIVE_SEG_SOURCE="'+str(path)+'"',
               str(RT/'live_seg_test.mm'), str(RT/'livestream.mm'), str(RT/'live_encode.mm'), str(RT/'clip_packets.mm'),
               str(FF/'lib/libavformat.a'), str(FF/'lib/libavcodec.a'), str(FF/'lib/libavutil.a'), '-lz']
    for framework in ('Foundation', 'CoreMedia', 'CoreVideo', 'VideoToolbox', 'AudioToolbox'):
        command += ['-framework', framework]
    checked(command+['-o', str(exe)], CACHE/(name+'-build.log'))
    return exe


def run(exe, mode, directory, expected=0):
    directory.mkdir(exist_ok=True)
    result = subprocess.run([str(exe), mode, str(directory)], capture_output=True, text=True, timeout=90)
    output = result.stdout + result.stderr
    (CACHE/(exe.name+'-'+mode+'.log')).write_text(output)
    print(exe.name+'/'+mode+': '+output[-1800:].strip(), flush=True)
    assert result.returncode == expected, (result.returncode, output[-2500:])
    return output


def payload(packet):
    words = []
    for line in packet.get('data', '').splitlines():
        if re.match(r'^[0-9a-f]{8}:', line):
            words.append(line.split(':', 1)[1].strip().split('  ', 1)[0].replace(' ', ''))
    return bytes.fromhex(''.join(words))


def leading_tables(directory):
    files = list(directory.glob('*.ts'))
    assert files
    first = None
    for path in files:
        body = path.read_bytes()
        assert len(body) >= 3*188 and len(body)%188 == 0
        pids = [((body[i+1]&31)<<8)|body[i+2] for i in (0,188,376)]
        if first is None: first = pids
        assert pids[:2] == [0,4096], 'PAT/PMT first: '+str(pids)
    print('leading tables: first PIDs='+str(first)+' segments='+str(len(files)), flush=True)


def inspect(probe, directory, stop=False):
    files = sorted(directory.glob('*.ts'), key=lambda p: int(p.stem))
    assert (len(files) == 3 if stop else len(files) >= 5), f'only {len(files)} segments'
    leading_tables(directory)
    last = {}
    totals = {'video': 0, 'audio': 0}
    durations = []
    for path in files:
        result = subprocess.run([str(probe), '-v', 'error', '-show_packets', '-show_streams',
                                 '-show_format', '-show_data', '-of', 'json', str(path)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr[-2000:]
        (CACHE/(directory.name+'-'+path.stem+'.json')).write_text(result.stdout)
        data = json.loads(result.stdout)
        streams = {s['index']: s['codec_type'] for s in data['streams']}
        assert set(streams.values()) == {'video', 'audio'}, streams
        packets = data['packets']
        video = [p for p in packets if streams[p['stream_index']] == 'video']
        audio = [p for p in packets if streams[p['stream_index']] == 'audio']
        terminal = stop and path == files[-1]
        assert video and (audio or terminal), 'regular segments need both tracks'
        if terminal:
            assert len(video) == 1 and not audio and int(video[0]['dts']) == 450000, 'final IDR missing'
        assert 'K' in video[0]['flags'], 'first video packet is not key'
        nals = [p[0] & 31 for p in re.split(b'\x00\x00(?:\x00)?\x01', payload(video[0])) if p]
        assert 5 in nals and 7 in nals and 8 in nals, f'IDR/SPS/PPS missing: {nals}'
        assert nals.index(7) < nals.index(8) < nals.index(5) and 1 not in nals[:nals.index(5)], nals
        duration = float(data['format']['duration'])
        durations.append(duration)
        # One video PES has no following DTS from which the probe can infer
        # frame duration; the harness checks its 1500-tick segment interval.
        if not terminal:
            assert abs(duration - 2.0) <= 1/60 + .00002, f'duration {duration}'
        for packet in packets:
            track = streams[packet['stream_index']]
            dts = int(packet['dts'])
            assert track not in last or dts > last[track], f'non-monotonic {track} DTS: {dts} <= {last.get(track)}'
            assert packet['pts'] == packet['dts'], 'unexpected reordered packet'
            last[track] = dts
            totals[track] += 1
        if audio:
            header = payload(audio[0])[:7]
            assert len(header) == 7 and header[0] == 255 and header[1] & 0xf6 == 0xf0, 'missing ADTS'
            assert (header[2] >> 6) == 1 and ((header[2] >> 2) & 15) == 3, 'not AAC-LC 48 kHz'
        print(f'ffprobe {path.name}: duration={duration:.6f} video={len(video)} audio={len(audio)} IDR/SPS/PPS=1', flush=True)
    assert totals['video'] == (241 if stop else 720), totals
    print(f'probe: segments={len(files)} durations={min(durations):.6f}..{max(durations):.6f} '
          f'video={totals["video"]} audio={totals["audio"]} non_monotonic=0', flush=True)


def muxer_contract(source):
    match = re.search(r"--enable-muxer='([^']+)'", source)
    assert match and 'mpegts' in match[1].split(','), 'mpegts missing from FFmpeg build list'
    assert '--disable-network' in source and '--disable-everything' in source


def main():
    global FF
    CACHE.mkdir(parents=True, exist_ok=True)
    config = (ROOT/'media/build_ffmpeg.sh').read_text()
    muxer_contract(config)
    try:
        muxer_contract(config.replace(',mpegts', ''))
    except AssertionError as error:
        print('removed-muxer mutant rejected: '+str(error), flush=True)
    else:
        raise AssertionError('removed muxer accepted')
    FF = production_ffmpeg()
    probe = probe_binary()
    source = (RT/'live_seg.mm').read_text()
    with tempfile.TemporaryDirectory(prefix='fixtures-', dir=CACHE) as temporary:
        directory = Path(temporary)
        fixed = build(directory, 'fixed', source)
        run(fixed, 'overlong', directory/'overlong')
        # Original tail and missing duration guard, pinned independently of history.
        guard = '        if(cut-start>450000) return fail("no-idr-5s");\n'
        old_tail = '        avio_flush(io);\n        return io->error>=0 && !segment.bytes.empty();'
        assert hashlib.sha256(old_tail.encode()).hexdigest() == 'ed69e1b168fecca7ca3394e2a1d344ac271074e44f482ee4f9ae3d2b9a20598f'
        begin = source.index('        avio_flush(io);')
        end = source.index('        return io->error>=0 && !segment.bytes.empty();',begin)+len('        return io->error>=0 && !segment.bytes.empty();')
        original_source = source[:begin]+old_tail+source[end:]
        assert original_source.count(guard)==1
        original_source=original_source.replace(guard,'')
        original=build(directory,'original-ingest',original_source)
        output=run(original,'overlong',directory/'original-overlong',1)
        assert 'segments=1 longest_ticks=540000 failed=0' in output
        run(original,'stream',directory/'original-tables')
        try: leading_tables(directory/'original-tables')
        except AssertionError as error:
            assert 'PAT/PMT first: [17, 0, 4096]' in str(error)
            print('original leading tables rejected: '+str(error),flush=True)
        else: raise AssertionError('original PAT/PMT ordering escaped')
        run(fixed, 'stop', directory/'stop-ts')
        inspect(probe, directory/'stop-ts', stop=True)
        run(fixed, 'off', directory/'off')
        run(fixed, 'stream', directory/'fixed-ts')
        inspect(probe, directory/'fixed-ts')
        run(fixed, 'controls', directory/'controls')
        old = 's.emit(s.end,true)'
        assert source.count(old) == 1
        required = build(directory, 'required-audio', source.replace(old, 's.emit(s.end)'))
        output = run(required, 'stop', directory/'required-audio-ts', 1)
        assert 'segments=2 video=241 finish=0 failed=1' in output
        assert 'FAIL segment: stop preserves final IDR' in output
        old = 's.key && s.dts-start>=180000'
        assert source.count(old) == 1
        nonidr = build(directory, 'nonidr', source.replace(old, '!s.key && s.dts-start>=180000'))
        output = run(nonidr, 'stream', directory/'nonidr-ts', 1)
        assert 'FAIL segment: mux packets' in output
        old = 'packet->pts=packet->dts=s.dts+90000;'
        assert source.count(old) == 1
        reset = build(directory, 'reset', source.replace(old, 'packet->pts=packet->dts=s.dts-segment.start90k+90000;'))
        output = run(reset, 'stream', directory/'reset-ts', 1)
        assert 'FAIL segment: finish segments' in output
        try:
            inspect(probe, directory/'reset-ts')
        except AssertionError as error:
            assert 'non-monotonic' in str(error), str(error)
            print('reset-DTS mutant rejected: '+str(error), flush=True)
        else:
            raise AssertionError('reset DTS accepted')
        old = 'if(!active) return {};'
        assert source.count(old) == 1
        on = build(directory, 'default-on', source.replace(old, 'if(false) return {}; (void)active;'))
        output = run(on, 'off', directory/'default-on-out', 1)
        assert 'FAIL segment: Off has zero allocations and sinks' in output
    print('live_seg PASS: six full segments + one-frame stop probed; five mutants rejected; bounded/off controls pass', flush=True)


if __name__ == '__main__':
    main()
