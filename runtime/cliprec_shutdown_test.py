"""Shutdown regression using the production recorder and a slow writer stub.
Proof: ORIGINAL replay-only quit 0.806214 s / exit 1; fixed 0.000000 s / exit 0.
Requested save: ORIGINAL 0.809907 s, fixed 0.810076 s; both completed (10 s bound).
The standalone cliprec suite also verifies real requested MP4 publication/decoding.
"""
from pathlib import Path
import hashlib
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

ORIGINAL = 'void cliprec_shutdown(void) {\n    ClipSession *s;\n    { std::lock_guard<std::mutex> lock(control); s = current; }\n    if (!s) return;\n    livestream_stop(); liveState=0;\n    cliprec_stop();\n    // Quit is allowed to wait; rendering never waits on GPU/encoder/writer.\n    if (dispatch_group_wait(s->finished,dispatch_time(DISPATCH_TIME_NOW,10*NSEC_PER_SEC)))\n        fprintf(stderr,"cliprec: finalization timed out; unpublished temporary clip retained\\n");\n    if(replayExport) replayExport->wait(5);\n}\n'
ORIGINAL_SHA = 'a14e2c5032f554d622d17baba312c89d620e82d677ff7e278dbc5780b30cfd6d'

def build(directory, original):
    source = (ROOT / 'runtime/cliprec.mm').read_text()
    if original:
        assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == ORIGINAL_SHA
        begin = source.index('void cliprec_shutdown(void) {')
        end = source.index('int cliprec_live_start(void) {', begin)
        source = source[:begin] + ORIGINAL + source[end:]
    implementation = directory / 'cliprec.mm'
    implementation.write_text(source)
    binary = directory / 'shutdown'
    command = ['clang++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-parameter', '-Wno-deprecated-declarations', '-fobjc-arc',
               '-I', str(ROOT / 'runtime'), '-I', str(ROOT / 'third_party/ffmpeg/include'),
               '-DCLIPREC_SOURCE="' + str(implementation) + '"']
    command += [str(ROOT / p) for p in (
        'runtime/cliprec_test.mm', 'runtime/live_encode.mm', 'runtime/live_seg.mm',
        'runtime/livestream.mm', 'runtime/clip_packets.mm', 'runtime/replay.mm',
        'runtime/replay_export.mm', 'host/audio/dsound_host.cpp', 'media/media.cpp')]
    command += [str(ROOT / 'third_party/ffmpeg/lib' / ('lib' + name + '.a'))
                for name in ('avformat', 'avcodec', 'swscale', 'swresample', 'avutil')]
    command += ['-lz']
    for framework in ('AudioToolbox', 'CoreAudio', 'CoreFoundation', 'Cocoa', 'Metal',
                      'QuartzCore', 'AVFoundation', 'VideoToolbox', 'CoreVideo', 'CoreMedia'):
        command += ['-framework', framework]
    command += ['-o', str(binary)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-4000:]
    result = subprocess.run([str(binary), '--shutdown-proof'], capture_output=True, text=True, timeout=20)
    print(('ORIGINAL' if original else 'fixed') + ': ' + result.stdout.strip(), flush=True)
    if original:
        assert result.returncode == 1 and 'unrequested replay shutdown under 0.5 s' in result.stderr, result.stderr
    else:
        assert result.returncode == 0, result.stderr


def main():
    with tempfile.TemporaryDirectory(prefix='cliprec-shutdown-') as temp:
        for original in (True, False):
            directory = Path(temp) / str(original)
            directory.mkdir()
            build(directory, original)
    print('cliprec_shutdown_test PASS')


if __name__ == '__main__':
    main()
