#!/usr/bin/env python3
"""Acceptance reader and passive qrsplit.c probe, no private assets or game.
The compiled x360.m frame hook and qrsplit.c probe generate both finish orders;
unrelated water/shadow callbacks are stubs. Each missing-stage prefix must
fail at that stage. Broken slot mapping must fail split-race; diagnostics off
must make zero clock calls and preserve all guest bytes. Live proof is recorded
in splitflow_check.py's header, separately from these synthetic lifecycle inputs.
"""
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('splitflow_check', ROOT/'runtime/splitflow_check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

PROBE = r'''
#include "host.h"
#include <stdarg.h>
#include <stdlib.h>
uint8_t *G; int diag_on, pads=2, views; unsigned captures, handles=12, clocks, events;
uint64_t host_ticks(void){++clocks;return (uint64_t)clocks*1000000000ull;}
int pad_count(void){return pads;}
unsigned threads_handle_count(void){return handles;}
unsigned split_capture_resources(void){return captures;}
int split_view_rect(uint32_t view,uint32_t *r){(void)view;(void)r;return views==2;}
void diag_event(const char *fmt,...){if(!strncmp(fmt,"x360:",5))return;printf("f%u t1 ",++events);va_list a;va_start(a,fmt);vprintf(fmt,a);va_end(a);puts("");}
static int sequence_failed,stopped,step;static uint64_t step_start;
'''
FRAME = r'''
#define F32(a) MF32(a)
#define TIME_OF_DAY_PTR 0x500000u
#define GAMEFLOW_STATE 0x925E90u
#define SHADOW_FOCUS_FAR 0x500010u
#define SHADOW_FOCUS_ANGLE 0x500014u
#define SHADOW_WIDTH 0x500018u
#define X360E_SHADOW_FOCUS_FE 0.0f
#define X360E_SHADOW_FOCUS_RACE 0.0f
static int fit;
static void mat_water_frame(void){}
static void shadow_options(void){}
static void test_sun(void){}
static void test_shadow_switch(void){}
static void put_float(uint32_t p,float v){(void)p;(void)v;}
'''
POST = r'''
#define REQUIRE(x) do{if(!(x)){fprintf(stderr,"probe failed: %s\n",#x);exit(1);}}while(0)
static void snapshot(void){
 uint8_t *before=malloc(16*1024*1024);memcpy(before,G,16*1024*1024);
 x360_frame();REQUIRE(!memcmp(before,G,16*1024*1024));free(before);
}
static void package(const char *name){strcpy((char*)G+0x112000,name);M8(0x111028)=1;}
static void fe(void){
 M32(0x925E90)=3;M32(0x988600+0x2c)=0;M32(0x92D884)=0;views=captures=0;
 package("MainMenu.fng");snapshot();REQUIRE(!qrsplit_accept_ready("split")&&!qrsplit_accept_ready("finished")&&!qrsplit_accept_ready("solo"));REQUIRE(qrsplit_accept_ready("quit"));
 captures=1;REQUIRE(!qrsplit_accept_ready("quit"));captures=0;
}
static void race(int split){
 M32(0x925E90)=6;M32(0x988600+0x2c)=3;M8(0x100000)=2;M8(0x10012c)=split?4:0;
 M32(0x92D884)=split?2:1;views=split?2:0;captures=split?36:0;
 M32(0x300000)=split?0x320000:0x310000;M32(0x300004)=0x310000;
 M8(0x200018+0x21)=M8(0x2001ac+0x21)=0;MF32(0x200018+0x18)=MF32(0x2001ac+0x18)=0;package("none");snapshot();
 REQUIRE(qrsplit_accept_ready(split?"split":"solo"));REQUIRE(!qrsplit_accept_ready("finished"));
 M32(0x9885c8)=1;REQUIRE(!qrsplit_accept_ready(split?"split":"solo"));M32(0x9885c8)=0;
 M32(0x98862c)=4;REQUIRE(!qrsplit_accept_ready(split?"split":"solo"));M32(0x98862c)=3;
}
int main(int argc,char **argv){
 unsigned winner=argc>1?atoi(argv[1]):1;
 G=calloc(1,16*1024*1024);
 // Off path is cheaper than a clock read, including single-pad diagnostic runs.
 snapshot();REQUIRE(clocks==0&&events==0);diag_on=1;pads=1;snapshot();REQUIRE(clocks==0&&events==0);pads=2;
 M32(0x91CF90)=0x100000;M32(0x91CADC)=0x110000;M32(0x110000)=0x110100;
 M32(0x1101e4)=0x111000;M32(0x111020)=1;M32(0x11100c)=0x112000;
 M32(0x9885e0)=0x988600;M32(0x91E000)=0x200000;
 M32(0x92D87c)=0x300000;M32(0x300000)=0x310000;M32(0x300004)=0x320000;
 M32(0x31001c)=0;M32(0x32001c)=1;M32(0x30fff8)=0x340000;M32(0x31fff8)=0x350000;
 // IPlayer's +18 is deliberately poisoned: the actual GetSimable thunk reads -8.
 M32(0x310018)=M32(0x320018)=0xdeadbeef;
 M32(0x92FEB4)=0x330000;M32(0x92FEBC)=2;
 M32(0x330000)=17;M32(0x330004)=0x340000;M32(0x330008)=29;M32(0x33000c)=0x350000;
 M32(0x201958)=2;M32(0x200018)=17;M32(0x2001ac)=29;
 M32(0x200028)=winner==1?1:2;M32(0x2001bc)=winner==2?1:2;
 M32(0x8EF998)=2;M32(0x9142D0)=0x400000;M32(0x9142D4)=0x410000;
 M32(0x400024)=0x420000;M32(0x410024)=0x430000;
 M32(0x420000)=0x8995c8;M32(0x430000)=0x899610;M8(0x42004c)=M8(0x43004c)=1;
 M32(0x420058)=0x440000;M32(0x420068)=0;M32(0x420080)=~0u;
 M32(0x430058)=0x450000;M32(0x430068)=1;M32(0x430070)=0x460000;M32(0x430080)=2;
 unsigned e,v;qs_audio(0,&e,&v);REQUIRE(e==1&&v==1);qs_audio(1,&e,&v);REQUIRE(e==1&&v==2);
 REQUIRE(qs_racer(0x310000)==0x200018&&qs_racer(0x320000)==0x2001ac);
 // Reversing list order does not reverse either diagnostic identity.
 M32(0x300000)=0x320000;M32(0x300004)=0x310000;
 fe();race(1);snapshot();
 step_start=10;wait_step("pad2wait:split",20);REQUIRE(step==1&&!stopped);
 step=0;step_start=10;wait_step("pad2wait:finished",20);REQUIRE(!step&&!stopped);
 wait_step("pad2wait:finished",12009);REQUIRE(!step&&!stopped);
 wait_step("pad2wait:finished",12010);REQUIRE(!step&&stopped==1&&sequence_failed);
 sequence_failed=stopped=0;wait_step("pad2wait:solo",1810);REQUIRE(!step&&stopped==1&&sequence_failed);
 sequence_failed=stopped=0;
 diag_event("scripted pad2:128 down (car 10.0 m/s)");
 package("Pause_Main.fng");M32(0x98862c)=4;snapshot();
 package("none");M32(0x98862c)=3;snapshot();
 M8((winner==1?0x200018:0x2001ac)+0x21)=1;MF32((winner==1?0x200018:0x2001ac)+0x18)=100;snapshot();REQUIRE(!qrsplit_accept_ready("finished"));
 M8((winner==2?0x200018:0x2001ac)+0x21)=1;MF32((winner==2?0x200018:0x2001ac)+0x18)=100;snapshot();REQUIRE(qrsplit_accept_ready("finished"));
 package("PostRace_Results.fng");snapshot();REQUIRE(qrsplit_accept_ready("results"));
 M8(0x111028)=0;REQUIRE(!qrsplit_accept_ready("results"));M8(0x111028)=1;
 diag_event("split lifecycle: restart invalidated both capture and post banks (006308E0)");
 race(1);fe();race(0);fe();race(1);fe();
 step=stopped=sequence_failed=0;wait_step("pad2wait:quit",20);REQUIRE(step==1&&stopped==1);
 free(G);return 0;
}
'''


def main():
    source = '// Passive acceptance evidence.' + (ROOT/'runtime/qrsplit.c').read_text().split('// Passive acceptance evidence.', 1)[1].split('typedef void (*QsFn)', 1)[0]
    frame = re.search(r'void x360_frame\(void\) \{.*?\n\}', (ROOT/'runtime/x360.m').read_text(), re.S)[0]
    user = (ROOT/'runtime/user32.m').read_text()
    branch = user.split('    } else if (!strncmp(tok, "pad2wait:", 9)) {', 1)[1].split('    } else if (!strncmp(tok, "pad:", 4)', 1)[0]
    terminate = '[NSApp performSelector:@selector(terminate:) withObject:nil];'
    assert branch.count(terminate) == 2
    wait = 'static void wait_step(const char *tok,uint64_t frame){if(sequence_failed)return;\n' + branch.replace(terminate, '++stopped;') + '\n}\n'
    with tempfile.TemporaryDirectory(prefix='splitflow-test-') as tmp:
        tmp = Path(tmp)
        c = tmp/'probe.c'; exe = tmp/'probe'; log = tmp/'events.log'
        c.write_text(PROBE + source + FRAME + frame + wait + POST)
        command = ['clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                   '-I'+str(ROOT/'runtime'), '-I'+str(ROOT/'recomp'), str(c), '-o', str(exe)]
        build = subprocess.run(command, capture_output=True, text=True)
        assert build.returncode == 0, build.stderr[-3000:]
        for winner in (1, 2):
            run = subprocess.run([str(exe), str(winner)], capture_output=True, text=True)
            assert run.returncode == 0, run.stderr[-3000:]
            log.write_text(run.stdout)
            output = []
            assert check.check(log, winner, output.append), output
            assert output[-1].startswith('SPLITFLOW PASS 15/15'), output[-1]
            assert 'engines=1 voices=2' in next(s for s in output if s.startswith('p2-audio='))
            # Truncate each arrival: every stage must fail closed at its own name.
            for arrival in output[:-1]:
                stage = arrival.split('=', 1)[0]
                if stage == 'p2-audio':
                    log.write_text(run.stdout.replace('voices=2', 'voices=0'))
                    failed = []
                    assert not check.check(log, winner, failed.append)
                    assert 'first missing: p2-audio' in failed[-1], failed[-1]
                    continue
                line = int(re.search(r'events\.log:(\d+)', arrival)[1])
                log.write_text('\n'.join(run.stdout.splitlines()[:line-1])+'\n')
                failed = []
                assert not check.check(log, winner, failed.append), stage
                assert f'first missing: {stage}' in failed[-1], failed[-1]
            log.write_text(run.stdout)
            assert not check.check(log, 3-winner, lambda _: None), 'wrong finish order passed'
            log.write_text(run.stdout.replace('flags=00/00/00/00', 'flags=00/01/00/00'))
            assert not check.check(log, winner, lambda _: None), 'DNF counted as a finish'
            # A later FE, one-player race or split race cannot fill an earlier gap.
            for old, new, stage in [('package=MainMenu.fng', 'package=Missing.fng', 'fe'),
                                     ('captures=0 locals=0', 'captures=1 locals=0', 'fe'),
                                     ('restart invalidated', 'restart omitted', 'restart'),
                                     ('handles=12 captures=0 locals=0 views=0', 'handles=13 captures=0 locals=0 views=0', 'quit-fe')]:
                changed = run.stdout.replace(old, new, 1)
                log.write_text(changed); failed = []
                assert not check.check(log, winner, failed.append), old
                assert f'first missing: {stage}' in failed[-1], failed[-1]
        for winner in (1, 2):
            for broken in (False, True):
                plan = check.settings(winner, broken)
                assert 'BOOT_RACE=0' in plan and 'BOOT_AUTODRIVE=2' in plan and 'BOOT_KEYS_STRICT=1' in plan
                assert all(len(t) <= 31 for t in plan[-1].removeprefix('BOOT_KEYS=seq:').split(','))
            normal = check.settings(winner)[-1].removeprefix('BOOT_KEYS=seq:').split(',')
            broken = check.settings(winner, True)[-1].removeprefix('BOOT_KEYS=seq:').split(',')
            index = max(i for i, token in enumerate(normal) if token == 'RETURN')
            assert normal[:index] + normal[index+1:] == broken, 'control changes more than the final quit key'
        for name, old, replacement in [('wrong-simable', 'M32(player - 8)', 'M32(player + 0x18)'),
                                        ('one-finisher', 'return done == 3;', 'return done != 0;')]:
            assert source.count(old) == 1
            c.write_text(PROBE + source.replace(old, replacement) + FRAME + frame + wait + POST)
            build = subprocess.run(command, capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-2000:]
            broken = subprocess.run([str(exe), '1'], capture_output=True, text=True)
            assert broken.returncode != 0 and 'probe failed' in broken.stderr, name
        old = 'if (diag_on) qrsplit_diag_frame();'
        assert frame.count(old) == 1
        c.write_text(PROBE + source + FRAME + frame.replace(old, '(void)0;') + wait + POST)
        build = subprocess.run(command, capture_output=True, text=True)
        assert build.returncode == 0, build.stderr[-2000:]
        broken = subprocess.run([str(exe), '1'], capture_output=True, text=True)
        assert broken.returncode == 0, broken.stderr[-2000:]
        log.write_text(broken.stdout); failed = []
        assert not check.check(log, 1, failed.append) and 'first missing: fe' in failed[-1], failed
        print('SPLITFLOW OFFLINE PASS: 2 finish orders, 30 missing-stage controls, 3 mutants including the frame hook, actual pad2wait timeouts, 16 MiB passive snapshots')


if __name__ == '__main__':
    main()
