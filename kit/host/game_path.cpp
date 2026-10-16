#include "game_path.h"
#include "game_config.h"

#include "../runtime/layout.h"
#include "../platform/os.h"
#include "../runtime/loader.h"

#include <stdio.h>
#include <stdlib.h>

static std::string saved_file() {
    return host_layout().profile_dir + "/game-path.txt";
}

static void mkdir_p(const std::string &path) {
    std::string acc;
    for (size_t i = 0; i <= path.size(); ++i) {
        if (i == path.size() || path[i] == '/') {
            if (!acc.empty())
                os_mkdir(acc.c_str());
        }
        if (i < path.size())
            acc.push_back(path[i]);
    }
}

bool game_path_is_supported(const std::string &path, std::string *digest_out) {
    std::string digest = loader_hash_file(path.c_str());
    if (digest_out)
        *digest_out = digest;
    return !digest.empty() && digest == LOADER_EXPECTED_SHA256;
}

bool game_path_save(const std::string &path) {
    mkdir_p(host_layout().profile_dir);
    FILE *f = fopen(saved_file().c_str(), "wb");
    if (!f)
        return false;
    fputs(path.c_str(), f);
    fputc('\n', f);
    return fclose(f) == 0;
}

GamePath game_path_resolve(const char *flag, const char *data_root) {
    GamePath g;
    // A mobile host owns its data location. Missing files must not fall back
    // to a developer path baked into a build made on another machine.
    if (data_root) {
        const std::string candidate = std::string(data_root) + "/game/" RECOMP_EXECUTABLE;
        OsStat st;
        if (*data_root && os_stat(candidate.c_str(), &st) == 0 && st.is_regular) {
            g.exe = candidate;
            g.source = GamePathSource::DataRoot;
        }
        return g;
    }
    if (flag && *flag) {
        g.exe = flag;
        g.source = GamePathSource::Flag;
        return g;
    }
    if (const char *env = recomp_env("EXE"); env && *env) {
        g.exe = env;
        g.source = GamePathSource::Environment;
        return g;
    }
    // The build's own copy of the game, for developer runs only (a checkout
    // above the app): game.toml names it by an absolute path (a game
    // repository's original/) or relative to the checkout root. A packaged app
    // never uses it, so it finds the player's game on this Mac as on any other
    // (the saved path, else the launcher).
    const HostLayout &l = host_layout();
    std::string candidate = l.developer ? std::string(RECOMP_DEVELOPER_EXE) : std::string();
    if (!candidate.empty() && candidate[0] != '/')
        candidate = l.checkout_root + "/" + candidate;
    OsStat st;
    if (!candidate.empty() && os_stat(candidate.c_str(), &st) == 0) {
        g.exe = candidate;
        g.source = GamePathSource::Checkout;
        return g;
    }
    if (FILE *f = fopen(saved_file().c_str(), "rb")) {
        char line[4096] = {0};
        if (fgets(line, sizeof line, f)) {
            std::string path(line);
            while (!path.empty() && (path.back() == '\n' || path.back() == '\r'))
                path.pop_back();
            if (!path.empty() && game_path_is_supported(path, nullptr)) {
                g.exe = path;
                g.source = GamePathSource::Saved;
            }
        }
        fclose(f);
    }
    return g;
}
