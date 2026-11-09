#!/usr/bin/env python3
"""Production compressed replay retention, independently decoded without a game.
Measured controls (baseline / broken, each broken executable exits 1):
Off: 0 allocations, 0 packets / 4 allocations, 1 packet in 10,000 calls.
90-second stream: [58,90], 32 seconds, 16 GOPs / [0,90], 90 seconds, 45 GOPs.
First GOP: sync=1, 20 decoded, 0 bad / sync=0, 0 decoded, 1 bad.
AAC preroll: first PTS 57.940000 <= cut 57.956000 / 57.982667 > cut.
Missing key: 0 packets, 100 rejected / 100 packets, 0 rejected.
Cap: peak 251,658,240 + 16,777,216 transfer <= 268,435,456 bytes /
536,870,912 retained bytes. Oversized GOP: 0 bytes, 10 deltas rejected.
Whole-GOP eviction: 7 complete GOPs / 8 GOPs, first_sync=0 (partial oldest).
Resolution change: generation 2, 1 packet, old reference released=1 /
generation 1, 3 packets, old reference released=0.
Shared payload: 130 bytes unchanged=1 / unchanged=0. File sink payload:
5,070 identical bytes with unset/off/on. Off assembly: 0 calls, 0 stack frame;
injected allocation call rejected (1 call). Removed replay/file helper hooks each rejected (0/1).
Attached runtime sink: armed=1, packets=1; omitted Arm rejects attachment
(healthy sinks 0 versus 1), before packet publication.
Delayed attachment preserves Disarm: armed=0 / forced re-arm: armed=1.
Eleven broken production controls rejected.
AAC payload is synthetic; only its packet timing, ownership and preroll are tested.
"""
from pathlib import Path
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
CACHE = RT.parent / '.metal-cache' / 'replay'
CACHE.mkdir(parents=True, exist_ok=True)


def build(directory, name, source):
    path = directory / (name + '.mm')
    path.write_text(source)
    exe = directory / name
    command = ['clang++', '-std=c++17', '-O2', '-Wall', '-Wextra', '-Werror',
               '-fobjc-arc', '-I' + str(RT), str(RT / 'replay_test.mm'),
               str(RT / 'clip_packets.mm'), str(path)]
    for framework in ('Foundation', 'CoreMedia', 'CoreVideo', 'AudioToolbox', 'VideoToolbox'):
        command += ['-framework', framework]
    result = subprocess.run(command + ['-o', str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-4000:]
    return exe


def run(exe, case, expected, failure=None):
    result = subprocess.run([str(exe), case], capture_output=True, text=True, timeout=90)
    output = result.stdout + result.stderr
    print(f'{exe.name}/{case}: {output.strip()}', flush=True)
    assert result.returncode == expected, (result.returncode, output[-3000:])
    if failure:
        assert 'FAIL replay: '+failure in output, ('unexpected failure', output[-3000:])


def changed(source, before, after):
    assert source.count(before) == 1, (before, source.count(before))
    return source.replace(before, after)


def main():
    source = (RT / 'replay.mm').read_text()
    with tempfile.TemporaryDirectory(dir=CACHE) as scratch:
        directory = Path(scratch)
        fixed = build(directory, 'fixed', source)
        for case in ('off', 'span', 'decode', 'preroll', 'missing', 'cap', 'generation', 'identity'):
            run(fixed, case, 0)
        mutants = [
            ('off', changed(source,'if(!ring) { ring=std::make_shared<Ring>(); ring->arm(); }',
                            'if(!ring) ring=std::make_shared<Ring>(); ring->arm();'),
             'delayed sink attachment preserves explicit Disarm'),
            ('off', changed(source, 'ring->arm();', '/* no Arm */'),
             'on attaches a controllable ring'),
            ('off', changed(source,
                            'if(!armed.load(std::memory_order_acquire)) return clip::Result::consumed;',
                            'if(!armed.load(std::memory_order_acquire)) arm();'),
             'initial Off consumes nothing and allocates nothing'),
            ('span', changed(source, 'CMTime cut=CMTimeSubtract(end,CMTimeMake(30,1));', 'CMTime cut=CMTimeSubtract(end,CMTimeMake(300,1));'),
             '90 seconds keeps last 30 plus preceding GOP'),
            ('decode', changed(source, 'if(keyframe(sample))', 'if(!keyframe(sample))'),
             'first retained GOP independently decodes from a keyframe'),
            ('preroll', changed(source, 'CMTime cut=CMTimeSubtract(anchor,CMTimeMake(prerollFrames,48000));', 'CMTime cut=CMTimeSubtract(anchor,CMTimeMake(0,48000));'),
             'AAC includes decoder preroll and crossing packet'),
            ('missing', changed(source, 'if(keyframe(sample))', 'if(keyframe(sample) || true)'),
             'non-keyframes cannot start retention'),
            ('cap', source.replace('retentionCap', '(1024ull*1024*1024)'),
             'oversized bitrate remains within 256 MiB'),
            ('generation', changed(source,
                                   'if(format && !CMFormatDescriptionEqual(format,p->format()))',
                                   'if(false && format && !CMFormatDescriptionEqual(format,p->format()))'),
             'resolution change drops old video and AAC generation'),
            ('identity', changed(source, 'auto &s=*state;\n    auto sample=p->sample();',
                                 'auto &s=*state; CMBlockBufferFillDataBytes(0xa5,CMSampleBufferGetDataBuffer(p->sample()),0,p->bytes);\n    auto sample=p->sample();'),
             'replay must never change shared compressed bytes'),
        ]
        whole = changed(source,
                        'for(auto &p:gops.front().video) counts.bytes-=p->bytes;\n        gops.pop_front(); ++counts.evicted;',
                        'counts.bytes-=gops.front().video.front()->bytes;\n'
                        '        gops.front().video.erase(gops.front().video.begin());\n'
                        '        if(gops.front().video.empty()) gops.pop_front(); ++counts.evicted;')
        mutants.append(('cap', whole, 'cap eviction preserves complete GOP start'))
        for index, (case, mutant, failure) in enumerate(mutants):
            broken = build(directory, f'broken-{index}-{case}', mutant)
            run(broken, case, 1, failure)
        assembly = directory / 'replay.s'
        result = subprocess.run(['clang++', '-std=c++17', '-O2', '-fobjc-arc',
                                 '-I'+str(RT), '-S', str(RT/'replay.mm'), '-o', str(assembly)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-3000:]
        match = re.search(r'^__ZN6replay4Ring7consume[^\n]*:\s*.*?\n(.*?)\.cfi_endproc',
                          assembly.read_text(), re.M | re.S)
        assert match, 'production consume entry not found'
        def off_ok(code):
            return not re.search(r'^\s*bl\s|\bsp\b', code, re.M) and bool(re.search(r'^\s*ret\s*$', code, re.M))
        # Follow the branch taken by armed=false, excluding the armed block.
        branch = re.search(r'^\s*b\.ne\s+(LBB\w+)', match[1], re.M)
        assert branch, match[1]
        target = re.search(r'^'+branch[1]+r':\s*\n(.*?\bret\b)', match[1], re.M | re.S)
        assert target, match[1]
        off_path = match[1][:branch.end()]+'\n'+target[1]
        assert off_ok(off_path), off_path
        assert not off_ok(off_path.replace('ret', 'bl _malloc\n\tret')), 'injected allocation must fail assembly check'
        print('Off assembly: 0 calls / 0 stack frame; allocation-call mutant rejected')
        recorder = (RT/'cliprec.mm').read_text()
        def hook_ok(code):
            # File setup is a helper now; verify its call order inside prepare.
            begin=code.index('- (void)prepare:(unsigned)w height:(unsigned)h {')
            prepare=code[begin:code.index('- (void)attachLive {',begin)]
            markers=('[self attachFile];','replay::attach(outputs,replayRing,&replaySink);','ready.store(true')
            return (code.count(markers[1])==1 and 'outputs.add(fileSink);' in code and
                    all(marker in prepare for marker in markers) and
                    prepare.index(markers[0])<prepare.index(markers[1])<prepare.index(markers[2]))
        assert hook_ok(recorder)
        assert not hook_ok(recorder.replace('replay::attach(outputs,replayRing,&replaySink);', '')), 'removed hook escaped check'
        assert not hook_ok(recorder.replace('[self attachFile];', '')), 'removed file helper call escaped check'
        print(f'replay_test PASS: 8 baseline cases; {len(mutants)}/11 broken controls rejected')


if __name__ == '__main__':
    main()
