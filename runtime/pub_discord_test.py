"""Public build: Discord status (pub_discord.h, ported from nfs-most-wanted-mac / PR 9 by prex-xo) against a fake
Discord (a Unix socket server at $TMPDIR/discord-ipc-0; never the real Discord, never the network).

Every check ships with a broken control: the same driver is built against a copy of pub_discord.h with one line broken
(CONTROLS below), and the check must FAIL on it; test_controls_fail_when_broken asserts that. Measured numbers (good vs
broken) are printed with `pytest -s`."""
import subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DRIVER = r'''
#define DISCORD_RETRY_S 1
#include "pub_discord.h"
#include <errno.h>
#include <sys/resource.h>

static const char *mode;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static char seen[4096];
static int accepts;
static void note(const char *s) { pthread_mutex_lock(&mu); strcat(seen, s); strcat(seen, " "); pthread_mutex_unlock(&mu); }
static int has(const char *s) { pthread_mutex_lock(&mu); int r = strstr(seen, s) != NULL; pthread_mutex_unlock(&mu); return r; }

static int frame(int fd, uint32_t *op, char *buf, size_t size) {
    uint8_t h[8];
    if (recv(fd, h, 8, MSG_WAITALL) != 8) return -1;
    *op = h[0] | h[1] << 8 | (uint32_t)h[2] << 16 | (uint32_t)h[3] << 24;
    uint32_t len = h[4] | h[5] << 8 | (uint32_t)h[6] << 16 | (uint32_t)h[7] << 24;
    if (len >= size || recv(fd, buf, len, MSG_WAITALL) != (ssize_t)len) return -1;
    buf[len] = 0;
    return 0;
}
static void reply(int fd, uint32_t op, const char *json) { ds_write(fd, op, json); }

static void *server(void *arg) {
    const int ls = *(int *)arg;
    for (;;) {
        const int fd = accept(ls, NULL, NULL);
        if (fd < 0) return NULL;
        pthread_mutex_lock(&mu); ++accepts; pthread_mutex_unlock(&mu);
        uint32_t op; char buf[2048];
        while (frame(fd, &op, buf, sizeof buf) == 0) {
            if (op == 0) {
                note(strstr(buf, "\"client_id\":\"1559581678853431396\"") ? "hs-ok" : "hs-wrong-id");
                if (!strcmp(mode, "hang")) continue;
                if (!strcmp(mode, "badid")) { reply(fd, 2, "{\"code\":4000,\"message\":\"Invalid Client ID\"}"); break; }
                reply(fd, 1, "{\"cmd\":\"DISPATCH\",\"data\":{\"user\":{\"username\":\"secret-name\"}},\"evt\":\"READY\"}");
            } else {
                note(strstr(buf, "Driving in Rockport") ? "act-driving" : strstr(buf, "In the menus") ? "act-menus"
                     : strstr(buf, "\"activity\"") ? "act-other" : "act-clear");
                if (!strcmp(mode, "error"))
                    reply(fd, 1, "{\"cmd\":\"SET_ACTIVITY\",\"data\":{\"code\":4002,\"message\":\"child \\\"activity\\\" fails\"},\"evt\":\"ERROR\",\"nonce\":\"1\"}");
                else reply(fd, 1, "{\"cmd\":\"SET_ACTIVITY\",\"data\":{},\"evt\":null,\"nonce\":\"1\"}");
                if (!strcmp(mode, "restart") && accepts == 1) { usleep(300000); break; } /* Discord quits after the first status */
            }
        }
        note("eof");
        close(fd);
    }
}

static char logs[2048];
static void pump(int want, int ms) {
    for (int t = 0; t < ms; t += 10) {
        char line[240];
        if (ds_set_want(want, line, sizeof line)) { strcat(logs, "["); strcat(logs, line); strcat(logs, "]"); }
        usleep(10000);
    }
}

int main(int argc, char **argv) {
    mode = argv[1];
    setenv("TMPDIR", argv[2], 1);
    const int ls = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un sa = {.sun_family = AF_UNIX};
    snprintf(sa.sun_path, sizeof sa.sun_path, "%s/discord-ipc-0", argv[2]);
    if (bind(ls, (struct sockaddr *)&sa, sizeof sa) || listen(ls, 4)) { perror("bind"); return 2; }
    pthread_t st; pthread_create(&st, NULL, server, (void *)&ls);
    if (!strcmp(mode, "off")) {
        pump(DS_OFF, 1500);
    } else if (!strcmp(mode, "on")) {
        pump(DS_MENUS, 1500);
        pump(DS_DRIVING, 2500);
        pump(DS_OFF, 1000);
    } else if (!strcmp(mode, "error") || !strcmp(mode, "badid")) {
        pump(DS_MENUS, 1500);
    } else if (!strcmp(mode, "restart")) {
        pump(DS_MENUS, 3500);
    } else if (!strcmp(mode, "absent")) {
        unlink(sa.sun_path);   /* no Discord: nothing listens */
        pump(DS_MENUS, 3500);
    } else if (!strcmp(mode, "hang")) {
        pump(DS_MENUS, 1000);
    }
    struct timeval a, b;
    gettimeofday(&a, NULL);
    const int was_started = g_ds_started;
    discord_status_stop();
    gettimeofday(&b, NULL);
    usleep(200000);
    const long stop_ms = (b.tv_sec - a.tv_sec) * 1000 + (b.tv_usec - a.tv_usec) / 1000;
    pthread_mutex_lock(&mu);
    struct rusage ru; getrusage(RUSAGE_SELF, &ru);
    const long cpu_ms = ru.ru_utime.tv_sec * 1000 + ru.ru_utime.tv_usec / 1000 + ru.ru_stime.tv_sec * 1000 + ru.ru_stime.tv_usec / 1000;
    printf("accepts=%d started=%d stop_ms=%ld cpu_ms=%ld\nseen=%s\nlogs=%s\n", accepts, was_started, stop_ms, cpu_ms, seen, logs);
    pthread_mutex_unlock(&mu);
    return 0;
}
'''

def run(mode, patch=None):
    with tempfile.TemporaryDirectory(dir='/tmp') as tmp:   # /tmp: a Unix socket path must stay under 104 bytes
        inc = Path(tmp) / 'inc'; inc.mkdir()
        h = (ROOT / 'pub_discord.h').read_text()
        if patch:
            assert patch[0] in h, patch
            h = h.replace(patch[0], patch[1], 1)
        (inc / 'pub_discord.h').write_text(h)
        src, exe = Path(tmp) / 'd.c', Path(tmp) / 'd'
        src.write_text(DRIVER)
        cc = subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-Wno-unused-function', '-I', str(inc), str(src),
                        '-o', str(exe), '-lpthread'], capture_output=True, text=True)
        assert cc.returncode == 0, cc.stderr
        out = subprocess.run([str(exe), mode, tmp], capture_output=True, text=True, check=True, timeout=30).stdout
        head, seen, logs = out.splitlines()
        vals = dict(kv.split('=') for kv in head.split())
        return {k: int(v) for k, v in vals.items()}, seen[len('seen='):].split(), logs[len('logs='):]


# (mode, check on the result, the deliberate break of pub_discord.h)
CHECKS = {
    'off_opens_nothing': ('off', lambda v, s, l: v['accepts'] == 0 and v['started'] == 0 and s == [] and l == '',
        ('if (want != DS_OFF && !g_ds_started) {', 'if (!g_ds_started) {')),
    'handshake_status_clear': ('on', lambda v, s, l: v['accepts'] == 1 and s == ['hs-ok', 'act-menus', 'act-driving', 'act-clear', 'eof']
                               and l == '[connected][disconnected]' and 'secret-name' not in l,
        ('"{\\"v\\":1,\\"client_id\\":\\"" DISCORD_APP_ID "\\"}"', '"{\\"v\\":1,\\"client_id\\":\\"1\\"}"')),
    'refusal_logged_once': ('badid', lambda v, s, l: s.count('hs-ok') >= 2 and 'code 4000, "Invalid Client ID"' in l and l.count('refused the connection') == 1,
        ('if (!strcmp(note, last)) return;', '')),
    'absent_no_spin': ('absent', lambda v, s, l: v['started'] == 1 and v['cpu_ms'] < 150 and v['accepts'] == 0,
        ('long usec = tv.tv_usec + 500000;', 'long usec = tv.tv_usec;')),
    'quit_with_hung_discord_fast': ('hang', lambda v, s, l: s[:1] == ['hs-ok'] and v['stop_ms'] < 100,
        ('if (g_ds_fd >= 0) shutdown(g_ds_fd, SHUT_RDWR);', '')),
    'comes_back_after_restart': ('restart', lambda v, s, l: s[:5] == ['hs-ok', 'act-menus', 'eof', 'hs-ok', 'act-menus'],
        ('if (n == 0 || (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK)) { ds_close(fd); fd = -1; }', '(void)n;')),
}


def test_good_build_passes_every_check():
    for name, (mode, ok, _) in CHECKS.items():
        res = run(mode)
        print(name, 'good', res[0])
        assert ok(*res), (name, res)


def test_controls_fail_when_broken():
    for name, (mode, ok, patch) in CHECKS.items():
        res = run(mode, patch)
        print(name, 'BROKEN', res[0])
        assert not ok(*res), ('the broken control still passed', name, res)


if __name__ == '__main__':
    test_good_build_passes_every_check()
    test_controls_fail_when_broken()
    print('PUB DISCORD TEST PASS')
