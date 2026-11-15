#!/usr/bin/env python3
"""Production coverage: software 60 encoders; native 1 encoder, 60 mip faces.
GPU direct/replay: good 0/60 bad faces; omitted generation 60/60, exit 1;
one stale face/level 1/60, exit 1; omitted replay opcode 60/60, exit 1.
Every texel is checked. Pattern comparisons measure all face/mip differences.
BGRA8 comparisons: good max 0; missing opcode with poison max 252, exit 1.
Default: unset sharp 1024/11; old PC-default mutant 256/1 exits 1.
Native default: unset 1; default-off mutant 0 exits 1.
Private host: 0 guest writes, unchanged creation descriptors; removing the real
edge guard writes 1024 over the sentinel (1 write, exit 1).
"""
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
gpu_enabled = os.environ.get('CODEX_GPU_CHECK') == '1' or os.environ.get('NFS_GPU_SANDBOX') == '1'
with tempfile.TemporaryDirectory(prefix='envcube-test-') as scratch:
    # Execute the actual included private host path with a guest-code sentinel.
    original = (root / 'runtime/envcube.m').read_text()
    private_source = Path(scratch) / 'private_envcube.m'
    harness = Path(scratch) / 'private_check.m'
    harness.write_text(r'''
#include "private_envcube.m"
uint8_t *G; int diag_on;
int main(void) {
    G=calloc(1,0x00900000); if(!G)return 2;
    M32(0x008F8FF4u)=0x13579BDFu;
    setenv("NFS_CAR_REFLECTION","sharp",1);envcube_live_reconfigure();
    unsigned writes=M32(0x008F8FF4u)!=0x13579BDFu;
    int fail=writes || envcube_creation_levels(1024,1,1,21)!=1 ||
        envcube_creation_edge(1024,0,21,1,0x006BD529u)!=1024;
    printf("private envcube writes=%u descriptors=%s\n",writes,fail?"FAIL":"unchanged");
    free(G);return fail;
}
''')
    anchor = '#if NFS_GAME == NFS_GAME_MW\n    // Re-entrant'
    assert original.count(anchor) == 1
    for broken in (False, True):
        private_source.write_text(original.replace(anchor, '#if 1\n    // Re-entrant', 1) if broken else original)
        private_exe = Path(scratch) / 'private_check'
        command = ['clang', '-O1', '-w', '-fobjc-arc', '-DNFS_GAME=2',
                   '-I', str(root / 'runtime'), '-I', str(root / 'recomp'),
                   str(harness), '-framework', 'Metal', '-framework', 'Foundation', '-o', str(private_exe)]
        # Only the mutant's guarded diagnostic needs this inert sink.
        sink = Path(scratch) / 'sink.c'; sink.write_text('void diag_event(const char *fmt,...) {(void)fmt;}\n')
        result = subprocess.run(command + [str(sink)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-2000:]
        result = subprocess.run([str(private_exe)], capture_output=True, text=True)
        assert result.returncode == int(broken), result.stdout + result.stderr
        assert f'writes={int(broken)}' in result.stdout
        print(('broken: ' if broken else 'good: ') + result.stdout.strip(), flush=True)
    exe = Path(scratch) / 'envcube'
    def compile_test(source, output, recorder=root / 'runtime/render_thread.m'):
        subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
                    '-fobjc-arc', '-I', str(root / 'recomp'),
                    '-I', str(root / 'runtime'), str(source), str(recorder),
                    '-framework', 'Metal', '-framework', 'Foundation', '-o', str(output)], check=True)
    compile_test(root / 'runtime/envcube_test.m', exe)
    for mode in ['--cpu'] + (['--gpu'] if gpu_enabled else []):
        paths = ['0', '1'] if mode == '--gpu' else ['0']
        for replay in paths:
            env = {**os.environ, 'NFS_RENDER_THREAD': replay, 'MTL_DEBUG_LAYER': '1'}
            for path in ([[], ['--blit']] if mode == '--gpu' else [[]]):
                good = subprocess.run([exe, mode, *path], env=env, capture_output=True, text=True)
                assert good.returncode == 0, good.stdout + good.stderr
                print(good.stdout.strip(), 'replay=' + replay, flush=True)
                bad = subprocess.run([exe, mode, *path, '--broken'], env=env, capture_output=True, text=True)
                assert bad.returncode == 1, bad.stdout + bad.stderr
                assert 'generated=0' in bad.stdout, bad.stdout
                assert ('bad_samples=60/60' if mode == '--gpu' else 'completed=0') in bad.stdout, bad.stdout
                print('PASS negative control:', bad.stdout.strip(), 'replay=' + replay, flush=True)
                if mode == '--gpu':
                    stale = subprocess.run([exe, mode, *path, '--stale'], env=env, capture_output=True, text=True)
                    assert stale.returncode == 1 and 'generated=60 bad_samples=1/60' in stale.stdout, stale.stdout + stale.stderr
                    print('PASS stale-level control:', stale.stdout.strip(), 'replay=' + replay, flush=True)
            if mode == '--gpu':
                comparison = subprocess.run([exe, '--compare'], env=env, capture_output=True, text=True)
                assert comparison.returncode == 0, comparison.stdout + comparison.stderr
                assert comparison.stdout.count('COMPARE scene=') == 198, comparison.stdout
                if os.environ.get('NFS_METAL_CACHE'):
                    proof = Path(os.environ['NFS_METAL_CACHE']) / 'cubemip-proof'
                    proof.mkdir(parents=True, exist_ok=True)
                    (proof / f'patterns-replay{replay}.txt').write_text(comparison.stdout)
                print(comparison.stdout.strip(), 'replay=' + replay, flush=True)
    original = (root / 'runtime/envcube.m').read_text()
    mutant = original.replace('return !value || strcmp(value, "pc");',
                              'return value && !strcmp(value, "sharp");')
    assert mutant != original
    mutant_source = Path(scratch) / 'envcube_mutant.m'
    mutant_source.write_text(mutant)
    harness = Path(scratch) / 'envcube_mutant_test.m'
    harness.write_text((root / 'runtime/envcube_test.m').read_text().replace(
        '#include "envcube.m"', f'#include "{mutant_source}"'))
    mutant_exe = Path(scratch) / 'envcube_mutant'
    compile_test(harness, mutant_exe)
    bad = subprocess.run([mutant_exe, '--cpu'], capture_output=True, text=True)
    assert bad.returncode == 1 and 'DEFAULT FAIL option=unset edge=256 levels=1' in bad.stdout, bad.stdout + bad.stderr
    print('PASS PC-default negative control:', bad.stdout.strip(), flush=True)
    mutant = original.replace('return !value || !strcmp(value,"1");',
                              'return value && !strcmp(value,"1");')
    assert mutant != original
    mutant_source.write_text(mutant)
    compile_test(harness, mutant_exe)
    bad = subprocess.run([mutant_exe, '--cpu'], capture_output=True, text=True)
    assert bad.returncode == 1 and 'BLIT DEFAULT FAIL unset=0 expected=1' in bad.stdout, bad.stdout + bad.stderr
    print('PASS native-default negative control:', bad.stdout.strip(), flush=True)
    if gpu_enabled:
        recorder = (root / 'runtime/render_thread.m').read_text()
        mutant = recorder.replace('case OP_MIPMAPS: [b generateMipmapsForTexture:O(a[0])]; break;',
                                  'case OP_MIPMAPS: break;')
        assert mutant != recorder
        mutant_recorder = Path(scratch) / 'recorder_mutant.m'
        mutant_recorder.write_text(mutant)
        mutant_exe = Path(scratch) / 'replay_mutant'
        compile_test(root / 'runtime/envcube_test.m', mutant_exe, mutant_recorder)
        bad = subprocess.run([mutant_exe, '--gpu', '--blit'],
                             env={**os.environ, 'NFS_RENDER_THREAD': '1'}, capture_output=True, text=True)
        assert bad.returncode == 1 and 'generated=60 bad_samples=60/60' in bad.stdout, bad.stdout + bad.stderr
        print('PASS missing replay opcode control:', bad.stdout.strip(), flush=True)
        bad = subprocess.run([mutant_exe, '--compare'],
                             env={**os.environ, 'NFS_RENDER_THREAD': '1'}, capture_output=True, text=True)
        assert bad.returncode == 1 and 'COMPARE FAIL scene=day max=' in bad.stdout, bad.stdout + bad.stderr
        print('PASS comparison poison control:', bad.stdout.splitlines()[-1], flush=True)
    if not gpu_enabled:
        print('GPU PENDING: run on a Metal-capable host: CODEX_GPU_CHECK=1 runtime/offline_tests.sh envcube_test')
