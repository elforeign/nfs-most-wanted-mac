"""F10 quit and restart require a fresh down after Enter or Space is released.

The original key branch protects 0/4 combinations from a held key. The fixed
branch protects 4/4, and all four still confirm on release and fresh key-down.
The production confirmation timer runs against a fixture clock. No app opens.
"""
import hashlib
from pathlib import Path
import subprocess
import tempfile

RUNTIME = Path(__file__).resolve().parent
OLD_KEYS = '''    case 0x0D: case 0x20:                                // Enter, Space
        if(!s || strncmp(s->id,"display.stream_",15) || (!repeat && !was_down)) activate(s);
        break;'''
assert hashlib.sha256(OLD_KEYS.encode()).hexdigest() == 'b26d827d10f5ba9061e3e0b1facf3d00396c162562b9d066543935ebd69bc67d'

HARNESS = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static uint64_t host_ticks(void) { return 1000000000; }
void portmenu_status(const char *fmt, ...) { (void)fmt; }
@@CONFIRM@@
typedef struct { const char *id; } Setting;
static double armed_until;
static int confirmed;
static uint8_t swallowed[256];
static void activate(const Setting *s) { confirmed+=confirm_twice(&armed_until,s->id); }
static void key(const Setting *s,uint8_t vk,int down,int repeat) {
    @@TRACKING@@
    if (!down) return;
    switch (vk) {
@@KEYS@@
    }
}
int main(void) {
    const Setting rows[]={{"diag.quit_game"},{"diag.restart_game"}};
    const uint8_t keys[]={0x0D,0x20};
    int held_safe=0, fresh_ok=0;
    for (unsigned i=0;i<2;++i) for (unsigned j=0;j<2;++j) {
        const Setting *row=&rows[i]; uint8_t vk=keys[j];
        armed_until=0; confirmed=0;
        key(row,vk,1,0);
        key(row,vk,1,1);
        key(row,vk,1,0);
        held_safe+=!confirmed;
        key(row,vk,0,0);
        int before=confirmed;
        key(row,vk,1,0);
        fresh_ok+=confirmed==before+1;
        key(row,vk,0,0);
    }
    printf("held safe=%d/4 fresh confirmation=%d/4\n",held_safe,fresh_ok);
    return held_safe!=4 || fresh_ok!=4;
}
'''


def main():
    source = (RUNTIME/'portmenu.m').read_text().split('int portmenu_key(',1)[1]
    keys = source[source.index('    case 0x0D: case 0x20:'):source.index('    case 0x09:')].rstrip()
    tracking = source[source.index('int was_down=swallowed[vk];'):source.index('    if (report_github_active())')]
    app = (RUNTIME/'app.m').read_text()
    start = app.index('static int confirm_twice(')
    confirm = app[start:app.index('\n}',start)+2]
    harness = HARNESS.replace('@@CONFIRM@@',confirm).replace('@@TRACKING@@',tracking)
    with tempfile.TemporaryDirectory(prefix='portmenu-confirm-') as directory:
        root = Path(directory)
        source, binary = root/'confirm.c', root/'confirm'
        for label, branch, expected in [('old',OLD_KEYS,0),('fixed',keys,4)]:
            source.write_text(harness.replace('@@KEYS@@',branch))
            build = subprocess.run(['clang','-std=c11','-Wall','-Werror',str(source),'-o',str(binary)],capture_output=True,text=True)
            assert build.returncode==0,build.stderr
            run = subprocess.run([str(binary)],capture_output=True,text=True)
            print(label,run.stdout.strip())
            assert run.returncode==int(expected!=4) and f'held safe={expected}/4' in run.stdout,run.stderr


if __name__=='__main__':
    main()
