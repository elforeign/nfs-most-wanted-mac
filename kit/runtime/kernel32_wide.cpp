// UTF-16 kernel32 entry points. File operations share the ANSI shim's path seam.
#include "kernel32_internal.h"
#include "windows_version.h"
#include "loader.h"
#include "resources.h"
#include "memory.h"
#include "win32.h"
#include "../platform/os.h"
#include <algorithm>
#include <cstring>
#include <string>
#include <vector>
#include <ctime>
#include <map>
#include <cctype>

namespace {
// gm_wstr produces valid UTF-8. Count UTF-16 units, including surrogate pairs.
uint32_t wide_units(const std::string &s) {
    uint32_t n = 0;
    for (unsigned char ch : s) {
        if ((ch & 0xc0) != 0x80)
            n += ch >= 0xf0 ? 2 : 1;
    }
    return n;
}

void k_CreateFileW(X86 *c) {
    create_file_named(c, gm_wstr(arg(c, 0)));
}
void k_GetFileAttributesW(X86 *c) {
    get_file_attributes_named(c, gm_wstr(arg(c, 0)));
}
void k_GetFileAttributesExW(X86 *c) {
    get_file_attributes_ex_named(c, gm_wstr(arg(c, 0)));
}
void k_SetFileAttributesW(X86 *c) {
    set_file_attributes_named(c, gm_wstr(arg(c, 0)));
}
void k_DeleteFileW(X86 *c) {
    delete_file_named(c, gm_wstr(arg(c, 0)));
}
void k_CopyFileW(X86 *c) {
    copy_file_named(c, gm_wstr(arg(c, 0)), gm_wstr(arg(c, 1)));
}
void k_CreateDirectoryW(X86 *c) {
    create_directory_named(c, gm_wstr(arg(c, 0)));
}
void k_RemoveDirectoryW(X86 *c) {
    remove_directory_named(c, gm_wstr(arg(c, 0)));
}
void k_FindFirstFileW(X86 *c) {
    if (!arg(c, 1) || !gm_valid(arg(c, 1), 592)) {
        set_last_error(87);
        set_eax(c, 0xffffffffu);
        return;
    }
    find_first_named(c, gm_wstr(arg(c, 0)), true);
}
void k_FindNextFileW(X86 *c) {
    if (!arg(c, 1) || !gm_valid(arg(c, 1), 592)) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    find_next(c, true);
}
void k_GetFullPathNameW(X86 *c) {
    std::string full = full_path_named(gm_wstr(arg(c, 0)));
    std::replace(full.begin(), full.end(), '/', '\\');
    uint32_t len = arg(c, 1), buf = arg(c, 2), pfile = arg(c, 3);
    uint32_t need = wide_units(full) + 1;
    if (!buf || len < need) {
        set_eax(c, need);
        return;
    }
    uint32_t n = gm_put_wstr(buf, full, len);
    if (pfile) {
        size_t slash = full.find_last_of('\\');
        wr32(pfile,
             buf + (slash == std::string::npos ? 0 : 2 * wide_units(full.substr(0, slash + 1))));
    }
    set_eax(c, n);
}
void k_GetSystemDirectoryW(X86 *c) {
    const std::string dir = "C:\\Windows\\System32";
    uint32_t cap = arg(c, 1);
    set_eax(c, !arg(c, 0) || cap <= dir.size() ? (uint32_t)dir.size() + 1
                                               : gm_put_wstr(arg(c, 0), dir, cap));
}
void k_GetVolumeInformationW(X86 *c) {
    volume_information_named(c, gm_wstr(arg(c, 0)), true);
}
void k_GetDriveTypeW(X86 *c) {
    drive_type_named(c, gm_wstr(arg(c, 0)));
}
void k_GetLogicalDriveStringsW(X86 *c) {
    logical_drive_strings(c, true);
}
void k_GetDiskFreeSpaceW(X86 *c) {
    disk_free_space(c);
}

void k_QueryDosDeviceW(X86 *c) {
    uint32_t name = arg(c, 0), out = arg(c, 1), cap = arg(c, 2);
    std::string dev = gm_wstr(name);
    if (name && os_strcasecmp(dev.c_str(), "C:") != 0) {
        set_last_error(2);
        set_eax(c, 0);
        return;
    }
    std::string result = name ? "\\Device\\HarddiskVolume1" : "C:";
    uint32_t need = wide_units(result) + 2; // MULTI_SZ includes its extra NUL.
    if (!out || cap < need) {
        set_last_error(122);
        set_eax(c, 0);
        return;
    }
    gm_put_wstr(out, result, cap);
    wr16(out + (need - 1) * 2, 0);
    set_eax(c, need);
}

// INI files are read through the overlay and rewritten through its write tier.
// Preserve unrelated lines, comments and existing UTF-16LE encoding.
struct ProfileFile {
    std::vector<std::string> lines;
    bool utf16 = false;
};
std::string trim(std::string s) {
    size_t first = s.find_first_not_of(" \t\r\n"), last = s.find_last_not_of(" \t\r\n");
    return first == std::string::npos ? "" : s.substr(first, last - first + 1);
}
bool equal_name(const std::string &a, const std::string &b) {
    return os_strcasecmp(a.c_str(), b.c_str()) == 0;
}
bool profile_read(const std::string &name, ProfileFile &ini) {
    std::string path = win32_host_path_op(name, WIN32_FILE_READ);
    if (path.empty())
        return true; // A missing INI starts empty.
    FILE *f = fopen(path.c_str(), "rb");
    if (!f)
        return false;
    std::string text;
    char chunk[4096];
    size_t n;
    while ((n = fread(chunk, 1, sizeof chunk, f)))
        text.append(chunk, n);
    bool ok = !ferror(f);
    fclose(f);
    if (!ok)
        return false;
    if (text.size() >= 2 && (uint8_t)text[0] == 0xff && (uint8_t)text[1] == 0xfe) {
        ini.utf16 = true;
        if (text.size() > GUEST_SIZE)
            return false;
        uint32_t tmp = heap_alloc((uint32_t)text.size() + 2, true);
        if (!tmp)
            return false;
        memcpy(g_mem + tmp, text.data() + 2, text.size() - 2);
        text = gm_wstr(tmp, text.size() / 2);
        heap_free(tmp);
    } else if (text.compare(0, 3, "\xef\xbb\xbf") == 0)
        text.erase(0, 3);
    size_t start = 0;
    while (start < text.size()) {
        size_t end = text.find('\n', start);
        std::string line = text.substr(start, end == std::string::npos ? end : end - start);
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        ini.lines.push_back(line);
        if (end == std::string::npos)
            break;
        start = end + 1;
    }
    return true;
}
// Return a section heading or a key/value pair without interpreting comments.
bool profile_section(const std::string &line, std::string &section) {
    std::string s = trim(line);
    if (s.size() < 2 || s.front() != '[' || s.back() != ']')
        return false;
    section = trim(s.substr(1, s.size() - 2));
    return true;
}
bool profile_key(const std::string &line, std::string &key, std::string &value) {
    std::string s = trim(line);
    size_t eq = s.find('=');
    if (s.empty() || s.front() == ';' || s.front() == '#' || eq == std::string::npos)
        return false;
    key = trim(s.substr(0, eq));
    value = trim(s.substr(eq + 1));
    if (value.size() >= 2 && (value.front() == '"' || value.front() == '\'') &&
        value.back() == value.front())
        value = value.substr(1, value.size() - 2);
    return !key.empty();
}
void k_GetPrivateProfileStringW(X86 *c) {
    std::string section = gm_wstr(arg(c, 0)), key = gm_wstr(arg(c, 1));
    uint32_t out = arg(c, 3), cap = arg(c, 4);
    ProfileFile ini;
    if (!profile_read(gm_wstr(arg(c, 5)), ini)) {
        set_last_error(5);
        set_eax(c, 0);
        return;
    }
    bool multi = !arg(c, 0) || !arg(c, 1);
    std::string result = trim(gm_wstr(arg(c, 2))), current, k, value;
    std::vector<std::string> names;
    for (const std::string &line : ini.lines) {
        if (profile_section(line, current)) {
            if (!arg(c, 0))
                names.push_back(current);
        } else if (equal_name(current, section) && profile_key(line, k, value)) {
            if (!arg(c, 1))
                names.push_back(k);
            else if (equal_name(k, key)) {
                result = value;
                break;
            }
        }
    }
    if (!out || cap == 0) {
        set_eax(c, 0);
        return;
    }
    if (!multi) {
        set_eax(c, gm_put_wstr(out, result, cap));
        return;
    }
    // MULTI_SZ sizes include each name's terminator, excluding the final one.
    uint32_t used = 0;
    wr16(out, 0);
    if (cap == 1) {
        set_eax(c, 0);
        return;
    }
    for (const auto &name : names) {
        uint32_t need = wide_units(name) + 1;
        if (need >= cap - used) {
            gm_put_wstr(out + used * 2, name, cap - used - 1);
            wr16(out + (cap - 2) * 2, 0);
            wr16(out + (cap - 1) * 2, 0);
            set_eax(c, cap - 2);
            return;
        }
        used += gm_put_wstr(out + used * 2, name, cap - used) + 1;
    }
    wr16(out + used * 2, 0);
    if (used == 0)
        wr16(out + 2, 0);
    set_eax(c, used);
}
void k_WritePrivateProfileStringW(X86 *c) {
    if (!arg(c, 0)) {
        set_eax(c, !arg(c, 1) && !arg(c, 2));
        return;
    } // cache flush
    ProfileFile ini;
    std::string name = gm_wstr(arg(c, 3));
    if (name.empty() || !profile_read(name, ini)) {
        set_last_error(5);
        set_eax(c, 0);
        return;
    }
    std::string section = gm_wstr(arg(c, 0)), key = gm_wstr(arg(c, 1)), current, k, value;
    std::string replacement = key + "=" + gm_wstr(arg(c, 2));
    std::vector<std::string> lines;
    bool inside = false, found_section = false, found_key = false;
    for (const auto &line : ini.lines) {
        if (profile_section(line, current)) {
            if (inside && !found_key && arg(c, 1) && arg(c, 2)) {
                lines.push_back(replacement);
                found_key = true;
            }
            inside = equal_name(current, section);
            if (inside)
                found_section = true;
        } else if (inside && profile_key(line, k, value) && equal_name(k, key)) {
            if (!found_key && arg(c, 2))
                lines.push_back(replacement);
            found_key = true;
            continue;
        }
        if (!(inside && !arg(c, 1)))
            lines.push_back(line);
    }
    if (!found_key && arg(c, 1) && arg(c, 2)) {
        if (!found_section)
            lines.push_back("[" + section + "]");
        lines.push_back(replacement);
    }
    std::string text;
    for (const auto &line : lines)
        text += line + "\r\n";
    if (ini.utf16) {
        if (text.size() > GUEST_SIZE / 2 - 1) {
            set_eax(c, 0);
            return;
        }
        uint32_t cap = (uint32_t)text.size() + 1, tmp = heap_alloc(cap * 2, true);
        if (!tmp) {
            set_eax(c, 0);
            return;
        }
        uint32_t n = gm_put_wstr(tmp, text, cap);
        text = std::string("\xff\xfe", 2) + std::string((char *)g_mem + tmp, n * 2);
        heap_free(tmp);
    }
    std::string path = win32_host_path_op(name, WIN32_FILE_WRITE);
    FILE *f = path.empty() ? nullptr : fopen(path.c_str(), "wb");
    if (!f) {
        set_last_error(5);
        set_eax(c, 0);
        return;
    }
    bool ok = fwrite(text.data(), 1, text.size(), f) == text.size();
    if (fclose(f) != 0)
        ok = false;
    win32_invalidate_dir_cache();
    set_eax(c, ok ? 1 : 0);
}

void k_CreateEventW(X86 *c) {
    create_event_named(c, gm_wstr(arg(c, 3)));
}
void k_CreateMutexW(X86 *c) {
    create_mutex_named(c, gm_wstr(arg(c, 2)));
}
void k_OpenMutexW(X86 *c) {
    open_mutex_named(c, gm_wstr(arg(c, 2)));
}
void k_CreateFileMappingW(X86 *c) {
    create_mapping_named(c, gm_wstr(arg(c, 5)));
}

uint32_t wstrlen(uint32_t p) {
    uint32_t n = 0;
    if (p)
        while (n < (GUEST_SIZE - std::min(p, GUEST_SIZE)) / 2 && rd16(p + n * 2))
            ++n;
    return n;
}
void k_lstrlenW(X86 *c) {
    set_eax(c, wstrlen(arg(c, 0)));
}
void k_lstrcatW(X86 *c) {
    uint32_t dest = arg(c, 0), src = arg(c, 1), n = wstrlen(dest), extra = wstrlen(src);
    if (dest && gm_valid(dest, n * 2) && extra < (GUEST_SIZE - dest) / 2 - n) {
        // memmove also preserves raw UTF-16 units when source and destination overlap.
        if (src)
            memmove(g_mem + dest + n * 2, g_mem + src, extra * 2);
        wr16(dest + (n + extra) * 2, 0);
    }
    set_eax(c, dest);
}
void k_FormatMessageW(X86 *c) {
    uint32_t flags = arg(c, 0), out = arg(c, 4), cap = arg(c, 5);
    if (!(flags & 0x1000) || (flags & (0x400 | 0x800)) || !out) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    std::string text = "Error " + std::to_string(arg(c, 2));
    uint32_t need = wide_units(text) + 1;
    if (flags & 0x100) { // FORMAT_MESSAGE_ALLOCATE_BUFFER
        cap = std::max(cap, need);
        if (cap > GUEST_SIZE / 2 || !gm_valid(out, 4)) {
            set_eax(c, 0);
            return;
        }
        uint32_t buffer = heap_alloc(cap * 2, true);
        if (!buffer) {
            set_last_error(8);
            set_eax(c, 0);
            return;
        }
        wr32(out, buffer);
        out = buffer;
    } else if (cap < need || !gm_valid(out, need * 2)) {
        set_last_error(122);
        set_eax(c, 0);
        return;
    }
    set_eax(c, gm_put_wstr(out, text, cap));
}
void k_OutputDebugStringW(X86 *c) {
    LOGV("OutputDebugStringW: %s", gm_wstr(arg(c, 0)).c_str());
    set_eax(c, 0);
}
// FILETIME is unsigned 100 ns ticks since 1601, SYSTEMTIME is eight WORDs.
// Keep conversion in UTC and use the platform time seam on every host.
bool filetime_fields(uint32_t p, struct tm &t, uint16_t &ms) {
    if (!p || !gm_valid(p, 8))
        return false;
    uint64_t ticks = (uint64_t)rd32(p) | ((uint64_t)rd32(p + 4) << 32);
    if (ticks >> 63)
        return false;
    int64_t seconds = (int64_t)(ticks / 10000000ull) - 11644473600ll;
    ms = (uint16_t)((ticks % 10000000ull) / 10000);
    return os_gmtime(seconds, &t) == 0;
}
void k_FileTimeToLocalFileTime(X86 *c) {
    uint32_t src = arg(c, 0), dst = arg(c, 1);
    bool ok = src && dst && gm_valid(src, 8) && gm_valid(dst, 8);
    if (ok)
        memmove(g_mem + dst, g_mem + src, 8); // The virtual machine uses UTC.
    set_eax(c, ok ? 1 : 0);
}
void k_FileTimeToSystemTime(X86 *c) {
    struct tm t{};
    uint16_t ms = 0;
    uint32_t out = arg(c, 1);
    if (!out || !gm_valid(out, 16) || !filetime_fields(arg(c, 0), t, ms)) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    const uint16_t fields[] = {(uint16_t)(t.tm_year + 1900), (uint16_t)(t.tm_mon + 1),
                               (uint16_t)t.tm_wday,          (uint16_t)t.tm_mday,
                               (uint16_t)t.tm_hour,          (uint16_t)t.tm_min,
                               (uint16_t)t.tm_sec,           ms};
    for (uint32_t i = 0; i < 8; ++i)
        wr16(out + i * 2, fields[i]);
    set_eax(c, 1);
}
void k_FileTimeToDosDateTime(X86 *c) {
    struct tm t{};
    uint16_t ms = 0;
    uint32_t date = arg(c, 1), time = arg(c, 2);
    if (!date || !time || !gm_valid(date, 2) || !gm_valid(time, 2) ||
        !filetime_fields(arg(c, 0), t, ms) || t.tm_year < 80 || t.tm_year > 207) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    wr16(date, (uint16_t)(((t.tm_year - 80) << 9) | ((t.tm_mon + 1) << 5) | t.tm_mday));
    wr16(time, (uint16_t)((t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec / 2)));
    set_eax(c, 1);
}
void k_GetDateFormatW(X86 *c) {
    uint32_t input = arg(c, 2), out = arg(c, 4), cap = arg(c, 5);
    int year, month, day;
    if (input) {
        if (!gm_valid(input, 16)) {
            set_eax(c, 0);
            return;
        }
        year = rd16(input);
        month = rd16(input + 2);
        day = rd16(input + 6);
    } else {
        struct tm t{};
        if (os_localtime((int64_t)(os_wall_time_us() / 1000000), &t) != 0) {
            set_eax(c, 0);
            return;
        }
        year = t.tm_year + 1900;
        month = t.tm_mon + 1;
        day = t.tm_mday;
    }
    if (month < 1 || month > 12 || day < 1 || day > 31) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    char text[32];
    snprintf(text, sizeof text, "%04d-%02d-%02d", year, month, day);
    uint32_t need = (uint32_t)strlen(text) + 1;
    if (cap == 0) {
        set_eax(c, need);
        return;
    }
    if (!out || cap < need) {
        set_last_error(122);
        set_eax(c, 0);
        return;
    }
    set_eax(c, gm_put_wstr(out, text, cap) + 1);
}

uint32_t g_thread_lcid = 0x0409; // The runtime exposes one process-wide locale.
void k_GetThreadLocale(X86 *c) {
    set_eax(c, g_thread_lcid);
}
void k_SetThreadLocale(X86 *c) {
    g_thread_lcid = arg(c, 0);
    set_eax(c, 1);
}
void k_GetUserDefaultUILanguage(X86 *c) {
    set_eax(c, 0x0409);
}
void k_GetSystemDefaultUILanguage(X86 *c) {
    set_eax(c, 0x0409);
}
void k_GetThreadUILanguage(X86 *c) {
    set_eax(c, 0x0409);
}

// The runtime exposes one installed UI language. Sizes are WCHAR counts,
// including both terminators of the single-entry multi-string.
void k_GetPreferredUILanguages(X86 *c) {
    uint32_t flags = arg(c, 0), count = arg(c, 1), out = arg(c, 2), size = arg(c, 3);
    if ((flags & 12) == 12 || !count || !gm_valid(count, 4) || !size || !gm_valid(size, 4)) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    const char *language = flags & 4 ? "0409" : "en-US";
    uint32_t need = (uint32_t)strlen(language) + 2, cap = rd32(size);
    wr32(count, 1);
    wr32(size, need);
    if (!out) {
        set_eax(c, 1);
        return;
    }
    if (cap < need || !gm_valid(out, need * 2)) {
        set_last_error(122);
        set_eax(c, 0);
        return;
    }
    gm_put_wstr(out, language, need);
    wr16(out + (need - 1) * 2, 0);
    set_eax(c, 1);
}
void k_SetThreadPreferredUILanguages(X86 *c) {
    // Acknowledge the preference; the installed-language set stays en-US.
    uint32_t count = arg(c, 2);
    if (count && !gm_valid(count, 4)) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    if (count)
        wr32(count, 1);
    set_eax(c, 1);
}
void k_IsDBCSLeadByteEx(X86 *c) {
    set_eax(c, 0);
}
void k_GetConsoleCP(X86 *c) {
    set_eax(c, 437);
}
void k_GetConsoleOutputCP(X86 *c) {
    set_eax(c, 437);
}
// The callback receives temporary guest storage. guest_call dispatches through
// recomp_call and restores the caller's registers/stack; no host lock is held.
void enumerate_text(X86 *c, const char *text) {
    uint32_t callback = arg(c, 0);
    if (!callback) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    uint32_t tmp = heap_alloc(((uint32_t)strlen(text) + 1) * 2, true);
    if (!tmp) {
        set_last_error(8);
        set_eax(c, 0);
        return;
    }
    gm_put_wstr(tmp, text, (uint32_t)strlen(text) + 1);
    guest_call(c, callback, tmp);
    heap_free(tmp);
    set_eax(c, 1);
}
void k_EnumSystemLocalesW(X86 *c) {
    enumerate_text(c, "00000409");
}
void k_EnumCalendarInfoW(X86 *c) {
    enumerate_text(c, "1");
}
void k_GetCPInfoExW(X86 *c) {
    uint32_t p = arg(c, 2);
    if (!p || !gm_valid(p, 544)) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    // CPINFOEXW: DefaultChar[2], LeadByte[12], WCHAR default, UINT code page,
    // then CodePageName[260]. The 32-bit guest record is exactly 544 bytes.
    memset(g_mem + p, 0, 544);
    wr32(p, 1);
    wr8(p + 4, '?');
    wr16(p + 18, '?');
    wr32(p + 20, 1252);
    gm_put_wstr(p + 24, "1252 (ANSI - Latin I)", 260);
    set_eax(c, 1);
}

uint32_t g_cmdline_w = 0;
std::map<std::string, uint16_t> g_atoms;
std::map<uint16_t, uint32_t> g_atom_refs;
uint32_t g_next_atom = 0xc000;
void k_GetCommandLineW(X86 *c) {
    if (!g_cmdline_w) {
        get_command_line(c);
        std::string line = gm_str(c->r[R_EAX]);
        uint32_t cap = (uint32_t)line.size() + 1;
        g_cmdline_w = heap_alloc(cap * 2, true);
        if (g_cmdline_w)
            gm_put_wstr(g_cmdline_w, line, cap);
    }
    set_eax(c, g_cmdline_w);
}
void k_GetStartupInfoW(X86 *c) {
    startup_info(c);
}
void k_VerifyVersionInfoW(X86 *c) {
    uint32_t p = arg(c, 0), types = arg(c, 1);
    uint64_t mask = (uint64_t)arg(c, 2) | ((uint64_t)arg(c, 3) << 32);
    auto fail = [&](uint32_t error) {
        set_last_error(error);
        set_eax(c, 0);
    };
    if (!p || !gm_valid(p, 284) || rd32(p) != 284 || !types || (types & ~0xffu)) {
        fail(87); // ERROR_INVALID_PARAMETER
        return;
    }
    uint32_t conditions[8] = {};
    for (unsigned bit = 0; bit < 8; ++bit) {
        if (!(types & (1u << bit)))
            continue;
        uint32_t op = conditions[bit] = (uint32_t)(mask >> (3 * bit)) & 7;
        if (bit == 6 ? (op != 6 && op != 7) : (op < 1 || op > 5)) {
            fail(87);
            return;
        }
    }
    auto compare = [](uint32_t actual, uint32_t wanted, uint32_t op) {
        switch (op) {
        case 1:
            return actual == wanted;
        case 2:
            return actual > wanted;
        case 3:
            return actual >= wanted;
        case 4:
            return actual < wanted;
        case 5:
            return actual <= wanted;
        default:
            return false;
        }
    };
    uint32_t actual[] = {windows_version::minor,
                         windows_version::major,
                         windows_version::build,
                         windows_version::platform,
                         0,
                         windows_version::service_pack,
                         0,
                         1};
    uint32_t wanted[] = {rd32(p + 8),   rd32(p + 4),   rd32(p + 12),  rd32(p + 16),
                         rd16(p + 278), rd16(p + 276), rd16(p + 280), rd8(p + 282)};
    bool matched = true;
    // Major, minor, SP major and SP minor form one ordered version tuple.
    // Equality permits the next field's own condition. Once a direction is
    // chosen, lower fields may narrow it to equality but cannot reverse it.
    uint32_t tuple_op = 0;
    for (unsigned bit : {1u, 0u, 5u, 4u}) {
        if (!(types & (1u << bit)))
            continue;
        uint32_t op = conditions[bit];
        if (tuple_op <= 1)
            tuple_op = op;
        bool same_direction = (op >= 4) == (tuple_op >= 4);
        if (op != 1 && !same_direction)
            op = tuple_op;
        matched = compare(actual[bit], wanted[bit], op);
        if (actual[bit] != wanted[bit])
            break;
    }
    for (unsigned bit : {2u, 3u, 7u}) {
        if (types & (1u << bit))
            matched = matched && compare(actual[bit], wanted[bit], conditions[bit]);
    }
    if (types & 0x40)
        matched = matched && (conditions[6] == 6 ? (actual[6] & wanted[6]) == wanted[6]
                                                 : (actual[6] & wanted[6]) != 0);
    if (!matched)
        fail(1150); // ERROR_OLD_WIN_VERSION
    else
        set_eax(c, 1);
}
void k_VerSetConditionMask(X86 *c) {
    // ULONGLONG occupies two x86 slots. Both version APIs use stdcall (four
    // slots), and winnt.h's VER_NUM_BITS_PER_CONDITION_MASK is 3, not 7.
    uint64_t mask = (uint64_t)arg(c, 0) | ((uint64_t)arg(c, 1) << 32);
    uint32_t type = arg(c, 2), condition = arg(c, 3) & 7;
    for (int bit = 7; bit >= 0; --bit) {
        if (type & (1u << bit)) {
            mask |= (uint64_t)condition << (3 * bit);
            break;
        }
    }
    set_eax64(c, mask);
}
void k_GetCurrentProcessId(X86 *c) {
    set_eax(c, 1);
}
void k_IsDebuggerPresent(X86 *c) {
    set_eax(c, 0);
}
void k_SwitchToThread(X86 *c) {
    set_eax(c, 0);
}
void k_MulDiv(X86 *c) {
    int64_t product = (int64_t)(int32_t)arg(c, 0) * (int32_t)arg(c, 1);
    int64_t divisor = (int32_t)arg(c, 2);
    if (!divisor) {
        set_eax(c, 0xffffffffu);
        return;
    }
    bool negative = (product < 0) != (divisor < 0);
    int64_t magnitude = product < 0 ? -product : product;
    int64_t denominator = divisor < 0 ? -divisor : divisor;
    int64_t result = (magnitude + denominator / 2) / denominator;
    if (negative)
        result = -result;
    set_eax(c, result < INT32_MIN || result > INT32_MAX ? 0xffffffffu : (uint32_t)result);
}
void k_VirtualProtect(X86 *c) {
    uint32_t old = arg(c, 3);
    if (old && gm_valid(old, 4))
        wr32(old, 0x40);
    set_eax(c, 1);
}
// MEMORY_BASIC_INFORMATION is seven DWORDs on the 32-bit guest. Describe
// the queried page through the end of its image/heap/stack range, or the
// gap before the next range. These are arena categories, not host mappings.
uint32_t virtual_query(uint32_t address, uint32_t out, uint32_t len) {
    if (address >= GUEST_SIZE || !out || len < 28 || !gm_valid(out, 28)) {
        set_last_error(87);
        return 0;
    }
    struct Region {
        uint32_t base, end, type, protection;
    };
    std::vector<Region> regions = {{loader_image_base(), loader_image_limit(), 0x1000000, 0x40},
                                   {HEAP_BASE, HEAP_LIMIT, 0x20000, 4},
                                   {STACK_LIMIT, STACK_TOP, 0x20000, 4}};
    for (uint32_t i = 0; const LoaderModule *m = loader_module(i); ++i)
        regions.push_back({m->base, m->base + m->size, 0x1000000, 0x40});
    uint32_t page = address & ~0xfffu, end = GUEST_SIZE, allocation = 0, type = 0, protection = 0;
    for (const auto &r : regions) {
        if (address >= r.base && address < r.end) {
            allocation = r.base;
            end = r.end;
            type = r.type;
            protection = r.protection;
            break;
        }
        if (r.base > address)
            end = std::min(end, r.base);
    }
    wr32(out, page);
    wr32(out + 4, allocation);
    wr32(out + 8, protection);
    wr32(out + 12, end - page);
    wr32(out + 16, type ? 0x1000 : 0x10000);
    wr32(out + 20, protection);
    wr32(out + 24, type);
    return 28;
}
void k_VirtualQuery(X86 *c) {
    set_eax(c, virtual_query(arg(c, 0), arg(c, 1), arg(c, 2)));
}
void k_VirtualQueryEx(X86 *c) {
    set_eax(c, virtual_query(arg(c, 1), arg(c, 2), arg(c, 3)));
}
std::string atom_name(uint32_t p) {
    std::string name = gm_wstr(p);
    for (char &ch : name)
        ch = (char)tolower((unsigned char)ch);
    return name;
}
void k_GlobalAddAtomW(X86 *c) {
    uint32_t p = arg(c, 0);
    if (p < 0x10000) {
        set_eax(c, p < 0xc000 ? p : 0);
        return;
    }
    std::string name = atom_name(p);
    if (name.empty() || wide_units(name) > 255) {
        set_last_error(87);
        set_eax(c, 0);
        return;
    }
    auto found = g_atoms.find(name);
    if (found != g_atoms.end()) {
        ++g_atom_refs[found->second];
        set_eax(c, found->second);
        return;
    }
    for (uint32_t i = 0; i < 0x4000; ++i) {
        uint16_t atom = (uint16_t)g_next_atom;
        g_next_atom = g_next_atom == 0xffff ? 0xc000 : g_next_atom + 1;
        if (!g_atom_refs.count(atom)) {
            g_atoms[name] = atom;
            g_atom_refs[atom] = 1;
            set_eax(c, atom);
            return;
        }
    }
    set_last_error(8);
    set_eax(c, 0);
}
void k_GlobalFindAtomW(X86 *c) {
    uint32_t p = arg(c, 0);
    if (p < 0x10000) {
        set_eax(c, p < 0xc000 ? p : 0);
        return;
    }
    auto it = g_atoms.find(atom_name(p));
    set_eax(c, it == g_atoms.end() ? 0 : it->second);
}
void k_GlobalDeleteAtom(X86 *c) {
    uint16_t atom = (uint16_t)arg(c, 0);
    auto ref = g_atom_refs.find(atom);
    if (ref != g_atom_refs.end() && --ref->second == 0) {
        g_atom_refs.erase(ref);
        for (auto it = g_atoms.begin(); it != g_atoms.end(); ++it)
            if (it->second == atom) {
                g_atoms.erase(it);
                break;
            }
    }
    set_eax(c, 0);
}
void k_WaitForMultipleObjectsEx(X86 *c) {
    wait_multiple_objects(c);
}

bool resource_module(uint32_t module) {
    if (!module || module == loader_image_base())
        return true;
    set_last_error(1812);
    return false;
}
void k_FindResourceW(X86 *c) {
    if (!resource_module(arg(c, 0))) {
        set_eax(c, 0);
        return;
    }
    std::string why;
    uint32_t entry = resource_find(arg(c, 2), arg(c, 1), &why);
    if (!entry) {
        set_last_error(1814);
        LOGV("FindResourceW: %s", why.c_str());
    }
    set_eax(c, entry);
}
// NFSU2 (run 1): D3DXCreateEffectFromResourceA finds the game's 24 RT_RCDATA effects by ANSI name.
void k_FindResourceA(X86 *c) {
    if (!resource_module(arg(c, 0))) {
        set_eax(c, 0);
        return;
    }
    std::string why;
    uint32_t entry = resource_find(arg(c, 2), arg(c, 1), &why, true);
    if (!entry) {
        set_last_error(1814);
        LOGV("FindResourceA: %s", why.c_str());
    }
    set_eax(c, entry);
}
void k_LoadResource(X86 *c) {
    uint32_t data = resource_module(arg(c, 0)) ? resource_data(arg(c, 1), nullptr) : 0;
    if (!data)
        set_last_error(1812);
    set_eax(c, data);
}
void k_LockResource(X86 *c) {
    set_eax(c, arg(c, 0));
}
void k_SizeofResource(X86 *c) {
    uint32_t size = 0;
    if (!resource_module(arg(c, 0)) || !resource_data(arg(c, 1), &size))
        set_last_error(1812);
    set_eax(c, size);
}
void k_FreeResource(X86 *c) {
    set_eax(c, 0);
} // PE resources remain image-backed.
void k_EnumResourceNamesW(X86 *c) {
    uint32_t module = arg(c, 0), type = arg(c, 1), callback = arg(c, 2), param = arg(c, 3);
    std::vector<ResourceName> names;
    if (!callback || !resource_module(module) || !resource_names(type, &names)) {
        set_last_error(1814);
        set_eax(c, 0);
        return;
    }
    for (const auto &name : names) {
        uint32_t guest_name = name.id, temporary = 0;
        if (name.is_string) {
            uint32_t cap = wide_units(name.name) + 1;
            temporary = heap_alloc(cap * 2, true);
            if (!temporary) {
                set_last_error(8);
                set_eax(c, 0);
                return;
            }
            gm_put_wstr(temporary, name.name, cap);
            guest_name = temporary;
        }
        uint32_t keep_going = guest_call(c, callback, module, type, guest_name, param);
        if (temporary)
            heap_free(temporary);
        if (!keep_going)
            break;
    }
    set_eax(c, 1);
}

static const ImportShim g_kernel32_wide[] = {
    {"KERNEL32.dll", "FindResourceW", 3, k_FindResourceW},
    {"KERNEL32.dll", "FindResourceA", 3, k_FindResourceA},
    {"KERNEL32.dll", "LoadResource", 2, k_LoadResource},
    {"KERNEL32.dll", "LockResource", 1, k_LockResource},
    {"KERNEL32.dll", "SizeofResource", 2, k_SizeofResource},
    {"KERNEL32.dll", "FreeResource", 1, k_FreeResource},
    {"KERNEL32.dll", "EnumResourceNamesW", 4, k_EnumResourceNamesW},

    {"KERNEL32.dll", "GetCommandLineW", 0, k_GetCommandLineW},
    {"KERNEL32.dll", "GetStartupInfoW", 1, k_GetStartupInfoW},
    {"KERNEL32.dll", "VerifyVersionInfoW", 4, k_VerifyVersionInfoW},
    {"KERNEL32.dll", "VerSetConditionMask", 4, k_VerSetConditionMask},
    {"KERNEL32.dll", "GetCurrentProcessId", 0, k_GetCurrentProcessId},
    {"KERNEL32.dll", "IsDebuggerPresent", 0, k_IsDebuggerPresent},
    {"KERNEL32.dll", "SwitchToThread", 0, k_SwitchToThread},
    {"KERNEL32.dll", "MulDiv", 3, k_MulDiv},
    {"KERNEL32.dll", "VirtualProtect", 4, k_VirtualProtect},
    {"KERNEL32.dll", "VirtualQuery", 3, k_VirtualQuery},
    {"KERNEL32.dll", "VirtualQueryEx", 4, k_VirtualQueryEx},
    {"KERNEL32.dll", "GlobalAddAtomW", 1, k_GlobalAddAtomW},
    {"KERNEL32.dll", "GlobalFindAtomW", 1, k_GlobalFindAtomW},
    {"KERNEL32.dll", "GlobalDeleteAtom", 1, k_GlobalDeleteAtom},
    {"KERNEL32.dll", "WaitForMultipleObjectsEx", 5, k_WaitForMultipleObjectsEx},

    {"KERNEL32.dll", "GetThreadLocale", 0, k_GetThreadLocale},
    {"KERNEL32.dll", "SetThreadLocale", 1, k_SetThreadLocale},
    {"KERNEL32.dll", "EnumSystemLocalesW", 2, k_EnumSystemLocalesW},
    {"KERNEL32.dll", "EnumCalendarInfoW", 4, k_EnumCalendarInfoW},
    {"KERNEL32.dll", "GetCPInfoExW", 3, k_GetCPInfoExW},
    {"KERNEL32.dll", "GetUserDefaultUILanguage", 0, k_GetUserDefaultUILanguage},
    {"KERNEL32.dll", "GetSystemDefaultUILanguage", 0, k_GetSystemDefaultUILanguage},
    {"KERNEL32.dll", "GetThreadUILanguage", 0, k_GetThreadUILanguage},
    {"KERNEL32.dll", "GetThreadPreferredUILanguages", 4, k_GetPreferredUILanguages},
    {"KERNEL32.dll", "GetUserPreferredUILanguages", 4, k_GetPreferredUILanguages},
    {"KERNEL32.dll", "GetSystemPreferredUILanguages", 4, k_GetPreferredUILanguages},
    {"KERNEL32.dll", "SetThreadPreferredUILanguages", 3, k_SetThreadPreferredUILanguages},
    {"KERNEL32.dll", "IsDBCSLeadByteEx", 2, k_IsDBCSLeadByteEx},
    {"KERNEL32.dll", "GetConsoleCP", 0, k_GetConsoleCP},
    {"KERNEL32.dll", "GetConsoleOutputCP", 0, k_GetConsoleOutputCP},

    {"KERNEL32.dll", "lstrlenW", 1, k_lstrlenW},
    {"KERNEL32.dll", "lstrcatW", 2, k_lstrcatW},
    {"KERNEL32.dll", "FormatMessageW", 7, k_FormatMessageW},
    {"KERNEL32.dll", "OutputDebugStringW", 1, k_OutputDebugStringW},
    {"KERNEL32.dll", "GetDateFormatW", 6, k_GetDateFormatW},
    {"KERNEL32.dll", "FileTimeToLocalFileTime", 2, k_FileTimeToLocalFileTime},
    {"KERNEL32.dll", "FileTimeToSystemTime", 2, k_FileTimeToSystemTime},
    {"KERNEL32.dll", "FileTimeToDosDateTime", 3, k_FileTimeToDosDateTime},

    {"KERNEL32.dll", "CreateEventW", 4, k_CreateEventW},
    {"KERNEL32.dll", "CreateMutexW", 3, k_CreateMutexW},
    {"KERNEL32.dll", "OpenMutexW", 3, k_OpenMutexW},
    {"KERNEL32.dll", "CreateFileMappingW", 6, k_CreateFileMappingW},

    {"KERNEL32.dll", "GetPrivateProfileStringW", 6, k_GetPrivateProfileStringW},
    {"KERNEL32.dll", "WritePrivateProfileStringW", 4, k_WritePrivateProfileStringW},

    {"KERNEL32.dll", "CreateFileW", 7, k_CreateFileW},
    {"KERNEL32.dll", "FindFirstFileW", 2, k_FindFirstFileW},
    {"KERNEL32.dll", "FindNextFileW", 2, k_FindNextFileW},
    {"KERNEL32.dll", "GetFullPathNameW", 4, k_GetFullPathNameW},
    {"KERNEL32.dll", "GetFileAttributesW", 1, k_GetFileAttributesW},
    {"KERNEL32.dll", "GetFileAttributesExW", 3, k_GetFileAttributesExW},
    {"KERNEL32.dll", "SetFileAttributesW", 2, k_SetFileAttributesW},
    {"KERNEL32.dll", "DeleteFileW", 1, k_DeleteFileW},
    {"KERNEL32.dll", "CopyFileW", 3, k_CopyFileW},
    {"KERNEL32.dll", "CreateDirectoryW", 2, k_CreateDirectoryW},
    {"KERNEL32.dll", "RemoveDirectoryW", 1, k_RemoveDirectoryW},
    {"KERNEL32.dll", "GetSystemDirectoryW", 2, k_GetSystemDirectoryW},
    {"KERNEL32.dll", "GetVolumeInformationW", 8, k_GetVolumeInformationW},
    {"KERNEL32.dll", "GetDriveTypeW", 1, k_GetDriveTypeW},
    {"KERNEL32.dll", "GetLogicalDriveStringsW", 2, k_GetLogicalDriveStringsW},
    {"KERNEL32.dll", "GetDiskFreeSpaceW", 5, k_GetDiskFreeSpaceW},
    {"KERNEL32.dll", "QueryDosDeviceW", 3, k_QueryDosDeviceW},
};
} // namespace

void kernel32_wide_register() {
    imports_register(g_kernel32_wide, sizeof g_kernel32_wide / sizeof g_kernel32_wide[0]);
}

void kernel32_wide_reset() {
    g_thread_lcid = 0x0409;
    g_cmdline_w = 0;
    g_atoms.clear();
    g_atom_refs.clear();
    g_next_atom = 0xc000;
}

void kernel32_wide_reset_command_line() {
    g_cmdline_w = 0;
}
