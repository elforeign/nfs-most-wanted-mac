"""Report save regression: production F10 action, snapshot, export and a fake guest sound writer.

A 650 ms export models slow filesystem/privacy work. Maximum permitted write gap: 200 ms.
Pinned original: request 1069.00 ms / sound gap 1070.54 ms (40 checks / 2 failures).
Queued writer: request 1.17 ms / sound gap 8.76 ms (80 checks / 0 failures).
Log budget: unbounded 11 checks / 1 failure; bounded 11 / 0.
Regex resource error: unchecked 1 / 1 failure; fail-closed 1 / 0.
The same fixture compares every bundle attachment byte for byte; no game or audio device needed.
"""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime'
ORIGINAL = r'''// ---------------- writing a report ----------------
static void copy_tail(const char *from, const char *to, long max) {
    if (max > 0) { ReportPrivacyCopyLog(from, to, max); return; }
    FILE *in = fopen(from, "rb");
    if (!in) return;
    FILE *out = fopen(to, "wb");
    if (!out) { fclose(in); return; }
    char buf[65536];
    size_t got;
    while ((got = fread(buf, 1, sizeof buf, in)) > 0) fwrite(buf, 1, got, out);
    fclose(in);
    fclose(out);
}
static const char *flow_name(uint32_t f) {
    switch (f) {
    case 3: return "front end";
    case 4: return "loading";
    case 6: return "racing / free roam";
    default: return "starting";
    }
}

static char last_report[1200];
const char *report_last_path(void) { return last_report; }

// Writes a report folder; returns its path ("" on failure). safe: called from a normal thread (not a signal handler).
const char *report_write(const char *category, const char *note, const char *why, const char *attach) {
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    char stamp[32], dir[1200];
    strftime(stamp, sizeof stamp, "%Y%m%d-%H%M%S", &tm);
    snprintf(dir, sizeof dir, "%s/%s", reports_dir(), stamp);
    for (int k = 2; mkdir(dir, 0755) != 0 && k < 10; ++k) snprintf(dir, sizeof dir, "%s/%s-%d", reports_dir(), stamp, k);
    char path[1400];
    // settings.txt
    snprintf(path, sizeof path, "%s/settings.txt", dir);
    FILE *f = fopen(path, "w");
    char vbuf[96];
    if (f) {
        fprintf(f, "# id = value (source)\n");
        for (int i = 0; i < settings_count(); ++i) {
            Setting *s = settings_at(i);
            if (s->type != SET_TOGGLE && s->type != SET_CHOICE && s->type != SET_SLIDER) continue;
            int v = s->get ? s->get() : s->value;
            fprintf(f, "%s = %s (%s%s%s)%s\n", s->id, settings_value_name(s, v, vbuf, sizeof vbuf), settings_source(s),
                    s->locked ? "=" : "", s->locked ? s->env_text : "", s->restart && v != s->boot_value ? " [after restart]" : "");
        }
        fclose(f);
    }
    // stats.txt
    snprintf(path, sizeof path, "%s/stats.txt", dir);
    if ((f = fopen(path, "w"))) {
        fprintf(f, "# t(s)     frame     fps   p50ms  p99ms  maxms  busyms  gpums  flow  mixrms FL FR C LFE BL BR\n");
        uint64_t first = nsecs > SECS ? nsecs - SECS : 0;
        for (uint64_t i = first; i < nsecs; ++i) {
            const Sec *s = &secs[i % SECS];
            fprintf(f, "%9.1f %9llu %6.1f %6.2f %6.2f %6.2f %7.2f %6.2f %5u  ", s->t, (unsigned long long)s->frame, s->fps, s->p50, s->p99,
                    s->max, s->busy, s->gpu, s->flow);
            if (s->have_rms) fprintf(f, "%.3f %.3f %.3f %.3f %.3f %.3f\n", s->rms[0], s->rms[1], s->rms[2], s->rms[3], s->rms[4], s->rms[5]);
            else fprintf(f, "n/a\n");
        }
        fclose(f);
    }
    // events.txt
    snprintf(path, sizeof path, "%s/events.txt", dir);
    if ((f = fopen(path, "w"))) {
        fprintf(f, "# notable events (time since start, frame), oldest first\n");
        uint32_t head = notes_head, start = head > NOTES ? head - NOTES : 0;
        for (uint32_t i = start; i < head; ++i) fprintf(f, "%s\n", notes[i % NOTES]);
        diag_ring_dump(f, 400);
        fclose(f);
    }
    // log.txt: this run's stderr when it goes to a file
    char logp[1024] = "";
    if (fcntl(2, F_GETPATH, logp) == 0 && logp[0]) {
        snprintf(path, sizeof path, "%s/log.txt", dir);
        copy_tail(logp, path, 256 * 1024);
    }
    // screenshot.png: the frame when the menu opened (a user report while the menu is open)
    int have_shot = 0;
    if (!attach && shot_file[0] && host_ticks() / 1e9 - shot_at < 600 && access(shot_file, R_OK) == 0) {
        snprintf(path, sizeof path, "%s/screenshot.png", dir);
        copy_tail(shot_file, path, 0);
        have_shot = 1;
    }
    if (attach) {
        snprintf(path, sizeof path, "%s/crash.txt", dir);
        copy_tail(attach, path, 0);
    }
    // report.txt, the summary on top
    snprintf(path, sizeof path, "%s/report.txt", dir);
    if ((f = fopen(path, "w"))) {
        char when[64];
        strftime(when, sizeof when, "%Y-%m-%d %H:%M:%S %Z", &tm);
        uint32_t flow = M32(GAMEFLOW_STATE);
        fprintf(f, "NFSMW port problem report\n\n");
        fprintf(f, "when:       %s\n", when);
        fprintf(f, "category:   %s\n", category && *category ? category : "Other");
        fprintf(f, "note:       %s\n", note && *note ? note : "(none)");
        fprintf(f, "reason:     %s\n", why && *why ? why : "reported from the F10 menu");
        fprintf(f, "build:      %s (speed.exe code %.12s)\n", port_build_id(), speed_exe_fingerprint);
        fprintf(f, "macOS:      %s\n", sys_os);
        fprintf(f, "Mac model:  %s\n", sys_model);
        fprintf(f, "game:       %s (game-flow %u), frame %llu, %.0f s since start%s\n", flow_name(flow), flow,
                (unsigned long long)diag_current_frame(), uptime(), diag_on ? ", BOOT_DIAG on" : "");
        fprintf(f, "display:    %s\n", sys_display);
        fprintf(f, "display:    %s\n", sys_edr);
        fprintf(f, "render:     ");
        print_render(f);
        fprintf(f, "\n");
        const char *mode = NULL;
        int live = -2;
        uint64_t under = 0;
        if (dsound_output_info(&mode, &live, &under))
            fprintf(f, "audio:      %s (%s, %llu underruns); macOS default output %s\n", mode ? mode : "?",
                    live > 0 ? "playing" : live == 0 ? "silent: NFS_AUDIO=null" : "waiting for a device", (unsigned long long)under,
                    sys_device);
        else fprintf(f, "audio:      no sound device yet (default output %s)\n", sys_device);
        fprintf(f, "controller: %s, %s layout\n", sys_pad, user32_pad_layout_xbox() ? "Xbox" : "PlayStation");
        // the last minute
        float fmin = 1e9f, fsum = 0, p99max = 0, gsum = 0;
        int n = 0;
        for (uint64_t i = nsecs > 60 ? nsecs - 60 : 0; i < nsecs; ++i) {
            const Sec *s = &secs[i % SECS];
            if (s->fps <= 0) continue;           // before the first frame
            ++n;
            fmin = fminf(fmin, s->fps); fsum += s->fps; p99max = fmaxf(p99max, s->p99); gsum += s->gpu;
        }
        if (n) fprintf(f, "last %ds:   fps mean %.1f min %.1f, worst p99 %.2f ms, GPU mean %.2f ms\n", n, fsum / n, fmin, p99max, gsum / n);
        else fprintf(f, "last 60s:   no frame samples yet (fps / p99 / GPU unavailable)\n");
        fprintf(f, "\nsettings that differ from their defaults or are set by a switch:\n");
        int any = print_nondefault(f);
        if (!any) fprintf(f, "  (none)\n");
        fprintf(f, "\nfiles: report.txt%s, stats.txt, events.txt%s, settings.txt%s\n", have_shot ? ", screenshot.png" : "",
                logp[0] ? ", log.txt" : "", attach ? ", crash.txt" : "");
        fclose(f);
    }
    ReportRedactBundle([NSString stringWithUTF8String:dir]);
    snprintf(last_report, sizeof last_report, "%s", dir);
    report_note("report written: %s (%s)", dir, category ? category : "?");
    fprintf(stderr, "boot: problem report written: %s\n", dir);
    if (diag_on) diag_event("report: %s (%s)", dir, category ? category : "?");
    return last_report;
}

'''
ORIGINAL_SAVE = r'''static void report_save(int v) {
    (void)v;
    Setting *c = settings_find("diag.report_category");
    const char *p = report_write(c ? kCatV[c->get ? c->get() : c->value] : "Other", settings_text("diag.report_note"), NULL, NULL);
    if (p && *p) portmenu_status("Report saved: %s", p);
    else portmenu_status("Couldn't write the report");
}
'''
assert hashlib.sha256(ORIGINAL.encode()).hexdigest() == '163318de6d9cf0d8025c589eefaa61645349900c206579040ce5283ce8c13218'
assert hashlib.sha256(ORIGINAL_SAVE.encode()).hexdigest() == '04ed1585e4c8bcc36baf6dc0bd5c5ee949869bf007da4eff288b0f23362a3bf3'


def run(work, baseline, *, budget=False, unbounded=False, unsafe_regex=False, regex_error=False):
    work.mkdir()
    source = (RUNTIME / 'report.m').read_text()
    fixture = (RUNTIME / 'report_lock_test.m').read_text()
    (work / 'report_lock_test.m').write_text(fixture)
    privacy = (RUNTIME / 'report_privacy.h').read_text()
    if unbounded:
        start = privacy.index('    // Do not read an ever-growing session log')
        end = privacy.index('    NSString *text = ReportPrivacyDecodeData(data);', start)
        privacy = privacy[:start] + '    NSData *data = [NSData dataWithContentsOfFile:source];\n' + privacy[end:]
    if unsafe_regex:
        line = '    if (!valid) return nil;  // ICU resource errors must not silently leave a credential unchanged.'
        assert privacy.count(line) == 1
        privacy = privacy.replace(line, '    (void)valid;')
    (work / 'report_privacy.h').write_text(privacy)
    shot_start = source.index('// ---------------- the screenshot:')
    writer_start = source.index('// ---------------- writing a report ----------------')
    writer_end = source.index('// diag.c diag_report:', writer_start)
    writer = ORIGINAL if baseline else source[writer_start:writer_end]
    (work / 'report_writer.inc').write_text(source[shot_start:writer_start] + writer)
    sec = next(line for line in source.splitlines() if line.startswith('typedef struct { double t;'))
    (work / 'report_sec.inc').write_text(sec + '\n')
    a = source.index('static void print_render(')
    b = source.index('static void refresh_power_thermal(void) {', a)
    (work / 'report_format.inc').write_text(source[a:b])
    settings = (RUNTIME / 'settings_port.c').read_text()
    a = settings.index('static void report_save(int v) {')
    b = settings.index('static void report_saved_text(', a)
    (work / 'report_save.inc').write_text(ORIGINAL_SAVE if baseline else settings[a:b])
    command = ['clang', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
               '-Wno-unused-variable', '-Wno-deprecated-declarations', '-fsanitize=address,undefined', '-fobjc-arc',
               '-DREPORT_LOCK_BASELINE=' + str(int(baseline)),
               '-I' + str(ROOT / 'recomp'), '-I' + str(ROOT / 'recomp/out'), '-I' + str(RUNTIME), '-I' + str(work),
               str(work / 'report_lock_test.m'), '-framework', 'Cocoa', '-framework', 'Metal',
               '-framework', 'QuartzCore', '-framework', 'IOKit', '-framework', 'SystemConfiguration',
               '-o', str(work / 'check')]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (work / 'compile.log').write_text(compiled.stdout + compiled.stderr)
    assert compiled.returncode == 0, compiled.stderr[-5000:]
    result = subprocess.run([str(work / 'check'), str(work / 'profile')] + (['--budget'] if budget else ['--regex-error'] if regex_error else []), capture_output=True, text=True, timeout=30,
                            env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
    (work / 'result.log').write_text(result.stdout + result.stderr)
    print(('unsafe_regex' if unsafe_regex else 'unbounded' if unbounded else 'original' if baseline else 'fixed') + ': ' + result.stdout.split('bundle=', 1)[0].strip(), flush=True)
    if budget or regex_error:
        assert result.returncode == int(unbounded or unsafe_regex), result.stdout + result.stderr
        if unbounded:
            assert 'out hasPrefix:@"Log omitted:"' in result.stdout, result.stdout
        if unsafe_regex:
            assert 'out == nil' in result.stdout, result.stdout
        return
    if baseline:
        assert result.returncode == 1 and 'sound_gap <= 200' in result.stdout, result.stdout + result.stderr
    else:
        assert result.returncode == 0 and '0 failures' in result.stdout, result.stdout + result.stderr
    if not baseline:
        drained = work / 'exit-profile'
        result_exit = subprocess.run([str(work / 'check'), str(drained), '--exit'],
                                     capture_output=True, text=True, timeout=30,
                                     env=dict(os.environ, ASAN_OPTIONS='detect_leaks=0'))
        assert result_exit.returncode == 0, result_exit.stdout + result_exit.stderr
        bundles = list((drained / 'reports').iterdir())
        assert len(bundles) == 1 and len(list(bundles[0].iterdir())) == 6
        print('PASS: exit drains the pending report (6 attachments)', flush=True)
    match = re.search(r'^bundle=(.+)$', result.stdout, re.M)
    assert match, result.stdout + result.stderr
    return Path(match[1])


def shot_hook(source):
    start = source.index('dispatch_async(png_q, ^{')
    end = source.index('            free(p);', start)
    block=source[start:end]
    assert 'report_shot_ready(p);' in block, 'encoded screenshot must reach report snapshot cache'
    ready=block.index('report_shot_ready(p);')
    for producer in ('diag_png(p,', 'shadow_near_ab_write_bmp(p,'):
        assert block.index(producer)<ready, 'every encoded format must precede report readiness'


def main():
    presenter = (RUNTIME / 'render_present.m').read_text()
    shot_hook(presenter)
    early=presenter.replace('            report_shot_ready(p);','')
    early=early.replace('size_t plen=strlen(p);','report_shot_ready(p);\n            size_t plen=strlen(p);',1)
    for label,broken in [('removed',presenter.replace('            report_shot_ready(p);','')),('early',early)]:
        try:
            shot_hook(broken)
        except AssertionError:
            print('PASS: PNG/BMP producer hook present; '+label+' readiness rejected')
        else:
            raise AssertionError(label+' screenshot producer hook escaped detection')
    scratch = ROOT / '.metal-cache'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='report-lock-', dir=scratch) as temporary:
        work = Path(temporary)
        original = run(work / 'original', True)
        fixed = run(work / 'fixed', False)
        names = sorted(path.name for path in original.iterdir())
        assert names == sorted(path.name for path in fixed.iterdir()), names
        for name in names:
            assert (original / name).read_bytes() == (fixed / name).read_bytes(), name + ' differs'
        print(f'PASS: {len(names)} identical attachments; old save stalls sound, queued save stays below 200 ms')
        run(work / 'budget', False, budget=True)
        run(work / 'unbounded', False, budget=True, unbounded=True)
        run(work / 'regex_error', False, regex_error=True)
        run(work / 'unsafe_regex', False, regex_error=True, unsafe_regex=True)


if __name__ == '__main__':
    main()
