// launcher.cpp - see launcher.h.
#include "launcher.h"

#include "../../platform/os.h"
#include "../../third_party/miniz/miniz.h"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>

namespace launcher {

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------
namespace {

std::string join(const std::string &a, const std::string &b) {
    if (a.empty())
        return b;
    if (b.empty())
        return a;
    return a.back() == '/' ? a + b : a + "/" + b;
}

std::string lower(std::string s) {
    for (char &c : s)
        c = char(std::tolower((unsigned char)c));
    return s;
}

bool same_name(const std::string &a, const std::string &b) {
    return a.size() == b.size() && lower(a) == lower(b);
}

std::string parent_of(const std::string &path) {
    const size_t slash = path.find_last_of('/');
    return slash == std::string::npos ? std::string() : path.substr(0, slash);
}

std::string base_of(const std::string &path) {
    const size_t slash = path.find_last_of('/');
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

bool is_dir(const std::string &path) {
    OsStat st{};
    return os_stat(path.c_str(), &st) == 0 && st.is_dir;
}

bool is_file(const std::string &path) {
    OsStat st{};
    return os_stat(path.c_str(), &st) == 0 && st.is_regular;
}

std::vector<std::string> children(const std::string &dir) {
    std::vector<std::string> names;
    os_listdir(
        dir.c_str(),
        [](const char *name, void *user) {
            static_cast<std::vector<std::string> *>(user)->push_back(name);
            return 0;
        },
        &names);
    std::sort(names.begin(), names.end());
    return names;
}

bool mkdirs(const std::string &path) {
    if (path.empty() || is_dir(path))
        return true;
    const std::string up = parent_of(path);
    if (!up.empty() && up != path && !mkdirs(up))
        return false;
    return os_mkdir(path.c_str()) == 0 || is_dir(path);
}

std::string normalize(std::string path) {
    std::replace(path.begin(), path.end(), '\\', '/');
    while (path.size() > 1 && path.back() == '/')
        path.pop_back();
    return path;
}

bool write_text(const std::string &path, const std::string &text) {
    const std::string part = path + ".part";
    FILE *f = fopen(part.c_str(), "wb");
    if (!f)
        return false;
    const bool ok = fwrite(text.data(), 1, text.size(), f) == text.size();
    if (fclose(f) != 0 || !ok) {
        os_unlink(part.c_str());
        return false;
    }
    return os_rename(part.c_str(), path.c_str()) == 0;
}

std::string read_text(const std::string &path) {
    std::ifstream in(path, std::ios::binary);
    std::stringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

std::string first_line(const std::string &text) {
    std::string line = text.substr(0, text.find('\n'));
    while (!line.empty() && (line.back() == '\r' || line.back() == ' '))
        line.pop_back();
    return line;
}

} // namespace

std::string find_name(const std::string &dir, const std::string &name) {
    // The spelling on disk, even where the file system would accept any case.
    std::string folded;
    for (const std::string &child : children(dir)) {
        if (child == name)
            return child;
        if (folded.empty() && same_name(child, name))
            folded = child;
    }
    return folded;
}

std::string find_root(const Spec &spec, const std::string &picked) {
    const std::string path = normalize(picked);
    if (path.empty())
        return "";
    if (is_file(path))
        return same_name(base_of(path), spec.executable) ? parent_of(path) : "";
    if (!is_dir(path))
        return "";
    // Breadth first, so the shallowest copy wins: an install often carries
    // backups or tools folders beside the real one.
    std::vector<std::string> level{path};
    for (int depth = 0; depth <= 2 && !level.empty(); ++depth) {
        std::vector<std::string> next;
        for (const std::string &dir : level) {
            const std::string name = find_name(dir, spec.executable);
            if (!name.empty() && is_file(join(dir, name)))
                return dir;
            if (depth < 2)
                for (const std::string &child : children(dir))
                    if (child[0] != '.' && is_dir(join(dir, child)))
                        next.push_back(join(dir, child));
        }
        level.swap(next);
    }
    return "";
}

const char *state_name(State s) {
    switch (s) {
    case State::NotFound:
        return "not found";
    case State::WrongVersion:
        return "wrong version";
    case State::Incomplete:
        return "incomplete";
    case State::Ready:
        return "ready";
    }
    return "?";
}

Status check(const Spec &spec, const std::string &root_in, const std::string &cache_file) {
    Status s;
    const std::string root = normalize(root_in);
    const std::string exe_name = root.empty() ? "" : find_name(root, spec.executable);
    if (exe_name.empty() || !is_file(join(root, exe_name)))
        return s;
    s.root = root;
    s.exe = join(root, exe_name);
    for (const std::string &dir : spec.required_dirs) {
        const std::string name = find_name(root, dir);
        if (name.empty() || !is_dir(join(root, name)))
            s.missing.push_back(dir);
    }
    OsStat st{};
    os_stat(s.exe.c_str(), &st);
    // An import's stamp names the digest it verified.
    const std::string stamp = first_line(read_text(join(root, ".stamp")));
    std::string digest;
    if (!stamp.empty() && stamp == spec.sha256)
        digest = stamp;
    // An install played in place: the digest cached for this size and mtime.
    const std::string key =
        s.exe + "\t" + std::to_string(st.size) + "\t" + std::to_string(st.mtime);
    if (digest.empty() && !cache_file.empty()) {
        std::istringstream lines(read_text(cache_file));
        for (std::string line; std::getline(lines, line);)
            if (line.size() > key.size() && line.compare(0, key.size(), key) == 0 &&
                line[key.size()] == '\t')
                digest = line.substr(key.size() + 1);
    }
    if (digest.empty()) {
        digest = sha256_file(s.exe);
        if (!cache_file.empty() && !digest.empty()) {
            std::string kept;
            std::istringstream lines(read_text(cache_file));
            for (std::string line; std::getline(lines, line);)
                if (line.compare(0, s.exe.size() + 1, s.exe + "\t") != 0)
                    kept += line + "\n";
            mkdirs(parent_of(cache_file));
            write_text(cache_file, kept + key + "\t" + digest + "\n");
        }
    }
    s.digest = digest;
    if (digest != spec.sha256)
        s.state = State::WrongVersion;
    else if (!s.missing.empty())
        s.state = State::Incomplete;
    else
        s.state = State::Ready;
    return s;
}

// ---------------------------------------------------------------------------
// Exclusion
// ---------------------------------------------------------------------------
bool wildcard_match(const char *p, const char *n) {
    for (; *p; ++p, ++n) {
        if (*p == '*') {
            while (p[1] == '*')
                ++p;
            if (!p[1])
                return true;
            for (const char *t = n; *t; ++t)
                if (wildcard_match(p + 1, t))
                    return true;
            return wildcard_match(p + 1, n + strlen(n));
        }
        if (!*n)
            return false;
        if (*p == '?')
            continue;
        if (*p == '[') {
            const char *q = p + 1;
            bool negate = *q == '!';
            if (negate)
                ++q;
            bool hit = false;
            for (; *q && *q != ']'; ++q) {
                if (q[1] == '-' && q[2] && q[2] != ']') {
                    const int lo = std::tolower((unsigned char)q[0]);
                    const int hi = std::tolower((unsigned char)q[2]);
                    const int c = std::tolower((unsigned char)*n);
                    hit |= c >= lo && c <= hi;
                    q += 2;
                } else
                    hit |= std::tolower((unsigned char)*q) == std::tolower((unsigned char)*n);
            }
            if (!*q || hit == negate)
                return false;
            p = q;
            continue;
        }
        if (std::tolower((unsigned char)*p) != std::tolower((unsigned char)*n))
            return false;
    }
    return !*n;
}

bool excluded(const std::string &relative, const std::vector<std::string> &patterns) {
    const std::string path = normalize(relative);
    const std::string top = path.substr(0, path.find('/'));
    const std::string name = base_of(path);
    for (const std::string &p : patterns)
        if (wildcard_match(p.c_str(), top.c_str()) || wildcard_match(p.c_str(), name.c_str()))
            return true;
    return false;
}

// ---------------------------------------------------------------------------
// SHA-256 (FIPS 180-4), streamed
// ---------------------------------------------------------------------------
namespace {
struct Sha256 {
    uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                     0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    uint8_t block[64];
    size_t used = 0;
    uint64_t length = 0;

    static uint32_t rotr(uint32_t x, int n) {
        return (x >> n) | (x << (32 - n));
    }
    void compress(const uint8_t *b) {
        static const uint32_t k[64] = {
            0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
            0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
            0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
            0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
            0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
            0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
            0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
            0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
            0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
            0xc67178f2};
        uint32_t w[64];
        for (int i = 0; i < 16; ++i)
            w[i] = uint32_t(b[i * 4]) << 24 | uint32_t(b[i * 4 + 1]) << 16 |
                   uint32_t(b[i * 4 + 2]) << 8 | b[i * 4 + 3];
        for (int i = 16; i < 64; ++i) {
            const uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = h[0], bb = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
        for (int i = 0; i < 64; ++i) {
            const uint32_t t1 =
                hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
            const uint32_t t2 =
                (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & bb) ^ (a & c) ^ (bb & c));
            hh = g;
            g = f;
            f = e;
            e = d + t1;
            d = c;
            c = bb;
            bb = a;
            a = t1 + t2;
        }
        h[0] += a;
        h[1] += bb;
        h[2] += c;
        h[3] += d;
        h[4] += e;
        h[5] += f;
        h[6] += g;
        h[7] += hh;
    }
    void update(const uint8_t *p, size_t n) {
        length += n;
        while (n) {
            const size_t take = std::min(n, sizeof block - used);
            memcpy(block + used, p, take);
            used += take;
            p += take;
            n -= take;
            if (used == sizeof block) {
                compress(block);
                used = 0;
            }
        }
    }
    std::string hex() {
        const uint64_t bits = length * 8;
        const uint8_t one = 0x80, zero = 0;
        update(&one, 1);
        while (used != 56)
            update(&zero, 1);
        uint8_t tail[8];
        for (int i = 0; i < 8; ++i)
            tail[i] = uint8_t(bits >> (56 - 8 * i));
        update(tail, 8);
        char out[65];
        for (int i = 0; i < 8; ++i)
            snprintf(out + i * 8, 9, "%08x", h[i]);
        return out;
    }
};
} // namespace

std::string sha256_file(const std::string &path) {
    const int fd = os_fd_open(path.c_str(), OS_O_RDONLY);
    if (fd < 0)
        return "";
    Sha256 sha;
    std::vector<uint8_t> buf(1 << 20);
    for (;;) {
        const int64_t n = os_fd_read(fd, buf.data(), buf.size());
        if (n < 0) {
            os_fd_close(fd);
            return "";
        }
        if (n == 0)
            break;
        sha.update(buf.data(), size_t(n));
    }
    os_fd_close(fd);
    return sha.hex();
}

// ---------------------------------------------------------------------------
// Sources
// ---------------------------------------------------------------------------
namespace {

class FolderSource final : public Source {
  public:
    explicit FolderSource(std::string root) : root_(normalize(std::move(root))) {}
    bool list(std::vector<Entry> *out, std::string *error) override {
        out->clear();
        if (!is_dir(root_)) {
            if (error)
                *error = root_ + " is not a folder";
            return false;
        }
        walk("", out);
        return true;
    }
    bool read(const Entry &e, const std::function<bool(const uint8_t *, size_t)> &sink,
              std::string *error) override {
        const std::string path = join(root_, e.relative);
        const int fd = os_fd_open(path.c_str(), OS_O_RDONLY);
        if (fd < 0) {
            if (error)
                *error = "cannot open " + path;
            return false;
        }
        std::vector<uint8_t> buf(1 << 20);
        bool ok = true;
        for (;;) {
            const int64_t n = os_fd_read(fd, buf.data(), buf.size());
            if (n < 0) {
                if (error)
                    *error = "cannot read " + path;
                ok = false;
                break;
            }
            if (n == 0 || !sink(buf.data(), size_t(n))) {
                ok = n == 0;
                break;
            }
        }
        os_fd_close(fd);
        return ok;
    }

    bool move_to(const Entry &e, const std::string &target) override {
        return os_rename(join(root_, e.relative).c_str(), target.c_str()) == 0;
    }

  private:
    void walk(const std::string &relative, std::vector<Entry> *out) {
        const std::string dir = join(root_, relative);
        for (const std::string &name : children(dir)) {
            const std::string rel = relative.empty() ? name : relative + "/" + name;
            OsStat st{};
            if (os_stat(join(root_, rel).c_str(), &st) != 0)
                continue;
            Entry e;
            e.relative = rel;
            e.is_dir = st.is_dir != 0;
            e.size = st.size;
            e.mtime = st.mtime;
            if (!e.is_dir && !st.is_regular)
                continue;
            out->push_back(e);
            if (e.is_dir)
                walk(rel, out);
        }
    }
    std::string root_;
};

bool has_parent_step(const std::string &relative) {
    size_t start = 0;
    for (;;) {
        const size_t end = relative.find('/', start);
        if (relative.compare(start, end == std::string::npos ? std::string::npos : end - start,
                             "..") == 0)
            return true;
        if (end == std::string::npos)
            return false;
        start = end + 1;
    }
}

class ZipSource final : public Source {
  public:
    ~ZipSource() override {
        if (open_)
            mz_zip_reader_end(&zip_);
        if (file_)
            fclose(file_);
    }
    bool open(FILE *file, std::string *error) {
        file_ = file;
        memset(&zip_, 0, sizeof zip_);
        if (!file_ || !mz_zip_reader_init_cfile(&zip_, file_, 0, 0)) {
            if (error)
                *error = std::string("not a readable ZIP: ") +
                         mz_zip_get_error_string(mz_zip_get_last_error(&zip_));
            return false;
        }
        open_ = true;
        return true;
    }
    bool list(std::vector<Entry> *out, std::string *) override {
        out->clear();
        const mz_uint count = mz_zip_reader_get_num_files(&zip_);
        for (mz_uint i = 0; i < count; ++i) {
            mz_zip_archive_file_stat st;
            if (!mz_zip_reader_file_stat(&zip_, i, &st))
                continue;
            Entry e;
            e.relative = normalize(st.m_filename);
            // No absolute or parent-relative names: a ZIP must not write
            // outside the folder it is imported into.
            if (e.relative.empty() || e.relative[0] == '/' ||
                e.relative.find(':') != std::string::npos || has_parent_step(e.relative))
                continue;
            e.is_dir = mz_zip_reader_is_file_a_directory(&zip_, i);
            e.size = st.m_uncomp_size;
            e.mtime = int64_t(st.m_time);
            e.index = i;
            out->push_back(e);
        }
        return true;
    }
    bool read(const Entry &e, const std::function<bool(const uint8_t *, size_t)> &sink,
              std::string *error) override {
        struct Ctx {
            const std::function<bool(const uint8_t *, size_t)> *sink;
            bool stopped;
        } ctx{&sink, false};
        auto cb = [](void *opaque, mz_uint64, const void *buf, size_t n) -> size_t {
            auto *c = static_cast<Ctx *>(opaque);
            if (!(*c->sink)(static_cast<const uint8_t *>(buf), n)) {
                c->stopped = true;
                return 0;
            }
            return n;
        };
        if (!mz_zip_reader_extract_to_callback(&zip_, e.index, cb, &ctx, 0)) {
            if (ctx.stopped)
                return false;
            if (error)
                *error = "cannot extract " + e.relative + ": " +
                         mz_zip_get_error_string(mz_zip_get_last_error(&zip_));
            return false;
        }
        return true;
    }

  private:
    mz_zip_archive zip_{};
    FILE *file_ = nullptr;
    bool open_ = false;
};

} // namespace

std::unique_ptr<Source> folder_source(const std::string &root) {
    return std::make_unique<FolderSource>(root);
}

std::unique_ptr<Source> zip_source_fd(int fd, std::string *error) {
    if (fd < 0) {
        if (error)
            *error = "no file";
        return nullptr;
    }
    FILE *f = static_cast<FILE *>(os_fdopen(fd, "rb"));
    if (!f) {
        os_fd_close(fd);
        if (error)
            *error = "cannot open the ZIP";
        return nullptr;
    }
    auto zip = std::make_unique<ZipSource>();
    if (!zip->open(f, error))
        return nullptr;
    return zip;
}

std::unique_ptr<Source> zip_source(const std::string &path, std::string *error) {
    const int fd = os_fd_open(path.c_str(), OS_O_RDONLY);
    if (fd < 0) {
        if (error)
            *error = "cannot open " + path;
        return nullptr;
    }
    return zip_source_fd(fd, error);
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------
const char *import_result_name(ImportResult r) {
    switch (r) {
    case ImportResult::Done:
        return "done";
    case ImportResult::Cancelled:
        return "cancelled";
    case ImportResult::NoExecutable:
        return "no executable";
    case ImportResult::WrongVersion:
        return "wrong version";
    case ImportResult::Incomplete:
        return "incomplete";
    case ImportResult::NoSpace:
        return "not enough space";
    case ImportResult::Failed:
        return "failed";
    }
    return "?";
}

namespace {
// Clutter an archive made on another system carries.
bool junk(const std::string &relative) {
    const std::string name = base_of(relative);
    return relative.compare(0, 9, "__MACOSX/") == 0 || relative == "__MACOSX" ||
           name == ".DS_Store" || name.compare(0, 2, "._") == 0 || same_name(name, "Thumbs.db") ||
           name == ".stamp";
}
} // namespace

ImportOutcome import_game(const Spec &spec, Source &source, const std::string &dest_in,
                          const std::function<bool(const Progress &)> &progress, bool move) {
    ImportOutcome out;
    const std::string dest = normalize(dest_in);
    std::vector<Entry> all;
    if (!source.list(&all, &out.error)) {
        out.result = ImportResult::Failed;
        return out;
    }
    // The shallowest executable decides which folder of the source is the game.
    const Entry *exe = nullptr;
    size_t exe_depth = SIZE_MAX;
    for (const Entry &e : all) {
        if (e.is_dir || junk(e.relative) || !same_name(base_of(e.relative), spec.executable))
            continue;
        const size_t depth = size_t(std::count(e.relative.begin(), e.relative.end(), '/'));
        if (depth < exe_depth && depth <= 3) {
            exe = &e;
            exe_depth = depth;
        }
    }
    if (!exe) {
        out.result = ImportResult::NoExecutable;
        out.error = "no " + spec.executable + " in the chosen files";
        return out;
    }
    const std::string base = parent_of(exe->relative);
    const std::string prefix = base.empty() ? "" : base + "/";
    // The source's entry, and its path under dest.
    struct File {
        Entry entry;
        std::string relative;
    };
    std::vector<File> files;
    std::vector<std::string> dirs;
    std::vector<std::string> tops;
    std::string exe_relative;
    for (const Entry &e : all) {
        if (junk(e.relative) || e.relative.compare(0, prefix.size(), prefix) != 0)
            continue;
        const std::string relative = e.relative.substr(prefix.size());
        if (relative.empty() || excluded(relative, spec.exclude))
            continue;
        const std::string top = relative.substr(0, relative.find('/'));
        if (relative.find('/') != std::string::npos || e.is_dir)
            tops.push_back(top);
        if (e.is_dir)
            dirs.push_back(relative);
        else {
            if (same_name(relative, spec.executable))
                exe_relative = relative;
            files.push_back({e, relative});
        }
    }
    for (const std::string &need : spec.required_dirs) {
        bool found = false;
        for (const std::string &t : tops)
            found |= same_name(t, need);
        if (!found) {
            out.result = ImportResult::Incomplete;
            out.error += (out.error.empty() ? "missing folder: " : ", ") + need;
        }
    }
    if (out.result == ImportResult::Incomplete)
        return out;

    if (!mkdirs(dest)) {
        out.result = ImportResult::Failed;
        out.error = "cannot create " + dest;
        return out;
    }
    // Incomplete until the very end, whatever happens between.
    os_unlink(join(dest, ".stamp").c_str());

    Progress p;
    p.files_total = files.size();
    std::vector<bool> complete(files.size(), false);
    uint64_t needed = 0;
    for (size_t i = 0; i < files.size(); ++i) {
        const Entry &e = files[i].entry;
        p.bytes_total += e.size;
        OsStat st{};
        const std::string target = join(dest, files[i].relative);
        complete[i] = os_stat(target.c_str(), &st) == 0 && st.is_regular && st.size == e.size &&
                      (!e.mtime || st.mtime == e.mtime);
        if (!complete[i])
            needed += e.size;
    }
    uint64_t free_bytes = 0;
    if (move)
        needed = 0; // a move needs no room; if one falls back to a copy, the write reports it
    if (os_free_space(dest.c_str(), &free_bytes) == 0 &&
        needed + spec.min_free_bytes > free_bytes) {
        out.result = ImportResult::NoSpace;
        out.bytes_needed = needed + spec.min_free_bytes;
        out.bytes_free = free_bytes;
        out.error = "not enough free space";
        return out;
    }
    for (const std::string &d : dirs)
        mkdirs(join(dest, d));

    bool cancelled = false;
    uint64_t last_report = 0;
    auto report = [&](bool force) {
        if (!progress)
            return true;
        if (!force && p.bytes_done - last_report < (4u << 20))
            return true;
        last_report = p.bytes_done;
        if (!progress(p))
            cancelled = true;
        return !cancelled;
    };
    for (size_t i = 0; i < files.size() && !cancelled; ++i) {
        const Entry &e = files[i].entry;
        p.current = files[i].relative;
        const std::string target = join(dest, files[i].relative);
        if (complete[i]) {
            ++out.files_skipped;
            p.bytes_done += e.size;
            ++p.files_done;
            report(true);
            continue;
        }
        mkdirs(parent_of(target));
        if (move && source.move_to(e, target)) {
            ++out.files_copied;
            p.bytes_done += e.size;
            ++p.files_done;
            report(true);
            continue;
        }
        const std::string part = target + ".part";
        const int fd = os_fd_open(part.c_str(), OS_O_WRONLY | OS_O_CREAT | OS_O_TRUNC);
        if (fd < 0) {
            out.result = ImportResult::Failed;
            out.error = "cannot write " + target;
            return out;
        }
        bool write_ok = true;
        const bool read_ok = source.read(
            e,
            [&](const uint8_t *data, size_t n) {
                size_t off = 0;
                while (off < n) {
                    const int64_t w = os_fd_write(fd, data + off, n - off);
                    if (w <= 0) {
                        write_ok = false;
                        return false;
                    }
                    off += size_t(w);
                }
                p.bytes_done += n;
                return report(false);
            },
            &out.error);
        os_fd_close(fd);
        if (cancelled || !read_ok || !write_ok) {
            os_unlink(part.c_str());
            if (cancelled) {
                out.result = ImportResult::Cancelled;
                return out;
            }
            out.result = write_ok ? ImportResult::Failed : ImportResult::NoSpace;
            if (!write_ok)
                out.error = "cannot write " + target + " (the disk may be full)";
            return out;
        }
        if (os_rename(part.c_str(), target.c_str()) != 0) {
            os_unlink(part.c_str());
            out.result = ImportResult::Failed;
            out.error = "cannot finish " + target;
            return out;
        }
        if (e.mtime)
            os_set_mtime(target.c_str(), e.mtime);
        ++out.files_copied;
        ++p.files_done;
        report(true);
    }
    if (cancelled) {
        out.result = ImportResult::Cancelled;
        return out;
    }
    const std::string digest = sha256_file(join(dest, exe_relative));
    if (digest != spec.sha256) {
        out.result = ImportResult::WrongVersion;
        out.error = spec.executable + " has SHA-256 " + (digest.empty() ? "(unreadable)" : digest) +
                    ", expected " + spec.sha256;
        return out;
    }
    if (!write_text(join(dest, ".stamp"), digest + "\n")) {
        out.result = ImportResult::Failed;
        out.error = "cannot write the stamp";
        return out;
    }
    out.result = ImportResult::Done;
    return out;
}

bool remove_tree(const std::string &path_in) {
    const std::string path = normalize(path_in);
    OsStat st{};
    if (os_lstat(path.c_str(), &st) != 0)
        return true;
    if (st.is_dir && !st.is_symlink) {
        for (const std::string &child : children(path))
            remove_tree(join(path, child));
        return os_rmdir(path.c_str()) == 0;
    }
    return os_unlink(path.c_str()) == 0;
}

// ---------------------------------------------------------------------------
// Remembered folders
// ---------------------------------------------------------------------------
std::vector<std::string> load_folders(const std::string &file) {
    std::vector<std::string> out;
    std::istringstream lines(read_text(file));
    for (std::string line; std::getline(lines, line);) {
        while (!line.empty() && (line.back() == '\r' || line.back() == ' '))
            line.pop_back();
        if (!line.empty() && std::find(out.begin(), out.end(), line) == out.end())
            out.push_back(line);
    }
    return out;
}

namespace {
bool save_folders(const std::string &file, const std::vector<std::string> &folders) {
    std::string text;
    for (size_t i = 0; i < folders.size() && i < 8; ++i)
        text += folders[i] + "\n";
    mkdirs(parent_of(file));
    return write_text(file, text);
}
} // namespace

bool remember_folder(const std::string &file, const std::string &root) {
    std::vector<std::string> folders = load_folders(file);
    folders.erase(std::remove(folders.begin(), folders.end(), normalize(root)), folders.end());
    folders.insert(folders.begin(), normalize(root));
    return save_folders(file, folders);
}

bool forget_folder(const std::string &file, const std::string &root) {
    std::vector<std::string> folders = load_folders(file);
    folders.erase(std::remove(folders.begin(), folders.end(), normalize(root)), folders.end());
    return save_folders(file, folders);
}

// ---------------------------------------------------------------------------
// Profile export and import
// ---------------------------------------------------------------------------
bool export_profile(const std::string &profile_dir, const std::string &zip_path,
                    const std::vector<std::string> &skip, std::string *error) {
    const std::string root = normalize(profile_dir);
    std::vector<Entry> entries;
    FolderSource src(root);
    if (!src.list(&entries, error))
        return false;
    mz_zip_archive zip{};
    const std::string part = zip_path + ".part";
    if (!mz_zip_writer_init_file(&zip, part.c_str(), 0)) {
        if (error)
            *error = "cannot create " + zip_path;
        return false;
    }
    bool ok = true;
    for (const Entry &e : entries) {
        const std::string top = e.relative.substr(0, e.relative.find('/'));
        bool skipped = junk(e.relative);
        for (const std::string &s : skip)
            skipped |= wildcard_match(s.c_str(), top.c_str());
        if (skipped || e.is_dir)
            continue;
        const std::string full = join(root, e.relative);
        if (!mz_zip_writer_add_file(&zip, e.relative.c_str(), full.c_str(), nullptr, 0,
                                    MZ_BEST_SPEED)) {
            if (error)
                *error = "cannot add " + e.relative;
            ok = false;
            break;
        }
    }
    ok = mz_zip_writer_finalize_archive(&zip) && ok;
    mz_zip_writer_end(&zip);
    if (!ok || os_rename(part.c_str(), zip_path.c_str()) != 0) {
        os_unlink(part.c_str());
        if (error && error->empty())
            *error = "cannot finish " + zip_path;
        return false;
    }
    return true;
}

bool import_profile(const std::string &zip_path, const std::string &profile_dir,
                    std::string *error) {
    auto zip = zip_source(zip_path, error);
    if (!zip)
        return false;
    std::vector<Entry> entries;
    if (!zip->list(&entries, error))
        return false;
    const std::string root = normalize(profile_dir);
    for (const Entry &e : entries) {
        if (e.is_dir || junk(e.relative))
            continue;
        const std::string target = join(root, e.relative);
        mkdirs(parent_of(target));
        std::string data;
        if (!zip->read(
                e,
                [&](const uint8_t *p, size_t n) {
                    data.append(reinterpret_cast<const char *>(p), n);
                    return true;
                },
                error) ||
            !write_text(target, data))
            return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Detection
// ---------------------------------------------------------------------------
std::vector<std::string> detect_under(const Spec &spec, const std::vector<std::string> &bases) {
    std::vector<std::string> found;
    // Only whole installs: a folder with the executable but not the game's
    // data (an unpacked patch, say) is not offered.
    auto add = [&](const std::string &root) {
        if (root.empty() || std::find(found.begin(), found.end(), root) != found.end())
            return;
        for (const std::string &dir : spec.required_dirs) {
            const std::string name = find_name(root, dir);
            if (name.empty() || !is_dir(join(root, name)))
                return;
        }
        found.push_back(root);
    };
    for (const std::string &base_in : bases) {
        const std::string base = normalize(base_in);
        if (!is_dir(base))
            continue;
        // The base itself may be the game (a registry or Steam path).
        const std::string exe = find_name(base, spec.executable);
        if (!exe.empty() && is_file(join(base, exe))) {
            add(base);
            continue;
        }
        const std::vector<std::string> names = children(base);
        for (const std::string &child : names) {
            if (child[0] == '.')
                continue;
            const std::string dir = join(base, child);
            if (!is_dir(dir))
                continue;
            bool named = false;
            for (const std::string &n : spec.install_names)
                named |= same_name(n, child);
            // A folder with a known name may hold the game a level or two
            // down; any other folder only directly.
            if (named)
                add(find_root(spec, dir));
            else {
                const std::string e = find_name(dir, spec.executable);
                if (!e.empty() && is_file(join(dir, e)))
                    add(dir);
            }
        }
    }
    return found;
}

namespace {
std::string env(const char *name) {
    const char *v = getenv(name);
    return v ? v : "";
}

// drive_c folders where a Windows install usually lands.
void add_drive_c(std::vector<std::string> *bases, const std::string &drive_c) {
    for (const char *sub :
         {"GOG Games", "Program Files (x86)/GOG Galaxy/Games", "Program Files (x86)/GOG.com",
          "Program Files (x86)", "Program Files", "Games"})
        bases->push_back(join(drive_c, sub));
}

// Steam's libraryfolders.vdf lists every library: "path"  "<dir>".
void add_steam_libraries(std::vector<std::string> *bases, const std::string &steam_root) {
    bases->push_back(join(steam_root, "steamapps/common"));
    std::istringstream lines(read_text(join(steam_root, "steamapps/libraryfolders.vdf")));
    for (std::string line; std::getline(lines, line);) {
        const size_t key = line.find("\"path\"");
        if (key == std::string::npos)
            continue;
        const size_t open = line.find('"', key + 6);
        const size_t close = open == std::string::npos ? open : line.find('"', open + 1);
        if (close == std::string::npos)
            continue;
        std::string path;
        for (size_t i = open + 1; i < close; ++i) {
            if (line[i] == '\\' && i + 1 < close && line[i + 1] == '\\')
                ++i;
            path.push_back(line[i]);
        }
        bases->push_back(join(normalize(path), "steamapps/common"));
    }
}

void add_bottles(std::vector<std::string> *bases, const std::string &bottles) {
    for (const std::string &b : children(bottles))
        add_drive_c(bases, join(join(bottles, b), "drive_c"));
}
} // namespace

std::vector<std::string> detect_installs(const Spec &spec) {
    std::vector<std::string> bases;
    // Tests: RECOMP_LAUNCHER_SEARCH=<dir>:<dir> replaces the places searched,
    // so a run does not depend on what this machine has installed.
    if (const char *search = recomp_env("LAUNCHER_SEARCH"); search && *search) {
        std::istringstream dirs(search);
        for (std::string dir; std::getline(dirs, dir, ':');)
            if (!dir.empty())
                bases.push_back(dir);
        return detect_under(spec, bases);
    }
#ifdef _WIN32
    char buf[4096];
    for (const std::string &id : spec.gog_ids)
        if (os_registry_read(("HKLM\\SOFTWARE\\GOG.com\\Games\\" + id).c_str(), "path", buf,
                             sizeof buf) == 0)
            bases.push_back(normalize(buf));
    for (const std::string &id : spec.steam_ids)
        if (os_registry_read(("HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
                              "Steam App " +
                              id)
                                 .c_str(),
                             "InstallLocation", buf, sizeof buf) == 0)
            bases.push_back(normalize(buf));
    if (os_registry_read("HKCU\\Software\\Valve\\Steam", "SteamPath", buf, sizeof buf) == 0)
        add_steam_libraries(&bases, normalize(buf));
    const std::string system_drive = env("SystemDrive").empty() ? "C:" : env("SystemDrive");
    add_drive_c(&bases, system_drive + "/");
#elif defined(__APPLE__)
    const std::string home = env("HOME");
    bases.push_back(join(home, "Games"));
    bases.push_back(join(home, "Downloads"));
    bases.push_back(join(home, "Documents"));
    bases.push_back(join(home, "Desktop"));
    bases.push_back(join(home, "Applications"));
    bases.push_back("/Applications");
    add_drive_c(&bases, join(home, ".wine/drive_c"));
    add_bottles(&bases, join(home, "Library/Application Support/CrossOver/Bottles"));
    add_bottles(&bases, join(home, "Library/Containers/com.isaacmarovitz.Whisky/Bottles"));
    add_steam_libraries(&bases, join(home, "Library/Application Support/Steam"));
    for (const std::string &v : children("/Volumes"))
        bases.push_back(join("/Volumes", v));
#else
    const std::string home = env("HOME");
    bases.push_back(join(home, "Games"));
    bases.push_back(join(home, "Games/Heroic"));
    bases.push_back(join(home, "Downloads"));
    add_drive_c(&bases, join(home, ".wine/drive_c"));
    for (const std::string &g : children(join(home, "Games")))
        add_drive_c(&bases, join(join(join(home, "Games"), g), "drive_c"));
    for (const char *steam :
         {".local/share/Steam", ".steam/steam", ".var/app/com.valvesoftware.Steam/data/Steam"}) {
        const std::string root = join(home, steam);
        add_steam_libraries(&bases, root);
        const std::string compat = join(root, "steamapps/compatdata");
        for (const std::string &id : children(compat))
            add_drive_c(&bases, join(join(compat, id), "pfx/drive_c"));
    }
    const std::string user = env("USER");
    for (const char *media : {"/run/media/", "/media/"})
        for (const std::string &v : children(media + user))
            bases.push_back(join(media + user, v));
#endif
    return detect_under(spec, bases);
}

} // namespace launcher
