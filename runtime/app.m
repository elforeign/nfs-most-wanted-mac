// app.m - the Mac app around the game: logs, the game folder, the menu bar and Quit.
//
// Run as NFSMW.app (runtime/bundle.sh), the runtime behaves like a Mac game: output goes to
// ~/Library/Logs/NFSMW-port/NFSMW.log, the game folder is remembered in USERDATA/app.txt (asked for with an open panel
// the first time, or when Option is held at launch), and Quit closes the game window, so the game leaves through its
// own exit path (WM_CLOSE sets ExitTheGameFlag; the main loop ends and DeInitializeEverything runs). Run as
// runtime/boot <game folder> from a terminal, nothing changes: output stays on the terminal and the folder comes from
// the command line. user32.m owns the game window and the event pump; this file only builds NSApp and its menus.
#include "roots.h"
#import <Cocoa/Cocoa.h>
#import <CommonCrypto/CommonDigest.h>
#include <fcntl.h>
#include <errno.h>
#include <mach-o/dyld.h>
#include <spawn.h>
#include <sys/event.h>
#include <sys/file.h>
#include <pthread.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "host.h"
#include "durable.h"
#include "../host/audio/dsound_host.h"

extern const char speed_exe_sha256[];   // recomp/out/table.c: the speed.exe the game code was translated from
int app_bundled;
extern char **environ;

// Capture the launch environment before settings_init/report_init export saved switches. Reusing those exports
// would lock stale app.txt values in the replacement process. Explicit launch overrides are preserved verbatim.
static char **restart_argv, **restart_env;
static char *restart_exe;
static int restart_requested, restart_clean, restart_guarded;
static char **copy_strings(char **src, int prefix) {
    int n = 0;
    while (src[n]) ++n;
    char **dst = calloc((size_t)(n + prefix + 1), sizeof *dst);
    if (!dst) return NULL;
    for (int i = 0; i < n; ++i) if (!(dst[prefix + i] = strdup(src[i]))) {
        for (int k = 0; k < i; ++k) free(dst[prefix + k]);
        free(dst);
        return NULL;
    }
    return dst;
}
// A second invocation of this executable is only a waiter, never a second running game. Register NOTE_EXIT
// before waiting (or detect an already reaped parent); exec happens after all of the old process has exited.
static void restart_wait(int argc, char **argv) {
    if (argc < 4 || strcmp(argv[1], "--port-restart-wait")) return;
    if (getenv("BOOT_DEADLINE") || getenv("BOOT_BACKGROUND")) _exit(125);
    pid_t parent = (pid_t)strtol(argv[2], NULL, 10);
    if (parent <= 1) _exit(125);
    if (getppid() == parent) {
        int q = kqueue();
        struct kevent ev;
        EV_SET(&ev, parent, EVFILT_PROC, EV_ADD | EV_ONESHOT, NOTE_EXIT, 0, NULL);
        if (q < 0) _exit(125);
        int rc = kevent(q, &ev, 1, NULL, 0, NULL);
        if (rc < 0 && errno != ESRCH) _exit(125);
        if (rc == 0) {
            do { rc = kevent(q, NULL, 0, &ev, 1, NULL); } while (rc < 0 && errno == EINTR);
            if (rc != 1 || (ev.flags & EV_ERROR)) _exit(125);
        }
        close(q);
    }
    execve(argv[0], argv + 3, environ);
    perror("boot: restart exec");
    _exit(126);
}
static void restart_at_exit(void) {
    if (!restart_requested || !restart_clean || restart_guarded || getenv("BOOT_DEADLINE") || getenv("BOOT_BACKGROUND")) return;
    char parent[32];
    snprintf(parent, sizeof parent, "%d", getpid());
    restart_argv[0] = restart_exe;
    restart_argv[1] = "--port-restart-wait";
    restart_argv[2] = parent;
    posix_spawnattr_t attr;
    if (posix_spawnattr_init(&attr)) return;
    posix_spawn_file_actions_t actions;
    if (posix_spawn_file_actions_init(&actions)) { posix_spawnattr_destroy(&attr); return; }
    for (int fd = 0; fd < 3; ++fd) if (fcntl(fd, F_GETFD) >= 0) posix_spawn_file_actions_adddup2(&actions, fd, fd);
    // No inherited game/audio/save handles. The waiter's stdout/stderr are replaced by log_setup on relaunch.
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_CLOEXEC_DEFAULT);
    pid_t child;
    int rc = posix_spawn(&child, restart_exe, &actions, &attr, restart_argv, restart_env);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    if (rc) fprintf(stderr, "boot: restart spawn failed: %s\n", strerror(rc));
    else fprintf(stderr, "boot: restart waiter %d: %s (%s), after pid %d exits\n", child, restart_exe, app_bundled ? "bundle" : "boot", getpid());
}
// Only the game's successful ExitProcess arms relaunch: a fatal exit or TerminateProcess must not restart.
void app_restart_game_exited(int successful) { restart_clean = successful; }
static void restart_capture(char **argv) {
    char path[4096], absolute[4096];
    uint32_t n = sizeof path;
    restart_guarded = getenv("BOOT_DEADLINE") != NULL || getenv("BOOT_BACKGROUND") != NULL;
    if (restart_guarded) return;
    if (_NSGetExecutablePath(path, &n) || !realpath(path, absolute)) return;
    restart_exe = strdup(absolute);
    restart_argv = copy_strings(argv, 3);
    restart_env = copy_strings(environ, 0);
    atexit(restart_at_exit);             // registered first: audio/rumble/report/save cleanup precedes it
}

const char *log_dir(void) {
    static char dir[1024];
    if (!dir[0]) {
        snprintf(dir, sizeof dir, "%s/Library/Logs/NFSMW-port", getenv("HOME") ? getenv("HOME") : "/tmp");
        mkdir(dir, 0755);
    }
    return dir;
}

// ---------------- USERDATA/app.txt: key=value lines (game_dir, fullscreen, the F10 menu's settings) ----------------
// BEGIN offline prefs: audit4fix_test.py compiles these exact functions in a scratch-only C harness.
enum { MAX_PREFS = 512 };          // 64 entries were fewer than the 66 persistent settings; 512 leaves room for unknown keys
static char prefs[MAX_PREFS][2][1024];   // the settings registry (settings.c) keeps its values here too
static int nprefs, prefs_loaded;
// Accepted id aliases -> the stable storage keys used by current consumers (settings_port.c).
// Explicit entries only: unrecognized keys survive every rewrite unchanged.
static const struct { const char *old_id, *new_id; } pref_migrations[] = {
    {"display.overlay", "overlay"}, {"audio.output", "audio_output"},
    {"controls.pad_layout", "pad_layout"}, {"diag.full_logging", "diag_logging"},
};
static char *pref_trim(char *s) {
    s += strspn(s, " \t\r\n\v\f");
    size_t n = strlen(s);
    while (n && strchr(" \t\r\n\v\f", s[n - 1])) s[--n] = 0;
    return s;
}
static int prefs_save(void);
static void prefs_path(char *out, size_t n) { snprintf(out, n, "%s/app.txt", userdata_dir()); }
const char *app_pref(const char *key) {
    if (!prefs_loaded) {
        prefs_loaded = 1;
        char path[1100], line[2100];
        prefs_path(path, sizeof path);
        FILE *f = fopen(path, "r");
        while (f && nprefs < MAX_PREFS && fgets(line, sizeof line, f)) {
            line[strcspn(line, "\r\n")] = 0;
            char *eq = strchr(line, '=');
            if (!eq) continue;
            *eq = 0;
            snprintf(prefs[nprefs][0], sizeof prefs[0][0], "%s", pref_trim(line));
            snprintf(prefs[nprefs][1], sizeof prefs[0][1], "%s", pref_trim(eq + 1));
            ++nprefs;
        }
        if (f) fclose(f);
        int migrated = 0;
        for (size_t m = 0; m < sizeof pref_migrations / sizeof pref_migrations[0]; ++m) {
            int old = -1, current = -1;
            for (int i = 0; i < nprefs; ++i) {
                if (!strcmp(prefs[i][0], pref_migrations[m].old_id)) old = i;
                if (!strcmp(prefs[i][0], pref_migrations[m].new_id)) current = i;
            }
            if (old < 0) continue;
            // A canonical key, even an explicit reset (empty value), always wins.
            if (current < 0) snprintf(prefs[old][0], sizeof prefs[old][0], "%s", pref_migrations[m].new_id);
            else { memmove(prefs[old], prefs[old + 1], (size_t)(nprefs - old - 1) * sizeof prefs[0]); --nprefs; }
            migrated = 1;
        }
        if (migrated && !prefs_save()) fprintf(stderr, "boot: could not migrate app.txt: %s\n", strerror(errno));
    }
    for (int i = 0; i < nprefs; ++i) if (!strcmp(prefs[i][0], key)) return prefs[i][1];
    return NULL;
}
static int prefs_save(void) {
    char path[1100], tmp[1200];
    prefs_path(path, sizeof path);
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f) return 0;
    for (int k = 0; k < nprefs; ++k) fprintf(f, "%s=%s\n", prefs[k][0], prefs[k][1]);
    // Synced (fsync, not F_FULLFSYNC: small and written while the game runs) and its directory flushed (audit4 #4); any write error keeps the previous app.txt.
    return durable_commit_with(f, tmp, path, 0);
}
// A batch (audit4 #7): the rows of one change (a look preset) write app.txt once at the end, so an interruption can't
// leave half a preset on disk.
static int pref_batch, pref_dirty;
void app_pref_batch_begin(void) { ++pref_batch; }
void app_pref_batch_end(void) {
    if (pref_batch && !--pref_batch && pref_dirty) {
        pref_dirty = 0;
        if (!prefs_save()) fprintf(stderr, "boot: could not save app.txt: %s\n", strerror(errno));
    }
}
void app_pref_set(const char *key, const char *value) {
    app_pref(key);
    int i = 0;
    while (i < nprefs && strcmp(prefs[i][0], key)) ++i;
    if (i == nprefs) { if (nprefs == MAX_PREFS) { fprintf(stderr, "boot: app.txt full (%d entries), %s not saved\n", MAX_PREFS, key); return; } snprintf(prefs[nprefs++][0], sizeof prefs[0][0], "%s", key); }
    snprintf(prefs[i][1], sizeof prefs[0][1], "%s", value);
    if (pref_batch) { pref_dirty = 1; return; }
    if (!prefs_save()) fprintf(stderr, "boot: could not save app.txt: %s\n", strerror(errno));
}
// END offline prefs

// ---------------- logs ----------------
// BOOT_LOG=<file> writes there, BOOT_LOG=- keeps the terminal; the app default is NFSMW.log in log_dir(), the
// previous run's kept as NFSMW.previous.log. Each running copy has its own: a copy holds NFSMW.lock while it runs
// (the system drops the lock when it exits or crashes), and one started meanwhile writes NFSMW-2.log, and so on,
// so a second launch never moves a log that's still being written. Crash reports follow the same names.
static char log_base[16] = "NFSMW";
const char *log_name(void) { return log_base; }
static void log_claim(void) {
    for (int n = 1; n <= 9; ++n) {
        if (n > 1) snprintf(log_base, sizeof log_base, "NFSMW-%d", n);
        char lock[1100];
        snprintf(lock, sizeof lock, "%s/%s.lock", log_dir(), log_base);
        int fd = open(lock, O_RDWR | O_CREAT | O_CLOEXEC, 0644);
        if (fd >= 0 && flock(fd, LOCK_EX | LOCK_NB) == 0) return;   // kept open (and locked) until the process ends
        if (fd >= 0) close(fd);
    }
}
static void log_setup(void) {
    const char *e = getenv("BOOT_LOG");
    if ((e && !strcmp(e, "-")) || (!e && !app_bundled)) return;
    char path[1100], prev[1100];
    if (e && *e) snprintf(path, sizeof path, "%s", e);
    else {
        log_claim();
        snprintf(path, sizeof path, "%s/%s.log", log_dir(), log_base);
        snprintf(prev, sizeof prev, "%s/%s.previous.log", log_dir(), log_base);
        rename(path, prev);
    }
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return;
    fflush(stdout); fflush(stderr);
    dup2(fd, 1); dup2(fd, 2);
    close(fd);
    setvbuf(stdout, NULL, _IOLBF, 0);
    time_t now = time(NULL);
    char when[64];
    strftime(when, sizeof when, "%Y-%m-%d %H:%M:%S", localtime(&now));
    NSString *ver = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleVersion"];
    printf("boot: NFSMW %s, %s, macOS %s\n", ver ? ver.UTF8String : "(not bundled)", when,
           NSProcessInfo.processInfo.operatingSystemVersionString.UTF8String);
}

// ---------------- the game folder ----------------
// The supported exe is the PC v1.3 *code*, not one file: SHA-256 over the .text/.rdata/.data bytes and the PE layout
// the port reads (recomp/exe_fingerprint.py has the definition and why; keep the two in step). The owner's
// whole-file hash is accepted too.
extern const char speed_exe_fingerprint[], speed_exe_region_sha256[4][65];
static const struct { const char *name; uint32_t rva, size; } fp_regions[3] = {
    {".text", 0x1000, 0x48E2A5}, {".rdata", 0x490000, 0x5971D}, {".data", 0x4EA000, 0xDCE10}};
static void hex32(const uint8_t *md, char *out) {
    for (int i = 0; i < 32; ++i) snprintf(out + 2 * i, 3, "%02x", md[i]);
}
static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
// Digests (layout, .text, .rdata, .data) of a speed.exe image, as hex; 0 when it isn't a 32-bit PE. A region the
// file doesn't contain gets an all-zero digest, as in the Python.
static int exe_digests(const uint8_t *d, size_t len, char parts[4][65], char fp[65]) {
    if (len < 0x40 || d[0] != 'M' || d[1] != 'Z') return 0;
    uint32_t off = rd32(d + 0x3C);
    if (off > len || len - off < 24 + 0x70 || memcmp(d + off, "PE\0\0", 4) || d[off + 4] != 0x4C || d[off + 5] != 0x01) return 0;
    uint32_t n = d[off + 6] | d[off + 7] << 8, optsz = d[off + 20] | d[off + 21] << 8, opt = off + 24;
    if (n > 96 || d[opt] != 0x0B || d[opt + 1] != 0x01 || optsz < 0x78) return 0;
    size_t s0 = (size_t)opt + optsz;
    if (s0 > len || len - s0 < n * 40u) return 0;
    uint8_t lay[16 + 3 * 8], md[CC_SHA256_DIGEST_LENGTH], all[4 * 32] = {0};
    memcpy(lay, d + opt + 28, 4);                   // image base
    memcpy(lay + 4, d + opt + 16, 4);               // entry point
    memcpy(lay + 8, d + opt + 96 + 8, 8);           // import directory rva, size
    for (int i = 0; i < 3; ++i) { memcpy(lay + 16 + 8 * i, &fp_regions[i].rva, 4); memcpy(lay + 20 + 8 * i, &fp_regions[i].size, 4); }
    CC_SHA256(lay, sizeof lay, md);
    hex32(md, parts[0]); memcpy(all, md, 32);
    for (int r = 0; r < 3; ++r) {
        uint32_t rva = fp_regions[r].rva, size = fp_regions[r].size;
        int found = 0;
        for (uint32_t i = 0; i < n && !found; ++i) {
            const uint8_t *s = d + s0 + i * 40;
            uint32_t vs = rd32(s + 8), va = rd32(s + 12), rs = rd32(s + 16), raw = rd32(s + 20);
            uint32_t span = vs > rs ? vs : rs;
            if (va > rva || rva + size > (uint64_t)va + span) continue;
            uint32_t skip = rva - va, take = skip < rs ? rs - skip : 0;
            if (take > size) take = size;
            if ((uint64_t)raw + skip + take > len) continue;
            CC_SHA256_CTX ctx;
            CC_SHA256_Init(&ctx);
            CC_SHA256_Update(&ctx, d + raw + skip, take);
            static const uint8_t zeros[1 << 16];
            for (uint32_t left = size - take; left;) {
                uint32_t k = left < sizeof zeros ? left : sizeof zeros;
                CC_SHA256_Update(&ctx, zeros, k);
                left -= k;
            }
            CC_SHA256_Final(md, &ctx);
            hex32(md, parts[r + 1]); memcpy(all + 32 * (r + 1), md, 32);
            found = 1;
        }
        if (!found) { memset(parts[r + 1], '0', 64); parts[r + 1][64] = 0; }
    }
    CC_SHA256_CTX ctx;
    CC_SHA256_Init(&ctx);
    CC_SHA256_Update(&ctx, "nfsmw-exe-fp-v1\n", 16);
    CC_SHA256_Update(&ctx, all, sizeof all);
    CC_SHA256_Final(md, &ctx);
    hex32(md, fp);
    return 1;
}

// 0 when `dir` has a speed.exe with the code the game was translated from; otherwise why not, for the alert.
static int check_game_dir(const char *dir, char *why, size_t n) {
    char path[1100];
    snprintf(path, sizeof path, "%s/speed.exe", dir);
    struct stat st;
    if (stat(dir, &st) || !S_ISDIR(st.st_mode)) { snprintf(why, n, "The folder %s can't be found.", dir); return 1; }
    FILE *f = fopen(path, "rb");
    if (!f) { snprintf(why, n, "%s doesn't contain speed.exe.", dir); return 1; }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *d = len > 0 && len < (200l << 20) ? malloc((size_t)len) : NULL;
    if (!d || fread(d, 1, (size_t)len, f) != (size_t)len) { fclose(f); free(d); snprintf(why, n, "speed.exe in %s can't be read.", dir); return 1; }
    fclose(f);
    uint8_t md[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(d, (CC_LONG)len, md);
    char whole[65], parts[4][65], fp[65];
    hex32(md, whole);
    if (!strcmp(whole, speed_exe_sha256)) { free(d); return 0; }
    int ok = exe_digests(d, (size_t)len, parts, fp);
    free(d);
    if (!ok) { snprintf(why, n, "speed.exe in %s isn't a 32-bit Windows program; this port supports the Need for Speed Most Wanted PC v1.3 speed.exe.", dir); return 1; }
    if (strcmp(fp, speed_exe_fingerprint)) {
        static const char *const names[4] = {"PE layout", ".text", ".rdata", ".data"};
        char bad[200] = "";
        for (int i = 0; i < 4; ++i)
            if (strcmp(parts[i], speed_exe_region_sha256[i])) snprintf(bad + strlen(bad), sizeof bad - strlen(bad), "%s%s", *bad ? ", " : "", names[i]);
        snprintf(why, n, "speed.exe in %s: %s differs: a different game version or a protected exe; this port supports "
                 "the Need for Speed Most Wanted PC v1.3 code.", dir, bad);
        return 1;
    }
    return 0;
}

static NSString *ns(const char *s) { return [NSString stringWithUTF8String:s]; }

static NSString *pick_folder(void) {
    NSOpenPanel *p = [NSOpenPanel openPanel];
    p.canChooseDirectories = YES;
    p.canChooseFiles = NO;
    p.allowsMultipleSelection = NO;
    p.title = @"Choose the Game Folder";
    p.message = @"Choose your Need for Speed Most Wanted folder: the one with speed.exe (version 1.3) in it.";
    p.prompt = @"Choose";
    [NSApp activateIgnoringOtherApps:YES];
    return [p runModal] == NSModalResponseOK ? p.URL.path : nil;
}

// Asks until a valid folder is chosen; NULL when the player cancels. `why` (if set) is shown first.
static NSString *ask_game_dir(const char *why) {
    for (;;) {
        if (why) {
            NSAlert *a = [NSAlert new];
            a.messageText = @"NFSMW needs the game folder";
            a.informativeText = ns(why);
            [a addButtonWithTitle:@"Choose Folder…"];
            [a addButtonWithTitle:@"Quit"];
            [NSApp activateIgnoringOtherApps:YES];
            if ([a runModal] != NSAlertFirstButtonReturn) return nil;
        }
        NSString *dir = pick_folder();
        if (!dir) return nil;
        static char reason[1400];
        if (!check_game_dir(dir.fileSystemRepresentation, reason, sizeof reason)) return dir;
        why = reason;
    }
}

// ---------------- NSApp, the menu bar, Quit ----------------
static void quit_now(void) { fflush(NULL); exit(0); }

// Confirm inside the F10 menu, not with a modal alert: a pad can't answer an NSAlert, and the
// frozen frame loop behind it tripped the 15 s hang watchdog. 1 on the second press within 4 s.
static int confirm_twice(double *armed_until, const char *what) {
    void portmenu_status(const char *fmt, ...);
    double now = host_ticks() / 1e9;
    if (now > *armed_until) {
        *armed_until = now + 4;
        portmenu_status("Press again within 4 s to %s; race progress since the last save is lost.", what);
        return 0;
    }
    *armed_until = 0;
    return 1;
}

// F10 > Display & performance > Quit game: the Mac's Quit (same WM_CLOSE exit path), reachable from the pad.
void app_quit_game(int unused) {
    (void)unused;
    static double armed_until;
    if (!confirm_twice(&armed_until, "quit")) return;
    if (!prefs_save()) fprintf(stderr, "boot: quit: settings save failed\n");
    fprintf(stderr, "boot: quit requested from the menu\n");
    [NSApp terminate:nil];
}

void app_restart_game(int unused) {
    (void)unused;
    void portmenu_status(const char *fmt, ...);
    if (restart_guarded || getenv("BOOT_DEADLINE") || getenv("BOOT_BACKGROUND")) {
        portmenu_status("Restart disabled in unattended runs.");
        fprintf(stderr, "boot: restart skipped (BOOT_DEADLINE/BOOT_BACKGROUND)\n");
        return;
    }
    if (restart_requested) return;
    if (!restart_exe || !restart_argv || !restart_env) { portmenu_status("Could not prepare the restart command."); return; }
    static double armed_until;
    if (!confirm_twice(&armed_until, "restart")) return;
    if (!prefs_save()) { portmenu_status("Could not save settings; restart cancelled."); return; }
    restart_requested = 1;
    fprintf(stderr, "boot: restart requested; settings saved, waiting for the game's ExitProcess\n");
    [NSApp terminate:nil];               // WM_CLOSE -> DeInitializeEverything -> ExitProcess -> atexit
}

@interface AppDelegate : NSObject <NSApplicationDelegate>
@end
@implementation AppDelegate {
    int quitting;
}
- (void)applicationWillResignActive:(NSNotification *)n {
    (void)n;
    feedback_suppress(FEEDBACK_APP, 1);
}
- (void)applicationDidBecomeActive:(NSNotification *)n {
    (void)n;
    feedback_poll_pause();
    feedback_suppress(FEEDBACK_APP, 0);
}
// Quit goes through the game: WM_CLOSE sets ExitTheGameFlag and the game shuts down itself (ExitProcess). Before the
// game window exists, or when Quit is chosen again while the game is shutting down, quit at once.
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
    (void)app;
    if (!quitting++ && user32_request_close()) return NSTerminateCancel;
    quit_now();
    return NSTerminateNow;
}
- (void)changeGameFolder:(id)sender {
    (void)sender;
    gil_release();                       // a modal panel inside the game's message pump: let the game's threads run
    NSString *dir = ask_game_dir(NULL);
    if (dir) {
        app_pref_set("game_dir", dir.fileSystemRepresentation);
        NSAlert *a = [NSAlert new];
        a.messageText = @"Game folder changed";
        a.informativeText = [NSString stringWithFormat:@"NFSMW will use %@ the next time it starts.", dir];
        [a runModal];
    }
    gil_acquire();
}
- (void)toggleGameFullScreen:(id)sender { (void)sender; user32_toggle_fullscreen(); }
- (void)setPadLayout:(NSMenuItem *)item { user32_set_pad_layout_xbox((int)item.tag); }
// View > Performance Overlay: off / compact / full (overlay.m), saved as overlay in app.txt; Cmd-Option-P cycles.
- (void)setOverlay:(NSMenuItem *)item { overlay_set_level((int)item.tag); }
- (void)cycleOverlay:(id)sender { (void)sender; overlay_set_level((overlay_level() + 1) % 3); }
// Audio menu: the output format, saved as audio_output in app.txt and applied at once (the output reopens in it).
static NSString *const kAudioChoices[] = {@"auto", @"dd", @"pcm51", @"stereo", @"ddp", @"truehd"};
- (void)setAudioOutput:(NSMenuItem *)item {
    app_pref_set("audio_output", kAudioChoices[item.tag].UTF8String);
    ds_set_output_choice(kAudioChoices[item.tag].UTF8String);
}
- (BOOL)validateMenuItem:(NSMenuItem *)item {
    if (item.action == @selector(toggleGameFullScreen:)) item.title = user32_fullscreen() ? @"Exit Full Screen" : @"Enter Full Screen";
    if (item.action == @selector(setAudioOutput:)) {
        const char *cur = app_pref("audio_output");
        BOOL on = cur && *cur ? !strcmp(cur, kAudioChoices[item.tag].UTF8String) : item.tag == 0;
        item.state = on ? NSControlStateValueOn : NSControlStateValueOff;
        const char *e = getenv("NFS_AUDIO");
        return !(e && *e);                // the environment decides
    }
    if (item.action == @selector(setOverlay:) || item.action == @selector(cycleOverlay:)) {
        if (item.action == @selector(setOverlay:)) item.state = item.tag == overlay_level() ? NSControlStateValueOn : NSControlStateValueOff;
        const char *e = getenv("NFS_OVERLAY");
        return !(e && *e);                // the environment decides
    }
    if (item.action == @selector(setPadLayout:)) {
        item.state = item.tag == user32_pad_layout_xbox() ? NSControlStateValueOn : NSControlStateValueOff;
        const char *e = getenv("NFS_PAD_LAYOUT");
        return !(e && *e);                // the environment decides
    }
    return YES;
}
- (void)showLogs:(id)sender { (void)sender; [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:ns(log_dir())]]; }
- (void)showSaves:(id)sender { (void)sender; [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:ns(userdata_dir())]]; }
@end

static NSMenuItem *add(NSMenu *m, NSString *title, SEL action, NSString *key, NSEventModifierFlags mods, id target) {
    NSMenuItem *it = [m addItemWithTitle:title action:action keyEquivalent:key];
    it.keyEquivalentModifierMask = mods;
    it.target = target;
    return it;
}
static NSMenu *submenu(NSMenu *bar, NSString *title) {
    NSMenu *m = [[NSMenu alloc] initWithTitle:title];
    [bar addItemWithTitle:title action:NULL keyEquivalent:@""].submenu = m;
    return m;
}

void app_cocoa_init(void) {
    static int done;
    if (done) return;
    done = 1;
    @autoreleasepool {
        [NSApplication sharedApplication];
        // BOOT_BACKGROUND=1 (the unattended launchers: run_guarded.sh, probe.sh, soak.sh): no Dock icon and never the
        // active app, so a test run can't take focus from the game the user is playing (which pauses on focus loss)
        [NSApp setActivationPolicy:getenv("BOOT_BACKGROUND") ? NSApplicationActivationPolicyAccessory : NSApplicationActivationPolicyRegular];
        // ...and no App Nap: a background app whose window is covered gets its timers and frame pacing throttled
        // (a background launch dropped to about a third of its frame rate)
        if (getenv("BOOT_BACKGROUND")) {
            static id activity;
            activity = [NSProcessInfo.processInfo beginActivityWithOptions:NSActivityUserInitiatedAllowingIdleSystemSleep | NSActivityLatencyCritical
                                                                    reason:@"unattended test run"];
        }
        static AppDelegate *delegate;
        delegate = [AppDelegate new];
        NSApp.delegate = delegate;
        NSWindow.allowsAutomaticWindowTabbing = NO;
        NSString *name = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleName"] ?: @"NFSMW";
        const NSEventModifierFlags cmd = NSEventModifierFlagCommand;
        NSMenu *bar = [NSMenu new];
        NSMenu *app = submenu(bar, name);
        add(app, [@"About " stringByAppendingString:name], @selector(orderFrontStandardAboutPanel:), @"", 0, NSApp);
        [app addItem:NSMenuItem.separatorItem];
        add(app, @"Change Game Folder…", @selector(changeGameFolder:), @"", 0, delegate);
        [app addItem:NSMenuItem.separatorItem];
        add(app, [@"Hide " stringByAppendingString:name], @selector(hide:), @"h", cmd, NSApp);
        add(app, @"Hide Others", @selector(hideOtherApplications:), @"h", cmd | NSEventModifierFlagOption, NSApp);
        add(app, @"Show All", @selector(unhideAllApplications:), @"", 0, NSApp);
        [app addItem:NSMenuItem.separatorItem];
        add(app, [@"Quit " stringByAppendingString:name], @selector(terminate:), @"q", cmd, NSApp);
        NSMenu *view = submenu(bar, @"View");
        add(view, @"Enter Full Screen", @selector(toggleGameFullScreen:), @"f", cmd | NSEventModifierFlagControl, delegate);
        [view addItem:NSMenuItem.separatorItem];
        NSMenu *ov = [[NSMenu alloc] initWithTitle:@"Performance Overlay"];   // overlay.m
        [view addItemWithTitle:@"Performance Overlay" action:NULL keyEquivalent:@""].submenu = ov;
        add(ov, @"Off", @selector(setOverlay:), @"", 0, delegate).tag = 0;
        add(ov, @"Compact", @selector(setOverlay:), @"", 0, delegate).tag = 1;
        add(ov, @"Full", @selector(setOverlay:), @"", 0, delegate).tag = 2;
        [ov addItem:NSMenuItem.separatorItem];
        add(ov, @"Next Level", @selector(cycleOverlay:), @"p", cmd | NSEventModifierFlagOption, delegate);
        NSMenu *pad = submenu(bar, @"Controller");      // the button layout while driving (user32.m pad_apply_layout)
        add(pad, @"PlayStation Layout", @selector(setPadLayout:), @"", 0, delegate).tag = 0;
        add(pad, @"Xbox Layout", @selector(setPadLayout:), @"", 0, delegate).tag = 1;
        NSMenu *audio = submenu(bar, @"Audio");         // the output format (dsound_host.cpp ds_set_output_choice)
        add(audio, @"Automatic (for the Output Device)", @selector(setAudioOutput:), @"", 0, delegate).tag = 0;
        add(audio, @"Dolby Digital 5.1 (HDMI / Optical)", @selector(setAudioOutput:), @"", 0, delegate).tag = 1;
        // Never the automatic choice: opt-in (passthrough of E-AC-3, 'cec3')
        add(audio, @"Dolby Digital Plus 5.1 (HDMI)", @selector(setAudioOutput:), @"", 0, delegate).tag = 4;
        // Lossless (Dolby TrueHD in MAT, 'mtct'); Automatic picks it for a device that offers MAT
        add(audio, @"Dolby TrueHD 5.1, Lossless (HDMI)", @selector(setAudioOutput:), @"", 0, delegate).tag = 5;
        add(audio, @"PCM 5.1", @selector(setAudioOutput:), @"", 0, delegate).tag = 2;
        add(audio, @"Stereo", @selector(setAudioOutput:), @"", 0, delegate).tag = 3;
        NSMenu *window = submenu(bar, @"Window");
        add(window, @"Minimize", @selector(performMiniaturize:), @"m", cmd, nil);
        NSApp.windowsMenu = window;
        NSMenu *help = submenu(bar, @"Help");
        add(help, @"Show Logs in Finder", @selector(showLogs:), @"", 0, delegate);
        add(help, @"Show Saves and Settings in Finder", @selector(showSaves:), @"", 0, delegate);
        NSApp.helpMenu = help;
        NSApp.mainMenu = bar;
        [NSApp finishLaunching];
    }
}

// A fatal runtime error (boot.c's die): tell the player, on the main thread only (others just log and exit).
void app_fatal(const char *msg) {
    if (!app_bundled || !pthread_main_np() || getenv("BOOT_HEADLESS")) return;
    @autoreleasepool {
        NSAlert *a = [NSAlert new];
        a.alertStyle = NSAlertStyleCritical;
        a.messageText = @"NFSMW stopped because of an error";
        a.informativeText = [NSString stringWithFormat:@"%s\n\nThe log is in %s.", msg, log_dir()];
        [a addButtonWithTitle:@"Quit"];
        [a addButtonWithTitle:@"Show Log"];
        [NSApp activateIgnoringOtherApps:YES];
        if ([a runModal] == NSAlertSecondButtonReturn)
            [NSWorkspace.sharedWorkspace openURL:[NSURL fileURLWithPath:ns(log_dir())]];
    }
}

// ---------------- start-up ----------------
// Returns the game folder: the command line's (runtime/boot <folder>), else the saved one, else the player's choice.
// ---------------- one running copy per profile (audit4 #2) ----------------
// A second copy on the same USERDATA would delete the first's live save stage at startup (save_cleanup) and could
// overwrite its career with stale data, so it holds USERDATA/.profile.lock (flock, dropped by the system when the copy
// exits or crashes) and a second one refuses to start. Other profiles (BOOT_USERDATA scratch dirs) are unaffected.
// BEGIN offline lock: audit4fix_test.py compiles this exact function in a scratch-only C harness.
static int profile_lock_at(const char *dir) {
    char lock[1100];
    snprintf(lock, sizeof lock, "%s/.profile.lock", dir);
    int fd = open(lock, O_RDWR | O_CREAT | O_CLOEXEC, 0644);
    if (fd < 0) return 0;                       // can't lock (read-only profile): the saves would fail anyway
    if (flock(fd, LOCK_EX | LOCK_NB)) { close(fd); return -1; }
    return 1;                                   // fd stays open (and locked) until the process ends
}
// END offline lock
static void profile_lock(void) {
    if (profile_lock_at(userdata_dir()) >= 0) return;
    const char *msg = "NFS: Most Wanted is already running with this profile. Quit the other copy first; two copies would overwrite each other's career saves.";
    fprintf(stderr, "boot: %s (%s)\n", msg, userdata_dir());
    if (app_bundled && !getenv("BOOT_BACKGROUND") && !getenv("BOOT_DEADLINE")) {
        app_cocoa_init();
        NSAlert *a = [NSAlert new];
        a.alertStyle = NSAlertStyleCritical;
        a.messageText = @"Already running";
        a.informativeText = [NSString stringWithUTF8String:msg];
        [a runModal];
    }
    exit(1);
}

const char *app_start(int argc, char **argv) {
    restart_wait(argc, argv);
    restart_capture(argv);
    @autoreleasepool {
        app_bundled = NSBundle.mainBundle.bundleIdentifier != nil;
        log_setup();
        profile_lock();
        const char *arg = NULL;
        for (int i = 1; i < argc; ++i) if (strncmp(argv[i], "-psn_", 5)) { arg = argv[i]; break; }   // Finder's old -psn_ argument
        static char why[1400];
        if (arg && !app_bundled) {
            if (check_game_dir(arg, why, sizeof why)) { fprintf(stderr, "boot: %s\n", why); exit(1); }
            return arg;
        }
        if (!app_bundled) { fprintf(stderr, "usage: boot <game folder>\n"); exit(1); }
        const char *saved = arg ? arg : app_pref("game_dir");    // open NFSMW.app --args <folder> also sets it
        if (!saved) saved = roots_get("pc_root");                // else the manifest Setup wrote (roots.h)
        int ask = saved == NULL || (NSEvent.modifierFlags & NSEventModifierFlagOption);
        if (saved && !ask && !check_game_dir(saved, why, sizeof why)) {
            if (arg) app_pref_set("game_dir", arg);
            return strdup(saved);
        }
        app_cocoa_init();
        NSString *dir = ask_game_dir(saved && !ask ? why : NULL);
        if (!dir) { printf("boot: no game folder chosen\n"); exit(0); }
        app_pref_set("game_dir", dir.fileSystemRepresentation);
        printf("boot: game folder %s\n", dir.fileSystemRepresentation);
        return strdup(dir.fileSystemRepresentation);
    }
}
