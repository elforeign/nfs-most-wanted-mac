#!/usr/bin/env python3
"""Replay shortcut production state, no window or controller.
Baseline: 512 key events + 4096 pad states unchanged while disabled;
500 held/repeated key and pad samples each save once; consumed releases=1,
focus/disconnect accidental saves=0; solo career tap/hold restored; 3 collisions.
Broken controls: key saves=250 and pad saves=500 vs 1; release leak=1 vs 0;
focus/reconnect accidental save=1 vs 0, restored tap=0 vs 16, collision
save=1 vs 0. Disabled key/pad controls each change input vs 0 changes.
Review regressions: two solo R1 taps originally produce 1 press/release pair,
fixed 2; a 200 ms hitch originally saves=1, hidden=304, fixed 0/0.
Enabling while held creates 0 saves (guard removed: 1). Twelve broken controls fail.
"""
from pathlib import Path
import subprocess
import tempfile

RT=Path(__file__).resolve().parent


def main():
    source=(RT/'replay_input.c').read_text()
    mutations=[
        ('disabled', '!enabled || vk!=key || blocked', 'vk!=key || blocked', 'disabled keyboard byte identity'),
        ('disabled', 'if(!chord && !s->consumed && !s->pending) return out;', '', 'disabled pad byte identity'),
        ('key', 'if(s->keys[vk])', 'if(0 && s->keys[vk])', 'held and repeated key saves once'),
        ('release', 'if(!down) s->keys[vk]=0;', 'if(!down) { s->keys[vk]=0; return 0; }', 'consumed release swallowed after focus loss'),
        ('pad', 'if(s->consumed) {', 'if(0 && s->consumed) {', 'held chord saves once'),
        ('pad', 'if(choice!=s->choice) {', 'if(0 && choice!=s->choice) {', 'enabling a held pad chord waits for release'),
        ('cancel', 'if(s->cancelled) {', 'if(0 && s->cancelled) {', 'focus loss cancels chord'),
        ('cancel', 's->pending=0; s->inject=0; s->consumed=0; s->passthrough=0; s->cancelled=1;',
         '(void)s;', 'held reconnect cannot save'),
        ('tap', 'out.inject=s->inject;', 'out.inject=0;', 'solo career tap restored'),
        ('double-tap', 's->inject &= ~parts;', '(void)parts;', 'two solo taps stay separate'),
        ('hitch', 'now-s->pendingAt<.15 && parts==chord', 'parts==chord', 'expired prefix cannot become a chord'),
        ('collision', '!chord || blocked ||', '!chord ||', 'menu/guide wins collision'),
    ]
    with tempfile.TemporaryDirectory() as scratch:
        d=Path(scratch)
        cases=('disabled','key','release','pad','cancel','tap','collision','double-tap','hitch')
        for index,item in enumerate([None,*mutations]):
            code=source
            if item:
                case,before,after,failure=item
                assert source.count(before)==1,(before,source.count(before))
                code=source.replace(before,after)
            path=d/f'input{index}.c'; path.write_text(code)
            exe=d/f'input{index}'
            built=subprocess.run(['clang','-std=c11','-O1','-Wall','-Wextra','-Werror','-Wno-unused-parameter',
                                  '-fsanitize=address,undefined','-I'+str(RT),str(path),str(RT/'replay_input_test.c'),'-o',str(exe)],capture_output=True,text=True)
            assert built.returncode==0,built.stderr[-3000:]
            for case in ((item[0],) if item else cases):
                run=subprocess.run([str(exe),case],capture_output=True,text=True,timeout=10)
                output=run.stdout+run.stderr
                print(f'{index}/{case}: {output.strip()}',flush=True)
                assert run.returncode==(1 if item else 0),(run.returncode,output)
                if item: assert 'FAIL replay input: '+failure in output,output
    print('replay_input_test PASS: 9 baseline cases; 12/12 broken controls rejected')


if __name__=='__main__': main()
