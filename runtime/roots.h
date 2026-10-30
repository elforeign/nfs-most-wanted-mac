// roots.h - where the player's files are: ONE manifest read at start-up, header-only (no link dependency).
//
// <userdata>/roots.txt (written by Setup; NFS_ROOTS=<file> names another one for tests), lines "key=value", '#' comments:
//   pc_root      the player's game folder (the default when no folder is given and app.txt has none)
//   x360_root    optional: the raw extracted X360 files folder (GLOBAL, SOUND, CARS, ...); absent = no X360 copy
//   assets_root  optional: Setup-converted assets (hd_textures_mips_alpha, hd_textures_ui, nis_arrest, ...)
//   movies       optional: the extracted X360 Movies folder
//   caches       optional: where converted caches go (default ~/Library/Caches/NFSMW-port)
//   tmp          optional: scratch for unlinked temp files (default $TMPDIR, else /tmp)
// With no roots.txt ("legacy"; a development checkout) every lookup is today's: NFS_* switches, then the owner's X360 extract,
// then the checkout's legacy asset folders. With a roots.txt nothing is searched beside the game or in the cwd: a key that is absent means
// that root does not exist, and the feature using it falls back to PC. NFS_* switches (development, tests) still win.
#pragma once
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dirent.h>
#include <unistd.h>
#include <sys/stat.h>
// The X360 shader constants (ZZDATA0.BIN) and engine constants (default.xex) the build generated, if any
// (tools/gen_tables.py): X360_SHADER_LITS / X360_ENGINE_CONSTS are 1 when the player's files were found and identified.
#if defined(__has_include)
#if __has_include("gen/x360_shader_lits.inc")
#include "gen/x360_shader_lits.inc"
#endif
#if __has_include("gen/x360_engine.inc")
#include "gen/x360_engine.inc"
#endif
#endif
#ifndef X360_SHADER_LITS
#define X360_SHADER_LITS 0
#endif
#ifndef X360_ENGINE_CONSTS
#define X360_ENGINE_CONSTS 0
#endif

extern const char *game_dir __attribute__((weak));   // boot.c; weak so offline tests of one module link without it

#define ROOTS_NONE "/dev/null/x360-not-installed"   // what a consumer gets for an absent root: every open of it fails

static inline const char *roots_file_(void) {
    static char path[1100];
    static int done;
    if (done) return path[0] ? path : NULL;
    done = 1;
    const char *e = getenv("NFS_ROOTS");
    if (e && *e) snprintf(path, sizeof path, "%s", e);
    else {
        const char *ud = getenv("BOOT_USERDATA"), *home = getenv("HOME");
        if (ud && *ud) snprintf(path, sizeof path, "%s/roots.txt", ud);
        else snprintf(path, sizeof path, "%s/Library/Application Support/NFSMW-port/roots.txt", home ? home : "/tmp");
    }
    struct stat st;
    if (stat(path, &st)) path[0] = 0;
    return path[0] ? path : NULL;
}
#ifdef NFS_PUBLIC
// The public build never looks beside the game or in the cwd (M4 F7): without a
// roots.txt there is no X360 or assets root unless NFS_X360_FILES names one explicitly.
static inline int roots_active(void) { return 1; }
#else
static inline int roots_active(void) { return roots_file_() != NULL; }
#endif

// The manifest value of a key, or NULL (also when there is no manifest).
static inline const char *roots_get(const char *key) {
    static char vals[8][1100];
    static const char *keys[8];
    static int n, loaded;
    if (!loaded) {
        loaded = 1;
        const char *p = roots_file_();
        FILE *f = p ? fopen(p, "r") : NULL;
        char line[1300];
        while (f && fgets(line, sizeof line, f) && n < 8) {
            char *eq = strchr(line, '=');
            if (line[0] == '#' || !eq) continue;
            *eq = 0;
            char *v = eq + 1;
            v[strcspn(v, "\r\n")] = 0;
            if (!*v) continue;
            for (char *k = line + strlen(line); k > line && (k[-1] == ' ' || k[-1] == '\t'); ) *--k = 0;
            static char names[8][32];
            snprintf(names[n], sizeof names[n], "%s", line);
            snprintf(vals[n], sizeof vals[n], "%s", v);
            keys[n] = names[n];
            ++n;
        }
        if (f) fclose(f);
    }
    for (int i = 0; i < n; ++i) if (!strcmp(keys[i], key)) return vals[i];
    return NULL;
}

// The X360 extract's "files" folder: NFS_X360_FILES, else the manifest's x360_root (ROOTS_NONE when the manifest has
// none), else the legacy sibling asset folder if it exists.
static inline const char *roots_x360_files(void) {
    static char dir[2048];
    if (dir[0]) return dir;
    const char *env = getenv("NFS_X360_FILES");
    struct stat st;
    if (env && *env) snprintf(dir, sizeof dir, "%s", env);
    else if (roots_active()) snprintf(dir, sizeof dir, "%s", roots_get("x360_root") ? roots_get("x360_root") : ROOTS_NONE);
    else if (game_dir && (snprintf(dir, sizeof dir, "%s/../x360/files", game_dir), !stat(dir, &st))) {}
    else snprintf(dir, sizeof dir, "../x360/files");
    return dir;
}
// A Setup-converted asset folder ("hd_textures_ui", "nis_arrest"): <assets_root>/<sub>, ROOTS_NONE when the manifest has
// no assets_root, the legacy sibling asset folder.
static inline const char *roots_asset_dir(const char *sub, char *out, size_t n) {
    if (roots_active()) {
        const char *a = roots_get("assets_root");
        if (a) snprintf(out, n, "%s/%s", a, sub); else snprintf(out, n, "%s/%s", ROOTS_NONE, sub);
    } else snprintf(out, n, "%s/../x360/%s", game_dir ? game_dir : ".", sub);
    return out;
}
// The X360 Movies folder: NFS_X360_MOVIES is the caller's; manifest "movies"; legacy sibling Movies folder.
// NULL when none.
static inline const char *roots_movies_dir(char *out, size_t n) {
    struct stat st;
    if (roots_active()) { const char *m = roots_get("movies"); if (!m) return NULL; snprintf(out, n, "%s", m); return out; }
    if (game_dir && (snprintf(out, n, "%s/../x360/Movies", game_dir), !stat(out, &st) && S_ISDIR(st.st_mode))) return out;
    if (!stat("../x360/Movies", &st) && S_ISDIR(st.st_mode)) { snprintf(out, n, "../x360/Movies"); return out; }
    return NULL;
}
// The temp directory for unlinked scratch files: manifest tmp, $TMPDIR, /tmp. Writes "<dir>/<name>XXXXXX" for mkstemp.
static inline char *roots_tmp_template(const char *name, char *out, size_t n) {
    const char *t = roots_get("tmp");
    if (!t) t = getenv("TMPDIR");
    if (!t || !*t) t = "/tmp";
    snprintf(out, n, "%s/%sXXXXXX", t, name);
    return out;
}
// Converted-cache base folder from the manifest ("caches"), or NULL for the caller's own default.
static inline const char *roots_caches(void) { return roots_get("caches"); }

// Is an X360 feature usable? Legacy (no manifest): always (the generated tables and a sibling extract are there).
// Manifest: the X360 root exists and holds the feature's files. Features: "audio" "music" "police" "nis" "reverb"
// "movies" "textures" "ui" "arrest" "vinyls" "world" "look" (tables built from X360 data) - a Setting names one.
static inline int roots_file_ok_(const char *root, const char *rel) {
    char p[2300];
    snprintf(p, sizeof p, "%s/%s", root, rel);
    struct stat st;
    return !stat(p, &st);
}
// The X360 root the feature gates test: the manifest's x360_root; with no roots.txt at all, an explicit NFS_X360_FILES
// (public build: it wins over the manifest too, as roots_x360_files() and tools/gen_tables.py have it), so the build and the runtime agree.
static inline const char *roots_x360_root_(void) {
    const char *r = roots_get("x360_root"), *env = getenv("NFS_X360_FILES");
#ifdef NFS_PUBLIC
    if (env && *env) return env;     // public: an explicit override wins over the manifest, as in roots_x360_files()
    return r;
#else
    if (r || roots_file_() || !env || !*env) return r;   // development: the manifest wins; the override only without one
    return env;
#endif
}
static inline int roots_x360_has(const char *feature) {
    // "shaders": the X360 NFS/ZZDATA0.BIN under the X360 root, identified at build (the shader constants); "engine": the
    // X360 default.xex in or beside it, identified at build (the post chain's constants). Legacy layout: the build's.
    if (!strcmp(feature, "shaders") || !strcmp(feature, "engine")) {
        int shaders = !strcmp(feature, "shaders"), built = shaders ? X360_SHADER_LITS : X360_ENGINE_CONSTS;
        if (!built || !roots_active()) return built;
        const char *r = roots_x360_root_();
        if (!r) return 0;
        return shaders ? roots_file_ok_(r, "NFS/ZZDATA0.BIN") : roots_file_ok_(r, "default.xex") || roots_file_ok_(r, "../default.xex");
    }
    if (!roots_active()) return 1;
    const char *x = roots_x360_root_(), *a = roots_get("assets_root"), *m = roots_get("movies");
    if (!strcmp(feature, "movies")) return m && !access(m, 0);
    if (!strcmp(feature, "textures") || !strcmp(feature, "ui") || !strcmp(feature, "arrest")) {
        const char *sub = !strcmp(feature, "ui") ? "hd_textures_ui" : !strcmp(feature, "arrest") ? "nis_arrest" : "hd_textures_mips";
        char p[1300];
        snprintf(p, sizeof p, "%s/%s", a ? a : ROOTS_NONE, sub);
        if (a && !access(p, 0)) return 1;
        if (strcmp(feature, "textures")) return 0;
        snprintf(p, sizeof p, "%s/hd_textures_mips_alpha", a ? a : ROOTS_NONE);
        return a && !access(p, 0);
    }
    if (!x) return 0;
    if (!strcmp(feature, "audio")) return roots_file_ok_(x, "SOUND") && roots_file_ok_(x, "GLOBAL/INGAMEMEMORYFILE.BIN");
    if (!strcmp(feature, "reverb")) return roots_file_ok_(x, "GLOBAL/INGAMEMEMORYFILE.BIN");
    if (!strcmp(feature, "music")) return roots_file_ok_(x, "SOUND/PFDATA/MW_MUSIC.MPF");
    if (!strcmp(feature, "police")) return roots_file_ok_(x, "SOUND/SPEECH/COPSPEECH_EN.BIG");
    if (!strcmp(feature, "nis")) return roots_file_ok_(x, "SOUND/STREAMS/NISAUDIO.BIG");
    if (!strcmp(feature, "vinyls")) return roots_file_ok_(x, "CARS");
    if (!strcmp(feature, "world")) return roots_file_ok_(x, "GLOBAL/GLOBALB.BUN");
    if (!strcmp(feature, "look")) return roots_file_ok_(x, "GLOBAL/ATTRIBUTES.BIN");
    return 1;
}
