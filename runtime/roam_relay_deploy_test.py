"""Deployment/staging fixture proof; no Docker daemon, network, downloads or installation.

Normal: isolated smoke + deployment limits + dependency staging pass; 12/12 unsafe mutants rejected.
Broken: root guard, writable root, missing memory/task/file/core/log limits, mutable
image validation, unpinned base, unsafe smoke bind/network and missing service
limits each fail the same audit. These are fixture results, never container proof.
"""
from pathlib import Path
import hashlib
import os
import re
import shutil
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
RELAY = RT / 'relay'


def run(args, env=None):
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=60)


def calls(path):
    result = []
    for part in path.read_text().split('@call\n')[1:]:
        result.append(part.split('@end\n')[0].splitlines())
    return result


def fixture(td):
    bins = td / 'bin'
    bins.mkdir()
    # Synthetic local content ID for fixture execution only; no real image is asserted.
    image = 'sha256:' + hashlib.sha256(b'deployment test fixture; no image').hexdigest()
    scripts = {
        'id': '#!/bin/sh\nif [ "${TEST_ROOT:-0}" = 1 ]; then echo 0; else echo 1000; fi\n',
        'uname': '#!/bin/sh\necho Linux\n',
        'docker': '''#!/bin/sh
printf '%s\\n' '@call' "$@" '@end' >> "$FIXTURE_ARGS"
case "$1" in
    inspect) case "$*" in *NetworkMode*) echo none ;; *) echo 0 ;; esac ;;
    logs) echo 'roam_relay: stopped signal 15' ;;
    *) echo fixture-only ;;
esac
''',
    }
    for name, src in scripts.items():
        path = bins / name
        path.write_text(src)
        path.chmod(0o755)
    state = td / 'state'
    state.mkdir(mode=0o700)
    log = td / 'args'
    env = dict(os.environ, PATH=str(bins) + os.pathsep + os.environ['PATH'], FIXTURE_ARGS=str(log),
               RELAY_STATE_DIR=str(state), RELAY_IMAGE=image, RELAY_SMOKE_IMAGE=image, RELAY_BIND='127.0.0.1', TEST_ROOT='0')
    return env, log


def limited(args):
    for flag in ('--user', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                 '--memory=128m', '--memory-swap=128m', '--pids-limit=16', '--ulimit'):
        assert flag in args, ('container limit', flag)
    assert args[args.index('--user') + 1] == '65532:65532', 'container runs as root'
    assert 'nofile=256:256' in args and 'core=0:0' in args, 'file/core limit'
    assert not any(x.startswith(('-v', '--volume', '--privileged')) for x in args), 'container writable mount/privileges'
    mounts = [args[i+1] for i, x in enumerate(args) if x == '--mount']
    assert len(mounts) <= 1 and all(x.startswith('type=bind,src=') and x.endswith(',dst=/var/lib/relay') for x in mounts), 'unexpected durable mount'
    assert '--pull=never' in args, 'implicit image pull'


def audit(rt, env, log):
    relay = rt / 'relay'
    log.write_text('')
    p = run(['sh', str(relay / 'run.sh')], env)
    assert p.returncode == 0, ('valid launcher', p.stderr)
    records = calls(log)
    assert len(records) == 1, 'deployment fixture call count'
    deploy = records[0]
    limited(deploy)
    for flag in ('--restart=on-failure:5', '--stop-timeout=5', '--log-driver=local', 'max-size=4m', 'max-file=3'):
        assert flag in deploy, ('deployment bound', flag)
    assert deploy[deploy.index('--bind') + 1] == '127.0.0.1', 'default bind'
    assert deploy[deploy.index('--port') + 1] == '47800', 'UDP port'
    assert deploy[deploy.index('--max') + 1] == '100', 'service cap'
    for changes, label in (({'TEST_ROOT': '1'}, 'root execution'),
                           ({'RELAY_IMAGE': 'debian:latest'}, 'mutable image'),
                           ({'RELAY_IMAGE': ''}, 'missing image')):
        log.write_text('')
        p = run(['sh', str(relay / 'run.sh')], dict(env, **changes))
        assert p.returncode == 2 and not calls(log), label
    # Default bind is safe even without a configured bind value.
    log.write_text('')
    default_env = dict(env)
    default_env.pop('RELAY_BIND')
    p = run(['sh', str(relay / 'run.sh')], default_env)
    assert p.returncode == 0
    default = calls(log)[0]
    assert default[default.index('--bind') + 1] == '127.0.0.1', 'unset bind'
    log.write_text('')
    p = run(['sh', str(relay / 'check.sh'), '--loopback'], env)
    assert p.returncode == 0 and 'RELAY CONTAINER PASS' in p.stdout, ('check fixture', p.stderr)
    smoke = calls(log)
    containers = [c for c in smoke if c[0] == 'run']
    assert len(containers) == 3, 'relay and two fresh smoke peers'
    for args in containers:
        limited(args)
        assert '--restart=no' in args, 'smoke restart policy'
        assert not any(x.startswith(('-p', '--publish')) for x in args), 'smoke published port'
        network = args[args.index('--network') + 1]
        if '--bind' in args:
            assert network == 'none', 'smoke must isolate network'
            assert args[args.index('--bind') + 1] == '127.0.0.1', 'unsafe smoke bind'
            assert args[args.index('--port') + 1] == '47800', 'smoke port'
            for flag in ('--log-driver=local', 'max-size=4m', 'max-file=3'):
                assert flag in args, ('smoke log bound', flag)
        else:
            assert network.startswith('container:nfsmw-relay-check-'), 'peer escaped namespace'
            assert '--log-driver=none' in args and '/peer' in args, 'peer configuration'
    assert sum(c[0] == 'stop' for c in smoke) == 2 and sum(c[0] == 'start' for c in smoke) == 1, 'restart fixture'
    for args in ([], ['--public'], ['--loopback', '--bind', '0.0.0.0']):
        log.write_text('')
        p = run(['sh', str(relay / 'check.sh'), *args], env)
        assert p.returncode == 2 and not calls(log), 'unsafe smoke option accepted'
    log.write_text('')
    p = run(['sh', str(relay / 'check.sh'), '--loopback'], dict(env, TEST_ROOT='1'))
    assert p.returncode == 2 and not calls(log), 'root smoke execution'
    service = (relay / 'nfsmw-relay.service').read_text()
    settings = dict(re.findall(r'^([A-Za-z]+)=(.*)$', service, re.M))
    expected = {'User': 'nfsmw-relay', 'Group': 'nfsmw-relay', 'ProtectSystem': 'strict',
                'ReadOnlyPaths': '/', 'NoNewPrivileges': 'yes', 'MemoryMax': '128M',
                'MemorySwapMax': '0', 'TasksMax': '16', 'LimitNOFILE': '256', 'LimitCORE': '0',
                'Restart': 'on-failure', 'TimeoutStopSec': '5', 'LogNamespace': 'nfsmw-relay',
                'IPAddressDeny': 'any', 'IPAddressAllow': 'localhost'}
    for key, value in expected.items():
        assert settings.get(key) == value, ('service setting', key)
    assert '--port 47800 --near 32 --max 100' in settings['ExecStart'], 'native service cap/port'
    dockerfile = (relay / 'Dockerfile').read_text()
    bases = re.findall(r'^FROM (\S+) AS (?:build|runtime)$', dockerfile, re.M)
    assert len(bases) == 2 and all(x == '${DEBIAN_IMAGE}@${DEBIAN_DIGEST}' for x in bases), 'unpinned base image'
    assert 'USER 65532:65532' in dockerfile and 'EXPOSE 47800/udp' in dockerfile, 'image user/port'
    assert 'CMD ["--bind", "127.0.0.1"' in dockerfile, 'image loopback default'
    assert dockerfile.rstrip().endswith('FROM runtime AS production'), 'default target includes smoke tooling'
    conf = (relay / 'relay.conf.example').read_text()
    assert re.search(r'^RELAY_BIND=127\.0\.0\.1$', conf, re.M), 'config loopback default'


def stage_proof(td):
    root = td / 'source'
    root.mkdir()
    for name in ('roam_relay_main.c', 'roam_core.c', 'roam_auth.c', 'roam_moderation.c', 'roam_relay.c', 'roam_relay_build.sh', 'roam_relay_cli_test.py'):
        shutil.copy2(RT / name, root / name)
    # Discover current transitive dependencies through the real stage first.
    initial = td / 'initial'
    p = run(['sh', str(RT / 'roam_relay_build.sh'), '--stage', str(initial)])
    assert p.returncode == 0, p.stderr
    for file in initial.rglob('*.h'):
        relative = file.relative_to(initial)
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, root / relative)
    shutil.copytree(RELAY, root / 'relay')
    (root / 'fixture_header.h').write_text('#include "fixture_nested.h"\n')
    (root / 'fixture_nested.h').write_text('#define FIXTURE_DEPENDENCY 1\n')
    source = root / 'roam_relay.c'
    source.write_text('#include "fixture_header.h"\n' + source.read_text())
    (root / 'private-assets.bin').write_bytes(b'not part of relay')
    stage = td / 'staged'
    p = run(['sh', str(root / 'roam_relay_build.sh'), '--stage', str(stage)])
    assert p.returncode == 0, p.stderr
    assert (stage / 'fixture_header.h').is_file() and (stage / 'fixture_nested.h').is_file(), 'transitive header staging'
    assert not (stage / 'private-assets.bin').exists(), 'asset staging'
    assert sorted(f.name for f in stage.glob('*.c')) == ['roam_auth.c', 'roam_core.c', 'roam_moderation.c', 'roam_relay.c', 'roam_relay_main.c'], 'relay TUs'
    p = run(['sh', str(stage / 'build.sh'), str(td / 'staged-relay')])
    assert p.returncode == 0, ('independent staged build', p.stderr[-1500:])
    p = run([str(td / 'staged-relay'), '--max', 'overflow'])
    assert p.returncode == 2, 'staged binary validation'
    # The same assertion must reject a stage whose dependency discovery was removed.
    (stage / 'fixture_nested.h').unlink()
    broken = run(['sh', str(stage / 'build.sh'), str(td / 'broken-stage')])
    assert broken.returncode != 0 and 'fixture_nested.h' in broken.stderr, 'missing header build survived'
    print('broken staged header omission: compiler rejected (1/1)', flush=True)
    print('stage: transitive new headers included; 5 relay TUs; no private asset; independent build PASS', flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix='relay-deploy-') as tmp:
        td = Path(tmp)
        env, log = fixture(td)
        audit(RT, env, log)
        print('deployment fixtures normal PASS (no real containers)', flush=True)
        mutations = [
            ('root', 'run.sh', '[ "$(id -u)" != 0 ]', '[ 1 = 1 ]'),
            ('writable root', 'run.sh', '--read-only ', '--read-only=false '),
            ('memory', 'run.sh', '--memory=128m ', ''),
            ('tasks', 'run.sh', '--pids-limit=16 ', ''),
            ('files', 'run.sh', '--ulimit nofile=256:256 ', ''),
            ('cores', 'run.sh', '--ulimit core=0:0 ', ''),
            ('logs', 'run.sh', '--log-opt max-file=3 ', ''),
            ('mutable image', 'run.sh', "echo 'RELAY_IMAGE must be a content digest' >&2; exit 2", ':'),
            ('unpinned base', 'Dockerfile', '${DEBIAN_IMAGE}@${DEBIAN_DIGEST}', '${DEBIAN_IMAGE}'),
            ('smoke bind', 'check.sh', '--bind 127.0.0.1', '--bind 0.0.0.0'),
            ('smoke network', 'check.sh', '--network none', '--network host'),
            ('native limit', 'nfsmw-relay.service', 'MemoryMax=128M', 'MemoryMax=infinity'),
        ]
        for label, name, old, new in mutations:
            mutant = td / 'mutant'
            if mutant.exists():
                shutil.rmtree(mutant)
            shutil.copytree(RELAY, mutant / 'relay')
            path = mutant / 'relay' / name
            text = path.read_text()
            assert old in text, ('mutation anchor', label)
            path.write_text(text.replace(old, new))
            try:
                audit(mutant, env, log)
            except AssertionError as e:
                print('broken', label + ': rejected:', str(e)[:120], flush=True)
            else:
                raise AssertionError(label + ' mutant survived')
        stage_proof(td)
    print('RELAY DEPLOY PASS: 12/12 unsafe deployment mutants + 1/1 staging omission rejected; Linux/container proof PENDING')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
