"""Production budget proof: online cap 60 fps, maximum 6 spawned cars.
Normal and deliberate cap/limit/pacing controls are compiled from production sources.
Proof: normal 1027/0 failures; original d5c6f71e hook 957/5 (measured once with this harness);
owner removed 969/2; veto removed 1019/3; unlist removed 1027/2; name slot removed 1027/52;
resource/body reasons removed 1027/1 each. Full-cache/off-road recovery covered.
Friend admission extension: e08e62b4 production with this harness 1054 checks/6 failures,
cop refused/kept=0, traffic evicted/spawned=1; fixed 1048/0, both outcomes=1.
New controls: friend filter removed 1054/6; class removed 1054/2;
traffic veto removed 1052/2; traffic requester removed 1048/4.
Cap removed 1027/2; limit removed 1063/199; pacing removed 1027/8; lost local 1027/2; signed deadline 1017/19.
Recurring controls restore the decoded defects without requiring historical Git objects.
Signed-deadline control covers virgin timers at 30 days, the signed boundary,
wrap-crossing lifecycle/retry waits, and armed retries after a 30-day pause.
Loopback fixture uses only 127.0.0.1; optional .metal-cache/roam-loopback.request holds 2..240 seconds.
The fixture never launches a game; a guarded game may connect during its requested lifetime.
"""
from pathlib import Path
import subprocess
import tempfile
RT = Path(__file__).resolve().parent
CF = ['clang', '-std=c11', '-D_DEFAULT_SOURCE', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror', '-g',
      '-fsanitize=address,undefined', '-I', str(RT), '-I', str(RT.parent / 'recomp')]
def main():
    mutations = {
        'normal': None,
        'cap_removed': ('roam_online.h', 'return online &&', 'return 0 && online &&'),
        'limit_removed': ('roam_game.c', '#define ROAM_CARS ROAM_CAR_BUDGET', '#define ROAM_CARS 32'),
        'pacing_removed': ('roam_online.h', '#define ROAM_LIFECYCLE_MS 200', '#define ROAM_LIFECYCLE_MS 0'),
        'lost_local': ('roam_game.c', 'car->used && !roam_vehicle_listed(car->sim + 0x80)',
                       'car->used && (!has_local || !roam_vehicle_listed(car->sim + 0x80))'),
        'signed_deadline': ('roam_game.c',
                            'if ((uint32_t)(now - wait->since) >= wait->delay) wait->delay = 0;\n    return wait->delay != 0;',
                            'return (int32_t)(now - (wait->since + wait->delay)) < 0;'),
        'cache_owner_removed': ('roam_game.c', 'M32(p + 0x24) = roam_cache;', 'M32(p + 0x24) = 0;'),
        'cache_veto_removed': ('roam_game.c', 'RET(0, 2); // __thiscall', 'RET(1, 2); // __thiscall'),
        'friend_filter_removed': ('roam_game.c', 'if (arg(c, 1) == roam_cache)', 'if (0 && arg(c, 1) == roam_cache)'),
        'friend_class_removed': ('roam_game.c', 'M32(vehicle + 0x94) != 1', '0'),
        'traffic_veto_removed': ('roam_game.c', 'RET(eligible, 2);', 'RET(eligible | 1u, 2);'),
        'traffic_requester_removed': ('roam_game.c', 'uint32_t args[2] = {vehicle, traffic + 8};',
                                      'uint32_t args[2] = {vehicle, roam_cache};'),
        'cache_unlist_removed': ('roam_game.c', 'roam_call(c, roam_cache + 8, 0x004258D0u, 0, NULL);', '(void)c;'),
        'cache_name_slot_removed': ('roam_game.c', 'M32(vt + 4) = host_fn(roam_cache_name, "roam cache name");',
                                    '(void)host_fn(roam_cache_name, "roam cache name"); M32(vt + 4) = 0;'),
        'resource_reason_removed': ('roam_game.c', 'roam_spawn_reason = "vehicle-cache-resources";', 'roam_spawn_reason = "unknown";'),
        'body_reason_removed': ('roam_game.c', 'roam_spawn_reason = "rigid-body-limit";', 'roam_spawn_reason = "unknown";'),
    }
    with tempfile.TemporaryDirectory(prefix='roam-budget-') as tmp:
        td = Path(tmp)
        for name, mutant in mutations.items():
            for file in ['roam_budget_test.c', 'roam_switch_test.c', 'roam_game.c', 'roam_online.h']:
                src = (RT / file).read_text()
                if mutant and mutant[0] == file:
                    assert src.count(mutant[1]) == 1
                    src = src.replace(mutant[1], mutant[2])
                # Removing storage capacity alone should not defeat the independent selection bound.
                if name == 'limit_removed' and file == 'roam_online.h':
                    src = src.replace('#define ROAM_CAR_BUDGET 6', '#define ROAM_CAR_BUDGET 32')
                (td / file).write_text(src)
            exe = td / name
            p = subprocess.run(CF + [str(RT / 'roam_auth.c')] + (['-framework', 'Security', '-framework', 'CoreFoundation'] if __import__('sys').platform == 'darwin' else ['-lcrypto', '-lm']) + ['-DROAM_FACTORY_DIAG_TEST'] + [str(td / 'roam_budget_test.c'), str(RT / 'roam_core.c'), '-o', str(exe)], capture_output=True, text=True)
            assert p.returncode == 0, p.stderr[-3000:]
            p = subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
            print(name + ': ' + p.stdout.strip(), flush=True)
            assert p.returncode == (0 if name == 'normal' else 1), p.stderr[-2500:]
        exe = td / 'loopback'
        p = subprocess.run(CF + [str(RT / 'roam_auth.c')] + (['-framework', 'Security', '-framework', 'CoreFoundation'] if __import__('sys').platform == 'darwin' else ['-lcrypto', '-lm']) + [str(RT / 'roam_moderation.c'), str(RT / 'roam_loopback_fixture.c'), str(RT / 'roam_core.c'),
                           str(RT / 'roam_relay.c'), '-o', str(exe)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-3000:]
        request = RT.parent / '.metal-cache/roam-loopback.request'
        seconds = 2
        if request.exists():
            seconds = max(2, min(240, int(request.read_text())))
            request.unlink()
        p = subprocess.run([str(exe), str(seconds)], capture_output=True, text=True, timeout=seconds+10)
        print(p.stdout.strip(), flush=True)
        if p.returncode == 3:
            print('SOCKET PENDING: run on a socket-enabled host', flush=True)
            return 3
        assert p.returncode == 0, p.stderr[-2500:]
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
