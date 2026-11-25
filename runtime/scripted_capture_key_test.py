"""Retail capture key delivery: original 4 checks / 2 failures; fixed 4 / 0.
Compile the production script parser and physical key map without a window.
The pinned original table silently drops F12; F10 remains a positive control.
"""
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
ORIGINAL = '{"F10", 0x79, 0x44}, {"TAB", 0x09, 0x0F}};'
assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == '8eb2ca02f519c413db8558939d1d2c4f111404e430e21269e1fe522537519b9a'
source = (RT / 'user32.m').read_text()
table = re.search(r'typedef struct \{ uint8_t vk, scan; \} KeyMap;\n.*?^\};', source, re.S | re.M)[0]
parser = source[source.index('static void press('):source.index('static const char *fe_package_in_control(void);')]
current = '{"F10", 0x79, 0x44}, {"F12", 0x7B, 0x58}, {"TAB", 0x09, 0x0F}};'
assert current in parser
prelude = r'''
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static int diag_on, calls, last_down;
static uint32_t focus_hwnd;
static uint8_t last_vk, last_scan;
static void key_event(uint32_t hwnd, uint8_t vk, uint8_t scan, int down, int repeat) {
    (void)hwnd; (void)repeat; ++calls; last_vk=vk; last_scan=scan; last_down=down;
}
static int portmenu_char(uint8_t c) { (void)c; return 0; }
static void post(uint32_t h, uint32_t m, uint32_t w, uint32_t l) { (void)h; (void)m; (void)w; (void)l; }
static void diag_event(const char *fmt, ...) { (void)fmt; }
void diag_scripted_key(uint64_t frame) { (void)frame; }
'''
main = r'''
int main(void) {
    int checks=0, failures=0;
    const char *names[]={"F12", "F10"};
    const unsigned codes[]={0x6F, 0x6D};
    for (int k=0;k<2;++k) for (int down=1;down>=0;--down) {
        calls=0; press(names[k],down,123);
        ++checks;
        if(calls!=1 || last_vk!=mac_keys[codes[k]].vk ||
           last_scan!=mac_keys[codes[k]].scan || last_down!=down) ++failures;
    }
    printf("%d checks / %d failures\n",checks,failures);
    return failures ? 1 : 0;
}
'''
with tempfile.TemporaryDirectory(prefix='capture-key-') as tmp:
    src, exe = Path(tmp) / 'key.c', Path(tmp) / 'key'
    for old in (True, False):
        src.write_text(prelude + table + (parser.replace(current, ORIGINAL) if old else parser) + main)
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', str(src), '-o', str(exe)], check=True, capture_output=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
        print(('original: ' if old else 'fixed: ') + result.stdout.strip())
        assert result.stdout.strip() == f'4 checks / {2 if old else 0} failures'
        assert result.returncode == int(old), result.stderr
