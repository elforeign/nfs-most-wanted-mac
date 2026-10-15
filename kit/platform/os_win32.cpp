// os_win32.cpp - the platform layer on Windows. Compiled by clang's GNU
// driver against the MSVC ABI and the Windows SDK; the only first-party file
// besides os_posix.cpp that includes a platform header. Paths are UTF-8 at
// the API and UTF-16 at the system calls.
#include "os.h"

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#include <process.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <string>

// A Windows presentation-clock backend is not implemented yet. Do not report
// producer or buffer-consumption counts as an output-device clock.
void os_audio_output_set_provider(const OsAudioOutputProvider *) {}
int os_audio_outputs_open(void) {
    return 0; // no output backend here yet
}
void os_audio_output_set_idle_callback(void (*)(void)) {}
void os_audio_on_service_restart(void (*)(void)) {}
OsAudioOutput *os_audio_output_open(uint32_t, OsAudioLayout, int *native_error) {
    if (native_error)
        *native_error = -1;
    return nullptr;
}
int os_audio_output_write(OsAudioOutput *, const int16_t *, uint32_t) {
    return -1;
}
int os_audio_output_start(OsAudioOutput *) {
    return -1;
}
int os_audio_output_pause(OsAudioOutput *) {
    return -1;
}
int os_audio_output_set_gain(OsAudioOutput *, float) {
    return -1;
}
int os_audio_output_get_gain(OsAudioOutput *, float *) {
    return -1;
}
int os_audio_output_clock(OsAudioOutput *, OsAudioClock *clock) {
    if (clock) {
        *clock = {};
        clock->native_status = -1;
    }
    return -1;
}
int os_audio_output_reset(OsAudioOutput *) {
    return -1;
}
void os_audio_output_close(OsAudioOutput *) {}
int os_audio_output_route(OsAudioOutput *, OsAudioRoute *) {
    return -1;
}
int os_audio_render_probe_install(uint32_t) {
    return -1;
}
size_t os_audio_render_probe_read(OsAudioRenderRecord *, size_t) {
    return 0;
}
int os_audio_marker_install(uint32_t, uint32_t) {
    return -1;
}
size_t os_audio_marker_read(OsAudioMarkerEdge *, size_t) {
    return 0;
}

// MinGW's headers predate these RegGetValue flags.
#ifndef RRF_SUBKEY_WOW6464KEY
#define RRF_SUBKEY_WOW6464KEY 0x00010000
#endif
#ifndef RRF_SUBKEY_WOW6432KEY
#define RRF_SUBKEY_WOW6432KEY 0x00020000
#endif

namespace {

std::wstring widen(const char *utf8) {
    int n = MultiByteToWideChar(CP_UTF8, 0, utf8, -1, nullptr, 0);
    std::wstring w(n > 0 ? (size_t)n - 1 : 0, L'\0');
    if (n > 0)
        MultiByteToWideChar(CP_UTF8, 0, utf8, -1, &w[0], n);
    return w;
}

std::string narrow(const wchar_t *wide) {
    int n = WideCharToMultiByte(CP_UTF8, 0, wide, -1, nullptr, 0, nullptr, nullptr);
    std::string s(n > 0 ? (size_t)n - 1 : 0, '\0');
    if (n > 0)
        WideCharToMultiByte(CP_UTF8, 0, wide, -1, &s[0], n, nullptr, nullptr);
    return s;
}

// FILETIME counts 100 ns ticks since 1601; the API speaks Unix seconds.
int64_t filetime_seconds(const FILETIME &ft) {
    ULARGE_INTEGER u;
    u.LowPart = ft.dwLowDateTime;
    u.HighPart = ft.dwHighDateTime;
    return (int64_t)(u.QuadPart / 10000000ull) - 11644473600ll;
}

int copy_out(const std::string &s, char *buf, size_t cap) {
    if (s.size() + 1 > cap)
        return -1;
    memcpy(buf, s.c_str(), s.size() + 1);
    for (char *c = buf; *c; ++c)
        if (*c == '\\')
            *c = '/';
    return 0;
}

struct ThreadStart {
    void *(*fn)(void *);
    void *arg;
};

unsigned __stdcall trampoline(void *p) {
    ThreadStart start = *(ThreadStart *)p;
    free(p);
    start.fn(start.arg);
    return 0;
}

char g_dlerror[256];
void remember_dlerror() {
    snprintf(g_dlerror, sizeof g_dlerror, "Windows error %lu", (unsigned long)GetLastError());
}

int native_flags(int flags) {
    int f = _O_BINARY;
    switch (flags & 3) {
    case OS_O_WRONLY:
        f |= _O_WRONLY;
        break;
    case OS_O_RDWR:
        f |= _O_RDWR;
        break;
    default:
        f |= _O_RDONLY;
        break;
    }
    if (flags & OS_O_CREAT)
        f |= _O_CREAT;
    if (flags & OS_O_EXCL)
        f |= _O_EXCL;
    if (flags & OS_O_TRUNC)
        f |= _O_TRUNC;
    return f;
}

int stat_attributes(const wchar_t *path, OsStat *out, bool follow) {
    WIN32_FILE_ATTRIBUTE_DATA d;
    if (!GetFileAttributesExW(path, GetFileExInfoStandard, &d))
        return -1;
    bool reparse = (d.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
    if (follow && reparse) {
        HANDLE h = CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                               nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
        if (h == INVALID_HANDLE_VALUE)
            return -1;
        BY_HANDLE_FILE_INFORMATION info;
        BOOL ok = GetFileInformationByHandle(h, &info);
        CloseHandle(h);
        if (!ok)
            return -1;
        d.dwFileAttributes = info.dwFileAttributes;
        d.ftCreationTime = info.ftCreationTime;
        d.ftLastAccessTime = info.ftLastAccessTime;
        d.ftLastWriteTime = info.ftLastWriteTime;
        d.nFileSizeHigh = info.nFileSizeHigh;
        d.nFileSizeLow = info.nFileSizeLow;
        reparse = false;
    }
    out->size = ((uint64_t)d.nFileSizeHigh << 32) | d.nFileSizeLow;
    out->atime = filetime_seconds(d.ftLastAccessTime);
    out->mtime = filetime_seconds(d.ftLastWriteTime);
    out->ctime = filetime_seconds(d.ftCreationTime);
    out->is_dir = (d.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ? 1 : 0;
    out->is_symlink = reparse ? 1 : 0;
    out->is_regular = (!out->is_dir && !reparse) ? 1 : 0;
    out->is_readonly = (d.dwFileAttributes & FILE_ATTRIBUTE_READONLY) ? 1 : 0;
    return 0;
}

} // namespace

struct OsThread {
    HANDLE handle;
    DWORD id;
};

extern "C" {

OsThread *os_thread_create(void *(*fn)(void *), void *arg, size_t stack_bytes) {
    ThreadStart *start = (ThreadStart *)malloc(sizeof *start);
    OsThread *t = (OsThread *)calloc(1, sizeof *t);
    if (!start || !t) {
        free(start);
        free(t);
        return nullptr;
    }
    start->fn = fn;
    start->arg = arg;
    unsigned id = 0;
    uintptr_t h = _beginthreadex(nullptr, (unsigned)stack_bytes, trampoline, start, 0, &id);
    if (!h) {
        free(start);
        free(t);
        return nullptr;
    }
    t->handle = (HANDLE)h;
    t->id = id;
    return t;
}
void os_thread_join(OsThread *t) {
    WaitForSingleObject(t->handle, INFINITE);
    CloseHandle(t->handle);
    free(t);
}
void os_thread_detach(OsThread *t) {
    CloseHandle(t->handle);
    free(t);
}
uint64_t os_thread_cpu_ns(void) {
    FILETIME created, exited, kernel, user;
    if (!GetThreadTimes(GetCurrentThread(), &created, &exited, &kernel, &user))
        return 0;
    const uint64_t k = (uint64_t(kernel.dwHighDateTime) << 32) | kernel.dwLowDateTime;
    const uint64_t u = (uint64_t(user.dwHighDateTime) << 32) | user.dwLowDateTime;
    return (k + u) * 100;
}
const char *os_thread_class_name(void) {
    return "unknown";
}
void os_thread_set_time_critical_wake(void) {}
void os_thread_prefer_performance(void) {
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL);
}
void os_thread_exit(void) {
    _endthreadex(0);
}
OsThreadId os_thread_self(void) {
    return GetCurrentThreadId();
}
OsThreadId os_thread_id_of(const OsThread *t) {
    return t->id;
}

void *os_vm_reserve(size_t bytes) {
    return VirtualAlloc(nullptr, bytes, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
}
void os_vm_release(void *p, size_t bytes) {
    (void)bytes;
    VirtualFree(p, 0, MEM_RELEASE);
}

void *os_dlopen(const char *path) {
    HMODULE m = LoadLibraryW(widen(path).c_str());
    if (!m)
        remember_dlerror();
    return m;
}
void *os_dlopen_noload(const char *path) {
    return GetModuleHandleW(widen(path).c_str());
}
void *os_dlsym(void *handle, const char *name) {
    return (void *)GetProcAddress((HMODULE)handle, name);
}
int os_dlclose(void *handle) {
    return FreeLibrary((HMODULE)handle) ? 0 : -1;
}
const char *os_dlerror(void) {
    return g_dlerror[0] ? g_dlerror : "unknown dynamic loader error";
}
const char *os_plugin_extension(void) {
    return ".dll";
}

int os_stat(const char *path, OsStat *out) {
    return stat_attributes(widen(path).c_str(), out, true);
}
int os_lstat(const char *path, OsStat *out) {
    return stat_attributes(widen(path).c_str(), out, false);
}
int os_mkdir(const char *path) {
    return CreateDirectoryW(widen(path).c_str(), nullptr) ? 0 : -1;
}
int os_rename(const char *from, const char *to) {
    return MoveFileExW(widen(from).c_str(), widen(to).c_str(), MOVEFILE_REPLACE_EXISTING) ? 0 : -1;
}
int os_unlink(const char *path) {
    return DeleteFileW(widen(path).c_str()) ? 0 : -1;
}
int os_rmdir(const char *path) {
    return RemoveDirectoryW(widen(path).c_str()) ? 0 : -1;
}
int os_getcwd(char *buf, size_t cap) {
    wchar_t w[MAX_PATH * 4];
    DWORD n = GetCurrentDirectoryW(sizeof w / sizeof w[0], w);
    if (!n || n >= sizeof w / sizeof w[0])
        return -1;
    return copy_out(narrow(w), buf, cap);
}
int os_chdir(const char *path) {
    return SetCurrentDirectoryW(widen(path).c_str()) ? 0 : -1;
}
int os_listdir(const char *dir, OsListDirFn fn, void *user) {
    std::wstring pattern = widen(dir) + L"\\*";
    WIN32_FIND_DATAW f;
    HANDLE h = FindFirstFileW(pattern.c_str(), &f);
    if (h == INVALID_HANDLE_VALUE)
        return -1;
    do {
        if (wcscmp(f.cFileName, L".") == 0 || wcscmp(f.cFileName, L"..") == 0)
            continue;
        if (fn(narrow(f.cFileName).c_str(), user) != 0)
            break;
    } while (FindNextFileW(h, &f));
    FindClose(h);
    return 0;
}
int os_mkstemp(char *template_path) {
    size_t len = strlen(template_path);
    if (len < 6 || strcmp(template_path + len - 6, "XXXXXX") != 0)
        return -1;
    static const char alphabet[] = "abcdefghijklmnopqrstuvwxyz0123456789";
    unsigned seed = (unsigned)GetTickCount() ^ (GetCurrentThreadId() * 2654435761u);
    for (int attempt = 0; attempt < 100; ++attempt) {
        for (int i = 0; i < 6; ++i) {
            seed = seed * 1103515245u + 12345u;
            template_path[len - 6 + i] = alphabet[(seed >> 16) % 36];
        }
        int fd = _wopen(widen(template_path).c_str(), _O_CREAT | _O_EXCL | _O_RDWR | _O_BINARY,
                        _S_IREAD | _S_IWRITE);
        if (fd >= 0)
            return fd;
    }
    return -1;
}

int os_mkdtemp(char *template_path) {
    size_t len = strlen(template_path);
    if (len < 6 || strcmp(template_path + len - 6, "XXXXXX") != 0)
        return -1;
    static const char alphabet[] = "abcdefghijklmnopqrstuvwxyz0123456789";
    unsigned seed = (unsigned)GetTickCount() ^ (GetCurrentThreadId() * 2654435761u);
    for (int attempt = 0; attempt < 100; ++attempt) {
        for (size_t i = len - 6; i < len; ++i) {
            seed = seed * 1103515245u + 12345u;
            template_path[i] = alphabet[(seed >> 16) % (sizeof alphabet - 1)];
        }
        if (CreateDirectoryW(widen(template_path).c_str(), nullptr))
            return 0;
        if (GetLastError() != ERROR_ALREADY_EXISTS)
            return -1;
    }
    return -1;
}

const char *os_temp_dir(void) {
    static char buf[MAX_PATH * 3];
    wchar_t w[MAX_PATH + 1];
    DWORD n = GetTempPathW(MAX_PATH + 1, w);
    if (!n || n > MAX_PATH)
        return ".";
    while (n > 1 && (w[n - 1] == L'\\' || w[n - 1] == L'/'))
        w[--n] = 0;
    std::string s = narrow(w);
    strncpy(buf, s.c_str(), sizeof buf - 1);
    buf[sizeof buf - 1] = 0;
    return buf;
}

const char *os_null_device(void) {
    return "NUL";
}

int os_free_space(const char *path, uint64_t *bytes_out) {
    ULARGE_INTEGER available;
    if (!bytes_out || !GetDiskFreeSpaceExW(widen(path).c_str(), &available, nullptr, nullptr))
        return -1;
    *bytes_out = available.QuadPart;
    return 0;
}
int os_set_mtime(const char *path, int64_t mtime) {
    HANDLE h = CreateFileW(widen(path).c_str(), FILE_WRITE_ATTRIBUTES,
                           FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                           OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (h == INVALID_HANDLE_VALUE)
        return -1;
    ULARGE_INTEGER u;
    u.QuadPart = uint64_t(mtime) * 10000000ull + 116444736000000000ull;
    FILETIME ft{u.LowPart, u.HighPart};
    const BOOL ok = SetFileTime(h, nullptr, &ft, &ft);
    CloseHandle(h);
    return ok ? 0 : -1;
}
int os_registry_read(const char *key, const char *value, char *buf, size_t cap) {
    if (!key || !value || !buf || !cap)
        return -1;
    HKEY root = nullptr;
    const char *path = nullptr;
    if (strncmp(key, "HKLM\\", 5) == 0) {
        root = HKEY_LOCAL_MACHINE;
        path = key + 5;
    } else if (strncmp(key, "HKCU\\", 5) == 0) {
        root = HKEY_CURRENT_USER;
        path = key + 5;
    } else
        return -1;
    for (REGSAM view : {KEY_WOW64_64KEY, KEY_WOW64_32KEY}) {
        wchar_t data[4096];
        DWORD size = sizeof data;
        LSTATUS st = RegGetValueW(
            root, widen(path).c_str(), widen(value).c_str(),
            RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ |
                (view == KEY_WOW64_64KEY ? RRF_SUBKEY_WOW6464KEY : RRF_SUBKEY_WOW6432KEY),
            nullptr, data, &size);
        if (st != ERROR_SUCCESS)
            continue;
        const std::string out = narrow(data);
        if (out.size() + 1 > cap)
            return -1;
        memcpy(buf, out.c_str(), out.size() + 1);
        return 0;
    }
    return -1;
}
int os_user_data_dir(const char *app, char *buf, size_t cap) {
    wchar_t w[MAX_PATH + 1];
    DWORD n = GetEnvironmentVariableW(L"APPDATA", w, MAX_PATH + 1);
    if (!n || n > MAX_PATH)
        return -1;
    std::string s = narrow(w) + "\\" + app;
    if (s.size() + 1 > cap)
        return -1;
    memcpy(buf, s.c_str(), s.size() + 1);
    return 0;
}

int os_spawn(const char *const argv[], int64_t *pid_out) {
    std::wstring cmd;
    for (int i = 0; argv[i]; ++i) {
        std::wstring a = widen(argv[i]);
        if (i)
            cmd += L' ';
        if (!a.empty() && a.find_first_of(L" \t\"") == std::wstring::npos)
            cmd += a;
        else {
            cmd += L'"';
            for (wchar_t ch : a)
                cmd += ch == L'"' ? std::wstring(L"\\\"") : std::wstring(1, ch);
            cmd += L'"';
        }
    }
    STARTUPINFOW si = {};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi = {};
    if (!CreateProcessW(widen(argv[0]).c_str(), cmd.data(), nullptr, nullptr, TRUE, 0, nullptr,
                        nullptr, &si, &pi))
        return -1;
    CloseHandle(pi.hThread);
    *pid_out = (int64_t)(intptr_t)pi.hProcess;
    return 0;
}

int os_wait(int64_t pid, int *exit_code) {
    HANDLE h = (HANDLE)(intptr_t)pid;
    if (WaitForSingleObject(h, INFINITE) != WAIT_OBJECT_0)
        return -1;
    DWORD code = 0;
    GetExitCodeProcess(h, &code);
    CloseHandle(h);
    // abort() exits with 3 under a debug C runtime and fails fast with
    // STATUS_STACK_BUFFER_OVERRUN (0xC0000409, FAST_FAIL_FATAL_APP_EXIT) under a
    // release one; report both as POSIX SIGABRT so tests agree.
    *exit_code = code == 3 || code == 0xC0000409u ? 134 : (int)code;
    return 0;
}

int os_fd_open(const char *path, int flags) {
    return _wopen(widen(path).c_str(), native_flags(flags), _S_IREAD | _S_IWRITE);
}
int64_t os_fd_read(int fd, void *buf, size_t n) {
    // _read takes an unsigned count; loop so a large guest read still works.
    int64_t total = 0;
    char *p = (char *)buf;
    while (n) {
        unsigned chunk = n > (1u << 30) ? (1u << 30) : (unsigned)n;
        int got = _read(fd, p, chunk);
        if (got < 0)
            return total ? total : -1;
        total += got;
        p += got;
        n -= (size_t)got;
        if ((unsigned)got < chunk)
            break;
    }
    return total;
}
int64_t os_fd_write(int fd, const void *buf, size_t n) {
    int64_t total = 0;
    const char *p = (const char *)buf;
    while (n) {
        unsigned chunk = n > (1u << 30) ? (1u << 30) : (unsigned)n;
        int put = _write(fd, p, chunk);
        if (put < 0)
            return total ? total : -1;
        total += put;
        p += put;
        n -= (size_t)put;
        if ((unsigned)put < chunk)
            break;
    }
    return total;
}
int64_t os_fd_seek(int fd, int64_t off, int whence) {
    return _lseeki64(fd, off, whence);
}
int os_fd_close(int fd) {
    return _close(fd);
}
int os_fd_dup(int fd) {
    return _dup(fd);
}
int os_fd_fsync(int fd) {
    return _commit(fd);
}
int os_fd_truncate(int fd, int64_t length) {
    return _chsize_s(fd, length) == 0 ? 0 : -1;
}
int os_fd_stat(int fd, OsStat *out) {
    struct _stat64 st;
    if (_fstat64(fd, &st) != 0)
        return -1;
    out->size = (uint64_t)st.st_size;
    out->atime = st.st_atime;
    out->mtime = st.st_mtime;
    out->ctime = st.st_ctime;
    out->is_dir = (st.st_mode & _S_IFDIR) ? 1 : 0;
    out->is_regular = (st.st_mode & _S_IFREG) ? 1 : 0;
    out->is_symlink = 0;
    out->is_readonly = (st.st_mode & _S_IWRITE) ? 0 : 1;
    return 0;
}
void *os_fdopen(int fd, const char *mode) {
    return _fdopen(fd, mode);
}

int os_exe_path(char *buf, size_t cap) {
    wchar_t w[32768];
    DWORD n = GetModuleFileNameW(nullptr, w, 32768);
    if (!n || n >= 32768)
        return -1;
    return copy_out(narrow(w), buf, cap);
}
// The unhandled-exception filter: the fault address relative to this
// executable's base (so a linker map resolves it), the top of the stack as
// candidate return addresses, and the registered callback for the guest EIP.
static OsFaultFn g_fault_fn;
static LONG WINAPI fault_filter(EXCEPTION_POINTERS *info) {
    char line[256];
    const EXCEPTION_RECORD *rec = info->ExceptionRecord;
    const uintptr_t base = (uintptr_t)GetModuleHandleW(nullptr);
    const uintptr_t at = (uintptr_t)rec->ExceptionAddress;
    int n = snprintf(line, sizeof line,
                     "[host] exception %08lx at %016llx (exe base %016llx, rva %llx)\n",
                     (unsigned long)rec->ExceptionCode, (unsigned long long)at,
                     (unsigned long long)base, (unsigned long long)(at - base));
    os_write_stderr_raw(line, (size_t)n);
    const uintptr_t *sp = (const uintptr_t *)info->ContextRecord->Rsp;
    for (int i = 0; i < 24; ++i) {
        uintptr_t v = 0;
        if (IsBadReadPtr(sp + i, sizeof v))
            break;
        v = sp[i];
        if (v >= base && v < base + (256u << 20)) {
            n = snprintf(line, sizeof line, "[host]   stack[%d] rva %llx\n", i,
                         (unsigned long long)(v - base));
            os_write_stderr_raw(line, (size_t)n);
        }
    }
    if (g_fault_fn)
        g_fault_fn(rec->ExceptionCode == EXCEPTION_ACCESS_VIOLATION ? "access violation"
                                                                    : "exception");
    return EXCEPTION_EXECUTE_HANDLER;
}

int os_install_fault_handlers(OsFaultFn fn) {
    g_fault_fn = fn;
    SetUnhandledExceptionFilter(fault_filter);
    return 1;
}
void os_fault_info(OsFaultInfo *out) {
    // The filter already prints the exception record; nothing more here yet.
    memset(out, 0, sizeof *out);
}
void os_write_stderr_raw(const char *s, size_t n) {
    DWORD written = 0;
    WriteFile(GetStdHandle(STD_ERROR_HANDLE), s, (DWORD)n, &written, nullptr);
}
void os_exit_immediately(int code) {
    _exit(code);
}

int os_localtime(int64_t seconds, struct tm *out) {
    __time64_t t = (__time64_t)seconds;
    return _localtime64_s(out, &t) == 0 ? 0 : -1;
}
int os_gmtime(int64_t seconds, struct tm *out) {
    __time64_t t = (__time64_t)seconds;
    return _gmtime64_s(out, &t) == 0 ? 0 : -1;
}

uint64_t os_monotonic_ns(void) {
    static LARGE_INTEGER freq = {};
    if (!freq.QuadPart)
        QueryPerformanceFrequency(&freq);
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    return (uint64_t)((double)c.QuadPart * 1e9 / (double)freq.QuadPart);
}
uint64_t os_wall_time_us(void) {
    FILETIME ft;
    GetSystemTimePreciseAsFileTime(&ft);
    ULARGE_INTEGER u;
    u.LowPart = ft.dwLowDateTime;
    u.HighPart = ft.dwHighDateTime;
    return u.QuadPart / 10ull - 11644473600000000ull;
}
void os_sleep_us(uint64_t us) {
    Sleep((DWORD)((us + 999) / 1000));
}

int os_strcasecmp(const char *a, const char *b) {
    return _stricmp(a, b);
}

int os_setenv(const char *name, const char *value) {
    return _putenv_s(name, value) == 0 ? 0 : -1;
}
int os_unsetenv(const char *name) {
    return _putenv_s(name, "") == 0 ? 0 : -1;
}

} // extern "C"

// NFSMW issue 8: no read-only guest pages on this platform yet (the hook is never asked).
size_t os_vm_page_size(void) {
    return 4096;
}
int os_vm_set_readonly(void *, size_t, int) {
    return -1;
}
void os_set_write_fault_hook(OsWriteFaultHook) {}
uint64_t os_image_load(void) {
    return 0;
}
