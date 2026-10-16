// launcher_ui.cpp - see launcher_ui.h.
#include "launcher_ui.h"

#include "../../platform/os.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace launcher {

std::unique_ptr<Source> Platform::open(const Picked &p, std::string *error) {
    OsStat st{};
    if (os_stat(p.path.c_str(), &st) != 0) {
        if (error)
            *error = "cannot open " + p.path;
        return nullptr;
    }
    if (st.is_dir)
        return folder_source(p.path);
    return zip_source(p.path, error);
}

void Platform::run_in_background(std::function<void()> work) {
    auto *heap = new std::function<void()>(std::move(work));
    OsThread *t = os_thread_create(
        [](void *arg) -> void * {
            auto *fn = static_cast<std::function<void()> *>(arg);
            (*fn)();
            delete fn;
            return nullptr;
        },
        heap, 0);
    if (t)
        os_thread_detach(t);
    else {
        (*heap)();
        delete heap;
    }
}

std::string human_bytes(uint64_t bytes) {
    char buf[32];
    if (bytes >= (uint64_t(1) << 30))
        snprintf(buf, sizeof buf, "%.1f GB", double(bytes) / double(uint64_t(1) << 30));
    else if (bytes >= (uint64_t(1) << 20))
        snprintf(buf, sizeof buf, "%.0f MB", double(bytes) / double(uint64_t(1) << 20));
    else
        snprintf(buf, sizeof buf, "%.0f KB", double(bytes) / 1024.0);
    return buf;
}

namespace {
const Color kBackground{16, 18, 24, 255};
const Color kText{230, 232, 238, 255};
const Color kDim{150, 156, 170, 255};
const Color kReady{110, 200, 120, 255};
const Color kWarn{235, 180, 80, 255};
const Color kBad{230, 100, 90, 255};
const Color kButton{52, 60, 78, 255};
const Color kButtonHot{70, 82, 108, 255};
const Color kButtonOff{36, 40, 50, 255};
const Color kFocus{240, 200, 90, 255};

// A path as the player would say it: under the home folder as "~/...".
std::string display(const std::string &path) {
    const char *env = getenv("HOME");
    if (!env || strlen(env) < 2)
        return path;
    // iOS spells the same container /private/var/... and /var/....
    std::string home = env;
    const std::vector<std::string> spellings = {
        home, home.compare(0, 9, "/private/") == 0 ? home.substr(8) : "/private" + home};
    for (const std::string &h : spellings)
        if (path.compare(0, h.size(), h) == 0 && (path.size() == h.size() || path[h.size()] == '/'))
            return "~" + path.substr(h.size());
    return path;
}

std::string shorten(const std::string &s, size_t max) {
    if (s.size() <= max)
        return s;
    return "..." + s.substr(s.size() - (max - 3));
}
} // namespace

Launcher::Launcher(const Spec &spec, Platform &platform)
    : spec_(spec), platform_(platform), shared_(std::make_shared<Shared>()),
      cancel_(std::make_shared<std::atomic<bool>>(false)) {
    info_ = platform_.info();
}

Launcher::~Launcher() {
    cancel_->store(true);
}

void Launcher::post(std::function<void()> fn) {
    std::lock_guard<std::mutex> lock(shared_->m);
    shared_->posted.push_back(std::move(fn));
}

void Launcher::evaluate(const std::string &root) {
    status_ = check(spec_, root, info_.plays_in_place ? info_.cache_file : "");
    dirty_ = true;
}

void Launcher::start(const std::string &known) {
    Status best;
    auto consider = [&](const std::string &root) {
        if (root.empty() || best.state == State::Ready)
            return;
        Status s = check(spec_, root, info_.plays_in_place ? info_.cache_file : "");
        if (s.state == State::Ready ||
            (best.state == State::NotFound && s.state != State::NotFound))
            best = s;
    };
    consider(find_root(spec_, known));
    consider(info_.import_root);
    if (info_.plays_in_place)
        for (const std::string &f : load_folders(info_.folders_file))
            consider(f);
    found_.clear();
    for (const std::string &c : platform_.candidates(spec_))
        if (std::find(found_.begin(), found_.end(), c) == found_.end())
            found_.push_back(c);
    // Desktop plays a found install in place; elsewhere a found folder is only
    // something to import.
    if (info_.plays_in_place && best.state != State::Ready)
        for (const std::string &c : found_)
            consider(c);
    status_ = best;
    screen_ = Screen::Main;
    rebuild();
    Picked opened;
    if (platform_.initial_pick(&opened)) {
        begin_import(opened);
        return;
    }
    if (status_.state == State::NotFound && !info_.auto_import.empty() &&
        !find_root(spec_, info_.auto_import).empty()) {
        Picked p;
        p.path = info_.auto_import;
        p.name = "the bundled game";
        begin_import(p);
    }
}

void Launcher::set_unplayable(const std::string &reason) {
    unplayable_ = reason;
    countdown_ = 0;
    dirty_ = true;
}

void Launcher::set_auto_play(double seconds) {
    if (!unplayable_.empty())
        return;
    countdown_ = status_.state == State::Ready && screen_ == Screen::Main ? seconds : 0;
    dirty_ = true;
}

void Launcher::advance(double seconds) {
    if (countdown_ <= 0)
        return;
    const int before = int(countdown_ + 0.999);
    countdown_ -= seconds;
    if (countdown_ <= 0) {
        countdown_ = 0;
        activate(kPlay);
    } else if (int(countdown_ + 0.999) != before)
        dirty_ = true;
}

void Launcher::drop(const std::string &path) {
    countdown_ = 0;
    if (screen_ == Screen::Importing)
        return;
    OsStat st{};
    if (os_stat(path.c_str(), &st) != 0)
        return;
    Picked p;
    p.path = path;
    if (st.is_dir && info_.plays_in_place) {
        const std::string root = find_root(spec_, path);
        if (root.empty()) {
            show("No " + spec_.executable + " in " + path + " or the two folder levels below it.");
            return;
        }
        evaluate(root);
        if (status_.state == State::Ready)
            remember_folder(info_.folders_file, root);
        screen_ = Screen::Main;
        rebuild();
        return;
    }
    begin_import(p);
}

void Launcher::show(const std::string &text) {
    message_ = text;
    screen_ = Screen::Message;
    rebuild();
}

void Launcher::rebuild() {
    buttons_.clear();
    auto add = [&](int id, const std::string &label, bool enabled = true) {
        buttons_.push_back({id, label, enabled, {}});
    };
    switch (screen_) {
    case Screen::Main:
        if (status_.state == State::Ready)
            add(kPlay, "Play");
        if (info_.plays_in_place) {
            add(kLocate,
                status_.state == State::Ready ? "Use a different folder..."
                                              : "Locate game folder...",
                info_.can_pick_folder);
            for (size_t i = 0; i < found_.size() && i < 4; ++i)
                if (found_[i] != status_.root)
                    add(kUseCandidate + int(i), "Use " + shorten(display(found_[i]), 44));
            if (status_.state == State::Ready && status_.root != info_.import_root)
                add(kCopyIntoApp, "Copy into app storage");
        } else {
            const bool have = status_.state != State::NotFound;
            if (info_.can_pick_folder)
                add(kImportFolder,
                    have ? "Import again from a folder..." : "Import game folder...");
            if (info_.can_pick_zip)
                add(kImportZip, have ? "Import again from a ZIP..." : "Import game ZIP...");
            for (size_t i = 0; i < found_.size() && i < 3; ++i)
                if (found_[i] != status_.root)
                    add(kUseCandidate + int(i), "Import " + shorten(display(found_[i]), 40));
        }
        add(kManage, "Manage...");
        if (!spec_.store_url.empty() && status_.state == State::NotFound)
            add(kStore, "Where to get the game");
        add(kQuit, "Quit");
        break;
    case Screen::Manage:
        add(kExportSaves, "Export saves...");
        add(kImportSaves, "Import saves...");
        if (!info_.import_root.empty() && status_.root == info_.import_root)
            add(kDeleteData,
                confirm_delete_ ? "Delete game data - press again" : "Delete game data");
        if (info_.can_open_folder && !status_.root.empty())
            add(kOpenFolder, "Open game folder");
        if (info_.can_open_folder)
            add(kOpenFolder + 1000, "Open saves folder");
        if (info_.can_open_folder && !info_.profile_dir.empty())   // NFSMW Remaster run 19 (D-132): player texture mods
            add(kOpenFolder + 2000, "Open mods folder");
        if (info_.can_open_folder && !info_.profile_dir.empty())   // NFSMW run 31: changed game files (DataMods)
            add(kOpenFolder + 3000, "Open DataMods folder");
        add(kBack, "Back");
        break;
    case Screen::Importing:
        add(kCancel, "Cancel");
        break;
    case Screen::Message:
        add(kOk, "OK");
        break;
    }
    focus_ = std::clamp(focus_, 0, std::max(0, int(buttons_.size()) - 1));
    if (!buttons_.empty() && !buttons_[size_t(focus_)].enabled)
        focus_ = 0;
    dirty_ = true;
}

const std::vector<Button> &Launcher::buttons() {
    return buttons_;
}

void Launcher::pick_then(bool folder, bool play_in_place) {
    auto weak = std::weak_ptr<Shared>(shared_);
    Launcher *self = this;
    auto done = [weak, self, play_in_place](std::vector<Picked> picked, std::string error) {
        auto shared = weak.lock();
        if (!shared)
            return;
        std::lock_guard<std::mutex> lock(shared->m);
        shared->posted.push_back([self, picked, error, play_in_place]() {
            if (picked.empty()) {
                if (!error.empty())
                    self->show("The picker failed: " + error);
                return;
            }
            if (play_in_place && !picked[0].path.empty()) {
                const std::string root = find_root(self->spec_, picked[0].path);
                if (root.empty()) {
                    self->show("No " + self->spec_.executable + " in " + picked[0].path +
                               " or the two folder levels below it.");
                    return;
                }
                self->evaluate(root);
                if (self->status_.state == State::Ready)
                    remember_folder(self->info_.folders_file, root);
                self->screen_ = Screen::Main;
                self->rebuild();
                return;
            }
            self->begin_import(picked[0]);
        });
    };
    if (folder)
        platform_.pick_folder(done);
    else
        platform_.pick_zip(done);
}

void Launcher::begin_import(const Picked &picked) {
    std::string error;
    // The app's own copy is not a source: copying it onto itself proves nothing
    // and a folder that contains it would be copied into itself.
    if (!picked.path.empty() && !info_.import_root.empty()) {
        const std::string root = find_root(spec_, picked.path);
        if (root == info_.import_root) {
            show("That is the game this app already imported. Choose the folder the game is "
                 "installed in.");
            return;
        }
    }
    std::shared_ptr<Source> source(platform_.open(picked, &error).release());
    if (!source) {
        show("Cannot read " + (picked.name.empty() ? picked.path : picked.name) + ": " + error);
        return;
    }
    {
        std::lock_guard<std::mutex> lock(shared_->m);
        shared_->progress = Progress{};
        shared_->importing = true;
        shared_->import_done = false;
    }
    cancel_->store(false);
    importing_ = picked;
    screen_ = Screen::Importing;
    rebuild();
    platform_.import_activity(true, nullptr);
    const std::string dest = info_.import_root;
    auto shared = shared_;
    auto cancel = cancel_;
    Platform *platform = &platform_;
    const Spec spec = spec_;
    const bool move = platform_.movable(picked);
    const std::string leftover = move ? picked.path : std::string();
    platform_.run_in_background([shared, cancel, platform, spec, source, dest, move, leftover]() {
        ImportOutcome out = import_game(
            spec, *source, dest,
            [&](const Progress &p) {
                {
                    std::lock_guard<std::mutex> lock(shared->m);
                    shared->progress = p;
                }
                platform->import_activity(true, &p);
                return !cancel->load();
            },
            move);
        // What a move leaves behind (excluded files, empty folders) goes too.
        if (move && out.result == ImportResult::Done && !leftover.empty())
            remove_tree(leftover);
        std::lock_guard<std::mutex> lock(shared->m);
        shared->outcome = out;
        shared->importing = false;
        shared->import_done = true;
    });
}

void Launcher::tick() {
    std::vector<std::function<void()>> posted;
    {
        std::lock_guard<std::mutex> lock(shared_->m);
        posted.swap(shared_->posted);
    }
    // A picker result may start (and, run inline, finish) an import.
    for (auto &fn : posted)
        fn();
    bool done = false;
    ImportOutcome outcome;
    {
        std::lock_guard<std::mutex> lock(shared_->m);
        if (shared_->import_done) {
            done = true;
            shared_->import_done = false;
            outcome = shared_->outcome;
        }
        if (shared_->importing)
            dirty_ = true;
    }
    if (!done)
        return;
    platform_.import_activity(false, nullptr);
    platform_.release(importing_);
    importing_ = Picked{};
    evaluate(info_.import_root);
    // A moved folder is gone; a new one may have appeared.
    found_.clear();
    for (const std::string &c : platform_.candidates(spec_))
        if (std::find(found_.begin(), found_.end(), c) == found_.end())
            found_.push_back(c);
    switch (outcome.result) {
    case ImportResult::Done:
        platform_.protect_import(info_.import_root);
        if (info_.plays_in_place && status_.state == State::Ready)
            remember_folder(info_.folders_file, info_.import_root);
        screen_ = Screen::Main;
        rebuild();
        if (status_.state != State::Ready)
            show(std::string("The import finished, but the game is ") + state_name(status_.state) +
                 ".");
        break;
    case ImportResult::Cancelled:
        show("Import cancelled. Importing again continues where it stopped.");
        break;
    case ImportResult::NoSpace:
        show("Not enough free space: the import needs " + human_bytes(outcome.bytes_needed) +
             " and the device has " + human_bytes(outcome.bytes_free) +
             ". Free some space and import again; copied files are kept.");
        break;
    case ImportResult::NoExecutable:
        show("The chosen files do not contain " + spec_.executable +
             ". Choose the folder the game is installed in (or a ZIP of it).");
        break;
    case ImportResult::WrongVersion:
        show("This is not the supported version of " + spec_.title + ". " + outcome.error);
        break;
    case ImportResult::Incomplete:
        show("The chosen files are incomplete: " + outcome.error + ".");
        break;
    case ImportResult::Failed:
        show("The import failed: " + outcome.error);
        break;
    }
}

void Launcher::activate(int id) {
    if (id != kPlay)
        countdown_ = 0;
    auto it =
        std::find_if(buttons_.begin(), buttons_.end(), [&](const Button &b) { return b.id == id; });
    if (it == buttons_.end() || !it->enabled)
        return;
    if (id != kDeleteData)
        confirm_delete_ = false;
    dirty_ = true;
    if (id >= kUseCandidate && id < kUseCandidate + 100) {
        const std::string root = found_[size_t(id - kUseCandidate)];
        if (info_.plays_in_place) {
            evaluate(root);
            if (status_.state == State::Ready)
                remember_folder(info_.folders_file, root);
            rebuild();
        } else {
            Picked p;
            p.path = root;
            begin_import(p);
        }
        return;
    }
    switch (id) {
    case kPlay:
        if (!unplayable_.empty()) {
            show(unplayable_);
            break;
        }
        if (status_.state == State::Ready) {
            if (info_.plays_in_place)
                remember_folder(info_.folders_file, status_.root);
            finished_ = true;
        }
        break;
    case kLocate:
        pick_then(true, true);
        break;
    case kImportFolder:
        pick_then(true, false);
        break;
    case kImportZip:
        pick_then(false, false);
        break;
    case kCopyIntoApp: {
        Picked p;
        p.path = status_.root;
        begin_import(p);
        break;
    }
    case kManage:
        screen_ = Screen::Manage;
        focus_ = 0;
        rebuild();
        break;
    case kExportSaves: {
        auto weak = std::weak_ptr<Shared>(shared_);
        Launcher *self = this;
        platform_.pick_export(
            spec_.title + " saves.zip", [weak, self](std::vector<Picked> p, std::string error) {
                auto shared = weak.lock();
                if (!shared)
                    return;
                std::lock_guard<std::mutex> lock(shared->m);
                shared->posted.push_back([self, p, error]() {
                    if (p.empty()) {
                        if (!error.empty())
                            self->show("The picker failed: " + error);
                        return;
                    }
                    std::string err;
                    if (export_profile(self->info_.profile_dir, p[0].path,
                                       {"game", "logs", "*.log", "launcher-*"}, &err)) {
                        self->show("Saves exported to " +
                                   (p[0].name.empty() ? p[0].path : p[0].name) + ".");
                        self->platform_.export_ready(p[0]);
                    } else
                        self->show("Could not export saves: " + err);
                });
            });
        break;
    }
    case kImportSaves: {
        auto weak = std::weak_ptr<Shared>(shared_);
        Launcher *self = this;
        platform_.pick_saves([weak, self](std::vector<Picked> p, std::string error) {
            auto shared = weak.lock();
            if (!shared)
                return;
            std::lock_guard<std::mutex> lock(shared->m);
            shared->posted.push_back([self, p, error]() {
                if (p.empty()) {
                    if (!error.empty())
                        self->show("The picker failed: " + error);
                    return;
                }
                std::string err;
                if (import_profile(p[0].path, self->info_.profile_dir, &err))
                    self->show("Saves imported.");
                else
                    self->show("Could not import saves: " + err);
            });
        });
        break;
    }
    case kDeleteData:
        if (!confirm_delete_) {
            confirm_delete_ = true;
            rebuild();
            break;
        }
        confirm_delete_ = false;
        if (remove_tree(info_.import_root)) {
            evaluate("");
            screen_ = Screen::Main;
            rebuild();
            show("Game data deleted. Saves are kept.");
        } else
            show("Could not delete " + info_.import_root + ".");
        break;
    case kOpenFolder:
        platform_.open_folder(status_.root);
        break;
    case kOpenFolder + 1000:
        platform_.open_folder(info_.profile_dir);
        break;
    case kOpenFolder + 2000:   // <profile>/Mods, created here when the game has not made it yet
        os_mkdir((info_.profile_dir + "/Mods").c_str());
        platform_.open_folder(info_.profile_dir + "/Mods");
        break;
    case kOpenFolder + 3000:   // <profile>/DataMods, the same way
        os_mkdir((info_.profile_dir + "/DataMods").c_str());
        platform_.open_folder(info_.profile_dir + "/DataMods");
        break;
    case kStore:
        platform_.open_url(spec_.store_url);
        break;
    case kBack:
    case kOk:
        screen_ = Screen::Main;
        focus_ = 0;
        rebuild();
        break;
    case kCancel:
        cancel_->store(true);
        break;
    case kQuit:
        quit_ = true;
        break;
    default:
        break;
    }
}

void Launcher::key(Key k) {
    if (countdown_ > 0) {
        countdown_ = 0;
        dirty_ = true;
        return;
    }
    if (buttons_.empty())
        return;
    const int n = int(buttons_.size());
    dirty_ = true;
    // The nearest enabled button `step` places away, wrapping.
    const auto move = [&](int step) {
        for (int i = 1; i <= n; ++i) {
            const int j = ((focus_ + step * i) % n + n) % n;
            if (buttons_[size_t(j)].enabled) {
                focus_ = j;
                break;
            }
        }
    };
    const int row = std::min(columns_, n);
    switch (k) {
    case Key::Up:
        move(-row);
        break;
    case Key::Left:
    case Key::Previous:
        move(-1);
        break;
    case Key::Down:
        move(row);
        break;
    case Key::Right:
    case Key::Next:
        move(1);
        break;
    case Key::Activate:
        activate(buttons_[size_t(focus_)].id);
        break;
    case Key::Back:
        if (screen_ == Screen::Importing)
            activate(kCancel);
        else if (screen_ != Screen::Main)
            activate(screen_ == Screen::Message ? kOk : kBack);
        else
            activate(kQuit);
        break;
    }
}

void Launcher::pointer(int x, int y, bool pressed) {
    if (countdown_ > 0) {
        // The touch that stops the countdown does nothing else.
        countdown_ = 0;
        pressed_ = -2;
        dirty_ = true;
        return;
    }
    if (pressed_ == -2) {
        if (!pressed)
            pressed_ = -1;
        return;
    }
    for (size_t i = 0; i < buttons_.size(); ++i) {
        if (!buttons_[i].rect.contains(x, y))
            continue;
        if (pressed) {
            pressed_ = int(i);
            focus_ = int(i);
            dirty_ = true;
        } else if (pressed_ == int(i)) {
            pressed_ = -1;
            activate(buttons_[i].id);
        }
        return;
    }
    if (!pressed)
        pressed_ = -1;
}

void Launcher::layout(int w, int h, int s, int top) {
    // One centred column under the text; two when one does not fit.
    const int n = int(buttons_.size());
    const int bh = 24 * s, gap = 6 * s, bottom = h - 18 * s;
    int columns = 1;
    int bw = std::min(w - 32 * s, 360 * s);
    if (top + n * (bh + gap) > bottom && w >= 2 * 200 * s + 48 * s) {
        columns = 2;
        bw = std::min((w - 48 * s) / 2, 360 * s);
    }
    columns_ = columns;
    const int rows = (n + columns - 1) / columns;
    const int total = rows * (bh + gap) - gap;
    // Under the text when there is room, else as low as they fit.
    const int y0 = std::max(0, std::min(std::max(top, bottom - total), bottom - total));
    const int x0 = (w - (columns * bw + (columns - 1) * 16 * s)) / 2;
    for (int i = 0; i < n; ++i)
        buttons_[size_t(i)].rect = {x0 + (i % columns) * (bw + 16 * s),
                                    y0 + (i / columns) * (bh + gap), bw, bh};
}

void Launcher::draw(Canvas &c, int s) {
    s = std::max(1, s);
    dirty_ = false;
    const int w = c.width();
    const int margin = 16 * s;
    c.clear(kBackground);
    int y = margin;
    // The title as large as fits between the margins, down to the body size.
    int ts = 3 * s;
    while (ts > 2 * s && Canvas::text_width(spec_.title, ts) > w - 2 * margin)
        --ts;
    std::string title = spec_.title;
    const size_t fits = size_t(std::max(4, (w - 2 * margin) / (6 * ts)));
    if (title.size() > fits)
        title = title.substr(0, fits - 3) + "...";
    c.text(margin, y, title, ts, kText);
    y += Canvas::text_height(ts) + 8 * s;

    int py = y + 10 * s;
    const int px = margin + 10 * s, pw = w - 2 * margin - 20 * s;
    // Status
    Color accent = kBad;
    std::string headline, detail;
    switch (status_.state) {
    case State::Ready:
        accent = kReady;
        headline = "Ready to play";
        detail = display(status_.root);
        if (countdown_ > 0) {
            headline = "Starting in " + std::to_string(int(countdown_ + 0.999)) + "...";
            detail = (info_.touch ? "Touch the screen" : "Press any key") +
                     std::string(" to open the launcher instead.");
        }
        break;
    case State::WrongVersion:
        accent = kWarn;
        headline = "This " + spec_.executable + " cannot be used";
        detail = display(status_.exe) + "\nIt is not the version of " + spec_.title +
                 " this port is made for. Nothing was changed.\nSHA-256 " + status_.digest;
        break;
    case State::Incomplete: {
        accent = kWarn;
        headline = "Game folder is incomplete";
        std::string missing;
        for (const std::string &m : status_.missing)
            missing += (missing.empty() ? "" : ", ") + m;
        detail = display(status_.root) + "\nMissing: " + missing;
        break;
    }
    case State::NotFound:
        headline = "Game not found";
        detail = info_.plays_in_place ? "Locate the folder " + spec_.title +
                                            " is installed in (it holds " + spec_.executable + ")."
                                      : "Import your copy of " + spec_.title +
                                            ": the installed game folder, or a ZIP of it.";
        if (!info_.drop_hint.empty())
            detail += "\n" + info_.drop_hint;
        if (!spec_.store_url.empty())
            detail += "\nGet the game: " + spec_.store_url;
        break;
    }
    std::vector<std::pair<std::string, Color>> lines;
    if (screen_ == Screen::Importing) {
        Progress p;
        {
            std::lock_guard<std::mutex> lock(shared_->m);
            p = shared_->progress;
        }
        c.text(px, py, "Importing...", 2 * s, kText);
        py += Canvas::text_height(2 * s) + 8 * s;
        const Rect bar{px, py, pw, 14 * s};
        c.fill(bar, kButtonOff);
        const double frac = p.bytes_total ? double(p.bytes_done) / double(p.bytes_total) : 0.0;
        c.fill({bar.x, bar.y, int(bar.w * std::min(1.0, frac)), bar.h}, kReady);
        py += bar.h + 8 * s;
        const std::string counts = std::to_string(p.files_done) + " / " +
                                   std::to_string(p.files_total) + " files, " +
                                   human_bytes(p.bytes_done) + " of " + human_bytes(p.bytes_total);
        c.text(px, py, counts, 2 * s, kDim);
        py += Canvas::text_height(2 * s) + 6 * s;
        c.text(px, py, shorten(p.current, size_t(pw / (12 * s))), 2 * s, kDim);
        py += Canvas::text_height(2 * s) + 6 * s;
        py += c.paragraph(
            px, py, pw,
            "Keep the app open. If it stops, importing again continues where it left off.", 2 * s,
            kDim);
    } else if (screen_ == Screen::Message) {
        py += c.paragraph(px, py, pw, message_, 2 * s, kText);
    } else {
        c.fill({margin, y, 6 * s, 30 * s}, accent);
        c.text(px, py, headline, 2 * s, accent);
        py += Canvas::text_height(2 * s) + 8 * s;
        // Manage keeps the folder, not the how-to-import text the main screen has.
        if (screen_ != Screen::Manage || status_.state != State::NotFound)
            py += c.paragraph(px, py, pw, detail, 2 * s, kDim);
        if (screen_ == Screen::Manage)
            py +=
                4 * s +
                c.paragraph(px, py + 4 * s, pw,
                            "Saves and settings: " + display(info_.profile_dir) +
                                "\nThey are kept when the game data is deleted or imported again.",
                            2 * s, kDim);
    }
    layout(w, c.height(), s, py + 14 * s);
    // Text too tall for the screen goes under the buttons, which stay reachable.
    if (!buttons_.empty() && buttons_.front().rect.y < py + 14 * s) {
        const int band = buttons_.front().rect.y - 8 * s;
        c.fill({0, band, w, c.height() - band}, kBackground);
    }

    for (size_t i = 0; i < buttons_.size(); ++i) {
        const Button &b = buttons_[i];
        const bool focused = int(i) == focus_;
        c.fill(b.rect, !b.enabled                        ? kButtonOff
                       : (pressed_ == int(i) || focused) ? kButtonHot
                                                         : kButton);
        if (focused && !info_.touch)
            c.frame(b.rect, std::max(1, s), kFocus);
        const std::string label = shorten(b.label, size_t(b.rect.w / (12 * s)));
        const int tw = Canvas::text_width(label, 2 * s);
        c.text(b.rect.x + (b.rect.w - tw) / 2,
               b.rect.y + (b.rect.h - Canvas::text_height(2 * s)) / 2, label, 2 * s,
               b.enabled ? kText : kDim);
    }
    const std::string hint = info_.touch
                                 ? "Tap a button."
                                 : "Arrows or D-pad: choose   Enter or A: select   Esc or B: back";
    c.text(margin, c.height() - 12 * s, shorten(hint, size_t((w - 2 * margin) / (6 * s))), s, kDim);
}

} // namespace launcher
