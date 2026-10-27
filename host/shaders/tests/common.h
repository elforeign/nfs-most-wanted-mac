// common.h - test-data helpers (manifest, .bin loading, string utils)
#pragma once
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

struct ManifestEntry { std::string effect, file, profile; };

inline std::vector<std::string> split(const std::string &s, char c) {
    std::vector<std::string> r;
    std::string cur;
    for (char ch : s) {
        if (ch == c) { r.push_back(cur); cur.clear(); }
        else cur += ch;
    }
    r.push_back(cur);
    return r;
}

inline std::vector<std::string> split_lines(const std::string &s) {
    std::vector<std::string> r = split(s, '\n');
    while (!r.empty() && r.back().empty()) r.pop_back();
    return r;
}

inline std::string trim(const std::string &s) {
    size_t a = s.find_first_not_of(" \t\r"), b = s.find_last_not_of(" \t\r");
    return a == std::string::npos ? "" : s.substr(a, b - a + 1);
}

inline std::string strip_comment(const std::string &s) {
    size_t p = s.find("//");
    return p == std::string::npos ? s : s.substr(0, p);
}

inline std::vector<ManifestEntry> read_manifest(const std::string &dir) {
    std::vector<ManifestEntry> r;
    std::ifstream f(dir + "/manifest.tsv");
    std::string line;
    std::getline(f, line);
    while (std::getline(f, line)) {
        auto c = split(line, '\t');
        if (c.size() >= 3) r.push_back({c[0], c[1], c[2]});
    }
    return r;
}

inline std::vector<uint32_t> read_tokens(const std::string &path) {
    std::ifstream f(path, std::ios::binary);
    std::string d((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    std::vector<uint32_t> t(d.size() / 4);
    if (!t.empty()) memcpy(t.data(), d.data(), t.size() * 4);
    return t;
}
