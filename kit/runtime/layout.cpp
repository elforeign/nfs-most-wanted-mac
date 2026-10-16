#include "layout.h"
#include "game_config.h"

#include "../platform/os.h"

#include <stdlib.h>
#include <string.h>

namespace {

std::string g_test_exe;
HostLayout g_layout;
bool g_computed = false;
std::string g_profile_env;   // the RECOMP_PROFILE_DIR g_layout was computed with
std::string g_resources_env; // the RECOMP_RESOURCES_DIR g_layout was computed with

bool exists(const std::string &p) {
    OsStat st;
    return os_stat(p.c_str(), &st) == 0;
}

std::string parent(const std::string &p) {
    size_t s = p.find_last_of('/');
    return s == std::string::npos ? "" : p.substr(0, s);
}

bool ends_with(const std::string &s, const char *suffix) {
    size_t n = strlen(suffix);
    return s.size() >= n && s.compare(s.size() - n, n, suffix) == 0;
}

HostLayout compute() {
    HostLayout l;
    std::string exe = g_test_exe;
    if (exe.empty()) {
        char path[4096];
        if (os_exe_path(path, sizeof path) == 0)
            exe = path;
    }
    for (char &c : exe)
        if (c == '\\')
            c = '/';
    const std::string dir = parent(exe);
    // A checkout above the executable makes this a developer run whatever the
    // executable's own shape (build/recomp/pop_smoke or build/PopRecomp.app).
    // The checkout is the game's directory (its game.toml; outputs under its
    // build/) or, for the kit's own stub builds, the kit itself.
    std::string up = dir;
    for (int depth = 0; depth < 12 && !up.empty(); ++depth) {
        if (exists(up + "/game.toml") || exists(up + "/tools/recomp/baseline/classic-modes.json")) {
            l.checkout_root = up;
            l.developer = true;
            break;
        }
        up = parent(up);
    }
    if (l.developer)
        l.build_dir = ends_with(dir, "/recomp") && dir != l.checkout_root + "/build/recomp" &&
                              exists(dir + "/mods")
                          ? dir
                          : l.checkout_root + "/build/recomp";
    // An app that knows better than the executable path says so: the Android
    // host has no executable of its own (the process is the system's
    // app_process), so it points this at its data folder.
    const char *resources = recomp_env("RESOURCES_DIR");
    if (resources && *resources)
        l.resources_dir = resources;
    else if (ends_with(dir, "/Contents/MacOS"))
        l.resources_dir = parent(dir) + "/Resources";
    else if (exists(dir + "/resources"))
        l.resources_dir = dir + "/resources";
    else if (exists(dir + "/Info.plist"))
        l.resources_dir = dir; // a flat bundle (iOS): resources beside the executable
    else if (l.developer)
        l.resources_dir = l.checkout_root;
    const char *env = recomp_env("PROFILE_DIR");
    if (env && *env)
        l.profile_dir = env;
    else if (l.developer)
        l.profile_dir = l.build_dir + "/profile";
    else {
        char buf[4096];
        // [game] data_dir_name (default app_name): the player-facing folder, e.g. "NFS Underground Native".
        if (os_user_data_dir(RECOMP_DATA_DIR_NAME, buf, sizeof buf) == 0)
            l.profile_dir = buf;
        else
            l.profile_dir = "profile"; // no home at all: beside the cwd
    }
    return l;
}

} // namespace

// Computed once, and again if RECOMP_PROFILE_DIR or RECOMP_RESOURCES_DIR
// changes afterwards: a constructor (mods/run_record.cpp) asks before an
// Android host has set the profile and the resources under its external
// files folder.
const HostLayout &host_layout() {
    const char *profile = recomp_env("PROFILE_DIR");
    const char *resources = recomp_env("RESOURCES_DIR");
    const std::string profile_env = profile ? profile : "";
    const std::string resources_env = resources ? resources : "";
    if (!g_computed || profile_env != g_profile_env || resources_env != g_resources_env) {
        g_layout = compute();
        g_profile_env = profile_env;
        g_resources_env = resources_env;
        g_computed = true;
    }
    return g_layout;
}

std::string host_resource(const char *rel) {
    const HostLayout &l = host_layout();
    if (l.resources_dir.empty())
        return "";
    // The regenerated translation's index, beside the shared gen/.
    if (l.developer && strcmp(rel, "symbols.json") == 0)
        return l.checkout_root + "/build/recomp/symbols.json";
    // The game's on-screen controls layouts: its repository's layouts/ in a
    // developer run (bundle or not), so an edit there needs no packaging.
    if (l.developer && strcmp(rel, "controls") == 0)
        return l.checkout_root + "/layouts";
    if (l.developer && l.resources_dir == l.checkout_root) {
        if (strcmp(rel, "mods/core") == 0)
            return l.build_dir + "/mods/core";
        if (strcmp(rel, "texture-pack") == 0)
            return l.checkout_root + "/build/texture-pack";
        if (strcmp(rel, "general-midi.sf2") == 0) {
            // The kit's bundled bank, wherever the kit is relative to the game.
            const char *const bank = "/third_party/soundfonts/generaluser-gs/GeneralUser-GS.sf2";
            const std::string own = l.checkout_root + bank;
            return exists(own) ? own : std::string(RECOMP_KIT_DIR) + bank;
        }
        if (strcmp(rel, "classic-modes.json") == 0) {
            // The kit's committed probe list, wherever the kit is relative to the game.
            const std::string own = l.checkout_root + "/tools/recomp/baseline/classic-modes.json";
            return exists(own)
                       ? own
                       : std::string(RECOMP_KIT_DIR) + "/tools/recomp/baseline/classic-modes.json";
        }
    }
    return l.resources_dir + "/" + rel;
}

std::string host_state_file(const char *name) {
    const HostLayout &l = host_layout();
    if (l.developer)
        return l.build_dir + "/" + name;
    return l.profile_dir + "/" + name;
}

void host_layout_set_exe_path_for_test(const char *exe_path) {
    g_test_exe = exe_path ? exe_path : "";
    g_computed = false;
}
