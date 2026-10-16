// bug_report.cpp - Settings > Mods > Help > Save bug report (NFSMW 3.0.4; the idea is SpeedBreaker's "Save Bug Report",
// github.com/SpeedBreakerProject/speedbreaker, no code taken): one zip on the Desktop (else in the profile folder) that a player can
// attach to an issue, instead of finding the logs in ~/Library. It holds the newest three logs, the settings files and
// the mod reports and the Setup app's build log, nothing else (no saves, no game files), and nothing is uploaded: the
// player decides what to send.
#include "../runtime/help_seam.h"
#include "../runtime/layout.h"
#include "game_config.h"

#include "../third_party/miniz/miniz.h"

#include <algorithm>
#include <filesystem>
#include <stdio.h>
#include <stdlib.h>
#include <string>
#include <system_error>
#include <time.h>
#include <vector>

namespace fs = std::filesystem;

namespace {
// The profile's files a report carries, by their names in the profile folder.
const char *const kProfileFiles[] = {"mod-settings.json",          "registry.json",   "NativeOptions.ini",
                                     "game-path.txt",              "DataMods/datamods-report.txt",
                                     "Mods/mods-report.txt"};
constexpr size_t kLogs = 3;

std::vector<fs::path> newest_logs(const fs::path &dir) {
    std::vector<std::pair<fs::file_time_type, fs::path>> found;
    std::error_code ec;
    for (fs::directory_iterator it(dir, ec), end; !ec && it != end; it.increment(ec)) {
        if (!it->is_regular_file(ec) || it->path().extension() != ".log")
            continue;
        found.push_back({it->last_write_time(ec), it->path()});
    }
    std::sort(found.begin(), found.end(), [](const auto &a, const auto &b) { return a.first > b.first; });
    std::vector<fs::path> out;
    for (size_t i = 0; i < found.size() && i < kLogs; ++i)
        out.push_back(found[i].second);
    return out;
}
} // namespace

extern "C" int host_bug_report_available(void) {
    return !host_layout().profile_dir.empty() && getenv("HOME") != nullptr;
}

extern "C" int host_save_bug_report(char *msg, size_t cap) {
    auto say = [&](const std::string &s) {
        if (msg && cap)
            snprintf(msg, cap, "%s", s.c_str());
    };
    const std::string profile = host_layout().profile_dir;
    const char *home = getenv("HOME");
    if (profile.empty() || !home) {
        say("No profile folder: nothing to report.");
        return 0;
    }
    const fs::path base(profile);
    char stamp[32];
    const time_t now = time(nullptr);
    struct tm tmv;
    localtime_r(&now, &tmv);
    strftime(stamp, sizeof stamp, "%Y-%m-%d %H%M%S", &tmv);
    const std::string name = base.filename().string() + " bug report " + stamp + ".zip";

    // One zip at <out>: -1 = could not be created, 0 = nothing added or not finished (removed), else files added.
    auto write_zip = [&](const fs::path &out) -> int {
        mz_zip_archive zip{};
        if (!mz_zip_writer_init_file(&zip, out.string().c_str(), 0))
            return -1;
        int added = 0;
        auto add = [&](const fs::path &file, const std::string &as) {
            std::error_code ec;
            if (!fs::is_regular_file(file, ec))
                return;
            // Fast compression: the zip is written on the menu's thread, and logs still shrink several times.
            if (mz_zip_writer_add_file(&zip, as.c_str(), file.string().c_str(), nullptr, 0, MZ_BEST_SPEED))
                ++added;
        };
        for (const fs::path &log : newest_logs(base / "Logs"))
            add(log, "Logs/" + log.filename().string());
        for (const char *f : kProfileFiles)
            add(base / f, f);
#ifdef RECOMP_SETUP_LOG
        // The Setup app's build log (game.toml [launcher] setup_log): the compiler and SDK this player's build used.
        if (RECOMP_SETUP_LOG[0])
            add(fs::path(home) / RECOMP_SETUP_LOG, "Setup/setup.log");
#endif
        const bool ok = mz_zip_writer_finalize_archive(&zip) != 0;
        mz_zip_writer_end(&zip);
        if (!ok || !added) {
            std::error_code ec;
            fs::remove(out, ec);
            return 0;
        }
        return added;
    };

    // The Desktop first; macOS may ask the player before an app writes there, and a "Don't Allow" (or a missing
    // Desktop) falls back to the profile folder, which needs no permission.
    fs::path out = fs::path(home) / "Desktop" / name;
    bool desktop = true;
    int added = write_zip(out);
    if (added < 0) {
        desktop = false;
        out = base / name;
        added = write_zip(out);
    }
    if (added < 0) {
        say("Could not write " + out.string() + ".");
        return 0;
    }
    if (added == 0) {
        say("Could not finish the bug report, or no logs or settings were found to report.");
        return 0;
    }
    printf("[host] bug report: %s (%d files)\n", out.string().c_str(), added);
    fflush(stdout);
    say(desktop ? "Saved to your Desktop: " + name : "Saved in the game's profile folder: " + out.string());
    return 1;
}
