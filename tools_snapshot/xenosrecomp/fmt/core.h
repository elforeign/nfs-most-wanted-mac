// fmt/core.h - a minimal stand-in for the {fmt} subset the XenosRecomp translator core uses (this repository's own code,
// MIT, see LICENSE). Supports exactly what shader_recompiler.* format: "{}", "{N}" and "{:X}" with integers, bool,
// char, floating point (shortest round-trip text, as {fmt}), C strings, std::string and std::string_view. Any other
// argument type or format specification is a compile error / an abort, never silently different text.
//
// Proof (2026-10-27, owner machine): all 205 shader containers of the retail ZZDATA0.BIN translate to text
// byte-identical to the same translator built with {fmt} (205/205 cmp); built with UNLEASHED_RECOMP instead: 0/205.
#pragma once

#include <charconv>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <iterator>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

namespace fmt {

struct arg {
    std::string text;      // the "{}" spelling
    bool integral = false;
    unsigned long long bits = 0;   // the value for "{:X}" (two's complement for negatives, as {fmt} prints them with '-')
    bool negative = false;
};

template<class T>
inline arg make_arg(const T& v)
{
    using U = std::decay_t<T>;
    arg a;
    if constexpr (std::is_same_v<U, bool>) {
        a.text = v ? "true" : "false";
    } else if constexpr (std::is_same_v<U, char>) {
        a.text = std::string(1, v);
    } else if constexpr (std::is_integral_v<U> || std::is_enum_v<U>) {
        static_assert(!std::is_enum_v<U>, "fmt shim: enums are not formatted by {fmt} either");
        a.text = std::to_string(v);
        a.integral = true;
        if constexpr (std::is_signed_v<U>) {
            a.negative = v < 0;
            a.bits = a.negative ? 0ull - (unsigned long long)(long long)v : (unsigned long long)v;
        } else {
            a.bits = (unsigned long long)v;
        }
    } else if constexpr (std::is_floating_point_v<U>) {
        char buf[64];
        auto r = std::to_chars(buf, buf + sizeof buf, v);
        a.text.assign(buf, r.ptr);
    } else if constexpr (std::is_same_v<U, const char*> || std::is_same_v<U, char*>) {
        a.text = v;
    } else if constexpr (std::is_same_v<U, std::string> || std::is_same_v<U, std::string_view>) {
        a.text = std::string(v);
    } else {
        static_assert(sizeof(U) == 0, "fmt shim: unsupported argument type");
    }
    return a;
}

template<size_t N>
inline arg make_arg(const char (&v)[N]) { arg a; a.text = v; return a; }

using format_args = std::vector<arg>;

template<class... Args>
inline format_args make_format_args(const Args&... args) { return format_args{make_arg(args)...}; }

template<class... Args>
struct basic_format_string {
    std::string_view s;
    template<class S, class = std::enable_if_t<std::is_convertible_v<const S&, std::string_view>>>
    constexpr basic_format_string(const S& str) : s(str) {}
    constexpr std::string_view get() const { return s; }
};
template<class T> struct type_identity { using type = T; };
template<class... Args>
using format_string = basic_format_string<typename type_identity<Args>::type...>;

[[noreturn]] inline void fail(std::string_view f)
{
    std::fprintf(stderr, "fmt shim: unsupported format \"%.*s\"\n", int(f.size()), f.data());
    std::abort();
}

inline void vformat_to(std::back_insert_iterator<std::string> it, std::string_view f, const format_args& args)
{
    size_t next = 0;
    for (size_t i = 0; i < f.size(); ++i) {
        char c = f[i];
        if (c == '{' && i + 1 < f.size() && f[i + 1] == '{') { *it++ = '{'; ++i; continue; }
        if (c == '}' && i + 1 < f.size() && f[i + 1] == '}') { *it++ = '}'; ++i; continue; }
        if (c != '{') { *it++ = c; continue; }
        size_t end = f.find('}', i);
        if (end == std::string_view::npos) fail(f);
        std::string_view spec = f.substr(i + 1, end - i - 1);
        size_t index = next;
        bool hex = false;
        size_t colon = spec.find(':');
        std::string_view id = spec.substr(0, colon), fmtspec = colon == std::string_view::npos ? "" : spec.substr(colon + 1);
        if (!id.empty()) {
            index = 0;
            for (char d : id) { if (d < '0' || d > '9') fail(f); index = index * 10 + size_t(d - '0'); }
        } else {
            ++next;
        }
        if (fmtspec == "X") hex = true;
        else if (!fmtspec.empty()) fail(f);
        if (index >= args.size()) fail(f);
        const arg& a = args[index];
        std::string text = a.text;
        if (hex) {
            if (!a.integral) fail(f);
            char buf[32];
            std::snprintf(buf, sizeof buf, "%s%llX", a.negative ? "-" : "", a.bits);
            text = buf;
        }
        for (char t : text) *it++ = t;
        i = end;
    }
}

template<class... Args>
inline std::string format(format_string<Args...> f, Args&&... args)
{
    std::string out;
    vformat_to(std::back_inserter(out), f.get(), make_format_args(args...));
    return out;
}

} // namespace fmt
