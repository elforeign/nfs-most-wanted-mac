// layout_tests.cpp - resource and profile resolution for the three layouts
// (resources/ beside the executable, a macOS bundle, a developer checkout)
// and the environment override. Label nogame.
#include "game_config.h"
#include "../../platform/os.h"
#include "../../runtime/layout.h"

#include <stdio.h>
#include <string.h>
#include <string>

static int g_checks = 0, g_failures = 0;
#define CHECK(x)                                                                                   \
    do {                                                                                           \
        ++g_checks;                                                                                \
        if (!(x)) {                                                                                \
            ++g_failures;                                                                          \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #x);                           \
        }                                                                                          \
    } while (0)

// The layout reports forward slashes on every platform, so the expected
// paths are built from a root spelled that way too.
static std::string temp_root() {
    char dir[512];
    snprintf(dir, sizeof dir, "%s/pop-layout-XXXXXX", os_temp_dir());
    if (os_mkdtemp(dir) != 0)
        return "";
    std::string root = dir;
    for (char &c : root)
        if (c == '\\')
            c = '/';
    return root;
}

static void touch(const std::string &path) {
    if (FILE *f = fopen(path.c_str(), "wb"))
        fclose(f);
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

int main() {
    os_unsetenv("RECOMP_PROFILE_DIR");
    os_unsetenv("RECOMP_RESOURCES_DIR");
    std::string root = temp_root();
    CHECK(!root.empty());
    // 1. resources/ beside the executable (Windows and Linux archives).
    mkdir_p(root + "/portable/resources/mods/core");
    touch(root + "/portable/" RECOMP_APP_NAME);
    host_layout_set_exe_path_for_test((root + "/portable/" RECOMP_APP_NAME).c_str());
    CHECK(host_layout().resources_dir == root + "/portable/resources");
    CHECK(!host_layout().developer);
    CHECK(host_resource("mods/core") == root + "/portable/resources/mods/core");
    // The per-user folder is [game] data_dir_name (default app_name; NFS Underground: "NFS Underground Native").
    CHECK(host_layout().profile_dir.find(RECOMP_DATA_DIR_NAME) != std::string::npos);
    CHECK(host_layout().profile_dir.find(root) ==
          std::string::npos); // per-user, not beside the exe
    // 2. A macOS bundle.
    mkdir_p(root + "/X.app/Contents/MacOS");
    mkdir_p(root + "/X.app/Contents/Resources");
    touch(root + "/X.app/Contents/MacOS/X");
    host_layout_set_exe_path_for_test((root + "/X.app/Contents/MacOS/X").c_str());
    CHECK(host_layout().resources_dir == root + "/X.app/Contents/Resources");
    CHECK(host_resource("classic-modes.json") ==
          root + "/X.app/Contents/Resources/classic-modes.json");
    // 3. A checkout: the marker file above the executable.
    mkdir_p(root + "/co/tools/recomp/baseline");
    mkdir_p(root + "/co/build/recomp");
    touch(root + "/co/tools/recomp/baseline/classic-modes.json");
    touch(root + "/co/build/recomp/pop_headless");
    host_layout_set_exe_path_for_test((root + "/co/build/recomp/pop_headless").c_str());
    CHECK(host_layout().developer);
    CHECK(host_layout().checkout_root == root + "/co");
    CHECK(host_resource("mods/core") == root + "/co/build/recomp/mods/core");
    CHECK(host_resource("texture-pack") == root + "/co/build/texture-pack");
    CHECK(host_resource("classic-modes.json") ==
          root + "/co/tools/recomp/baseline/classic-modes.json");
    CHECK(host_layout().profile_dir == root + "/co/build/recomp/profile");
    // 4. RECOMP_PROFILE_DIR wins everywhere.
    os_setenv("RECOMP_PROFILE_DIR", "/elsewhere/profile");
    host_layout_set_exe_path_for_test((root + "/co/build/recomp/pop_headless").c_str());
    CHECK(host_layout().profile_dir == "/elsewhere/profile");
    // Set after the layout was first asked for (an Android host, after a
    // constructor asked): the next answer follows it.
    os_setenv("RECOMP_PROFILE_DIR", "/later/profile");
    CHECK(host_layout().profile_dir == "/later/profile");
    os_unsetenv("RECOMP_PROFILE_DIR");
    CHECK(host_layout().profile_dir == root + "/co/build/recomp/profile");
    // 5. Nothing found: empty resources, per-user profile.
    mkdir_p(root + "/bare");
    touch(root + "/bare/exe");
    host_layout_set_exe_path_for_test((root + "/bare/exe").c_str());
    CHECK(host_layout().resources_dir.empty());
    CHECK(host_resource("mods/core").empty());
    CHECK(!host_layout().developer);
    // 6. A bundle built inside a checkout: developer profile, the bundle's own resources.
    mkdir_p(root + "/co/build/Y.app/Contents/MacOS");
    mkdir_p(root + "/co/build/Y.app/Contents/Resources");
    touch(root + "/co/build/Y.app/Contents/MacOS/Y");
    host_layout_set_exe_path_for_test((root + "/co/build/Y.app/Contents/MacOS/Y").c_str());
    CHECK(host_layout().developer);
    CHECK(host_layout().resources_dir == root + "/co/build/Y.app/Contents/Resources");
    CHECK(host_resource("mods/core") == root + "/co/build/Y.app/Contents/Resources/mods/core");
    CHECK(host_layout().profile_dir == root + "/co/build/recomp/profile");
    // 7. A game repository: game.toml above the executable, the kit elsewhere.
    mkdir_p(root + "/game/build/recomp");
    touch(root + "/game/game.toml");
    touch(root + "/game/build/recomp/pop_headless");
    host_layout_set_exe_path_for_test((root + "/game/build/recomp/pop_headless").c_str());
    CHECK(host_layout().developer);
    CHECK(host_layout().checkout_root == root + "/game");
    CHECK(host_resource("symbols.json") == root + "/game/build/recomp/symbols.json");
    CHECK(host_resource("mods/core") == root + "/game/build/recomp/mods/core");
    CHECK(host_resource("classic-modes.json") ==
          std::string(RECOMP_KIT_DIR) + "/tools/recomp/baseline/classic-modes.json");
    CHECK(host_layout().profile_dir == root + "/game/build/recomp/profile");
    // 8. A flat bundle (iOS): Info.plist beside the executable, no Contents/MacOS.
    mkdir_p(root + "/Flat.app");
    touch(root + "/Flat.app/Info.plist");
    touch(root + "/Flat.app/Flat");
    host_layout_set_exe_path_for_test((root + "/Flat.app/Flat").c_str());
    CHECK(host_layout().resources_dir == root + "/Flat.app");
    CHECK(!host_layout().developer);
    CHECK(host_resource("classic-modes.json") == root + "/Flat.app/classic-modes.json");

    // 9. RECOMP_RESOURCES_DIR wins everywhere: the Android host points the
    // layout at its app data folder, where no executable path could lead.
    os_setenv("RECOMP_RESOURCES_DIR", "/data/app-files");
    host_layout_set_exe_path_for_test((root + "/bare/exe").c_str());
    CHECK(host_layout().resources_dir == "/data/app-files");
    CHECK(!host_layout().developer);
    CHECK(host_resource("controls") == "/data/app-files/controls");
    CHECK(host_resource("classic-modes.json") == "/data/app-files/classic-modes.json");
    // Set after the layout was first asked for, as for the profile.
    os_setenv("RECOMP_RESOURCES_DIR", "/data/other-files");
    CHECK(host_layout().resources_dir == "/data/other-files");
    // A developer run keeps its name mapping: the checkout still wins for the
    // names that only exist there, so an override aimed at one resource does
    // not send symbols.json or the game's layouts somewhere they never are.
    host_layout_set_exe_path_for_test((root + "/game/build/recomp/pop_headless").c_str());
    CHECK(host_layout().developer);
    CHECK(host_layout().resources_dir == "/data/other-files");
    CHECK(host_resource("symbols.json") == root + "/game/build/recomp/symbols.json");
    CHECK(host_resource("controls") == root + "/game/layouts");
    CHECK(host_resource("mods/core") == "/data/other-files/mods/core");
    CHECK(host_layout().profile_dir == root + "/game/build/recomp/profile");
    os_unsetenv("RECOMP_RESOURCES_DIR");
    CHECK(host_layout().resources_dir == root + "/game");

    host_layout_set_exe_path_for_test(nullptr);
    printf("%d checks, %d failures\n", g_checks, g_failures);
    if (!g_failures)
        printf("all layout tests passed\n");
    return g_failures ? 1 : 0;
}
