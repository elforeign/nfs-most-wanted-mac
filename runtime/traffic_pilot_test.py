"""Bare production policy: 21,749 private + 21,749 public checks, zero failures.
Broken shared-count/fixed/percentage/zero/Off/forced controls fail
3,271/2,485/2,682/288/192/1,136 checks respectively.
An unrelated-selection control fails 384 checks.
Boot: 779 private + 432 public checks, zero failures; missing guard/Off bypass/
public activation fail 281/49/396 checks. No game, assets or source I/O.
Compiled missing-row/default/preset controls live in private_cars_test.py.
"""
from pathlib import Path
import ctypes as C
import itertools
import os
import subprocess
import tempfile

RT = Path(__file__).resolve().parent


class Quota(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in
                ('native_count', 'pilot_count', 'fixed_limit', 'percent_limit', 'population')]


class Input(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('original_key', 'native_key', 'pilot_key', 'choice')]
    _fields_ += [(n, C.c_int) for n in ('enabled', 'native_available', 'pilot_available', 'force')]
    _fields_ += [('quota', Quota)]


class Choice(C.Structure):
    _fields_ = [('key', C.c_uint32), ('status', C.c_int)]


STUBS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "traffic_pilot.h"
#include "private_cars.h"
static unsigned discovery, guards;
static int asset, scratch;
const PrivateCar *private_cars_traffic_get(void) {
    ++discovery;
    static PrivateCar car;
    memset(&car,0,sizeof car);
    car.id=asset==2?159:TRAFFIC_PILOT_TYPE; car.selected=asset!=3;
    strcpy(car.name,asset==4?"WRONG":TRAFFIC_PILOT_NAME);
    strcpy(car.donor,asset==5?"WRONG":TRAFFIC_PILOT_DONOR);
    return asset?&car:NULL;
}
int car85_profile_scratch(void) { ++guards; return scratch; }
int car85_traffic_scratch_eligible(void) { return 0; }
#ifdef TEST_MAIN
int main(int argc,char **argv) {
    if(argc!=4) return 9;
    int setting=atoi(argv[1]); asset=atoi(argv[2]); scratch=atoi(argv[3]);
    int rc=traffic_pilot_boot(setting);
    int again=traffic_pilot_boot(!setting);
    printf("%d %d %d %d %d %u %u %d\n",rc,again,traffic_pilot_enabled(),
        traffic_pilot_available(),traffic_pilot_force(),discovery,guards,!!traffic_pilot_error()[0]);
    if(rc) fprintf(stderr,"diagnostic failure: %s\n",traffic_pilot_error());
    return 0;
}
#endif
'''


def compile_c(tmp, source, public=False, main=False):
    (tmp/'policy.c').write_text(source)
    (tmp/'stubs.c').write_text(STUBS)
    output = tmp/('boot' if main else 'policy.dylib')
    command = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable',
               '-O1', '-I'+str(RT), str(tmp/'policy.c'), str(tmp/'stubs.c'), '-o', str(output)]
    command += ['-DTEST_MAIN'] if main else ['-dynamiclib']
    if public:
        command += ['-DNFS_PUBLIC=1']
    p = subprocess.run(command, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    return output


def reference_quota(q):
    total = q.native_count + q.pilot_count
    bounds = []
    if q.fixed_limit:
        bounds.append(q.fixed_limit)
    if q.percent_limit:
        bounds.append(max(1, q.population * q.percent_limit // 100))
    return all(total < bound for bound in bounds)


def reference_choice(i, public):
    if public or (not i.enabled and i.force == 0):
        return i.original_key, 0
    if i.force not in (0, 1, 2):
        return 0, 3
    choices = {}
    if i.native_key and i.native_available:
        choices[1] = i.native_key
    if i.enabled and i.pilot_key and i.pilot_available:
        choices[2] = i.pilot_key
    forced_key = i.native_key if i.force == 1 else i.pilot_key
    if i.force and i.force not in choices:
        return 0, 3
    if not i.original_key or (i.force == 0 and i.original_key not in (i.native_key, i.pilot_key)):
        return i.original_key, 0
    if i.native_key and i.native_key == i.pilot_key:
        return 0, 3 if i.force else 2
    if not reference_quota(i.quota):
        return 0, 2
    if i.force:
        return forced_key, 1
    if not choices:
        return 0, 2
    return list(choices.values())[i.choice % len(choices)], 1


def policy_checks(path, public=False):
    lib = C.CDLL(str(path))
    lib.traffic_pilot_quota_allows.argtypes = [C.POINTER(Quota)]
    lib.traffic_pilot_choose.argtypes = [C.POINTER(Input)]
    lib.traffic_pilot_choose.restype = Choice
    checks = failures = 0
    for n, p, fixed, percent, population in itertools.product(
            range(6), range(6), range(6), (0, 1, 25, 50, 99, 100, 101, 200), range(8)):
        q = Quota(n, p, fixed, percent, population)
        checks += 1
        failures += lib.traffic_pilot_quota_allows(C.byref(q)) != reference_quota(q)
    # Wide arithmetic, percentage minimum-one and zero-unlimited boundaries.
    for n, p, fixed, percent, population in itertools.product(
            (0, 1, 0xffffffff), repeat=5):
        q = Quota(n, p, fixed, percent, population)
        checks += 1
        failures += lib.traffic_pilot_quota_allows(C.byref(q)) != reference_quota(q)
    q = Quota()
    for original, native, pilot, enabled, nav, pav, force, ordinal, capped in itertools.product(
            (0, 11, 22, 33), (0, 11), (0, 11, 22), (0, 1), (0, 1), (0, 1),
            (-1, 0, 1, 2, 3), (0, 1, 2, 0xffffffff), (0, 1)):
        q = Quota(1, capped, 2, 50, 4)
        i = Input(original, native, pilot, ordinal, enabled, nav, pav, force, q)
        got = lib.traffic_pilot_choose(C.byref(i))
        checks += 1
        failures += (got.key, got.status) != reference_choice(i, public)
    checks += 2
    failures += lib.traffic_pilot_quota_allows(None) != 0
    got = lib.traffic_pilot_choose(None)
    failures += (got.key, got.status) != (0, 0)
    return checks, failures


def boot_checks(exe, public=False):
    base = {k: v for k, v in os.environ.items() if k not in ('BOOT_DIAG_TRAFFIC_CAR', 'BOOT_DIAG')}
    checks = failures = 0
    for enabled, asset, scratch, diag, subject in itertools.product(
            (0, 1), range(6), (0, 1), (None, '', '/scratch/diag'),
            (None, '', 'traf4dseda', 'trafficsedan', 'TRAFFICSEDAN', 'unknown')):
        env = dict(base)
        if diag is not None:
            env['BOOT_DIAG'] = diag
        if subject is not None:
            env['BOOT_DIAG_TRAFFIC_CAR'] = subject
        p = subprocess.run([str(exe), str(enabled), str(asset), str(scratch)],
                           env=env, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-1000:]
        got = tuple(map(int, p.stdout.split()))
        guards = int(subject is not None and bool(diag))
        forced = 0
        fail = False
        discovery = 0
        available = False
        if subject is not None:
            if not diag or not scratch or subject not in ('traf4dseda', 'trafficsedan'):
                fail = True
            else:
                forced = 1 if subject == 'traf4dseda' else 2
        if not fail:
            discovery = enabled
            available = enabled and asset == 1
            fail = forced == 2 and not available
        expected = ((-1 if fail else 0),)*2 + (enabled, int(available), forced, discovery, guards, int(fail))
        if public:
            expected = (0,)*8
        checks += 1
        failures += got != expected
        if fail and not public:
            checks += 1
            failures += 'diagnostic failure:' not in p.stderr
    return checks, failures


def main():
    source = (RT/'traffic_pilot.c').read_text()
    with tempfile.TemporaryDirectory(prefix='traffic-policy-') as directory:
        root = Path(directory)
        variants = {
            'production': source,
            'shared-count': source.replace('(uint64_t)q->native_count + q->pilot_count', '(uint64_t)q->native_count'),
            'fixed-cap': source.replace('q->fixed_limit && count >= q->fixed_limit', '0'),
            'percentage-cap': source.replace('if (q->percent_limit)', 'if (0)'),
            'zero-refusal': source.replace('if (!in->original_key) return out;', ''),
            'off-bypass': source.replace('!in->enabled && in->force == TRAFFIC_PILOT_NORMAL', '0'),
            'forced-fallback': source.replace('in->force == TRAFFIC_PILOT_FORCE_PILOT && !pilot', '0'),
            'unrelated-selection': source.replace('if (in->force == TRAFFIC_PILOT_NORMAL &&\n        in->original_key != in->native_key && in->original_key != in->pilot_key) return out;', ''),
        }
        for name, actual in variants.items():
            tmp = root/name
            tmp.mkdir()
            assert name == 'production' or actual != source
            checks, failures = policy_checks(compile_c(tmp, actual))
            print(name, 'policy checks=', checks, 'failures=', failures)
            assert (failures == 0) == (name == 'production')
        tmp = root/'public'
        tmp.mkdir()
        checks, failures = policy_checks(compile_c(tmp, source, public=True), public=True)
        print('public policy checks=', checks, 'failures=', failures)
        assert failures == 0
        for name, actual, public in (
                ('boot', source, False), ('public-boot', source, True),
                ('guard-removed', source.replace('!diag || !*diag || !car85_profile_scratch()', '0'), False),
                ('off-discovery', source.replace('if (enabled) {', 'if (1) {'), False),
                ('public-activation', source.replace('#if defined(NFS_PUBLIC) && NFS_PUBLIC', '#if 0')
                 .replace('#if !defined(NFS_PUBLIC) || !NFS_PUBLIC', '#if 1'), True)):
            tmp = root/name
            tmp.mkdir()
            checks, failures = boot_checks(compile_c(tmp, actual, public=public, main=True), public=public)
            print(name, 'boot checks=', checks, 'failures=', failures)
            assert (failures == 0) == (name in ('boot', 'public-boot'))
    print('traffic pilot PASS')


if __name__ == '__main__':
    main()
