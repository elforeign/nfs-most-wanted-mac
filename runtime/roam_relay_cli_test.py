"""Loopback CLI regression and mutation proof (normal passes; parsing/shutdown mutants fail).

Pre-change measurement: 3 malformed/dangling cases started (exit 0); SIGTERM exit -15.
Socket instrumentation proves invalid input exits 2 before calling socket, even with an occupied port.
Normal: 54 invalid cases, zero socket calls; two orderly signals exit 0; port reuse and fresh admission.
Broken: unchecked numeric parsing and ignored shutdown must each fail their named assertion.
"""
from pathlib import Path
import argparse
import os
import re
import selectors
import signal
import socket
import subprocess
import tempfile
import time

RT = Path(__file__).resolve().parent
CF = ['cc', '-std=c11', '-D_DEFAULT_SOURCE', '-D_DARWIN_C_SOURCE', '-Wall', '-Wextra', '-Werror']
LIBS = ['-lm', '-lcrypto'] if os.uname().sysname == 'Linux' else ['-framework', 'Security', '-framework', 'CoreFoundation']
# Use the actual client codec/session so admission proof follows wire/cookie changes after merge.
PEER = r'''
#include "roam_core.h"
#include <arpa/inet.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    int port = atoi(argv[1]);
    if (port < 1 || port > 65535) return 2;
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in local = {.sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_LOOPBACK)};
    struct sockaddr_in dest = local; dest.sin_port = htons((uint16_t)port);
    if (fd < 0 || bind(fd, (struct sockaddr *)&local, sizeof local)) return 1;
    RoamClient *c = calloc(1, sizeof *c);
    if (!c) return 1;
    roam_client_init(c, 1, "smoke");
    for (int i = 0; i < 100 && !c->welcomed; ++i) {
        uint8_t b[ROAM_MAX_DGRAM];
        int n = roam_client_hello(c, b);
        if (n > 0) sendto(fd, b, (size_t)n, 0, (struct sockaddr *)&dest, sizeof dest);
        struct timespec wait = {.tv_nsec = 50000000}; nanosleep(&wait, NULL);
        for (int j = 0; j < 32; ++j) {
            struct sockaddr_in from; socklen_t len = sizeof from;
            n = (int)recvfrom(fd, b, sizeof b, MSG_DONTWAIT, (struct sockaddr *)&from, &len);
            if (n < 0) break;
            if (from.sin_addr.s_addr == dest.sin_addr.s_addr && from.sin_port == dest.sin_port)
                roam_client_input(c, b, n, (uint32_t)(i * 50));
        }
    }
    int ok = c->welcomed;
    free(c); close(fd);
    puts(ok ? "fresh admission PASS" : "fresh admission FAIL");
    return ok ? 0 : 1;
}
'''


def command(args, **kwargs):
    p = subprocess.run(args, capture_output=True, text=True, timeout=60, **kwargs)
    assert p.returncode == 0, (args, p.returncode, (p.stdout + p.stderr)[-2500:])
    return p.stdout


def build_peer(output):
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 'peer.c'
        src.write_text(PEER)
        command(CF + [str(RT / 'roam_auth.c')] + ['-I', str(RT), str(src), str(RT / 'roam_core.c'), *LIBS, '-o', str(output)])


def build(td, source, name):
    main = Path(td) / (name + '.c')
    obj = Path(td) / (name + '.o')
    exe = Path(td) / name
    main.write_text(source)
    command(CF + ['-I', str(RT), '-Dsocket=tracked_socket', '-c', str(main), '-o', str(obj)])
    tracker = Path(td) / 'tracker.c'
    tracker.write_text('''#include <sys/socket.h>
#include <stdio.h>
#include <stdlib.h>
int tracked_socket(int a, int b, int c) {
    const char *p = getenv("RELAY_TEST_SOCKET_MARKER");
    if (p) { FILE *f = fopen(p, "a"); if (f) { fputs("socket\\n", f); fclose(f); } }
    return socket(a, b, c);
}
''')
    command(CF + [str(RT / 'roam_auth.c'), str(RT / 'roam_moderation.c'), str(obj), str(tracker), str(RT / 'roam_core.c'), str(RT / 'roam_relay.c'),
                  *LIBS, '-o', str(exe)])
    return exe


def invalid_cases(exe, td):
    cases = [['--unknown'], ['--unknown', '1'], ['--bind', 'localhost'], ['--bind', '::1'],
             ['--bind', '127.0.0.999'], ['--port', '1', '--port', '2'], ['stray']]
    for opt in ('--bind', '--port', '--near', '--max', '--seconds'):
        cases.append([opt])
    for opt, outside in (('--port', ('65536',)), ('--near', ('0', '33')),
                         ('--max', ('0', '65001')), ('--seconds', ('86401',))):
        for value in ('', '-1', '+1', ' 1', '1 ', '1x', '1.0', '0x10', '9' * 200, *outside):
            cases.append([opt, value])
    marker = Path(td) / 'socket-marker'
    env = dict(os.environ, RELAY_TEST_SOCKET_MARKER=str(marker))
    # Also check invalid trailing options on a port already occupied by a loopback socket.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as occupied:
        occupied.bind(('127.0.0.1', 0))
        port = str(occupied.getsockname()[1])
        for args in cases:
            argv = [str(exe), '--port', port, *args] if '--port' not in args else [str(exe), *args]
            if '--seconds' not in args:
                argv += ['--seconds', '1']
            p = subprocess.run(argv,
                               capture_output=True, text=True, env=env, timeout=15)
            assert p.returncode == 2 and 'listening' not in p.stdout and not marker.exists(), (
                'validation-before-bind', args, p.returncode, p.stdout, p.stderr)
    for path, request in (('x'*200, 'list 0'), ('x', ''), ('x', 'x'*160), ('x', 'list 0\n'), ('x', 'list 0\t')):
        p = subprocess.run([str(exe), '--control', path, '--request', request], capture_output=True, text=True, env=env, timeout=15)
        assert p.returncode == 2 and not marker.exists(), 'local-control validation before socket'
    print('validation-before-bind:',  len(cases), 'invalid cases rejected; socket calls 0', flush=True)


def start(exe, port=0, extra=()):
    p = subprocess.Popen([str(exe), '--port', str(port), *extra], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(p.stdout, selectors.EVENT_READ)
            assert sel.select(15), 'startup timeout'
        line = p.stdout.readline()
        m = re.search(r'listening port (\d+) near (\d+) max (\d+) seconds (\d+)', line)
        assert m and 0 < int(m[1]) <= 65535, ('settings/actual port', line, p.poll())
        return p, int(m[1]), tuple(map(int, m.group(2, 3, 4)))
    except BaseException:
        if p.poll() is None:
            p.kill()
        p.wait()
        raise


def stop(p, sig):
    try:
        before = time.monotonic()
        p.send_signal(sig)
        try:
            out, err = p.communicate(timeout=5)
        except subprocess.TimeoutExpired as e:
            raise AssertionError('orderly signal cleanup timed out') from e
        assert p.returncode == 0 and f'stopped signal {sig}' in out, (
            'orderly signal cleanup', p.returncode, out, err)
        print('orderly signal cleanup:', sig, 'exit 0;', round(time.monotonic() - before, 3), 's', flush=True)
    finally:
        if p.poll() is None:
            p.kill()
        p.wait()


def lifecycle(exe, peer):
    p, port, settings = start(exe)
    try:
        assert settings == (32, 100, 0), ('defaults', settings)
        command([str(peer), str(port)])
        # A second process must fail rather than silently sharing the UDP endpoint.
        conflict = subprocess.run([str(exe), '--port', str(port), '--seconds', '1'],
                                  capture_output=True, text=True, timeout=15)
        assert conflict.returncode == 1 and 'bind' in conflict.stderr, 'port conflict'
        stop(p, signal.SIGTERM)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
    # The old endpoint is reusable and a newly created client can join the fresh relay state.
    p, again, _ = start(exe, port)
    try:
        assert again == port
        command([str(peer), str(port)])
        stop(p, signal.SIGINT)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
    out = command([str(exe), '--port', '0', '--near', '1', '--max', '1', '--seconds', '1'])
    assert 'near 1 max 1 seconds 1' in out and 'stopped signal 0' in out
    print('restart: same port reused; fresh admission before/after PASS; finite duration PASS', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-peer', type=Path)
    parser.add_argument('--binary', type=Path)
    args = parser.parse_args()
    if args.build_peer:
        build_peer(args.build_peer)
        return 0
    if args.binary:
        # Test a real standalone build, as well as the instrumented mutation binaries below.
        with tempfile.TemporaryDirectory() as td:
            peer = Path(td) / 'peer'
            build_peer(peer)
            invalid_cases(args.binary, td)
            lifecycle(args.binary, peer)
        return 0
    with tempfile.TemporaryDirectory(prefix='relay-cli-') as td:
        peer = Path(td) / 'peer'
        build_peer(peer)
        exe = Path(td) / 'standalone'
        command(['sh', str(RT / 'roam_relay_build.sh'), str(exe)])
        invalid_cases(exe, td)
        lifecycle(exe, peer)
        src = (RT / 'roam_relay_main.c').read_text()
        normal = build(td, src, 'normal')
        invalid_cases(normal, td)
        begin = src.index('    unsigned n = 0;', src.index('static int number('))
        end = src.index('\n}\n', begin)
        unchecked = src[:begin] + '    (void)lo; (void)hi; *out = atoi(s); return 1;' + src[end:]
        unchecked = '#include <stdlib.h>\n' + unchecked
        mutant = build(td, unchecked, 'unchecked')
        try:
            invalid_cases(mutant, td)
        except AssertionError as e:
            assert 'validation-before-bind' in str(e), str(e)
            print('broken unchecked parser: rejected by validation-before-bind', flush=True)
        else:
            raise AssertionError('unchecked parser mutant survived')
        assert src.count('while (!stopping) {') == src.count('if (stopping) break;') == 1
        mutant = build(td, src.replace('while (!stopping) {', 'while (1) {')
                       .replace('if (stopping) break;', 'if (0) break;'), 'shutdown')
        p, _, _ = start(mutant)
        try:
            stop(p, signal.SIGTERM)
        except AssertionError as e:
            assert 'orderly signal cleanup' in str(e), str(e)
            print('broken shutdown: rejected by orderly signal cleanup deadline', flush=True)
        else:
            raise AssertionError('shutdown mutant survived')
    print('RELAY CLI PASS: standalone + validation + lifecycle; 2/2 mutants rejected')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
