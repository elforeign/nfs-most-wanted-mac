"""Public build: the "Check for updates" logic (pub_update_core.h, ported from nfs-most-wanted-mac's update_check.cpp)
against a local JSON fixture (runtime/pub_update_fixture.json); no network, no OS. Each check has a broken control.
Proof: 5 good checks pass / 5 broken controls fail.
The driver is built against a copy of the header with one line broken and the check must fail (test_controls_fail).
Run: python3 runtime/pub_update_test.py"""
import subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRIVER = r'''
#include "pub_update_core.h"
static const char *app_ver; static int jobs, fetches; static char target_version[512];
static int ver(char *o, size_t c) { snprintf(o, c, "%s", app_ver); return app_ver[0] != 0; }
static int fetch(void) { ++fetches; return 1; }          /* the answer arrives later via pu_done */
static int start(const char *u) { ++jobs; snprintf(target_version, sizeof target_version, "%s", u); return 1; }
int main(int argc, char **argv) {
    const char *cmd = argv[1];
    if (!strcmp(cmd, "cmp")) { printf("%d\n", pu_compare_versions(argv[2], argv[3])); return 0; }
    app_ver = argv[2];
    FILE *f = fopen(argv[3], "rb"); static char buf[8192]; size_t n = f ? fread(buf, 1, sizeof buf - 1, f) : 0; buf[n] = 0;
    char t[128];
    pu_status(t, sizeof t); printf("0:%s\n", t);
    pu_press(ver, fetch, start);
    pu_status(t, sizeof t); printf("1:%s|fetches=%d\n", t, fetches);   /* checking */
    pu_press(ver, fetch, start);                                          /* a press while checking does nothing */
    printf("fetches_after_second=%d\n", fetches);
    pu_done(!strcmp(cmd, "ok"), buf);                                   /* "fail": a network failure */
    pu_status(t, sizeof t); printf("2:%s\n", t);
    pu_press(ver, fetch, start);                                          /* newer: starts preparation; else checks again */
    printf("jobs=%d version=%s fetches=%d\n", jobs, target_version, fetches);
    return 0;
}
'''
FIXTURE = ROOT / 'pub_update_fixture.json'


def build(patch=None):
    tmp = tempfile.mkdtemp(dir='/tmp')
    inc = Path(tmp)
    h = (ROOT / 'pub_update_core.h').read_text()
    if patch:
        assert patch[0] in h, patch
        h = h.replace(patch[0], patch[1], 1)
    (inc / 'pub_update_core.h').write_text(h)
    (inc / 'd.c').write_text(DRIVER)
    cc = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-I', str(inc), str(inc / 'd.c'),
                         '-o', str(inc / 'd')], capture_output=True, text=True)
    assert cc.returncode == 0, cc.stderr
    return str(inc / 'd')


def cmp_(exe, a, b):
    return int(subprocess.run([exe, 'cmp', a, b], capture_output=True, text=True, check=True).stdout)


def flow(exe, mode, ver, fixture=FIXTURE):
    out = subprocess.run([exe, mode, ver, str(fixture)], capture_output=True, text=True, check=True).stdout
    return dict(l.split(':', 1) if l[0] in '012' and l[1] == ':' else l.split('=', 1) for l in out.splitlines())


def ok_versions(exe):
    return (cmp_(exe, 'v4.0.0', '4.0.0-rc1') > 0 and cmp_(exe, 'v4.0.0', '3.0.4') > 0 and cmp_(exe, '3.10', '3.9') > 0
            and cmp_(exe, '3.9', '3.10') < 0 and cmp_(exe, '4.0', '4.0.0') == 0 and cmp_(exe, 'v4.0.0', '4.0.0') == 0)


def ok_newer_starts_update(exe):   # fixture has nested decoy tags: the release's own tag is used
    r = flow(exe, 'ok', '3.0.4')
    return r['2'] == '4.0.0 is out: Update now' and r['jobs'].startswith('1 version=4.0.0 fetches=1')


def ok_up_to_date_no_job(exe):
    r = flow(exe, 'ok', '4.0.0')
    return r['2'] == 'Up to date (4.0.0)' and r['jobs'].startswith('0 ') and r['jobs'].endswith('fetches=2')


def ok_failure_message(exe):
    r = flow(exe, 'fail', '3.0.4')
    return r['2'] == 'No answer: press to retry' and r['jobs'].startswith('0 ') and r['jobs'].endswith('fetches=2')


def ok_press_while_checking_ignored(exe):
    r = flow(exe, 'ok', '3.0.4')
    return r['1'] == 'Checking...|fetches=1' and r['fetches_after_second'] == '1'


CHECKS = {
    'numeric_compare': (ok_versions, ('const long x = strtol(a, &ea, 10), y = strtol(b, &eb, 10);\n        if (x != y) return x < y ? -1 : 1;',
                                      'const long x = strtol(a, &ea, 10), y = strtol(b, &eb, 10);\n        if (x != y) return strcmp(a, b);')),
    'newer_starts_update': (ok_newer_starts_update, ('pu_state = pu_compare_versions(pu_latest, pu_current) > 0 ? PU_NEWER : PU_UP_TO_DATE;', 'pu_state = PU_UP_TO_DATE;')),
    'up_to_date_no_job': (ok_up_to_date_no_job, ('> 0 ? PU_NEWER', '>= 0 ? PU_NEWER')),
    'network_failure_message': (ok_failure_message, ('if (!ok || !body ||', 'if (!body ||')),
    'press_while_checking_ignored': (ok_press_while_checking_ignored, ('if (pu_state == PU_CHECKING || pu_busy() || pu_state == PU_READY) return;', 'if (pu_busy() || pu_state == PU_READY) return;')),
}


def test_good():
    exe = build()
    for name, (ok, _) in CHECKS.items():
        assert ok(exe), name


def test_controls_fail():
    for name, (ok, patch) in CHECKS.items():
        assert not ok(build(patch)), ('the broken control still passed', name)
        print(name, 'good PASS, broken FAIL')


if __name__ == '__main__':
    test_good()
    test_controls_fail()
    print('PUB UPDATE TEST PASS')
