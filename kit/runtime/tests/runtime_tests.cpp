// runtime_tests.cpp - runtime, loader, allocator and Win32 shim tests.
//
// Run from the repository root:
//   .venv/bin/python tools/test.py --compile-only && build/recomp/runtime_tests
#include "../imports.h"
#include "../../dx/dx.h"
#include "../../dx/ddraw.h"
#include "../resources.h"
#include "../mods_seam.h"
#include "../intrinsics.h"
#include "../interp.h"
#include "../loader.h"
#include "../discovery.h"
#include "../memory.h"
#include "../win32.h"
#include "../guest_pause.h"
#include "../../platform/os.h"
#include "game_config.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <stdarg.h>
#include <map>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <set>
#include <string>
#include <thread>
#include <vector>

static int g_checks = 0, g_failures = 0, g_skips = 0;
static const char *g_section = "";

static void section(const char *s) {
    g_section = s;
    printf("\n== %s\n", s);
}

static bool check(bool ok, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static bool check(bool ok, const char *fmt, ...) {
    ++g_checks;
    va_list ap;
    va_start(ap, fmt);
    char msg[512];
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    if (!ok)
        ++g_failures;
    printf("  [%s] %s\n", ok ? "ok" : "FAIL", msg);
    return ok;
}

// ---------------------------------------------------------------------------
// Shim call helper: pushes args and a return address, then dispatches.
// ---------------------------------------------------------------------------
static uint32_t g_fake_ret = RECOMP_ENTRY_POINT;

static uint32_t call_import(X86 *c, const char *dll, const char *name,
                            const std::vector<uint32_t> &args) {
    // imports_resolve allocates a trampoline for a registered shim that no IAT
    // slot referenced, which is the same path GetProcAddress takes.
    uint32_t tramp = imports_resolve(dll, name);
    if (!tramp) {
        printf("  [FAIL] no trampoline for %s!%s\n", dll, name);
        ++g_failures;
        ++g_checks;
        return 0;
    }
    uint32_t esp = c->r[R_ESP];
    uint32_t before = esp;
    for (size_t i = args.size(); i-- > 0;) {
        esp -= 4;
        wr32(esp, args[i]);
    }
    esp -= 4;
    wr32(esp, g_fake_ret);
    c->r[R_ESP] = esp;
    imports_dispatch(c, tramp);
    uint32_t expected =
        imports_argc(tramp) == ARGC_CDECL ? before - uint32_t(args.size()) * 4 : before;
    if (c->r[R_ESP] != expected) {
        printf("  [FAIL] %s!%s left ESP at %08x, expected %08x (bad argc?)\n", dll, name,
               c->r[R_ESP], expected);
        ++g_failures;
        ++g_checks;
        c->r[R_ESP] = before;
    }
    c->r[R_ESP] = before; // cdecl callers remove their arguments.
    return c->r[R_EAX];
}

// Writes a NUL-terminated string into a scratch area of the guest stack.
static uint32_t scratch = 0;
static uint32_t put_str(const char *s) {
    uint32_t a = scratch;
    uint32_t n = (uint32_t)strlen(s) + 1;
    memcpy(g_mem + a, s, n);
    scratch += (n + 15) & ~15u;
    return a;
}
// Portable stand-ins for `rm -rf` and `mkdir -p` over the platform layer.
static int remove_tree_entry(const char *name, void *user) {
    if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0)
        return 0;
    std::string path = *static_cast<const std::string *>(user) + "/" + name;
    OsStat st{};
    if (os_lstat(path.c_str(), &st) == 0 && st.is_dir && !st.is_symlink) {
        os_listdir(path.c_str(), remove_tree_entry, &path);
        os_rmdir(path.c_str());
    } else {
        os_unlink(path.c_str());
    }
    return 0;
}
static void remove_tree(const std::string &root) {
    OsStat st{};
    if (os_lstat(root.c_str(), &st) != 0)
        return;
    if (st.is_dir && !st.is_symlink) {
        std::string r = root;
        os_listdir(root.c_str(), remove_tree_entry, &r);
        os_rmdir(root.c_str());
    } else {
        os_unlink(root.c_str());
    }
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

static uint32_t scratch_block(uint32_t bytes) {
    uint32_t a = scratch;
    memset(g_mem + a, 0, bytes);
    scratch += (bytes + 15) & ~15u;
    return a;
}

// ---------------------------------------------------------------------------
// Sections, cross-checked against pefile.
// ---------------------------------------------------------------------------
struct ExpectedSection {
    std::string name;
    uint32_t va, vsize, raw, offset, flags;
};
struct ExpectedImport {
    uint32_t slot;
    std::string dll, name;
};
struct ExpectedImage {
    uint32_t base = 0, size = 0, entry = 0;
    std::vector<ExpectedSection> sections;
    std::vector<ExpectedImport> imports;
};

static bool pefile_sections(ExpectedImage &out, std::string &err) {
#ifdef _WIN32
#define popen _popen
#define pclose _pclose
#define POP_VENV_PYTHON ".venv/Scripts/python.exe"
#else
#define POP_VENV_PYTHON ".venv/bin/python"
#endif
    // The interpreter that configured the build (CTest passes RECOMP_PYTHON), else
    // the checkout's venv relative to the working directory.
    const char *python = recomp_env("PYTHON");
    const std::string cmd_s =
        std::string(python && *python ? python : POP_VENV_PYTHON) +
        " -c \""
        "import pefile;pe=pefile.PE('" RECOMP_DEVELOPER_EXE "');b=pe.OPTIONAL_HEADER.ImageBase;"
        "print('IMAGE %x %x %x' % (b, pe.OPTIONAL_HEADER.SizeOfImage, "
        "b+pe.OPTIONAL_HEADER.AddressOfEntryPoint));"
        "[print('SECTION %s %x %x %x %x %x' % (s.Name.hex(), b+s.VirtualAddress, "
        "s.Misc_VirtualSize, s.SizeOfRawData, s.PointerToRawData, s.Characteristics)) for s in "
        "pe.sections];"
        "[print('IMPORT %x %s %s' % (i.address, d.dll.decode(), "
        "'ord%d' % i.ordinal if i.ordinal is not None else i.name.decode())) "
        "for d in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []) for i in d.imports]"
        "\" 2>/dev/null";
    const char *cmd = cmd_s.c_str();
    FILE *p = popen(cmd, "r");
    if (!p) {
        err = "popen failed";
        return false;
    }
    char line[1024];
    bool image = false;
    while (fgets(line, sizeof line, p)) {
        char name[512], dll[256];
        unsigned va, vsize, raw, offset, flags;
        if (sscanf(line, "IMAGE %x %x %x", &va, &vsize, &raw) == 3) {
            out.base = va;
            out.size = vsize;
            out.entry = raw;
            image = true;
        } else if (sscanf(line, "SECTION %511s %x %x %x %x %x", name, &va, &vsize, &raw, &offset,
                          &flags) == 6) {
            // PE section names are arbitrary eight-byte fields, including all
            // spaces. Hex keeps the helper protocol independent of whitespace.
            std::string decoded;
            bool valid = strlen(name) == 16;
            for (size_t i = 0; valid && i < 16; i += 2) {
                unsigned byte = 0;
                valid = std::isxdigit((unsigned char)name[i]) &&
                        std::isxdigit((unsigned char)name[i + 1]) &&
                        sscanf(name + i, "%2x", &byte) == 1;
                if (valid)
                    decoded.push_back((char)byte);
            }
            if (!valid) {
                err = "invalid hex PE section name";
                continue;
            }
            decoded.resize(decoded.find('\0') == std::string::npos ? decoded.size()
                                                                   : decoded.find('\0'));
            out.sections.push_back({decoded, va, vsize, raw, offset, flags});
        } else if (sscanf(line, "IMPORT %x %255s %511s", &va, dll, name) == 3) {
            out.imports.push_back({va, dll, name});
        } else {
            err = "unrecognized pefile record";
        }
    }
    int rc = pclose(p);
    if (rc != 0 || !image || out.sections.empty() || !err.empty()) {
        if (err.empty())
            err = rc == 0 ? "incomplete output from pefile" : "pefile helper failed";
        return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
// The unit binary dispatches import trampolines and stubs the translated entry.
// Temporarily supply two TLS callbacks so an empty image list still exercises
// argument order, the return sentinel, and both caller and callee stack cleanup.
static uint32_t g_tls_callback_hits = 0;
static void fake_loader_tls_callback(X86 *c) {
    ++g_tls_callback_hits;
    check(rd32(c->r[R_ESP]) == GUEST_RETURN_SENTINEL, "TLS callback has the return sentinel");
    check(arg(c, 0) == loader_image_base() && arg(c, 1) == 1 && arg(c, 2) == 0,
          "TLS callback receives the image base, process attach, and null reserved argument");
    const LoaderTls &tls = loader_tls();
    check(rd32(rd32(c->fs_base + 0x2c) + 4 * tls.index) != 0,
          "TLS callback sees the initialized main thread block");
}

static void test_modules_and_wide() {
    section("modules and wide strings");
    X86 c;
    loader_init_context(&c);
    // This runtime-only binary does not link DirectX. Register a fixture with
    // the same DLL/export spelling to exercise run-time lookup of an extra table.
    static const ImportShim shims[] = {{"DDRAW.dll", "DirectDrawCreate", 3, nullptr}};
    imports_register(shims, sizeof shims / sizeof shims[0]);
    uint32_t name = 0x00300000; // scratch in the arena below the image
    for (const auto &entry :
         std::vector<std::pair<const char *, const char *>>{{"kernel32.dll", "GetVersion"},
                                                            {"user32.dll", "IsWindow"},
                                                            {"gdi32.dll", "GetDeviceCaps"}}) {
        gm_put_wstr(name, entry.first, 64);
        uint32_t module = call_import(&c, "KERNEL32.dll", "GetModuleHandleW", {name});
        check(module != 0, "GetModuleHandleW(%s) works before LoadLibrary", entry.first);
        check(call_import(&c, "KERNEL32.dll", "GetModuleHandleW", {name}) == module,
              "GetModuleHandleW(%s) is stable", entry.first);
        gm_put_str(name + 128, entry.first, 64);
        check(call_import(&c, "KERNEL32.dll", "GetModuleHandleA", {name + 128}) == module,
              "GetModuleHandleA(%s) shares the wide handle", entry.first);
        gm_put_str(name + 64, entry.second, 64);
        check(call_import(&c, "KERNEL32.dll", "GetProcAddress", {module, name + 64}) != 0,
              "GetProcAddress resolves %s from the implicit module", entry.second);
        check(call_import(&c, "KERNEL32.dll", "LoadLibraryW", {name}) == module,
              "LoadLibraryW(%s) reuses the implicit module", entry.first);
    }
    gm_put_str(name, "ddraw.dll", 64);
    uint32_t h = call_import(&c, "KERNEL32.dll", "LoadLibraryA", {name});
    check(h != 0, "LoadLibraryA(ddraw.dll) -> %08x", h);
    gm_put_str(name + 64, "DirectDrawCreate", 64);
    check(call_import(&c, "KERNEL32.dll", "GetProcAddress", {h, name + 64}) != 0,
          "GetProcAddress(ddraw, DirectDrawCreate) resolves");
    gm_put_wstr(name + 128, "soaddraw.dll", 64);
    check(call_import(&c, "KERNEL32.dll", "LoadLibraryW", {name + 128}) == 0,
          "LoadLibraryW(soaddraw.dll): no shims, reported missing");
    check(call_import(&c, "KERNEL32.dll", "GetLastError", {}) == 126,
          "missing wide module reports ERROR_MOD_NOT_FOUND");
    gm_put_wstr(name + 128, "DDRAW.DLL", 64);
    check(call_import(&c, "KERNEL32.dll", "LoadLibraryW", {name + 128}) == h,
          "LoadLibraryW(DDRAW.DLL) returns the same module");
    check(call_import(&c, "KERNEL32.dll", "GetModuleHandleW", {name + 128}) == h,
          "GetModuleHandleW agrees");
    check(gm_wstr(name + 128) == "DDRAW.DLL", "gm_wstr round-trips");
    gm_put_wstr(name + 256, "abc", 3);
    check(gm_wstr(name + 256) == "ab", "gm_put_wstr truncates to the cap");

    check(imports_has_dll("ddraw.dll") && imports_has_dll("DdRaW.DlL"),
          "registered DLL lookup is case-insensitive");
    check(!imports_has_dll("soaddraw.dll") && !imports_has_dll("") && !imports_has_dll(nullptr),
          "unregistered and empty DLL names are absent");
    gm_put_wstr(name + 128, "C:\\WINDOWS\\SYSTEM32\\DDRAW", 64);
    check(call_import(&c, "KERNEL32.dll", "LoadLibraryExW", {name + 128, 0, 8}) == h,
          "LoadLibraryExW normalizes paths and extension and pops three arguments");
    check(call_import(&c, "KERNEL32.dll", "GetModuleHandleW", {0}) == IMAGE_BASE,
          "GetModuleHandleW(NULL) returns the image");
    gm_put_wstr(name + 128, "unregistered.dll", 64);
    check(call_import(&c, "KERNEL32.dll", "GetModuleHandleW", {name + 128}) == 0,
          "GetModuleHandleW reports an unloaded module as missing");

    for (uint32_t module : {0u, IMAGE_BASE, h}) {
        uint32_t narrow = name + 512, wide = name + 1024;
        call_import(&c, "KERNEL32.dll", "GetModuleFileNameA", {module, narrow, 260});
        uint32_t n = call_import(&c, "KERNEL32.dll", "GetModuleFileNameW", {module, wide, 260});
        check(n == gm_str(narrow).size() && gm_wstr(wide) == gm_str(narrow),
              "GetModuleFileNameW(%08x) agrees with the guest path from A", module);
    }
    uint32_t out = name + 2048;
    uint32_t n = call_import(&c, "KERNEL32.dll", "GetModuleFileNameW", {h, out, 3});
    check(n == 2 && gm_wstr(out) == "C:", "GetModuleFileNameW truncates in UTF-16 units");
    wr16(out, 0x1234);
    check(call_import(&c, "KERNEL32.dll", "GetModuleFileNameW", {h, out, 0}) == 0 &&
              rd16(out) == 0x1234,
          "GetModuleFileNameW with zero capacity leaves the buffer alone");

    // Independent code-unit expectations catch mutually wrong encoders/decoders.
    const std::string unicode = "A\xc3\xa9\xe6\xb0\xb4\xf0\x9f\x98\x80";
    check(gm_put_wstr(out, unicode, 6) == 5 && rd16(out) == 'A' && rd16(out + 2) == 0x00e9 &&
              rd16(out + 4) == 0x6c34 && rd16(out + 6) == 0xd83d && rd16(out + 8) == 0xde00 &&
              rd16(out + 10) == 0,
          "gm_put_wstr encodes BMP characters and a surrogate pair");
    check(gm_wstr(out) == unicode, "gm_wstr decodes BMP characters and a surrogate pair");
    check(gm_wstr(out, 3) == "A\xc3\xa9\xe6\xb0\xb4", "gm_wstr respects the code-unit bound");
    check(gm_wstr(out + 6, 1) == "\xef\xbf\xbd",
          "gm_wstr does not consume a surrogate beyond its bound");
    check(gm_put_wstr(out, "\xf0\x9f\x98\x80", 2) == 0 && rd16(out) == 0,
          "gm_put_wstr never writes half a surrogate pair");
    wr16(out + 2, 0x1234);
    check(gm_put_wstr(out, "abc", 1) == 0 && rd16(out) == 0 && rd16(out + 2) == 0x1234,
          "gm_put_wstr capacity one writes only the terminator");
    check(gm_put_wstr(out + 2, "abc", 0) == 0 && rd16(out + 2) == 0x1234,
          "gm_put_wstr capacity zero writes nothing");
    check(gm_wstr(0).empty() && gm_wstr(out, 0).empty() && gm_put_wstr(0, "abc", 8) == 0,
          "wide helpers accept null addresses and zero read bounds");
    check(gm_put_wstr(out, std::string("a\0b", 3), 8) == 1 && gm_wstr(out) == "a",
          "gm_put_wstr stops at an embedded NUL");
    wr16(out, 0xdc00);
    wr16(out + 2, 0);
    check(gm_wstr(out) == "\xef\xbf\xbd", "gm_wstr replaces an unpaired surrogate");
    check(gm_put_wstr(out, std::string("\xe2\x82", 2), 8) == 2 && rd16(out) == 0xfffd &&
              rd16(out + 2) == 0xfffd,
          "gm_put_wstr replaces incomplete UTF-8 without reading past the string");
    check(gm_put_wstr(out, std::string("\xc0\xaf", 2), 8) == 2 &&
              gm_wstr(out) == "\xef\xbf\xbd\xef\xbf\xbd",
          "gm_put_wstr rejects overlong UTF-8");
    wr16(GUEST_SIZE - 2, 'Z');
    check(gm_wstr(GUEST_SIZE - 2) == "Z" && gm_wstr(0xfffffffeu).empty(),
          "gm_wstr stops at the arena boundary without wrapping guest addresses");
    check(gm_put_wstr(GUEST_SIZE - 2, "abc", 8) == 0 && rd16(GUEST_SIZE - 2) == 0,
          "gm_put_wstr reserves a terminator at the arena boundary");
    check(gm_put_wstr(0xfffffffeu, "abc", 8) == 0,
          "gm_put_wstr rejects addresses outside the arena");
}

// CPUID leaf 1: the kit's FPU | TSC | CMOV plus exactly the game's [game] cpu_features (run 2: UG1 reports MMX).
static void test_cpuid_features() {
    section("cpuid features");
    X86 c;
    loader_init_context(&c);
    c.r[R_EAX] = 1;
    recomp_cpuid(&c);
    check(c.r[R_EDX] == (0x00008011u | RECOMP_CPUID_FEATURES_EDX), "leaf 1 EDX %08x = base | game features %08x",
          c.r[R_EDX], RECOMP_CPUID_FEATURES_EDX);
    check((c.r[R_EDX] & 0x02000000u) == 0, "no SSE is reported");
    check(c.r[R_EBX] == RECOMP_CPUID_BRAND_INDEX, "leaf 1 EBX %08x = the game's brand index %02x (run 7: UG1 0x0c)",
          c.r[R_EBX], RECOMP_CPUID_BRAND_INDEX);
}

static void test_session_notification_service_unavailable() {
    section("session notification service");
    X86 c;
    loader_init_context(&c);
    const uint32_t text = 0x00308000;
    gm_put_wstr(text, "wtsapi32.dll", 64);
    uint32_t module = call_import(&c, "KERNEL32.dll", "LoadLibraryW", {text});
    check(module != 0, "session notification module is present even without a session service");
    for (const char *name :
         {"WTSRegisterSessionNotification", "WTSUnRegisterSessionNotification"}) {
        gm_put_str(text + 128, name, 64);
        check(call_import(&c, "KERNEL32.dll", "GetProcAddress", {module, text + 128}) != 0,
              "%s resolves through the module handle", name);
    }
    check(call_import(&c, "WTSAPI32.dll", "WTSRegisterSessionNotification", {0x20004, 0}) == 0 &&
              call_import(&c, "KERNEL32.dll", "GetLastError", {}) == 1702,
          "session registration reports RPC_S_INVALID_BINDING when the service is absent");
    check(call_import(&c, "WTSAPI32.dll", "WTSUnRegisterSessionNotification", {0x20004}) == 0 &&
              call_import(&c, "KERNEL32.dll", "GetLastError", {}) == 1702,
          "session unregistration reports the same unavailable service");
}

static void test_buffered_paint_unavailable() {
    section("buffered painting unavailable");
    X86 c;
    loader_init_context(&c);
    const uint32_t text = 0x00308000;
    gm_put_wstr(text, "uxtheme.dll", 64);
    uint32_t module = call_import(&c, "KERNEL32.dll", "LoadLibraryW", {text});
    check(module != 0, "the theme module exposes the buffered-paint availability probe");
    gm_put_str(text + 128, "BufferedPaintInit", 64);
    check(call_import(&c, "KERNEL32.dll", "GetProcAddress", {module, text + 128}) != 0,
          "BufferedPaintInit resolves through GetProcAddress");
    check(call_import(&c, "UXTHEME.dll", "BufferedPaintInit", {}) == 0x80004001u,
          "BufferedPaintInit reports E_NOTIMPL for unavailable buffered painting");
    check(call_import(&c, "UXTHEME.dll", "BufferedPaintUnInit", {}) == 0,
          "BufferedPaintUnInit safely completes without buffered-paint resources");
    for (const char *api : {"IsThemeActive", "IsAppThemed"}) {
        gm_put_str(text + 128, api, 64);
        check(call_import(&c, "KERNEL32.dll", "GetProcAddress", {module, text + 128}) != 0 &&
                  call_import(&c, "UXTHEME.dll", api, {}) == 1,
              "%s reports visual styles on, as every Windows since Vista does", api);
    }
    // A VCL program binds every one of these at start and calls through the
    // pointer it got, so a missing export is a call to address zero later.
    for (const char *api : {"OpenThemeData",
                            "CloseThemeData",
                            "DrawThemeBackground",
                            "DrawThemeText",
                            "GetThemeBackgroundContentRect",
                            "GetThemeBackgroundExtent",
                            "GetThemePartSize",
                            "GetThemeTextExtent",
                            "GetThemeTextMetrics",
                            "GetThemeBackgroundRegion",
                            "HitTestThemeBackground",
                            "DrawThemeEdge",
                            "DrawThemeIcon",
                            "IsThemePartDefined",
                            "IsThemeBackgroundPartiallyTransparent",
                            "GetThemeColor",
                            "GetThemeMetric",
                            "GetThemeString",
                            "GetThemeBool",
                            "GetThemeInt",
                            "GetThemeEnumValue",
                            "GetThemePosition",
                            "GetThemeFont",
                            "GetThemeRect",
                            "GetThemeMargins",
                            "GetThemeIntList",
                            "GetThemePropertyOrigin",
                            "SetWindowTheme",
                            "GetThemeFilename",
                            "GetThemeSysColor",
                            "GetThemeSysColorBrush",
                            "GetThemeSysBool",
                            "GetThemeSysSize",
                            "GetThemeSysFont",
                            "GetThemeSysString",
                            "GetThemeSysInt",
                            "GetWindowTheme",
                            "EnableThemeDialogTexture",
                            "IsThemeDialogTextureEnabled",
                            "GetThemeAppProperties",
                            "SetThemeAppProperties",
                            "GetCurrentThemeName",
                            "GetThemeDocumentationProperty",
                            "DrawThemeParentBackground",
                            "EnableTheming",
                            "DrawThemeTextEx",
                            "OpenThemeDataForDpi",
                            "BeginBufferedPaint",
                            "EndBufferedPaint",
                            "BufferedPaintSetAlpha",
                            "BeginBufferedAnimation",
                            "EndBufferedAnimation",
                            "BufferedPaintRenderAnimation",
                            "BufferedPaintStopAllAnimations"}) {
        gm_put_str(text + 128, api, 64);
        check(call_import(&c, "KERNEL32.dll", "GetProcAddress", {module, text + 128}) != 0,
              "%s resolves through GetProcAddress", api);
    }
    gm_put_wstr(text + 128, "BUTTON", 16);
    check(call_import(&c, "UXTHEME.dll", "OpenThemeData", {0, text + 128}) == 0,
          "OpenThemeData finds no theme data, so controls are drawn the classic way");
    // comctl32.dll's version is what a VCL program turns themed painting on by.
    gm_put_wstr(text, "comctl32.dll", 64);
    gm_put_wstr(text + 256, "\\", 4);
    const uint32_t block = 0x00309000;
    uint32_t size = call_import(&c, "VERSION.dll", "GetFileVersionInfoSizeW", {text, 0});
    uint32_t fixed = 0;
    if (size && size <= 0x1000 &&
        call_import(&c, "VERSION.dll", "GetFileVersionInfoW", {text, 0, size, block}) &&
        call_import(&c, "VERSION.dll", "VerQueryValueW",
                    {block, text + 256, text + 512, text + 516}))
        fixed = rd32(text + 512);
    uint32_t major = fixed && gm_valid(fixed, 52) ? rd32(fixed + 8) >> 16 : 0;
    check(fixed && rd32(fixed) == 0xfeef04bdu && (major == 5 || major == 6) &&
              rd32(fixed + 36) == 2,
          "comctl32.dll has a DLL version resource, 5.82 or 6.10 (major %u)", major);
    gm_put_wstr(text, "dwmapi.dll", 64);
    module = call_import(&c, "KERNEL32.dll", "LoadLibraryW", {text});
    check(module != 0, "the desktop composition module is present");
    gm_put_str(text + 128, "DwmIsCompositionEnabled", 64);
    check(call_import(&c, "KERNEL32.dll", "GetProcAddress", {module, text + 128}) != 0,
          "DwmIsCompositionEnabled resolves through GetProcAddress");
    wr32(text, 0xa5a5a5a5);
    wr32(text + 4, 0xa5a5a5a5);
    check(call_import(&c, "DWMAPI.dll", "DwmIsCompositionEnabled", {text}) == 0 &&
              rd32(text) == 0 && rd32(text + 4) == 0xa5a5a5a5,
          "DwmIsCompositionEnabled writes a false BOOL and preserves its guard");
    check(call_import(&c, "DWMAPI.dll", "DwmIsCompositionEnabled", {0}) == 0x80070057u,
          "DwmIsCompositionEnabled rejects a null output pointer");
    check(call_import(&c, "DWMAPI.dll", "DwmExtendFrameIntoClientArea", {0x20004, text}) ==
              0x80004001u,
          "DwmExtendFrameIntoClientArea reports unavailable desktop composition");
}

static void test_preferred_ui_languages() {
    section("preferred UI languages");
    X86 c;
    loader_init_context(&c);
    const uint32_t count = 0x00308000, size = count + 8, buffer = count + 32;
    check(call_import(&c, "KERNEL32.dll", "GetThreadUILanguage", {}) == 0x0409,
          "GetThreadUILanguage returns the en-US language identifier");
    for (const char *api : {"GetThreadPreferredUILanguages", "GetUserPreferredUILanguages",
                            "GetSystemPreferredUILanguages"}) {
        for (uint32_t flags : {0u, 8u, 4u}) {
            const char *expected = flags == 4 ? "0409" : "en-US";
            uint32_t need = (uint32_t)strlen(expected) + 2;
            memset(g_mem + count, 0xa5, 128);
            wr32(size, 0);
            check(call_import(&c, "KERNEL32.dll", api, {flags, count, 0, size}) == 1 &&
                      rd32(count) == 1 && rd32(size) == need && rd32(count + 4) == 0xa5a5a5a5 &&
                      rd32(size + 4) == 0xa5a5a5a5,
                  "%s size query includes both WCHAR terminators (flags=%u)", api, flags);
            wr32(size, need - 1);
            check(call_import(&c, "KERNEL32.dll", api, {flags, count, buffer, size}) == 0 &&
                      call_import(&c, "KERNEL32.dll", "GetLastError", {}) == 122 &&
                      rd32(size) == need && rd16(buffer) == 0xa5a5,
                  "%s rejects a short buffer without a partial write", api);
            wr32(size, need);
            check(call_import(&c, "KERNEL32.dll", api, {flags, count, buffer, size}) == 1 &&
                      rd32(count) == 1 && rd32(size) == need && gm_wstr(buffer) == expected &&
                      rd16(buffer + (need - 2) * 2) == 0 && rd16(buffer + (need - 1) * 2) == 0 &&
                      rd16(buffer + need * 2) == 0xa5a5,
                  "%s writes the complete multi-string and preserves its guard", api);
        }
        check(call_import(&c, "KERNEL32.dll", api, {12, count, 0, size}) == 0 &&
                  call_import(&c, "KERNEL32.dll", "GetLastError", {}) == 87,
              "%s rejects conflicting language formats", api);
        check(call_import(&c, "KERNEL32.dll", api, {8, count, 0, 0}) == 0,
              "%s rejects a missing size pointer", api);
    }
    wr32(size, 0);
    check(call_import(&c, "KERNEL32.dll", "GetThreadPreferredUILanguages",
                      {0x38, count, 0, size}) == 1 &&
              rd32(size) == 7,
          "thread language query accepts merged fallback flags");
    gm_put_wstr(buffer, "en-US", 32);
    wr16(buffer + 12, 0);
    wr32(count, 0);
    check(call_import(&c, "KERNEL32.dll", "SetThreadPreferredUILanguages", {8, buffer, count}) ==
                  1 &&
              rd32(count) == 1 && rd32(count + 4) == 0xa5a5a5a5,
          "SetThreadPreferredUILanguages acknowledges the fixed en-US preference");
    check(call_import(&c, "KERNEL32.dll", "SetThreadPreferredUILanguages", {0, 0, 0}) == 1,
          "SetThreadPreferredUILanguages accepts optional null pointers");
}

static void test_propvariant_clear() {
    section("PropVariantClear empties a variant");
    X86 c;
    loader_init_context(&c);
    const uint32_t pv = 0x00300000;
    for (uint32_t i = 0; i < 16; ++i)
        wr8(pv + i, 0xab);
    check(call_import(&c, "ole32.dll", "PropVariantClear", {pv}) == 0,
          "PropVariantClear returns S_OK");
    bool emptied = true;
    for (uint32_t i = 0; i < 16; ++i)
        emptied = emptied && rd8(pv + i) == 0;
    check(emptied, "and leaves the whole sixteen-byte variant VT_EMPTY");
    check(call_import(&c, "ole32.dll", "PropVariantClear", {0}) == 0x80070057u,
          "a null variant is E_INVALIDARG rather than a silent success");

    // The copy is the same sixteen bytes, which is what a VT_EMPTY source is.
    const uint32_t src = pv + 64, dst = pv + 128;
    for (uint32_t i = 0; i < 16; ++i) {
        wr8(src + i, (uint8_t)(i + 1));
        wr8(dst + i, 0xff);
    }
    check(call_import(&c, "ole32.dll", "PropVariantCopy", {dst, src}) == 0,
          "PropVariantCopy returns S_OK");
    bool copied = true;
    for (uint32_t i = 0; i < 16; ++i)
        copied = copied && rd8(dst + i) == (uint8_t)(i + 1);
    check(copied, "and the destination is the source");
}

static void test_media_foundation_unavailable() {
    section("Media Foundation present but unsupported");
    X86 c;
    loader_init_context(&c);
    uint32_t name = 0x00300000, out = name + 256;
    gm_put_wstr(name, "mfplat.dll", 64);
    uint32_t platform = call_import(&c, "KERNEL32.dll", "LoadLibraryW", {name});
    check(platform != 0, "LoadLibraryW(mfplat.dll) succeeds");
    gm_put_str(name + 128, "MFStartup", 64);
    uint32_t startup = call_import(&c, "KERNEL32.dll", "GetProcAddress", {platform, name + 128});
    check(startup != 0, "GetProcAddress resolves MFStartup");
    check(call_import(&c, "mfplat.dll", "MFStartup", {0x20070, 0}) == 0,
          "MFStartup initializes the platform before factories report unsupported playback");
    check(call_import(&c, "mfplat.dll", "MFShutdown", {}) == 0,
          "MFShutdown succeeds after platform startup");

    gm_put_wstr(name, "mf.dll", 64);
    uint32_t media = call_import(&c, "KERNEL32.dll", "LoadLibraryW", {name});
    check(media != 0, "LoadLibraryW(mf.dll) succeeds");
    struct Factory {
        const char *name;
        std::vector<uint32_t> args;
    };
    for (const Factory &factory : {
             Factory{"MFCreateMediaSession", {0, out}},
             Factory{"MFCreateSourceResolver", {out}},
             Factory{"MFCreateTopology", {out}},
             Factory{"MFCreateTopologyNode", {0, out}},
             Factory{"MFCreateAudioRendererActivate", {out}},
             Factory{"MFCreateVideoRendererActivate", {0, out}},
             Factory{"MFGetService", {0, 0, 0, out}},
         }) {
        gm_put_str(name + 128, factory.name, 64);
        check(call_import(&c, "KERNEL32.dll", "GetProcAddress", {media, name + 128}) != 0,
              "mf.dll resolves %s", factory.name);
        wr32(out - 4, 0x12345678);
        wr32(out, 0xdeadbeef);
        wr32(out + 4, 0x87654321);
        check(call_import(&c, "mf.dll", factory.name, factory.args) == 0x80004001u,
              "%s returns E_NOTIMPL", factory.name);
        check(rd32(out) == 0 && rd32(out - 4) == 0x12345678 && rd32(out + 4) == 0x87654321,
              "%s clears only its 32-bit output pointer", factory.name);
        auto null_args = factory.args;
        null_args.back() = 0;
        check(call_import(&c, "mf.dll", factory.name, null_args) == 0x80004001u,
              "%s accepts a null output while reporting unsupported", factory.name);
    }
}

static void test_loader() {
    section("loader");
    bool ok = loader_load(nullptr);
    if (!check(ok, "loader_load(%s): %s", RECOMP_DEVELOPER_EXE, ok ? "loaded" : loader_error())) {
        printf("cannot continue without the image\n");
        exit(1);
    }
    check(loader_image_base() == RECOMP_IMAGE_BASE, "image base is %08x", loader_image_base());
    check(loader_entry_point() == RECOMP_ENTRY_POINT, "entry point is %08x", loader_entry_point());

    // A Delphi image carries a TLS directory; the loader reserves a slot, writes
    // its index where the image reads it, and gives the main thread a block that
    // starts with the directory's raw bytes.
    const LoaderTls &tls = loader_tls();
    if (tls.raw_end > tls.raw_start) {
        check(tls.index < TLS_SLOTS, "TLS slot %u reserved", tls.index);
        check(rd32(tls.index_addr) == tls.index, "the image's TLS index reads %u",
              rd32(tls.index_addr));
        uint32_t block = rd32(TLS_BASE + 4 * tls.index);
        check(block != 0, "main thread TLS block at %08x", block);
        check(memcmp(g_mem + block, g_mem + tls.raw_start, tls.raw_end - tls.raw_start) == 0,
              "the block starts with the directory's raw data");
    } else {
        check(tls.index == 0xffffffffu, "no TLS directory: no slot reserved");
    }

    if (tls.index < TLS_SLOTS && tls.callbacks) {
        uint32_t saved[3];
        memcpy(saved, g_mem + tls.callbacks, sizeof saved);
        wr32(tls.callbacks,
             imports_alloc_trampoline("test", "tls_callback_cdecl", fake_loader_tls_callback, 0));
        wr32(tls.callbacks + 4,
             imports_alloc_trampoline("test", "tls_callback_stdcall", fake_loader_tls_callback, 3));
        wr32(tls.callbacks + 8, 0);
        X86 c = *loader_context();
        uint32_t esp = c.r[R_ESP];
        g_tls_callback_hits = 0;
        run_entry(&c);
        check(g_tls_callback_hits == 2, "both TLS callbacks ran in the entry path");
        check(c.r[R_ESP] == esp && rd32(esp) == GUEST_RETURN_SENTINEL,
              "TLS callbacks preserve the entry stack and its return sentinel");
        memcpy(g_mem + tls.callbacks, saved, sizeof saved);
    }

    ExpectedImage expect;
    std::string err;
    if (!pefile_sections(expect, err)) {
        check(false, "pefile cross-check could not run: %s", err.c_str());
    } else {
        check(expect.base == loader_image_base() && expect.entry == loader_entry_point(),
              "pefile agrees on base %08x and entry %08x", expect.base, expect.entry);
        check(loader_image_limit() == expect.base + expect.size,
              "the image ends at %08x, derived from SizeOfImage %x", loader_image_limit(),
              expect.size);
        uint32_t aux_imports = 0;
        for (uint32_t i = 0; const LoaderModule *m = loader_module(i); ++i) {
            uint32_t opt = m->base + rd32(m->base + 0x3c) + 24;
            uint32_t imports = rd32(opt + 104);
            for (uint32_t d = m->base + imports; imports && rd32(d + 12); d += 20)
                for (uint32_t thunk = m->base + rd32(d + 16); rd32(thunk); thunk += 4)
                    ++aux_imports;
        }
        check(loader_iat_patched() == expect.imports.size() + aux_imports,
              "patched %u IAT slots (expected %zu main + %u auxiliary)", loader_iat_patched(),
              expect.imports.size(), aux_imports);
        const auto &got = loader_sections();
        check(got.size() == expect.sections.size(), "section count %zu matches pefile %zu",
              got.size(), expect.sections.size());
        bool all = true;
        for (size_t i = 0; i < std::min(got.size(), expect.sections.size()); ++i) {
            const auto &e = expect.sections[i];
            const auto &section = got[i];
            if (section.name != e.name || section.va != e.va || section.vsize != e.vsize ||
                section.raw_size != e.raw)
                all = false;
        }
        check(all, "every section maps at the address, size and raw size pefile reports");

        // Use the section table for both code samples; entry need not be in the
        // first executable section, and raw offsets need not equal RVAs.
        FILE *f = fopen(RECOMP_DEVELOPER_EXE, "rb");
        if (check(f != nullptr, "opened the image for a byte-level spot check")) {
            auto spot_check = [&](uint32_t va, const ExpectedSection &section, size_t n) {
                uint8_t bytes[64];
                bool read = va >= section.va && uint64_t(va - section.va) + n <= section.raw &&
                            fseek(f, section.offset + (va - section.va), SEEK_SET) == 0 &&
                            fread(bytes, 1, n, f) == n;
                check(read && memcmp(bytes, g_mem + va, n) == 0,
                      "code bytes at %08x in %s match the file", va, section.name.c_str());
            };
            bool code = false, entry = false;
            for (const auto &section : expect.sections) {
                if (!code && (section.flags & 0x20000000u) && section.raw) {
                    spot_check(section.va, section, std::min(section.raw, 64u));
                    code = true;
                }
                uint32_t va = loader_entry_point();
                if (va >= section.va && uint64_t(va - section.va) + 16 <= section.raw) {
                    spot_check(va, section, 16);
                    entry = true;
                }
            }
            check(code && entry, "section table locates executable bytes and the entry point");
            fclose(f);
        }

        // Test the zero-fill tails the image actually has. The loader writes
        // the TLS index after mapping, so those four bytes are no longer BSS.
        bool zeroed = true, has_tail = false;
        for (const auto &section : expect.sections) {
            has_tail |= section.vsize > section.raw;
            for (uint32_t i = section.raw; i < section.vsize; ++i) {
                uint32_t va = section.va + i;
                if (tls.index < TLS_SLOTS && va >= tls.index_addr && va - tls.index_addr < 4)
                    continue;
                zeroed &= rd8(va) == 0;
            }
        }
        if (has_tail)
            check(zeroed, "section tails past SizeOfRawData are zero filled");
        else {
            printf("  [SKIP] the image has no zero-fill section tails\n");
            ++g_skips;
        }

        uint32_t data_slots = 0;
        bool iat_ok = true, imports_weanetr = false;
        for (const auto &import : expect.imports) {
            std::string dll = import.dll;
            std::transform(dll.begin(), dll.end(), dll.begin(),
                           [](unsigned char ch) { return char(std::tolower(ch)); });
            imports_weanetr |= dll == "weanetr.dll";
            uint32_t data = imports_data_address(import.dll.c_str(), import.name.c_str());
            uint32_t trampoline = imports_trampoline_for(import.dll.c_str(), import.name.c_str());
            uint32_t value = rd32(import.slot);
            if (data) {
                ++data_slots;
                iat_ok &= value == data && !imports_is_trampoline(value);
            } else {
                iat_ok &= value == trampoline && imports_is_trampoline(value) &&
                          imports_describe(value) != nullptr;
            }
            if (value != (data ? data : trampoline))
                printf("  IAT mismatch at %08x for %s!%s\n", import.slot, import.dll.c_str(),
                       import.name.c_str());
        }
        check(iat_ok, "every PE import slot holds its symbol's trampoline or data storage");
        check(loader_iat_data_imports() == data_slots,
              "%u IAT slots hold data symbols (expected %u)", loader_iat_data_imports(),
              data_slots);

        // imports_has_dll reports the shim registry, not which DLLs this PE
        // imports. Require both so another image cannot use unallocated data.
        if (imports_weanetr && imports_has_dll("weanetr.dll")) {
            uint32_t guid = imports_data_address("weanetr.dll", "?BFAID_INet@@3U_GUID@@A");
            check(guid != 0 && !imports_is_trampoline(guid),
                  "weanetr!BFAID_INet resolved to guest storage at %08x", guid);
            bool guid_zero = guid != 0;
            for (int i = 0; guid && i < 16; ++i)
                guid_zero &= g_mem[guid + i] == 0;
            check(guid_zero, "the GUID storage is 16 zeroed bytes");
            uint32_t table = imports_data_address("weanetr.dll", "?options_to_parity_table@@3PAHA");
            check(table != 0 && heap_size(table) == 4096,
                  "options_to_parity_table has %u bytes of storage", heap_size(table));
        } else {
            printf("  [SKIP] the image imports no data symbols\n");
            ++g_skips;
        }
    }

    // TEB.
    check(rd32(0x0fe00000) == 0xffffffffu, "FS:[0] SEH head is -1");
    check(rd32(0x0fe00018) == 0x0fe00000u, "FS:[0x18] points at the TEB");
    check(rd32(0x0fe0002c) == 0x0fe01000u, "FS:[0x2c] points at the TLS array");
    check(loader_context()->fs_base == 0x0fe00000u, "fs_base is the TEB");
    const X86 *ic = loader_context();
    check(ic->fpu_cw == 0x037f && ic->fpu_sw == 0 && ic->fpu_tag == 0xffff && ic->fpu_top == 0,
          "the x87 starts at cw=%04x sw=%04x tag=%04x top=%u", ic->fpu_cw, ic->fpu_sw, ic->fpu_tag,
          ic->fpu_top);
    check(x86_get_eflags(ic) == 0x00000202u, "PUSHFD at process start reads %08x",
          x86_get_eflags(ic));
    check(loader_context()->r[R_ESP] > 0x0ef00000 && loader_context()->r[R_ESP] < 0x0f000000,
          "initial ESP %08x is inside the stack", loader_context()->r[R_ESP]);

    // A wrong image must be refused.
    char bad_dir[512];
    snprintf(bad_dir, sizeof bad_dir, "%s/recomp-bad-image-XXXXXX", os_temp_dir());
    if (check(os_mkdtemp(bad_dir) == 0, "created a scratch directory for the wrong image")) {
        std::string bad_path = std::string(bad_dir) + "/wrong.exe";
        FILE *bad = fopen(bad_path.c_str(), "wb");
        if (check(bad != nullptr, "created a deliberately invalid image")) {
            fputs("not the configured executable", bad);
            fclose(bad);
            check(!loader_load(bad_path.c_str()), "a different EXE is refused: %s", loader_error());
        }
        remove_tree(bad_dir);
    }
    check(loader_load(nullptr), "reloaded the correct image");
}

// The recorded lines of a discovery file, without its comment header.
static std::string file_text(const std::string &path) {
    std::string out;
    if (FILE *in = fopen(path.c_str(), "r")) {
        char line[256];
        while (fgets(line, sizeof line, in))
            if (line[0] != '#')
                out += line;
        fclose(in);
    }
    return out;
}

// The recorder closes the loop between a run and the next translation, so what
// it refuses matters as much as what it keeps: the next pass hands every
// recorded address to the translator as an entry point, and one the translator
// refuses ends discovery there.
static void test_discovery_recorder() {
    uint32_t code = 0, data = 0;
    for (const SectionInfo &sec : loader_sections()) {
        bool exec = (sec.characteristics & 0x20000000u) != 0;
        if (exec && !code)
            code = sec.va + 0x40;
        if (!exec && !data)
            data = sec.va + 0x40;
    }
    if (!check(code != 0, "the image has a code section"))
        return;

    char dir[512];
    snprintf(dir, sizeof dir, "%s/recomp-discovery-XXXXXX", os_temp_dir());
    if (!check(os_mkdtemp(dir) == 0, "created a discovery directory"))
        return;
    std::string file = std::string(dir) + "/discovered.txt";
    check(discovery_count() == 0, "nothing is recorded before RECOMP_DISCOVERY names a file");
    discovery_note("call", code, 0x00401000);
    check(discovery_count() == 0, "and nothing is recorded while it is unset");

    os_setenv("RECOMP_DISCOVERY", file.c_str());
    discovery_note("call", 0, 0x00401000);
    check(discovery_count() == 0, "a call through a pointer the guest never filled in is not code");
    discovery_note("call", IMAGE_BASE - 0x1000, 0x00401000);
    discovery_note("jump", loader_image_limit() + 0x1000, 0x00401000);
    check(discovery_count() == 0, "nor is an address outside the image");
    if (data) { // the stub image need not have one
        discovery_note("call", data, 0x00401000);
        check(discovery_count() == 0, "nor one in a section the image cannot execute: %08x", data);
    }

    discovery_note("call", code, 0x00401000);
    discovery_note("call", code, 0x00402000);
    check(discovery_count() == 1,
          "an address in a code section is one find however often it is reached");
    // Each fresh address rewrites the file, so a run that dies in a signal
    // still leaves its addresses behind; a later hit on one already recorded
    // only updates the count, which reaches the file when the run ends.
    char want[64];
    snprintf(want, sizeof want, "%08x call 00401000 1", code);
    check(file_text(file) == std::string(want) + "\n",
          "a fresh address reaches the file at once, as \"%s\"", want);
    discovery_write();
    snprintf(want, sizeof want, "%08x call 00401000 2", code);
    check(file_text(file) == std::string(want) + "\n",
          "and the run's end records how often it was reached, as \"%s\": %s", want,
          file_text(file).c_str());
    for (uint32_t i = 0; i < loader_module_count(); ++i) {
        const LoaderModule *module = loader_module(i);
        for (const SectionInfo &sec : module->sections) {
            uint32_t before = discovery_count();
            bool executable = (sec.characteristics & 0x20000000u) != 0;
            discovery_note("call", sec.va, module->base);
            check(discovery_count() == before + unsigned(executable),
                  "auxiliary discovery respects %s section %s execute flag", module->name.c_str(),
                  sec.name.c_str());
        }
    }
    os_unsetenv("RECOMP_DISCOVERY");
    remove_tree(dir);
}

namespace stack_switch_test {
constexpr uint32_t entry = 0x0e100100, resume = entry + 0x10, worker = entry + 0x20;
uint32_t saved_stack, worker_stack, visits;
void guest_ret(X86 *c) {
    c->eip = rd32(c->r[R_ESP]);
    c->r[R_ESP] += 4;
    recomp_return(c);
}
void continuation(X86 *c) {
    ++visits;
    c->r[R_EAX] += 1;
    guest_ret(c);
}
void start(X86 *c) {
    c->r[R_ESP] -= 4;
    wr32(c->r[R_ESP], resume);
    saved_stack = c->r[R_ESP];
    c->r[R_ESP] = worker_stack;
    guest_ret(c); // A scheduler RET on the other stack names the worker.
    if (c->eip != resume)
        return;
    continuation(c);
}
void other_stack(X86 *c) {
    c->r[R_EAX] += 10;
    c->r[R_ESP] = saved_stack;
    guest_ret(c); // Resume a CALL continuation whose host frame has unwound.
}
} // namespace stack_switch_test

// Exercise two guest stacks and a resumed CALL continuation through the real
// entry driver. The translator suite separately checks the emitted CALL guards.
static void test_resumable_stacks() {
    if (!recomp_resumable_stacks)
        return;
    section("cooperative guest stacks");
    using namespace stack_switch_test;
    static const uint32_t addresses[] = {entry, resume, worker};
    static void (*const functions[])(X86 *) = {start, continuation, other_stack};
    static RecompHookFn hooks[3]{};
    static uint8_t hooked[3]{};
    static const RecompModule module = {"stack-switch-test",
                                        entry,
                                        worker + 1,
                                        addresses,
                                        3,
                                        functions,
                                        hooks,
                                        hooked,
                                        nullptr,
                                        0,
                                        nullptr};
    recomp_module_register(&module);
    const uint32_t stacks = heap_alloc(512);
    X86 c{};
    c.r[R_ESP] = stacks + 128;
    wr32(c.r[R_ESP], GUEST_RETURN_SENTINEL);
    worker_stack = stacks + 384;
    wr32(worker_stack, worker);
    wr32(worker_stack + 4, 0x12345678);
    visits = 0;
    recomp_run(&c, entry);
    check(c.r[R_EAX] == 11 && visits == 1,
          "worker ran before the original continuation, exactly once");
    check(c.eip == GUEST_RETURN_SENTINEL && c.r[R_ESP] == stacks + 132,
          "return restored the original stack and reached its caller");
    check(rd32(worker_stack + 4) == 0x12345678, "suspended worker stack was preserved");
    heap_free(stacks);
}

static void test_allocator() {
    section("allocator");
    check(heap_check().empty(), "the heap starts consistent with %u used blocks",
          heap_stats().used_blocks);

    uint32_t a = heap_alloc(100);
    uint32_t b = heap_alloc(100);
    uint32_t c = heap_alloc(100);
    check(a && b && c, "three 100-byte allocations: %08x %08x %08x", a, b, c);
    check((a & 15) == 0 && (b & 15) == 0 && (c & 15) == 0, "all are 16-byte aligned");
    check(b == a + 112 && c == b + 112, "blocks are packed with the size rounded to 16");
    check(heap_size(a) == 100, "heap_size reports the requested size %u", heap_size(a));

    memset(g_mem + a, 0xab, 100);
    uint32_t a2 = heap_realloc(a, 400);
    check(a2 != 0, "realloc 100 -> 400 gives %08x", a2);
    bool kept = true;
    for (int i = 0; i < 100; ++i)
        if (g_mem[a2 + i] != 0xab)
            kept = false;
    check(kept, "realloc preserved the original contents");

    check(heap_free(b), "freed the middle block");
    check(heap_free(c), "freed the third block");
    check(heap_check().empty(), "heap is consistent after coalescing: %s",
          heap_check().empty() ? "no gaps or adjacent free blocks" : heap_check().c_str());

    // a was freed by the realloc, and b and c have just been freed, so the
    // three coalesce into one hole starting at the old `a`.
    uint32_t d = heap_alloc(200);
    check(d == a, "first fit reused the coalesced hole at %08x", d);
    heap_free(d);
    heap_free(a2);
    check(heap_check().empty(), "heap is consistent after freeing everything");

    uint32_t page = heap_alloc(4096, true, 4096);
    check(page && (page & 4095) == 0, "4096-aligned allocation at %08x", page);
    bool zero = true;
    for (int i = 0; i < 4096; ++i)
        if (g_mem[page + i])
            zero = false;
    check(zero, "the zeroing allocation is zero filled");
    heap_free(page);

    HeapStats s = heap_stats();
    check(s.free_bytes > 0xc000000, "free bytes %llu after the churn",
          (unsigned long long)s.free_bytes);
    check(heap_alloc(GUEST_SIZE) == 0, "an allocation larger than any heap span fails cleanly");
    if (GUEST_HEAP_EXTRA_SIZE > HEAP_LIMIT - HEAP_BASE) {
        // Larger than the whole original arena: must live beyond the fixed
        // stack/TEB/shims, and freeing/reallocating must not bridge that gap.
        const uint32_t large_size = HEAP_LIMIT - HEAP_BASE + 4096;
        uint32_t large = heap_alloc(large_size, false, 4096);
        check(large >= GUEST_HEAP_EXTRA_BASE && large != 0, "large allocation uses extra span");
        if (large) {
            g_mem[large] = 0x35;
            g_mem[large + large_size - 1] = 0x79;
            uint32_t grown = heap_realloc(large, large_size + 8192);
            check(grown && g_mem[grown] == 0x35 && g_mem[grown + large_size - 1] == 0x79,
                  "extra-span realloc preserves both ends");
            heap_free(grown ? grown : large);
            check(heap_check().empty(), "disjoint heap coalesces without bridging runtime space");
        }
    }
    check(heap_alloc(0xffffffffu) == 0, "a 0xffffffff request is refused, not rounded to zero");
    uint32_t keep = heap_alloc(64);
    memset(g_mem + keep, 0x5a, 64);
    check(heap_realloc(keep, 0xffffffffu, true) == 0 && g_mem[keep] == 0x5a,
          "a 0xffffffff realloc is refused and leaves the block untouched");
    heap_free(keep);
    check(heap_check().empty(), "heap still consistent: %s",
          heap_check().empty() ? "yes" : heap_check().c_str());

    // Check the actual refusal log in a child, without redirecting this
    // process's stderr or disturbing its current guest register file.
    char dir[] = "build/recomp/heap-refusal-XXXXXX", exe[4096];
    if (check(os_mkdtemp(dir) == 0, "created a heap diagnostic directory")) {
        std::string path = std::string(dir) + "/refusal.log";
        check(os_exe_path(exe, sizeof exe) == 0, "heap diagnostic knows its executable");
        const char *args[] = {exe, "--child-heap-refusal", path.c_str(), nullptr};
        int64_t pid = 0;
        int code = -1;
        check(os_spawn(args, &pid) == 0 && os_wait(pid, &code) == 0 && code == 0,
              "heap refusal child exits cleanly (exit %d)", code);
        std::string text;
        if (FILE *log = fopen(path.c_str(), "r")) {
            char line[1024];
            while (fgets(line, sizeof line, log))
                text += line;
            fclose(log);
        }
        check(text.find("refusing a 4294967295 byte request") != std::string::npos,
              "refusal log retains the requested size");
        check(text.find("EAX=13579bdf") != std::string::npos &&
                  text.find("EDI=2468ace0") != std::string::npos &&
                  text.find("ESP=") != std::string::npos && text.find("EIP=") != std::string::npos,
              "refusal log includes the active guest registers");
        char frame[64];
        snprintf(frame, sizeof frame, "frame 0 returns to %08x", loader_image_base() + 0x1234);
        check(text.find(frame) != std::string::npos, "refusal log includes the guest return chain");
        remove_tree(dir);
    }
}

// NFSMW issue 8: a guest write that runs off the end of a bulk-written block
// (a DirectSound ring, a thread stack) must land in the block's gap, not on the
// small kit object the first-fit heap puts next to it.
static void test_heap_guard() {
    section("heap guard");
    uint32_t hits0 = heap_guard_hits();
    // Control: the old layout, where the first-fit heap put the 16-byte view
    // straight after its ring (264576 bytes, a multiple of 16, so no slack).
    uint32_t ring = heap_alloc(264576 + 16, true, 16);
    uint32_t view = ring + 264576;
    check(ring != 0, "control: an unguarded ring with the object against its end (%08x)", ring);
    wr32(view + 4, 0x504f504du);
    for (uint32_t i = 0; i < 16; i += 2)
        wr16(ring + 264576 + i, 0x6a29);
    check(rd32(view + 4) != 0x504f504du, "control: a write past the unguarded ring's end lands on the object");
    heap_free(ring);

    uint32_t g = heap_alloc_guarded(264576, true, 0x4000, "dsound buffer", 164);
    uint32_t v = heap_alloc(16, true, 16);
    check(g && v, "a guarded ring and the next object: %08x, %08x", g, v);
    check(v >= g + 264576 + 0x4000 || v + 16 <= g - 0x4000, "the next object is outside the ring's gaps");
    check(rd32(g) == 0 && rd32(g + 264572) == 0, "the guarded ring is zeroed");
    check(heap_guard_check(g), "an untouched guarded ring checks clean");
    wr32(v + 4, 0x504f504du);
    for (uint32_t i = 0; i < 16; i += 2)
        wr16(g + 264576 + i, 0x6a29);
    check(rd32(v + 4) == 0x504f504du, "a write past the guarded ring's end leaves the next object alone");
    check(!heap_guard_check(g) && heap_guard_hits() == hits0 + 1, "the write is found and counted once");
    check(heap_guard_check(g), "the gap is filled again after the report");
    wr8(g - 1, 0x00);
    check(heap_guard_check_all() == 1 && heap_guard_hits() == hits0 + 2,
          "a write just before the start is found by the sweep");
    check(heap_free_guarded(g), "a guarded block frees");
    check(heap_guard_check(g), "a freed guarded block is no longer tracked");
    heap_free(v);
    uint32_t plain = heap_alloc(64);
    check(heap_free_guarded(plain), "heap_free_guarded frees a plain block too");
    check(heap_check().empty(), "the heap is consistent after the guard checks");
}

// NFSMW issue 8 (3.0.3): a write to a sealed read-only page is stopped in the fault handler and recorded; the
// object keeps what the kit wrote and the writer goes on.
static void readonly_test_fault(const char *what) {
    fprintf(stderr, "runtime_tests: unexpected %s in the read-only page test\n", what);
    _exit(3);
}
static void test_heap_kit_span() {
    section("heap kit span");
    uint32_t k = heap_alloc_kit(0x10000, true, 16);
    uint32_t g = heap_alloc(0x10000, true, 16);
    const uint32_t top = GUEST_HEAP_EXTRA_BASE + GUEST_HEAP_EXTRA_SIZE;
    if (GUEST_HEAP_EXTRA_SIZE < 0x08000000u) {
        check(k != 0, "no extra span: a kit object comes from the shared heap (%08x)", k);
    } else {
        check(k >= top - 0x02000000u && k < top, "a kit object is in the kit span at the top (%08x)", k);
        check(g && (g < top - 0x02000000u), "a guest block is outside the kit span (%08x)", g);
        uint32_t big = heap_alloc(GUEST_HEAP_EXTRA_SIZE - 0x01000000u);
        check(big == 0 || big + GUEST_HEAP_EXTRA_SIZE - 0x01000000u <= top - 0x02000000u,
              "a guest block never reaches into the kit span (%08x)", big);
        if (big)
            heap_free(big);
    }
    uint32_t r = heap_realloc(g, 0x20000, true);
    check(r && !(r >= top - 0x02000000u && r < top), "a grown guest block stays out of the kit span (%08x)", r);
    heap_free(r);
    heap_free(k);
    check(heap_check().empty(), "the heap is consistent after the kit span checks");
}

static void test_heap_readonly() {
    section("heap read-only pages");
    setenv("RECOMP_READONLY_PAGES", "1", 1); // opt-in from 3.0.4
    if (!heap_readonly_enabled()) {
        ++g_skips;
        printf("  [skip] read-only pages need macOS on arm64\n");
        return;
    }
    os_install_fault_handlers(readonly_test_fault);
    const uint32_t hits0 = heap_readonly_hits();
    uint32_t v = heap_alloc_readonly(16, "dsound buffer object", 164);
    check(v && v % (uint32_t)os_vm_page_size() == 0, "a read-only page is page-aligned (%08x)", v);
    wr32(v, 0x031dce80u);
    wr32(v + 4, 0x504f504du);
    check(rd32(v + 4) == 0x504f504du, "control: the page takes writes before it is sealed");
    check(heap_seal_readonly(v), "the page seals");
    wr32(v + 4, 0x74db0bcdu);
    wr16(v + 2, 0x74e2);
    check(rd32(v + 4) == 0x504f504du && rd32(v) == 0x031dce80u, "writes to the sealed page are stopped, the object intact");
    check(heap_readonly_hits() == hits0 + 2, "both stopped writes are counted (%u)", heap_readonly_hits() - hits0);
    heap_readonly_report();
    check(rd32(v) == 0x031dce80u, "reads of the sealed page work");
    check(heap_free_guarded(v), "a sealed page frees");
    uint32_t again = heap_alloc(64, true, 16);
    wr32(again, 7);
    check(rd32(again) == 7, "memory allocated after the free takes writes");
    heap_free(again);
    check(heap_check().empty(), "the heap is consistent after the read-only checks");
    unsetenv("RECOMP_READONLY_PAGES");
}

static void test_heap_shims(X86 *c) {
    section("HeapAlloc / GlobalAlloc / VirtualAlloc shims");
    uint32_t h = call_import(c, "KERNEL32.dll", "HeapCreate", {0, 0x1000, 0});
    check(h != 0, "HeapCreate -> %08x", h);
    uint32_t p = call_import(c, "KERNEL32.dll", "HeapAlloc", {h, 8, 256});
    check(p != 0, "HeapAlloc(256) -> %08x", p);
    bool zeroed = true;
    for (int i = 0; i < 256; ++i)
        if (g_mem[p + i])
            zeroed = false;
    check(zeroed, "HEAP_ZERO_MEMORY produced a zeroed block");
    check(call_import(c, "KERNEL32.dll", "HeapSize", {h, 0, p}) == 256, "HeapSize reports 256");
    uint32_t p2 = call_import(c, "KERNEL32.dll", "HeapReAlloc", {h, 8, p, 1024});
    check(p2 != 0 && call_import(c, "KERNEL32.dll", "HeapSize", {h, 0, p2}) == 1024,
          "HeapReAlloc to 1024 -> %08x", p2);
    check(call_import(c, "KERNEL32.dll", "HeapFree", {h, 0, p2}) == 1, "HeapFree succeeded");

    uint32_t g = call_import(c, "KERNEL32.dll", "GlobalAlloc", {0x40, 64});
    check(g != 0, "GlobalAlloc(GMEM_ZEROINIT, 64) -> %08x", g);
    check(call_import(c, "KERNEL32.dll", "GlobalLock", {g}) == g, "GlobalLock returns the block");
    check(call_import(c, "KERNEL32.dll", "GlobalFree", {g}) == 0, "GlobalFree returns NULL");

    uint32_t v = call_import(c, "KERNEL32.dll", "VirtualAlloc", {0, 8192, 0x1000, 4});
    check(v != 0 && (v & 4095) == 0, "VirtualAlloc(8192) -> page-aligned %08x", v);
    check(call_import(c, "KERNEL32.dll", "VirtualFree", {v, 0, 0x8000}) == 1,
          "VirtualFree(MEM_RELEASE)");
}

static void test_memory_shims_2(X86 *c) {
    section("VirtualAlloc granularity, waits, modules, TLS, timers");
    uint32_t v1 = call_import(c, "KERNEL32.dll", "VirtualAlloc", {0, 1, 0x1000, 4});
    uint32_t v2 = call_import(c, "KERNEL32.dll", "VirtualAlloc", {0, 1, 0x1000, 4});
    check(v1 && v2 && (v1 & 4095) == 0 && (v2 & 4095) == 0 && (v2 - v1) >= 4096,
          "VirtualAlloc(1 byte) reserves a whole page: %08x then %08x", v1, v2);
    // Committing pages that are already committed must not disturb them.
    memset(g_mem + v1, 0xcd, 4096);
    check(call_import(c, "KERNEL32.dll", "VirtualAlloc", {v1, 4096, 0x1000, 4}) == v1 &&
              g_mem[v1] == 0xcd,
          "MEM_COMMIT over live pages leaves their contents alone");

    // A reservation commits to zeroed pages the first time.
    uint32_t v3 = call_import(c, "KERNEL32.dll", "VirtualAlloc", {0, 8192, 0x2000, 4});
    check(v3 != 0, "VirtualAlloc(MEM_RESERVE) -> %08x", v3);
    memset(g_mem + v3, 0xee, 8192);
    check(call_import(c, "KERNEL32.dll", "VirtualAlloc", {v3, 8192, 0x1000, 4}) == v3 &&
              g_mem[v3] == 0,
          "the first MEM_COMMIT of a reservation zeroes it");
    call_import(c, "KERNEL32.dll", "VirtualFree", {v3, 0, 0x8000});

    // A release of an interior pointer must fail and must leave the region
    // tracked, so a later decommit of the real base still works.
    uint32_t v4 = call_import(c, "KERNEL32.dll", "VirtualAlloc", {0, 8192, 0x1000, 4});
    check(v4 != 0, "VirtualAlloc(8192) -> %08x", v4);
    check(call_import(c, "KERNEL32.dll", "VirtualFree", {v4 + 4096, 0, 0x8000}) == 0,
          "MEM_RELEASE of an interior pointer fails");
    memset(g_mem + v4, 0x77, 8192);
    check(call_import(c, "KERNEL32.dll", "VirtualFree", {v4, 0, 0x4000}) == 1 && g_mem[v4] == 0 &&
              g_mem[v4 + 4096] == 0,
          "the region is still tracked, so decommitting its base succeeds");
    check(call_import(c, "KERNEL32.dll", "VirtualFree", {v4, 0, 0x8000}) == 1,
          "and releasing the base then succeeds");

    check(call_import(c, "KERNEL32.dll", "VirtualFree", {v1, 0, 0x4000}) == 1,
          "VirtualFree(MEM_DECOMMIT)");
    bool cleared = true;
    for (int i = 0; i < 4096; ++i)
        if (g_mem[v1 + i])
            cleared = false;
    check(cleared, "decommitted pages no longer hold their old contents");
    call_import(c, "KERNEL32.dll", "VirtualFree", {v1, 0, 0x8000});
    call_import(c, "KERNEL32.dll", "VirtualFree", {v2, 0, 0x8000});

    // WaitForMultipleObjects(wait all) must not consume anything when it fails.
    uint32_t s1 = call_import(c, "KERNEL32.dll", "CreateSemaphoreA", {0, 1, 4, 0});
    uint32_t s2 = call_import(c, "KERNEL32.dll", "CreateSemaphoreA", {0, 0, 4, 0});
    uint32_t arr = scratch_block(8);
    wr32(arr, s1);
    wr32(arr + 4, s2);
    check(call_import(c, "KERNEL32.dll", "WaitForMultipleObjects", {2, arr, 1, 0}) == 0x102,
          "wait-all on [signalled, unsignalled] reports WAIT_TIMEOUT");
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {s1, 0}) == 0,
          "the first semaphore still holds its count");
    call_import(c, "KERNEL32.dll", "ReleaseSemaphore", {s1, 1, 0});
    call_import(c, "KERNEL32.dll", "ReleaseSemaphore", {s2, 1, 0});
    check(call_import(c, "KERNEL32.dll", "WaitForMultipleObjects", {2, arr, 1, 0}) == 0,
          "wait-all succeeds once both are signalled");
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {s1, 0}) == 0x102 &&
              call_import(c, "KERNEL32.dll", "WaitForSingleObject", {s2, 0}) == 0x102,
          "and it consumed both");

    // Waitable timers fire: once after a relative due time, periodically, at
    // an absolute UTC time, and not at all once cancelled.
    {
        uint32_t timer = call_import(c, "KERNEL32.dll", "CreateWaitableTimerA", {0, 0, 0});
        uint32_t due = scratch_block(8);
        auto set_due = [&](int64_t v) {
            wr32(due, (uint32_t)v);
            wr32(due + 4, (uint32_t)((uint64_t)v >> 32));
        };
        set_due(-200000); // 20 ms from now
        check(call_import(c, "KERNEL32.dll", "SetWaitableTimer", {timer, due, 0, 0, 0, 0}) == 1,
              "SetWaitableTimer accepts a relative due time");
        check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {timer, 0}) == 0x102,
              "a timer is not signalled before its due time");
        check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {timer, 2000}) == 0,
              "a wait on it returns when the timer fires");
        check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {timer, 50}) == 0x102,
              "a one-shot synchronization timer fires once");
        set_due(-10000);
        call_import(c, "KERNEL32.dll", "SetWaitableTimer", {timer, due, 10, 0, 0, 0});
        check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {timer, 2000}) == 0 &&
                  call_import(c, "KERNEL32.dll", "WaitForSingleObject", {timer, 2000}) == 0 &&
                  call_import(c, "KERNEL32.dll", "WaitForSingleObject", {timer, 2000}) == 0,
              "a periodic timer fires every period");
        call_import(c, "KERNEL32.dll", "CancelWaitableTimer", {timer});
        uint32_t now = scratch_block(8);
        call_import(c, "KERNEL32.dll", "GetSystemTimeAsFileTime", {now});
        uint64_t ft = (uint64_t)rd32(now) | ((uint64_t)rd32(now + 4) << 32);
        set_due((int64_t)(ft + 300000)); // 30 ms from now, absolute
        call_import(c, "KERNEL32.dll", "SetWaitableTimer", {timer, due, 0, 0, 0, 0});
        check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {timer, 0}) == 0x102 &&
                  call_import(c, "KERNEL32.dll", "WaitForSingleObject", {timer, 2000}) == 0,
              "an absolute due time is UTC FILETIME");
        set_due(-100000000); // ten seconds
        call_import(c, "KERNEL32.dll", "SetWaitableTimer", {timer, due, 0, 0, 0, 0});
        call_import(c, "KERNEL32.dll", "CancelWaitableTimer", {timer});
        check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {timer, 50}) == 0x102,
              "a cancelled timer does not fire");
        call_import(c, "KERNEL32.dll", "CloseHandle", {timer});
    }

    // LoadLibraryA only succeeds for modules the runtime can serve.
    check(call_import(c, "KERNEL32.dll", "LoadLibraryA", {put_str("unregistered.dll")}) == 0,
          "LoadLibraryA(\"unregistered.dll\") fails: no shims for it");
    uint32_t hmod = call_import(c, "KERNEL32.dll", "LoadLibraryA", {put_str("winmm.dll")});
    check(hmod != 0, "LoadLibraryA(\"winmm.dll\") -> %08x", hmod);
    uint32_t proc =
        call_import(c, "KERNEL32.dll", "GetProcAddress", {hmod, put_str("timeGetTime")});
    check(proc == imports_trampoline_for("WINMM.dll", "timeGetTime"),
          "GetProcAddress returned the timeGetTime trampoline");

    uint32_t slot = call_import(c, "KERNEL32.dll", "TlsAlloc", {});
    call_import(c, "KERNEL32.dll", "TlsSetValue", {slot, 0x99});
    check(call_import(c, "KERNEL32.dll", "TlsFree", {slot}) == 1 &&
              call_import(c, "KERNEL32.dll", "TlsGetValue", {slot}) == 0,
          "TlsFree clears the slot");
    check(call_import(c, "KERNEL32.dll", "TlsAlloc", {}) == slot,
          "the freed index is handed straight back out");
    call_import(c, "KERNEL32.dll", "TlsFree", {slot});
    bool exhausted = false;
    for (int i = 0; i < 200; ++i) {
        uint32_t s = call_import(c, "KERNEL32.dll", "TlsAlloc", {});
        if (s == 0xffffffffu) {
            exhausted = true;
            break;
        }
        call_import(c, "KERNEL32.dll", "TlsFree", {s});
    }
    check(!exhausted, "200 allocate/free cycles do not exhaust the 64 slots");

    uint32_t li = scratch_block(8);
    check(call_import(c, "KERNEL32.dll", "QueryPerformanceFrequency", {li}) == 1 &&
              rd32(li) == 1000000,
          "QueryPerformanceFrequency reports 1 MHz");
    check(call_import(c, "KERNEL32.dll", "QueryPerformanceCounter", {li}) == 1,
          "QueryPerformanceCounter");
    // Run 5 (UG1): with game.toml [game] qpc_us the counter moves in microseconds, not 1000-tick steps (a frame
    // limiter spinning on it rounded every frame up to 17 ms), and it never goes back. Up to 20 ms of reads must show
    // a step that is not a whole millisecond. Without it (NFSMW, D-203) every reading is a whole millisecond.
    if (!host_time_source_is_pinned()) {
        auto qpc = [&] {
            call_import(c, "KERNEL32.dll", "QueryPerformanceCounter", {li});
            return (uint64_t)rd32(li) | ((uint64_t)rd32(li + 4) << 32);
        };
        uint64_t first = qpc(), prev = first;
        bool fine = false, monotonic = true;
        while (!fine && prev - first < 20000) {
            uint64_t now = qpc();
            monotonic = monotonic && now >= prev;
            fine = now != prev && (now - first) % 1000 != 0;
            prev = now;
        }
        if (RECOMP_QPC_US)
            check(fine && monotonic, "QueryPerformanceCounter moves in microseconds and never goes back");
        else
            check(!fine && monotonic && first % 1000 == 0,
                  "QueryPerformanceCounter keeps whole-millisecond steps without [game] qpc_us");
    }

    // The wide environment block must be UTF-16, not ANSI bytes.
    uint32_t wenv = call_import(c, "KERNEL32.dll", "GetEnvironmentStringsW", {});
    check(wenv != 0 && rd16(wenv) == 'P' && rd16(wenv + 2) == 'A' && rd16(wenv + 4) == 'T',
          "GetEnvironmentStringsW returns wide characters");
    uint32_t aenv = call_import(c, "KERNEL32.dll", "GetEnvironmentStrings", {});
    check(aenv != wenv && rd8(aenv) == 'P' && rd8(aenv + 1) == 'A',
          "GetEnvironmentStrings returns a separate ANSI block");
}

// The executable's own version resource, as VERSION.dll would serve it: a
// game that shows its version reads it from here. The test finds RT_VERSION
// in the mapped image's resource directory itself, so it knows whether the
// image has one before it asks the shims.
static bool image_has_version_resource() {
    uint32_t base = loader_image_base();
    uint32_t pe = rd32(base + 0x3c);
    uint32_t rsrc = rd32(base + pe + 24 + 96 + 2 * 8);
    if (!rsrc)
        return false;
    uint32_t dir = base + rsrc;
    uint32_t named = rd16(dir + 12), ids = rd16(dir + 14);
    for (uint32_t k = 0; k < named + ids; ++k)
        if (rd32(dir + 16 + 8 * k) == 16) // RT_VERSION
            return true;
    return false;
}

static void test_version_resource(X86 *c) {
    section("VERSION.dll");
    uint32_t name = put_str(RECOMP_GUEST_ROOT "\\" RECOMP_EXECUTABLE);
    uint32_t handle = scratch_block(4);
    uint32_t size = call_import(c, "VERSION.dll", "GetFileVersionInfoSizeA", {name, handle});
    if (!image_has_version_resource()) {
        check(size == 0 && get_last_error() == 1813,
              "an image without a version resource: size 0, ERROR_RESOURCE_TYPE_NOT_FOUND");
        return;
    }
    check(size > 0x34, "GetFileVersionInfoSizeA reports the resource (%u bytes)", size);
    uint32_t block = scratch_block(size + 16);
    check(call_import(c, "VERSION.dll", "GetFileVersionInfoA", {name, 0, size, block}) == 1,
          "GetFileVersionInfoA copies it");
    uint32_t pval = scratch_block(4), plen = scratch_block(4);
    uint32_t root = put_str("\\");
    check(call_import(c, "VERSION.dll", "VerQueryValueA", {block, root, pval, plen}) == 1 &&
              rd32(plen) == 0x34 && rd32(rd32(pval)) == 0xFEEF04BDu,
          "VerQueryValueA(\"\\\\\") finds VS_FIXEDFILEINFO by its signature");
    uint32_t tr = put_str("\\VarFileInfo\\Translation");
    check(call_import(c, "VERSION.dll", "VerQueryValueA", {block, tr, pval, plen}) == 1 &&
              rd32(plen) >= 4,
          "the translation table is there");
    uint32_t lang = rd32(rd32(pval));
    char key[64];
    snprintf(key, sizeof key, "\\StringFileInfo\\%04x%04x\\FileDescription", lang & 0xffff,
             lang >> 16);
    uint32_t sk = put_str(key);
    check(call_import(c, "VERSION.dll", "VerQueryValueA", {block, sk, pval, plen}) == 1 &&
              rd32(plen) > 1 && !gm_str(rd32(pval)).empty(),
          "a string value is found through its translation: \"%s\"", gm_str(rd32(pval)).c_str());
    uint32_t bogus = put_str("\\StringFileInfo\\040904b0\\NoSuchKey");
    check(call_import(c, "VERSION.dll", "VerQueryValueA", {block, bogus, pval, plen}) == 0,
          "an absent key is refused");
    uint32_t other = put_str("C:\\somewhere\\else.exe");
    check(call_import(c, "VERSION.dll", "GetFileVersionInfoSizeA", {other, handle}) == 0,
          "another file has no version resource here");
}

static void test_files(X86 *c) {
    section("file layer");
    // Every game has its configured executable. Flip each ASCII letter's
    // case to exercise the guest resolver on case-sensitive host filesystems.
    std::string flipped = RECOMP_EXECUTABLE;
    for (char &ch : flipped) {
        unsigned char byte = ch;
        ch = char(std::islower(byte) ? std::toupper(byte) : std::tolower(byte));
    }
    uint32_t name = put_str(flipped.c_str());
    uint32_t h =
        call_import(c, "KERNEL32.dll", "CreateFileA", {name, 0x80000000u, 1, 0, 3, 0x80, 0});
    check(h != 0xffffffffu, "CreateFileA(\"%s\") -> handle %08x", flipped.c_str(), h);

    OsStat st{};
    check(os_stat(RECOMP_DEVELOPER_EXE, &st) == 0, "host executable is available for comparison");
    uint32_t size = call_import(c, "KERNEL32.dll", "GetFileSize", {h, 0});
    check(size == (uint32_t)st.size, "GetFileSize reports %u, host file is %lld", size,
          (long long)st.size);

    uint32_t buf = scratch_block(256), read_count = scratch_block(4);
    check(call_import(c, "KERNEL32.dll", "ReadFile", {h, buf, 64, read_count, 0}) == 1,
          "ReadFile of 64 bytes succeeded");
    check(rd32(read_count) == 64, "ReadFile reported 64 bytes");
    FILE *f = fopen(RECOMP_DEVELOPER_EXE, "rb");
    uint8_t host[64];
    size_t got = f ? fread(host, 1, 64, f) : 0;
    if (f)
        fclose(f);
    check(got == 64 && memcmp(host, g_mem + buf, 64) == 0, "the bytes match the host file");

    check(call_import(c, "KERNEL32.dll", "SetFilePointer", {h, 16, 0, 0}) == 16,
          "SetFilePointer to 16");
    check(call_import(c, "KERNEL32.dll", "ReadFile", {h, buf, 8, read_count, 0}) == 1 &&
              memcmp(host + 16, g_mem + buf, 8) == 0,
          "reading after the seek returns offset 16");
    check(call_import(c, "KERNEL32.dll", "CloseHandle", {h}) == 1, "CloseHandle");

    uint32_t missing = put_str("data\\NO_SUCH_FILE.DAT");
    check(call_import(c, "KERNEL32.dll", "CreateFileA", {missing, 0x80000000u, 1, 0, 3, 0x80, 0}) ==
              0xffffffffu,
          "a missing file gives INVALID_HANDLE_VALUE");
    check(call_import(c, "KERNEL32.dll", "GetLastError", {}) == 2,
          "GetLastError is ERROR_FILE_NOT_FOUND");

    uint32_t dirname = put_str(RECOMP_GUEST_ROOT);
    uint32_t attributes = call_import(c, "KERNEL32.dll", "GetFileAttributesA", {dirname});
    check(attributes != 0xffffffffu && (attributes & 0x10),
          "GetFileAttributesA of the executable's directory reports a directory");

    // The executable's stem may also name logs or configuration files. Walk
    // until exhaustion instead of assuming exactly two matches or their order.
    std::string stem = RECOMP_EXECUTABLE;
    size_t extension = stem.find_last_of('.');
    if (extension != std::string::npos)
        stem.erase(extension);
    std::string glob = stem + ".*";
    uint32_t pattern = put_str(glob.c_str());
    uint32_t fd = scratch_block(0x140);
    uint32_t fh = call_import(c, "KERNEL32.dll", "FindFirstFileA", {pattern, fd});
    check(fh != 0xffffffffu, "FindFirstFileA(\"%s\") -> %08x", glob.c_str(), fh);
    if (fh != 0xffffffffu) {
        std::set<std::string> found;
        bool unique = true, matches = true;
        do {
            std::string name = gm_str(fd + 44);
            unique &= found.insert(name).second;
            std::string prefix = stem + ".";
            matches &= name.size() >= prefix.size() &&
                       std::equal(prefix.begin(), prefix.end(), name.begin(),
                                  [](unsigned char a, unsigned char b) {
                                      return std::tolower(a) == std::tolower(b);
                                  });
            if (!unique) // fail instead of hanging on a broken enumerator
                break;
        } while (call_import(c, "KERNEL32.dll", "FindNextFileA", {fh, fd}));
        check(found.count(RECOMP_EXECUTABLE) != 0,
              "file enumeration includes the configured executable %s", RECOMP_EXECUTABLE);
        check(unique && matches, "all %zu enumerated names match the stem and occur once",
              found.size());
        check(call_import(c, "KERNEL32.dll", "GetLastError", {}) == 18,
              "FindNextFileA ends with ERROR_NO_MORE_FILES");
        check(call_import(c, "KERNEL32.dll", "FindNextFileA", {fh, fd}) == 0,
              "exhausted FindNextFileA keeps reporting no more files");
        check(call_import(c, "KERNEL32.dll", "FindClose", {fh}) == 1, "FindClose succeeds");
    }

    // Guest-visible paths.
    uint32_t pathbuf = scratch_block(300);
    uint32_t n = call_import(c, "KERNEL32.dll", "GetModuleFileNameA", {0, pathbuf, 260});
    check(gm_str(pathbuf) == RECOMP_GUEST_ROOT "\\" RECOMP_EXECUTABLE,
          "GetModuleFileNameA -> \"%s\" (%u chars)", gm_str(pathbuf).c_str(), n);
    call_import(c, "KERNEL32.dll", "GetCurrentDirectoryA", {260, pathbuf});
    check(gm_str(pathbuf) == RECOMP_GUEST_ROOT, "GetCurrentDirectoryA -> \"%s\"",
          gm_str(pathbuf).c_str());

    // The path GetModuleFileNameA hands out must open, whatever the guest
    // root's shape: a game installed under C:\GOG Games\<name> spells its
    // own files through two root components, not one.
    uint32_t absolute = put_str(RECOMP_GUEST_ROOT "\\" RECOMP_EXECUTABLE);
    uint32_t ah =
        call_import(c, "KERNEL32.dll", "CreateFileA", {absolute, 0x80000000u, 1, 0, 3, 0x80, 0});
    check(ah != 0xffffffffu, "an absolute path through the whole guest root opens: \"%s\"",
          RECOMP_GUEST_ROOT "\\" RECOMP_EXECUTABLE);
    if (ah != 0xffffffffu)
        call_import(c, "KERNEL32.dll", "CloseHandle", {ah});
}

// The imports a Visual C++ 6 CRT's start-up and a windowed game's first
// frame reach before any DirectDraw: each has a fixed stdcall argument count
// the trampoline must pop, which call_import checks through ESP.
static void test_boot_shims(X86 *c) {
    section("boot-path shims: CRT locale, shell folders, window metrics");
    uint32_t buf = scratch_block(300);
    check(call_import(c, "KERNEL32.dll", "GetEnvironmentVariableA", {put_str("MAJX"), buf, 300}) ==
                  0 &&
              call_import(c, "KERNEL32.dll", "GetLastError", {}) == 203,
          "GetEnvironmentVariableA of an unset name returns 0 with ERROR_ENVVAR_NOT_FOUND");
    check(call_import(c, "KERNEL32.dll", "GetUserDefaultLCID", {}) == 0x0409,
          "GetUserDefaultLCID is en-US");
    check(call_import(c, "KERNEL32.dll", "IsValidCodePage", {1252}) == 1 &&
              call_import(c, "KERNEL32.dll", "IsValidCodePage", {12345}) == 0,
          "IsValidCodePage knows the Windows code pages and refuses an invented one");
    check(call_import(c, "KERNEL32.dll", "IsValidLocale", {0x0409, 1}) == 1,
          "IsValidLocale accepts en-US");
    check(call_import(c, "KERNEL32.dll", "EnumSystemLocalesA", {0, 1}) == 1,
          "EnumSystemLocalesA succeeds without calling back");
    uint32_t path = scratch_block(260);
    check(call_import(c, "SHELL32.dll", "SHGetSpecialFolderPathA", {0, path, 5, 0}) == 1 &&
              gm_str(path).rfind(RECOMP_GUEST_ROOT, 0) == 0 &&
              gm_str(path).size() > strlen(RECOMP_GUEST_ROOT),
          "SHGetSpecialFolderPathA(CSIDL_PERSONAL) names a directory under the guest root: "
          "\"%s\"",
          gm_str(path).c_str());
    check(call_import(c, "USER32.dll", "IsWindowUnicode", {0x10001}) == 0,
          "IsWindowUnicode: every window is ANSI");
    uint32_t cx = call_import(c, "USER32.dll", "GetSystemMetrics", {0});
    uint32_t cy = call_import(c, "USER32.dll", "GetSystemMetrics", {1});
    check(cx >= 640 && cy >= 480 && cx > cy, "GetSystemMetrics reports a screen of %ux%u", cx, cy);
    check(call_import(c, "USER32.dll", "GetSystemMetrics", {4}) > 0,
          "SM_CYCAPTION is a caption height");
    check(call_import(c, "USER32.dll", "LoadCursorA", {0, 0x7f00}) != 0,
          "LoadCursorA(IDC_ARROW) hands out a handle");
    // The command line is the executable's path, plus whatever switches the
    // developer asks for through RECOMP_GUEST_ARGS: a game's own -debugout or
    // -nointro, which are how it is told to write its log or skip its intro.
    os_setenv("RECOMP_GUEST_ARGS", "-debugout -nointro");
    win32_reset_command_line_for_test();
    std::string cmdline = gm_str(call_import(c, "KERNEL32.dll", "GetCommandLineA", {}));
    check(cmdline == std::string(RECOMP_GUEST_ROOT "\\" RECOMP_EXECUTABLE) + " -debugout -nointro",
          "GetCommandLineA appends RECOMP_GUEST_ARGS: \"%s\"", cmdline.c_str());
    os_unsetenv("RECOMP_GUEST_ARGS");
    win32_reset_command_line_for_test();
    uint32_t ms = scratch_block(32);
    wr32(ms, 32);
    call_import(c, "KERNEL32.dll", "GlobalMemoryStatus", {ms});
    check(rd32(ms + 8) >= 256u * 1024 * 1024 && rd32(ms + 12) > 0 &&
              rd32(ms + 12) <= rd32(ms + 8) && rd32(ms + 4) <= 100 &&
              rd32(ms + 24) >= rd32(ms + 28),
          "GlobalMemoryStatus reports %u MB physical, %u MB free, load %u%%", rd32(ms + 8) >> 20,
          rd32(ms + 12) >> 20, rd32(ms + 4));
}

// ---------------------------------------------------------------------------
// MIDI out, with a synth that records rather than sounds.
//
// The game's music is MIDI: 0x575e40 walks the midiOut devices, calls
// midiOutGetDevCapsA on each, and keeps the first whose szPname begins with
// the nine characters "SoundFont". If none does it returns -1 and there is no
// music at all, so the device name is not decoration - it is the whole gate.
// Then it opens with a null callback, sends a twelve-byte sysex through
// PrepareHeader and LongMsg, and plays note by note with ShortMsg.
// ---------------------------------------------------------------------------
static std::vector<uint32_t> g_midi_shorts;
static std::vector<std::vector<uint8_t>> g_midi_sysexes;
static int g_midi_opens = 0, g_midi_closes = 0, g_midi_resets = 0;
static std::string g_midi_sf2;
static bool g_midi_have_synth = true;

extern "C" {
int host_midi_open(const char *path) {
    ++g_midi_opens;
    g_midi_sf2 = path ? path : "";
    return g_midi_have_synth ? 1 : 0;
}
void host_midi_short(uint32_t msg) {
    g_midi_shorts.push_back(msg);
}
void host_midi_sysex(const void *data, uint32_t bytes) {
    const uint8_t *p = (const uint8_t *)data;
    g_midi_sysexes.push_back(std::vector<uint8_t>(p, p + bytes));
}
void host_midi_reset(void) {
    ++g_midi_resets;
}
void host_midi_close(void) {
    ++g_midi_closes;
}
}

static void test_midi(X86 *c) {
    section("MIDI out");
    g_midi_shorts.clear();
    g_midi_sysexes.clear();
    g_midi_opens = g_midi_closes = g_midi_resets = 0;

    check(call_import(c, "WINMM.dll", "midiOutGetNumDevs", {}) == 1,
          "midiOutGetNumDevs reports one device, without which the game never asks again");

    // The name gate, exactly as 0x575e40 applies it.
    uint32_t caps = scratch_block(64);
    for (uint32_t i = 0; i < 64; ++i)
        wr8(caps + i, 0xcd);
    check(call_import(c, "WINMM.dll", "midiOutGetDevCapsA", {0, caps, 52}) == 0,
          "midiOutGetDevCapsA succeeds for device 0");
    char name[10] = {0};
    for (int i = 0; i < 9; ++i)
        name[i] = (char)rd8(caps + 8 + (uint32_t)i);
    check(strncmp(name, "SoundFont", 9) == 0,
          "the device is named \"%s...\", which is what the game looks for", name);
    check(rd16(caps + 40) == 7, "wTechnology is MOD_SWSYNTH");
    check(rd16(caps + 46) == 0xffff, "every channel is available");
    check(rd8(caps + 52) == 0xcd, "nothing was written past the 52 bytes it asked for");
    check(call_import(c, "WINMM.dll", "midiOutGetDevCapsA", {1, caps, 52}) == 2,
          "a second device is MMSYSERR_BADDEVICEID");

    // Open, as the game opens it: device 0, no callback.
    uint32_t phmo = scratch_block(4);
    check(call_import(c, "WINMM.dll", "midiOutOpen", {phmo, 0, 0, 0, 0}) == 0,
          "midiOutOpen succeeds");
    uint32_t hmo = rd32(phmo);
    check(hmo != 0, "and hands back a handle");
    check(g_midi_opens == 1, "the host synth was asked to open once");

    // All notes off on channel 0, which is 0x576140's own message.
    check(call_import(c, "WINMM.dll", "midiOutShortMsg", {hmo, 0x00007bb0}) == 0,
          "midiOutShortMsg accepts a short message");
    check(g_midi_shorts.size() == 1 && g_midi_shorts[0] == 0x00007bb0,
          "and forwards it packed, unaltered");
    check(call_import(c, "WINMM.dll", "midiOutShortMsg", {hmo + 1, 0x403c90}) != 0,
          "a message on a handle that was never opened is refused");

    // The sysex path: a twelve-byte buffer, which is the length the game uses.
    uint32_t buf = scratch_block(16);
    const uint8_t sysex[12] = {0xf0, 0x7e, 0x7f, 0x09, 0x01, 0xf7,
                               0x11, 0x22, 0x33, 0x44, 0x55, 0x66};
    for (uint32_t i = 0; i < 12; ++i)
        wr8(buf + i, sysex[i]);
    uint32_t hdr = scratch_block(64);
    for (uint32_t i = 0; i < 64; ++i)
        wr8(hdr + i, 0);
    wr32(hdr + 0, buf);
    wr32(hdr + 4, 12);

    check(call_import(c, "WINMM.dll", "midiOutLongMsg", {hmo, hdr, 64}) == 64,
          "an unprepared header is MIDIERR_UNPREPARED");
    check(g_midi_sysexes.empty(), "and nothing was sent");
    check(call_import(c, "WINMM.dll", "midiOutPrepareHeader", {hmo, hdr, 64}) == 0,
          "midiOutPrepareHeader succeeds");
    check((rd32(hdr + 16) & 0x2) != 0, "and sets MHDR_PREPARED");
    check(call_import(c, "WINMM.dll", "midiOutLongMsg", {hmo, hdr, 64}) == 0,
          "midiOutLongMsg accepts the prepared header");
    check(g_midi_sysexes.size() == 1 && g_midi_sysexes[0].size() == 12 &&
              g_midi_sysexes[0][0] == 0xf0 && g_midi_sysexes[0][11] == 0x66,
          "and the twelve bytes reach the synth exactly");
    check((rd32(hdr + 16) & 0x1) != 0,
          "MHDR_DONE is set, which is how the guest knows it may reuse the buffer");
    check(call_import(c, "WINMM.dll", "midiOutUnprepareHeader", {hmo, hdr, 64}) == 0 &&
              (rd32(hdr + 16) & 0x2) == 0,
          "unpreparing clears MHDR_PREPARED");

    check(call_import(c, "WINMM.dll", "midiOutReset", {hmo}) == 0 && g_midi_resets == 1,
          "midiOutReset reaches the synth");
    check(call_import(c, "WINMM.dll", "midiOutClose", {hmo}) == 0 && g_midi_closes == 1,
          "midiOutClose closes it");
    check(call_import(c, "WINMM.dll", "midiOutShortMsg", {hmo, 0x403c90}) != 0,
          "and nothing is accepted afterwards");

    // A host with no synth still opens. The game has one MIDI path and no
    // fallback, so failing the open loses the music and gains nothing.
    g_midi_have_synth = false;
    check(call_import(c, "WINMM.dll", "midiOutOpen", {phmo, 0, 0, 0, 0}) == 0,
          "the open succeeds even when the host has no synth");
    check(call_import(c, "WINMM.dll", "midiOutShortMsg", {rd32(phmo), 0x403c90}) == 0,
          "and the messages are accepted and not heard");
    call_import(c, "WINMM.dll", "midiOutClose", {rd32(phmo)});
    g_midi_have_synth = true;
}

// One pinned clock, in the runtime, so the parity fixture and every host pin
// the same counter. Two counters with one name is how a run ends up described
// as pinned while something still reads the wall.
//
// The description is asserted as hard as the counter. A run record that says
// "monotonic" about a pinned run makes two incomparable runs look comparable,
// and installing a time source is not the same as pinning one - the boot hosts
// install a source that reads the real clock.
// NFSMW 3.0.4 (D-202 item 4, runtime/guest_pause.h): the settings menu pausing the game. The guest clock hold stops
// GetTickCount, timeGetTime and the microsecond counter and lets them carry on from there; it never steps back, a
// pinned clock is never held, and the park loop holds, parks, releases and logs in that order.
static void test_guest_pause(X86 *c) {
    section("guest pause: clock hold and the park loop");
    host_clear_time_source();
    const auto ms = [](int n) { std::this_thread::sleep_for(std::chrono::milliseconds(n)); };
    const uint32_t before = host_millis();
    host_guest_clock_hold(true);
    check(host_guest_clock_held(), "the hold is on");
    const uint32_t frozen = host_millis();
    const uint32_t tick0 = call_import(c, "KERNEL32.dll", "GetTickCount", {});
    const uint32_t tgt0 = call_import(c, "WINMM.dll", "timeGetTime", {});
    const uint64_t us0 = host_guest_clock_us(os_monotonic_ns() / 1000ull);
    ms(300);
    const uint32_t tick1 = call_import(c, "KERNEL32.dll", "GetTickCount", {});
    const uint32_t tgt1 = call_import(c, "WINMM.dll", "timeGetTime", {});
    const uint64_t us1 = host_guest_clock_us(os_monotonic_ns() / 1000ull);
    check(host_millis() - frozen < 2, "300 ms held: the clock stood still (%u ms)", host_millis() - frozen);
    check(tick1 - tick0 < 2 && tgt1 - tgt0 < 2, "GetTickCount (%u) and timeGetTime (%u) stood still", tick1 - tick0,
          tgt1 - tgt0);
    check(us1 - us0 < 2000, "the microsecond counter stood still (%llu us)", (unsigned long long)(us1 - us0));
    host_guest_clock_hold(false);
    const uint32_t after = host_millis();
    check(!host_guest_clock_held() && after - frozen < 5, "released, it carries on from where it stopped (+%u ms)",
          after - frozen);
    check(frozen - before < 50, "and the hold began where the clock was (+%u ms)", frozen - before);
    ms(60);
    check(host_millis() - after >= 50, "then it runs again (+%u ms in 60)", host_millis() - after);
    // Control: the same wait without a hold shows on the clock.
    const uint32_t r0 = host_millis();
    ms(300);
    check(host_millis() - r0 >= 250, "control: 300 ms unheld show (%u ms)", host_millis() - r0);
    // Never a step back, with readers on other threads while the hold goes on and off.
    std::atomic<bool> stop{false}, backwards{false};
    std::thread reader([&] {
        uint32_t last = host_millis();
        while (!stop.load()) {
            const uint32_t now = host_millis();
            if ((int32_t)(now - last) < 0)
                backwards.store(true);
            last = now;
        }
    });
    for (int i = 0; i < 40; ++i) {
        host_guest_clock_hold(true);
        ms(2);
        host_guest_clock_hold(false);
        ms(1);
    }
    stop.store(true);
    reader.join();
    check(!backwards.load(), "40 holds with a reader running: the clock never went back");
    // A pinned clock is its owner's: never held.
    host_set_time_source_pinned(1000, 50);
    host_guest_clock_hold(true);
    check(!host_guest_clock_held(), "a pinned clock is not held");
    host_pinned_clock_advance();
    check(host_millis() == 1050u, "and still steps (%u)", host_millis());
    host_clear_time_source();
    check(!host_guest_clock_held(), "clearing the clock leaves no hold behind");

    // The park loop with fakes: nothing when not wanted; else hold, log, slices while wanted, release, log.
    struct Fake {
        int wanted_for = 0, slices = 0;
        double t = 10.0;
        std::string ev;
    } f;
    GuestPauseOps o;
    o.ctx = &f;
    o.wanted = [](void *p) { return static_cast<Fake *>(p)->wanted_for > 0; };
    o.hold = [](void *p, bool on) { static_cast<Fake *>(p)->ev += on ? "H" : "h"; };
    o.slice = [](void *p) {
        Fake &x = *static_cast<Fake *>(p);
        ++x.slices;
        --x.wanted_for;
        x.t += 0.016;
        x.ev += "s";
    };
    o.now = [](void *p) { return static_cast<Fake *>(p)->t; };
    o.log = [](void *p, bool paused, double) { static_cast<Fake *>(p)->ev += paused ? "P" : "R"; };
    check(guest_pause_run(o) == 0.0 && f.ev.empty(), "not wanted: no hold, no log, no slice (\"%s\")", f.ev.c_str());
    f.wanted_for = 3;
    const double parked = guest_pause_run(o);
    check(f.ev == "HPssshR" && f.slices == 3, "wanted for 3 slices: \"%s\" (hold, log, 3 slices, release, log)",
          f.ev.c_str());
    check(parked > 0.047 && parked < 0.049, "the seconds parked are the slices' (%.3f)", parked);
    // Without the mods module (this suite) the menu never wants a pause: the real park point returns at once.
    host_guest_pause_point();
    check(!host_guest_clock_held(), "the park point with no menu: no hold");
}

static void test_pinned_clock(X86 *c) {
    section("pinned clock");
    (void)c;
    host_set_time_source_pinned(100, 50);
    check(host_time_source_is_pinned(), "a pin is installed");
    check(host_millis() == 100u, "it starts where it was told, %u", host_millis());
    check(host_millis() == 100u, "and does not move between reads on its own");

    host_pinned_clock_advance();
    check(host_millis() == 150u, "one advance is one step, %u", host_millis());
    host_pinned_clock_advance();
    host_pinned_clock_advance();
    check(host_millis() == 250u, "and each advance is one more, %u", host_millis());
    check(host_pinned_clock_value() == host_millis(),
          "the counter and the clock the guest reads are the same number");

    check(strcmp(host_clock_description(), "pinned start=100 step=50") == 0,
          "installing it describes it: \"%s\"", host_clock_description());

    // A second pin replaces the first, counter and description together. If
    // the description could lag, a record would name the wrong pin.
    host_set_time_source_pinned(0, 16);
    check(host_millis() == 0u, "a second pin restarts at its own start");
    host_pinned_clock_advance();
    check(host_millis() == 16u, "and steps by its own step, %u", host_millis());
    check(strcmp(host_clock_description(), "pinned start=0 step=16") == 0,
          "and renames itself: \"%s\"", host_clock_description());

    // Leave NOTHING installed. This suite shares a process with tests that
    // wait on time, and a pinned clock nobody advances does not merely change
    // what they see - it stops. Every timed wait becomes eternal and every
    // spin on the clock becomes infinite. The first version of this test put
    // the fixture's 100/50 back "so as not to change what later tests see",
    // which froze the clock for all of them and hung the suite for fifteen
    // minutes while it held the build lock.
    host_clear_time_source();
    check(!host_time_source_is_pinned(), "the pin is gone when this test ends");
    check(strcmp(host_clock_description(), "monotonic") == 0,
          "and the clock is described as what it is again");
    uint32_t a = host_millis();
    uint32_t b = host_millis();
    check(b >= a, "the real clock is back and does not go backwards");
}

// The cadence trace: how often the guest asks the time, and how often anything
// ticks. Intervals rather than absolute times, on the guest's clock rather than
// the wall, so that two runs are comparable at all - see the note in misc.cpp.
static void test_cadence_trace(X86 *c) {
    section("cadence trace");
    const char *path = "build/recomp/cadence-test.log";
    os_unlink(path);
    host_set_cadence_trace(path);

    // Two reads of the clock with a known gap between them, and the gap is
    // MADE rather than waited for. The first version of this spun until the
    // clock had moved 3 ms, which is an infinite loop the moment anything has
    // pinned the clock - and the test above it had. A test that waits for a
    // clock it does not itself advance is a test that can hang, whatever it
    // is testing.
    host_set_time_source_pinned(1000, 50);
    call_import(c, "WINMM.dll", "timeGetTime", {});
    host_pinned_clock_advance();
    call_import(c, "WINMM.dll", "timeGetTime", {});

    // A window timer message, which nothing in this game posts. The seam is
    // real even though the game never uses it, and the test says so.
    //
    // Then the queue is drained, because these are real messages in the real
    // queue that the window tests later in this suite peek at. The first
    // version of this left them there and five checks in test_windows failed -
    // "PeekMessageA on an empty queue returns FALSE" found my WM_TIMER instead.
    // That is the same mistake as leaving a pinned clock installed, one file
    // over: a test that leaves state behind breaks whoever runs next, and the
    // failure surfaces far from its cause.
    host_post_message(0, 0x0113, 0, 0);
    host_post_message(0, 0x0113, 0, 0);
    uint32_t msgbuf = scratch_block(28);
    int drained = 0;
    while (call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, 0, 0, 0, 1}) == 1)
        if (++drained > 8)
            break; // bounded: never spin on a queue
    check(drained == 2, "the two WM_TIMERs were taken back off the queue (%d)", drained);
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, 0, 0, 0, 0}) == 0,
          "and this test leaves the queue as it found it");

    host_set_cadence_trace(nullptr);

    FILE *f = fopen(path, "r");
    check(f != nullptr, "the trace file was written");
    std::string log;
    if (f) {
        char buf[512];
        size_t n;
        while ((n = fread(buf, 1, sizeof buf, f)) > 0)
            log.append(buf, n);
        fclose(f);
    }
    check(log.find("timeGetTime,50") != std::string::npos,
          "it records the interval exactly: one pinned step between the reads");
    check(log.find("WM_TIMER,") != std::string::npos, "it records a WM_TIMER interval");

    // One line per interval, not per event: two events of a kind make one
    // line, because there is no interval before the first.
    size_t lines = 0;
    for (char ch : log)
        if (ch == '\n')
            ++lines;
    check(lines == 2, "two events of each of two kinds make two lines, not four (%zu)", lines);

    // Closing it stops the writing. A trace that kept growing after the run
    // that asked for it would put another run's cadence in the same file.
    size_t was = log.size();
    call_import(c, "WINMM.dll", "timeGetTime", {});
    f = fopen(path, "r");
    size_t now = 0;
    if (f) {
        fseek(f, 0, SEEK_END);
        now = (size_t)ftell(f);
        fclose(f);
    }
    check(now == was, "nothing is written after the trace is closed");

    // TRACING MUST NOT CHANGE PACING.
    //
    // A boot host's time source counts a poll on every read and takes a
    // stall-breaking step after 256 of them. If the trace timestamped its
    // entries by reading that source, a traced run would pace differently from
    // an untraced one and the trace would be measuring its own effect. This
    // counts the source's calls directly: noting cadence must not touch it.
    // This check depends on the clock being PINNED: unpinned, host_note_cadence
    // reads host_millis() by design, because boot_clock_poll does nothing when
    // there is no pin and there is no other way to get a real time. So the
    // assertion below is about the pinned path, and it is worthless if the pin
    // is not installed - which is asserted rather than assumed.
    static int source_calls = 0;
    source_calls = 0;
    host_set_time_source_pinned(700, 50);
    check(host_time_source_is_pinned(),
          "the pin is installed, which is what makes the next check meaningful");
    host_set_time_source([]() -> uint32_t {
        ++source_calls;
        return 4242u;
    });
    check(host_time_source_is_pinned(),
          "and installing a counting source over it leaves the pin flag set");
    host_set_cadence_trace(path);
    int before_calls = source_calls;
    for (int i = 0; i < 300; ++i)
        host_note_cadence("timeGetTime");
    check(source_calls == before_calls, "300 traced events read the time source %d times, not %d",
          source_calls - before_calls, 0);
    host_set_cadence_trace(nullptr);
    host_clear_time_source();

    // And a pinned clock does not move because something was traced.
    host_set_time_source_pinned(500, 50);
    host_set_cadence_trace(path);
    for (int i = 0; i < 300; ++i)
        host_note_cadence("GetTickCount");
    check(host_millis() == 500u, "the pinned clock is where it was, %u", host_millis());
    host_set_cadence_trace(nullptr);
    host_clear_time_source();

    // A path that cannot be opened is not fatal: a run that cannot write its
    // trace is still a run, and losing it is better than losing the run. The
    // assertion is that the run CONTINUES and the trace is off, not the
    // check(true) this used to be, which could not fail.
    host_set_cadence_trace("build/recomp/no-such-dir/cadence.log");
    uint32_t before_clock = host_millis();
    call_import(c, "WINMM.dll", "timeGetTime", {});
    host_note_cadence("GetTickCount");
    check(host_millis() >= before_clock,
          "the clock still runs after a trace that could not be opened");
    OsStat no_such{};
    check(os_stat("build/recomp/no-such-dir/cadence.log", &no_such) != 0,
          "and no trace file appeared where one could not be created");
    host_set_cadence_trace(nullptr);
    host_clear_time_source();
    check(!host_time_source_is_pinned(), "and this test leaves no pin behind either");
    os_unlink(path);
}

static void test_misc_shims(X86 *c) {
    section("time, TLS, semaphores, strings");
    uint32_t t0 = call_import(c, "KERNEL32.dll", "GetTickCount", {});
    uint32_t t1 = call_import(c, "WINMM.dll", "timeGetTime", {});
    check(t1 >= t0 && t1 - t0 < 1000, "GetTickCount %u and timeGetTime %u share one clock", t0, t1);

    uint32_t slot = call_import(c, "KERNEL32.dll", "TlsAlloc", {});
    check(slot < 64, "TlsAlloc -> slot %u", slot);
    call_import(c, "KERNEL32.dll", "TlsSetValue", {slot, 0xdeadbeef});
    check(call_import(c, "KERNEL32.dll", "TlsGetValue", {slot}) == 0xdeadbeef, "TLS round trip");

    uint32_t sem = call_import(c, "KERNEL32.dll", "CreateSemaphoreA", {0, 1, 4, 0});
    check(sem != 0, "CreateSemaphoreA(initial 1, max 4) -> %08x", sem);
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {sem, 0}) == 0,
          "the first wait takes the count");
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {sem, 0}) == 0x102,
          "the second wait reports WAIT_TIMEOUT");
    uint32_t prev = scratch_block(4);
    check(call_import(c, "KERNEL32.dll", "ReleaseSemaphore", {sem, 1, prev}) == 1 &&
              rd32(prev) == 0,
          "ReleaseSemaphore restores the count, previous %u", rd32(prev));
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {sem, 0}) == 0,
          "the wait succeeds again after the release");

    uint32_t osvi = scratch_block(160);
    wr32(osvi, 148);
    check(call_import(c, "KERNEL32.dll", "GetVersionExA", {osvi}) == 1 &&
              rd32(osvi + 4) == RECOMP_WINDOWS_MAJOR && rd32(osvi + 8) == RECOMP_WINDOWS_MINOR &&
              rd32(osvi + 16) == RECOMP_WINDOWS_PLATFORM,
          "GetVersionExA reports the configured Windows version");
    check(call_import(c, "KERNEL32.dll", "GetProcessHeap", {}) != 0, "GetProcessHeap");
    uint32_t sysinfo = scratch_block(80);
    memset(g_mem + sysinfo, 0xa5, 80);
    call_import(c, "KERNEL32.dll", "GetSystemInfo", {sysinfo});
    call_import(c, "KERNEL32.dll", "GetNativeSystemInfo", {sysinfo + 40});
    check(memcmp(g_mem + sysinfo, g_mem + sysinfo + 40, 36) == 0 &&
              rd32(sysinfo + 36) == 0xa5a5a5a5 && rd32(sysinfo + 76) == 0xa5a5a5a5 &&
              rd16(sysinfo + 40) == 0 && rd32(sysinfo + 44) == 4096,
          "GetNativeSystemInfo shares the 32-bit guest SYSTEM_INFO layout and preserves guards");
    check(call_import(c, "KERNEL32.dll", "IsBadCodePtr", {loader_entry_point()}) == 0 &&
              call_import(c, "KERNEL32.dll", "IsBadCodePtr", {loader_image_limit()}) == 1,
          "IsBadCodePtr uses the image bounds from the PE headers");

    uint32_t s1 = put_str("RuntimeX");
    check(call_import(c, "KERNEL32.dll", "lstrlenA", {s1}) == 8, "lstrlenA");
    uint32_t dst = scratch_block(64);
    call_import(c, "KERNEL32.dll", "lstrcpyA", {dst, s1});
    uint32_t s2 = put_str(" TB");
    call_import(c, "KERNEL32.dll", "lstrcatA", {dst, s2});
    check(gm_str(dst) == "RuntimeX TB", "lstrcpyA + lstrcatA -> \"%s\"", gm_str(dst).c_str());

    uint32_t fmt = put_str("%s has %d units (%04x)");
    uint32_t va = scratch_block(16);
    wr32(va + 0, put_str("blue"));
    wr32(va + 4, 12);
    wr32(va + 8, 0x2a);
    uint32_t out = scratch_block(128);
    call_import(c, "USER32.dll", "wvsprintfA", {out, fmt, va});
    check(gm_str(out) == "blue has 12 units (002a)", "wvsprintfA -> \"%s\"", gm_str(out).c_str());
    uint32_t fmt_result = call_import(c, "USER32.dll", "wsprintfA", {out, fmt, rd32(va), 12, 0x2a});
    check(gm_str(out) == "blue has 12 units (002a)" && fmt_result == 24 &&
              imports_argc(imports_resolve("USER32.dll", "wsprintfA")) == ARGC_CDECL,
          "wsprintfA reads inline guest varargs and uses caller stack cleanup");
    fmt = put_str("%d/%u/%X %% %c");
    fmt_result = call_import(c, "USER32.dll", "wsprintfA", {out, fmt, uint32_t(-7), 9, 0xab, 'Z'});
    check(gm_str(out) == "-7/9/AB % Z" && fmt_result == 11,
          "wsprintfA consumes mixed varargs without consuming one for percent");

    uint32_t spc = scratch_block(4), bps = scratch_block(4), fr = scratch_block(4),
             tot = scratch_block(4);
    uint32_t root = put_str("C:\\");
    check(call_import(c, "KERNEL32.dll", "GetDiskFreeSpaceA", {root, spc, bps, fr, tot}) == 1,
          "GetDiskFreeSpaceA succeeds");
    check(rd32(spc) == 8 && rd32(bps) == 512 && rd32(fr) == 0x00100000 && rd32(tot) == 0x00200000,
          "GetDiskFreeSpaceA reports 4 GB free of 8 GB");
    uint32_t sysdir = scratch_block(64);
    check(call_import(c, "KERNEL32.dll", "GetSystemDirectoryA", {sysdir, 64}) == 19 &&
              gm_str(sysdir) == "C:\\Windows\\System32",
          "GetSystemDirectoryA");
    check(call_import(c, "KERNEL32.dll", "GetSystemDirectoryA", {sysdir, 4}) == 20,
          "GetSystemDirectoryA reports the size needed when the buffer is short");
}

// What a C++ throw looks like from the runtime: the MSVC exception record
// names the type through its throw info, the object usually carries a
// message, and the EBP chain names where it came from. This is what the
// RaiseException shim prints before it gives up, so a throw in a game whose
// exceptions the runtime cannot unwind still says what went wrong.
static void test_cxx_throw_description(X86 *c) {
    section("describing a C++ exception record");
    // TypeDescriptor: vtable, spare, then the mangled name in place.
    uint32_t type = scratch_block(48);
    wr32(type, 0);
    wr32(type + 4, 0);
    memcpy(g_mem + type + 8, ".?AVGameException@@", 20);
    uint32_t catchable = scratch_block(28); // properties, pType, thisDisplacement...
    wr32(catchable, 0);
    wr32(catchable + 4, type);
    uint32_t array = scratch_block(8); // nCatchableTypes, arrayOfCatchableTypes[]
    wr32(array, 1);
    wr32(array + 4, catchable);
    uint32_t throw_info =
        scratch_block(16); // attributes, pmfnUnwind, pForwardCompat, pCatchableTypeArray
    wr32(throw_info, 0);
    wr32(throw_info + 4, 0);
    wr32(throw_info + 8, 0);
    wr32(throw_info + 12, array);
    uint32_t what = put_str("Quest file not found: Quests\\random.q");
    uint32_t object = scratch_block(12); // vtable, char *what, int owns
    wr32(object, 0x00680000);
    wr32(object + 4, what);
    wr32(object + 8, 1);
    uint32_t args = scratch_block(12);
    wr32(args, 0x19930520);
    wr32(args + 4, object);
    wr32(args + 8, throw_info);
    std::string text = win32_describe_cxx_throw(0xe06d7363, 3, args);
    check(text.find("GameException") != std::string::npos, "the type is named: %s", text.c_str());
    check(text.find("Quest file not found") != std::string::npos, "the message is quoted");
    // A class of the game's own keeps its text wherever it likes: every dword
    // of the object that points at text is quoted too, with its offset.
    uint32_t line_text = put_str("GplError: unknown function foo, line#12");
    wr32(object, line_text);
    text = win32_describe_cxx_throw(0xe06d7363, 3, args);
    check(text.find("+0 \"GplError: unknown function foo") != std::string::npos,
          "object dwords that point at text are quoted: %s", text.c_str());
    // Without frame pointers the chain stops; the stack still holds return
    // addresses, recognisable because a CALL precedes each one.
    uint32_t code = scratch_block(64);
    g_mem[code + 0] = 0xe8; // CALL rel32 ... the return address is code + 5
    wr32(code + 1, 0x10);
    g_mem[code + 5] = 0x90;
    g_mem[code + 16] = 0xff; // CALL EAX ... return address code + 18
    g_mem[code + 17] = 0xd0;
    g_mem[code + 32] = 0x90; // a NOP: code + 33 follows no CALL
    uint32_t stack = scratch_block(32);
    wr32(stack + 0, 0x12345678);
    wr32(stack + 4, code + 18);
    wr32(stack + 8, code + 33);
    wr32(stack + 12, code + 5);
    std::vector<uint32_t> found = win32_stack_return_candidates(stack, 16, code, code + 64, 8);
    check(found.size() == 2 && found[0] == code + 18 && found[1] == code + 5,
          "the stack scan keeps the %zu dwords that follow a CALL", found.size());
    // A register that holds an object with a vtable is named by its class:
    // MSVC's RTTI hangs the complete-object locator off vtable[-1], and its
    // type descriptor carries the mangled name.  Anything else is described as
    // the bare value.
    uint32_t col = scratch_block(20), vtbl = scratch_block(16), obj = scratch_block(8);
    wr32(col + 12, type); // pCompleteObject locator -> TypeDescriptor
    wr32(vtbl, col);      // vtable[-1]
    wr32(obj, vtbl + 4);  // the object's vtable pointer
    std::string named = win32_describe_pointer(obj);
    check(named.find("GameException") != std::string::npos, "an object is named by its RTTI: %s",
          named.c_str());
    check(win32_describe_pointer(0x12345678).empty() || win32_describe_pointer(0x12345678) == "",
          "a value that points at nothing describes as nothing");
    // A frame chain: [EBP] -> caller's EBP, [EBP+4] -> return address in the image.
    uint32_t f1 = scratch_block(8), f2 = scratch_block(8); // the caller's frame lies above
    wr32(f1, f2);
    wr32(f1 + 4, loader_image_base() + 0x1234);
    wr32(f2, 0);
    wr32(f2 + 4, loader_image_base() + 0x5678);
    std::vector<uint32_t> chain = win32_return_chain(f1, 8);
    check(chain.size() == 2 && chain[0] == loader_image_base() + 0x1234 &&
              chain[1] == loader_image_base() + 0x5678,
          "the return chain walks EBP through %zu frames", chain.size());
}

// The GDI a software-rendered game leans on: a DIB section as its frame
// buffer, a memory DC to hold it, a colour table for 8-bit modes, palettes;
// plus the odd process shims and COM class creation its start-up reaches.
static void test_gdi_and_com(X86 *c) {
    section("GDI DIB sections, palettes, process shims, COM class creation");
    // A top-down 64x32 16-bpp 565 DIB, the shape the game's frame buffer takes.
    uint32_t bmi = scratch_block(0x440);
    memset(g_mem + bmi, 0, 0x440);
    wr32(bmi + 0, 40);
    wr32(bmi + 4, 64);
    wr32(bmi + 8, (uint32_t)-32);
    wr16(bmi + 12, 1);
    wr16(bmi + 14, 16);
    wr32(bmi + 16, 3); // BI_BITFIELDS
    wr32(bmi + 40, 0xf800);
    wr32(bmi + 44, 0x07e0);
    wr32(bmi + 48, 0x001f);
    uint32_t bits = scratch_block(4);
    wr32(bits, 0);
    uint32_t hdc = call_import(c, "USER32.dll", "GetDC", {0});
    // Read the same mode hook as GDI. Runtime-only builds use its weak default.
    uint32_t mw = 1024, mh = 768, mbpp = 32;
    ddraw_display_mode(&mw, &mh, &mbpp);
    check(call_import(c, "GDI32.dll", "GetDeviceCaps", {hdc, 8}) == mw &&
              call_import(c, "GDI32.dll", "GetDeviceCaps", {hdc, 10}) == mh,
          "GetDeviceCaps HORZRES/VERTRES are the mode");
    check(call_import(c, "GDI32.dll", "GetDeviceCaps", {hdc, 12}) == 32,
          "BITSPIXEL is the canvas depth");
    check(call_import(c, "GDI32.dll", "GetDeviceCaps", {hdc, 14}) == 1, "PLANES");
    check(call_import(c, "GDI32.dll", "GetDeviceCaps", {hdc, 38}) == 0x2a01u,
          "RASTERCAPS supports bitmap and DIB blits");
    check(call_import(c, "GDI32.dll", "GetDeviceCaps", {hdc, 104}) == (mbpp == 8 ? 256u : 0u),
          "SIZEPALETTE is 256 only at 8 bpp");
    check(call_import(c, "GDI32.dll", "GetDeviceCaps", {hdc, 24}) == 0xffffffffu,
          "NUMCOLORS is -1 for a true-color canvas");
    check(call_import(c, "GDI32.dll", "GetDeviceCaps", {hdc, 0x2000}) == 0,
          "an unknown index is 0");
    uint32_t sz = scratch_block(8), text = put_str("ABCDEFG");
    uint32_t tm = scratch_block(56);
    call_import(c, "GDI32.dll", "GetTextMetricsA", {hdc, tm});
    check(call_import(c, "GDI32.dll", "GetTextExtentPointA", {hdc, text, 7, sz}) == 1 &&
              rd32(sz) == 7 * rd32(tm + 20) && rd32(sz + 4) == rd32(tm + 0),
          "GetTextExtentPointA agrees with GetTextMetricsA (tmAveCharWidth, tmHeight)");
    check(call_import(c, "GDI32.dll", "SetBkColor", {hdc, 0x00ff0000}) == 0x00ffffff,
          "SetBkColor returns white first");
    check(call_import(c, "GDI32.dll", "SetBkColor", {hdc, 0}) == 0x00ff0000,
          "then the previous colour");
    uint32_t hbm = call_import(c, "GDI32.dll", "CreateDIBSection", {hdc, bmi, 0, bits, 0, 0});
    check(hbm != 0 && rd32(bits) != 0 && heap_owns(rd32(bits)),
          "CreateDIBSection -> %08x with bits at %08x on the guest heap", hbm, rd32(bits));
    check(heap_size(rd32(bits)) != 0xffffffffu && heap_size(rd32(bits)) >= 64u * 2 * 32,
          "the bits cover 64x32 at 16 bpp (%u bytes)", heap_size(rd32(bits)));
    uint32_t bm = scratch_block(24);
    check(call_import(c, "GDI32.dll", "GetObjectA", {hbm, 24, bm}) == 24 && rd32(bm + 4) == 64 &&
              rd32(bm + 8) == 32 && rd32(bm + 12) == 128 && rd16(bm + 18) == 16 &&
              rd32(bm + 20) == rd32(bits),
          "GetObjectA describes the bitmap: 64x32, stride %u, %u bpp, bits %08x", rd32(bm + 12),
          rd16(bm + 18), rd32(bm + 20));
    uint32_t mdc = call_import(c, "GDI32.dll", "CreateCompatibleDC", {hdc});
    check(mdc != 0 && call_import(c, "GDI32.dll", "SelectObject", {mdc, hbm}) != 0,
          "a memory DC takes the bitmap");
    check(call_import(c, "GDI32.dll", "DeleteDC", {mdc}) == 1, "DeleteDC");
    uint32_t old_bits = rd32(bits);
    check(call_import(c, "GDI32.dll", "DeleteObject", {hbm}) == 1 && !heap_owns(old_bits),
          "DeleteObject frees the bits");
    // 8 bpp with a colour table set through the DC it is selected into.
    wr16(bmi + 14, 8);
    wr32(bmi + 16, 0);
    uint32_t hbm8 = call_import(c, "GDI32.dll", "CreateDIBSection", {0, bmi, 0, bits, 0, 0});
    uint32_t mdc8 = call_import(c, "GDI32.dll", "CreateCompatibleDC", {0});
    call_import(c, "GDI32.dll", "SelectObject", {mdc8, hbm8});
    uint32_t pal = scratch_block(16);
    wr32(pal, 0x00ff0000);
    wr32(pal + 4, 0x0000ff00);
    check(hbm8 != 0 && call_import(c, "GDI32.dll", "SetDIBColorTable", {mdc8, 0, 2, pal}) == 2,
          "an 8-bpp DIB takes a colour table");
    call_import(c, "GDI32.dll", "DeleteDC", {mdc8});
    call_import(c, "GDI32.dll", "DeleteObject", {hbm8});
    // A logical palette round-trips its entries.
    uint32_t lp = scratch_block(4 + 4 * 4);
    wr16(lp, 0x300);
    wr16(lp + 2, 4);
    for (uint32_t i = 0; i < 4; ++i)
        wr32(lp + 4 + 4 * i, 0x00102030u + i);
    uint32_t hpal = call_import(c, "GDI32.dll", "CreatePalette", {lp});
    uint32_t got = scratch_block(16);
    check(hpal != 0 && call_import(c, "GDI32.dll", "GetPaletteEntries", {hpal, 1, 2, got}) == 2 &&
              rd32(got) == 0x00102031u && rd32(got + 4) == 0x00102032u,
          "CreatePalette / GetPaletteEntries round-trip");
    check(call_import(c, "GDI32.dll", "SelectPalette", {hdc, hpal, 0}) != 0 &&
              call_import(c, "GDI32.dll", "RealizePalette", {hdc}) == 4,
          "SelectPalette / RealizePalette report the entries");
    call_import(c, "GDI32.dll", "DeleteObject", {hpal});
    call_import(c, "USER32.dll", "ReleaseDC", {0, hdc});

    check(call_import(c, "KERNEL32.dll", "SetErrorMode", {0x8001}) == 0 &&
              call_import(c, "KERNEL32.dll", "SetErrorMode", {0}) == 0x8001,
          "SetErrorMode returns the previous mode");
    check(call_import(c, "KERNEL32.dll", "GetLogicalDrives", {}) & 0x4, "GetLogicalDrives has C:");
    uint32_t msg = scratch_block(128);
    check(call_import(c, "WINMM.dll", "mciGetErrorStringA", {266, msg, 128}) == 1 &&
              !gm_str(msg).empty(),
          "mciGetErrorStringA(MCIERR_DEVICE_NOT_INSTALLED) -> \"%s\"", gm_str(msg).c_str());

    // The mixer API: no mixer device, said with the right argument counts.
    check(call_import(c, "WINMM.dll", "mixerGetNumDevs", {}) == 0, "mixerGetNumDevs: none");
    uint32_t hmx = scratch_block(4);
    check(call_import(c, "WINMM.dll", "mixerOpen", {hmx, 0, 0, 0, 0, 0}) == 6 &&
              call_import(c, "WINMM.dll", "mixerClose", {0}) == 6 &&
              call_import(c, "WINMM.dll", "mixerGetDevCapsA", {0, msg, 128}) == 6 &&
              call_import(c, "WINMM.dll", "mixerGetLineInfoA", {0, msg, 0}) == 6 &&
              call_import(c, "WINMM.dll", "mixerGetLineControlsA", {0, msg, 0}) == 6 &&
              call_import(c, "WINMM.dll", "mixerGetControlDetailsA", {0, msg, 0}) == 6 &&
              call_import(c, "WINMM.dll", "mixerSetControlDetails", {0, msg, 0}) == 6,
          "every mixer call reports MMSYSERR_NODRIVER");
    // An icon built from bitmaps is a handle the host never draws.
    uint32_t iconinfo = scratch_block(20);
    memset(g_mem + iconinfo, 0, 20);
    wr32(iconinfo, 0); // fIcon = FALSE: a cursor
    uint32_t hicon = call_import(c, "USER32.dll", "CreateIconIndirect", {iconinfo});
    check(hicon != 0 && call_import(c, "USER32.dll", "DestroyIcon", {hicon}) == 1,
          "CreateIconIndirect / DestroyIcon");
    // The version resource of the executable is not served: the game's own
    // version string comes from its data instead.
    uint32_t vh = scratch_block(4);
    check(call_import(c, "VERSION.dll", "GetFileVersionInfoSizeA", {put_str("x.exe"), vh}) == 0 &&
              call_import(c, "VERSION.dll", "GetFileVersionInfoA", {put_str("x.exe"), 0, 0, 0}) ==
                  0 &&
              call_import(c, "VERSION.dll", "VerQueryValueA", {0, put_str("\\"), vh, vh}) == 0,
          "VERSION.dll reports no version information");
}

// A stand-in for generated guest code: a trampoline the stub recomp_call can
// dispatch, so callbacks that go through recomp_call can be tested without the
// generated function table.
static uint32_t g_fake_time = 0;
static uint32_t fake_clock() {
    return g_fake_time;
}
static int g_display_fps = 0;
extern "C" int mods_display_fps() {
    return g_display_fps;
}

static void test_native_draw_waits(X86 *c) {
    section("native cap replaces both original draw waits without changing simulation time");
    // These are synthetic shim calls, so even unused configured sites can
    // exercise the clock hook without executing the image's draw loop.
    if (!gm_valid(RECOMP_HOOK_FRAME_CLOCK_WAIT_DEADLINE, 4) ||
        !gm_valid(RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE, 4)) {
        printf("  [SKIP] frame clock hooks are sentinels for this game\n");
        ++g_skips;
        return;
    }
    const uint32_t canaries = scratch_block(32);
    const uint32_t old_ret = g_fake_ret, old_edi = c->r[R_EDI];
    const uint8_t old_limit = rd8(canaries), old_flags = rd8(canaries + 4);
    const uint32_t addresses[] = {RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE,
                                  RECOMP_HOOK_FRAME_CLOCK_WAIT_DEADLINE, canaries + 8,
                                  canaries + 12, canaries + 16};
    uint32_t saved[5];
    for (int i = 0; i < 5; ++i)
        saved[i] = rd32(addresses[i]);
    host_set_time_source(fake_clock);
    g_fake_time = 1000;
    wr8(canaries, 40);
    wr8(canaries + 4, 8);
    wr32(canaries + 8, 1234);
    wr32(canaries + 12, 83);
    wr32(canaries + 16, 99);
    for (int rate : {40, 60, 120}) {
        g_display_fps = rate;
        for (uint32_t caller : {RECOMP_HOOK_FRAME_CLOCK_WAIT, RECOMP_HOOK_FRAME_CLOCK_WAIT_CLAMP}) {
            g_fake_ret = RECOMP_HOOK_FRAME_CLOCK_BEGIN;
            check(call_import(c, "KERNEL32.dll", "GetTickCount", {}) == 1000 && rd8(canaries) == 40,
                  "%d Hz pacing preserves unrelated storage and the real clock", rate);
            wr32(RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE, 1016);
            wr32(RECOMP_HOOK_FRAME_CLOCK_WAIT_DEADLINE, 1025);
            c->r[R_EDI] = 60;
            g_fake_ret = caller;
            uint32_t now = call_import(c, "KERNEL32.dll", "GetTickCount", {});
            const bool alternate = caller == RECOMP_HOOK_FRAME_CLOCK_WAIT_CLAMP;
            check(now == 1000 && rd32(alternate ? RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE
                                                : RECOMP_HOOK_FRAME_CLOCK_WAIT_DEADLINE) == now,
                  "%08x retires its old wait at %d Hz, without accelerating time", caller, rate);
            check(rd32(alternate
                           ? RECOMP_HOOK_FRAME_CLOCK_WAIT_DEADLINE
                           : RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE) == (alternate ? 1025u : 1016u),
                  "only the active draw deadline changes");
            check(c->r[R_EDI] == (alternate ? uint32_t(rate) : 60u),
                  "alternate wait uses the selected cap for the measured rendering-rate ceiling");
            check(rd32(canaries + 8) == 1234 && rd32(canaries + 12) == 83 &&
                      rd32(canaries + 16) == 99 && rd8(canaries + 4) == 8,
                  "unrelated timing and flag canaries stay intact");
        }
    }
    wr32(RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE, 1016);
    wr32(RECOMP_HOOK_FRAME_CLOCK_WAIT_DEADLINE, 1025);
    c->r[R_EDI] = 60;
    g_fake_ret = GUEST_RETURN_SENTINEL;
    check(call_import(c, "KERNEL32.dll", "GetTickCount", {}) == 1000 &&
              rd32(RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE) == 1016 &&
              rd32(RECOMP_HOOK_FRAME_CLOCK_WAIT_DEADLINE) == 1025 && c->r[R_EDI] == 60,
          "unrelated clock calls cannot alter draw pacing");
    for (const char *pin : {"RECOMP_PIN_CLOCK"}) {
        os_setenv(pin, "1000,8");
        g_fake_ret = RECOMP_HOOK_FRAME_CLOCK_WAIT_CLAMP;
        call_import(c, "KERNEL32.dll", "GetTickCount", {});
        check(rd32(RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE) == 1016 && c->r[R_EDI] == 60,
              "%s retains original fixture behavior", pin);
        os_unsetenv(pin);
    }
    g_display_fps = 0;
    g_fake_ret = RECOMP_HOOK_FRAME_CLOCK_BEGIN;
    call_import(c, "KERNEL32.dll", "GetTickCount", {});
    check(rd8(canaries) == 40, "original mode preserves unrelated storage");
    g_fake_ret = RECOMP_HOOK_FRAME_CLOCK_WAIT_CLAMP;
    call_import(c, "KERNEL32.dll", "GetTickCount", {});
    check(rd32(RECOMP_HOOK_FRAME_CLOCK_CLAMP_DEADLINE) == 1016 && c->r[R_EDI] == 60,
          "original mode keeps its alternate wait and rate ceiling");
    host_clear_time_source();
    g_fake_ret = old_ret;
    c->r[R_EDI] = old_edi;
    wr8(canaries, old_limit);
    wr8(canaries + 4, old_flags);
    for (int i = 0; i < 5; ++i)
        wr32(addresses[i], saved[i]);
}

static uint32_t g_callback_hits = 0;
static uint32_t g_callback_args[4] = {0, 0, 0, 0};

static void fake_guest_fn(X86 *c) {
    ++g_callback_hits;
    for (int i = 0; i < 4; ++i)
        g_callback_args[i] = arg(c, i);
    set_eax(c, 0x600d);
}

static std::vector<uint32_t> g_geometry_messages;
static uint32_t g_geometry_width, g_geometry_height;
static bool g_geometry_suppress;
static void geometry_wndproc(X86 *c) {
    uint32_t hwnd = arg(c, 0), msg = arg(c, 1), wp = arg(c, 2), lp = arg(c, 3);
    if (msg == 0x47 || msg == 3 || msg == 5)
        g_geometry_messages.push_back(msg);
    if (msg == 5) {
        g_geometry_width = lp & 0xffff;
        g_geometry_height = lp >> 16;
    }
    if (msg == 0x47 && g_geometry_suppress)
        set_eax(c, 0);
    else
        call_import(c, "USER32.dll", "DefWindowProcW", {hwnd, msg, wp, lp});
}

static void test_synchronous_geometry(X86 *c) {
    section("USER32 synchronous window geometry");
    uint32_t proc = imports_alloc_trampoline("test", "geometry_wndproc", geometry_wndproc, 4);
    uint32_t cls = put_str("GeometryWnd"), wc = scratch_block(40);
    wr32(wc + 4, proc);
    wr32(wc + 36, cls);
    call_import(c, "USER32.dll", "RegisterClassA", {wc});
    uint32_t hwnd = call_import(c, "USER32.dll", "CreateWindowExA",
                                {0, cls, cls, 0x80000000u, 0, 0, 320, 240, 0, 0, 0, 0});
    uint32_t msg = scratch_block(28), rect = scratch_block(16);
    while (call_import(c, "USER32.dll", "PeekMessageW", {msg, hwnd, 3, 5, 1})) {
    }
    g_geometry_messages.clear();
    g_geometry_width = 320;
    g_geometry_height = 240;
    g_geometry_suppress = false;
    call_import(c, "USER32.dll", "SetWindowPos", {hwnd, 0, 10, 20, 560, g_geometry_height, 4});
    check(g_geometry_width == 560 && g_geometry_height == 240,
          "SetWindowPos updates the window procedure's size before returning");
    check(g_geometry_messages == std::vector<uint32_t>({0x47, 3, 5}),
          "WM_WINDOWPOSCHANGED delegates synchronous WM_MOVE and WM_SIZE to DefWindowProc");
    call_import(c, "USER32.dll", "SetWindowPos", {hwnd, 0, 10, 20, g_geometry_width, 410, 4});
    call_import(c, "USER32.dll", "GetClientRect", {hwnd, rect});
    check(rd32(rect + 8) == 560 && rd32(rect + 12) == 410,
          "successive width and height changes preserve the first dimension");
    check(call_import(c, "USER32.dll", "PeekMessageW", {msg, hwnd, 3, 5, 1}) == 0,
          "SetWindowPos does not leave stale geometry messages queued");
    g_geometry_messages.clear();
    g_geometry_suppress = true;
    call_import(c, "USER32.dll", "SetWindowPos", {hwnd, 0, 30, 40, 600, 420, 4});
    check(g_geometry_messages == std::vector<uint32_t>({0x47}) && g_geometry_width == 560,
          "handling WM_WINDOWPOSCHANGED without DefWindowProc suppresses WM_MOVE and WM_SIZE");
    call_import(c, "USER32.dll", "DestroyWindow", {hwnd});
}

static void test_windows(X86 *c) {
    section("USER32 windows and messages");
    // The window procedure is a stand-in the test-only recomp_call can reach;
    // it answers WM_NCCREATE with a non-zero value, as a real one must.
    uint32_t wndproc = imports_alloc_trampoline("test", "guest_callback", fake_guest_fn, 4);
    uint32_t clsname = put_str("RuntimeTestWnd");
    uint32_t wc = scratch_block(40);
    wr32(wc + 0, 3);       // style
    wr32(wc + 4, wndproc); // lpfnWndProc
    wr32(wc + 12, 8);      // cbWndExtra
    wr32(wc + 36, clsname);
    check(call_import(c, "USER32.dll", "RegisterClassA", {wc}) != 0, "RegisterClassA");

    uint32_t title = put_str("RuntimeX");
    uint32_t hwnd =
        call_import(c, "USER32.dll", "CreateWindowExA",
                    {0, clsname, title, 0x80000000u, 0, 0, 640, 480, 0, 0, RECOMP_IMAGE_BASE, 0});
    check(hwnd != 0, "CreateWindowExA -> %08x", hwnd);

    // Windows sends a new window its position and size; a game sizes its blit
    // rectangle from them and never asks again.
    uint32_t msgbuf = scratch_block(28);
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 0, 0, 1 /* PM_REMOVE */}) ==
                  1 &&
              rd32(msgbuf) == hwnd && rd32(msgbuf + 4) == 0x0003 && rd32(msgbuf + 8) == 0 &&
              rd32(msgbuf + 12) == 0u,
          "WM_MOVE follows CreateWindowExA (lParam %08x)", rd32(msgbuf + 12));
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 0, 0, 1}) == 1 &&
              rd32(msgbuf) == hwnd && rd32(msgbuf + 4) == 0x0005 && rd32(msgbuf + 8) == 0 &&
              rd32(msgbuf + 12) == ((480u << 16) | 640u),
          "WM_SIZE follows it with the client size (lParam %08x)", rd32(msgbuf + 12));
    g_callback_hits = 0;
    call_import(c, "USER32.dll", "SetWindowPos", {hwnd, 0, 10, 20, 800, 600, 0});
    check(g_callback_hits == 1 && g_callback_args[1] == 0x47,
          "SetWindowPos sends WM_WINDOWPOSCHANGED synchronously");
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 3, 5, 1}) == 0,
          "a procedure that consumes WM_WINDOWPOSCHANGED receives no WM_MOVE/WM_SIZE");
    call_import(c, "USER32.dll", "SetWindowPos", {hwnd, 0, 10, 20, 800, 600, 0});
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 3, 5, 1}) == 0,
          "unchanged geometry does not enqueue another WM_MOVE or WM_SIZE");
    call_import(c, "USER32.dll", "SetWindowPos", {hwnd, 0, 0, 0, 0, 0, 0x0003 /* NOSIZE|NOMOVE */});
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 0, 0, 1}) == 0,
          "a SetWindowPos that neither moves nor sizes posts nothing");

    call_import(c, "USER32.dll", "ShowWindow", {hwnd, 0});
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 0, 0, 1}) == 0,
          "ShowWindow hiding a new window posts no WM_SIZE");
    call_import(c, "USER32.dll", "ShowWindow", {hwnd, 1});
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 0, 0, 1}) == 1 &&
              rd32(msgbuf + 4) == 0x0005 && rd32(msgbuf + 8) == 0 &&
              rd32(msgbuf + 12) == ((600u << 16) | 800u),
          "ShowWindow posts WM_SIZE on the first show");
    call_import(c, "USER32.dll", "ShowWindow", {hwnd, 1});
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 5, 5, 1}) == 0,
          "ShowWindow on a visible window posts no second WM_SIZE");
    call_import(c, "USER32.dll", "ShowWindow", {hwnd, 0});
    call_import(c, "USER32.dll", "ShowWindow", {hwnd, 1});
    check(call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 5, 5, 1}) == 0,
          "ShowWindow after hiding posts no second WM_SIZE");

    // Restore the geometry and visibility used by the existing window checks.
    call_import(c, "USER32.dll", "ShowWindow", {hwnd, 0});
    call_import(c, "USER32.dll", "SetWindowPos", {hwnd, 0, 0, 0, 640, 480, 0});
    while (call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 0x0003, 0x0005, 1})) {
    }
    check(host_main_window() == hwnd, "host_main_window sees it");
    check(host_window_proc(hwnd) == wndproc, "the class WNDPROC was recorded");

    // A touch at a client point must survive the game's GetCursorPos ->
    // ScreenToClient round trip, including negative desktop origins.
    uint32_t touch_point = scratch_block(8);
    for (int32_t origin : {100, -100}) {
        call_import(c, "USER32.dll", "SetWindowPos",
                    {hwnd, 0, uint32_t(origin), uint32_t(origin), 640, 480, 0});
        host_set_client_cursor_pos(hwnd, 320, 240);
        call_import(c, "USER32.dll", "GetCursorPos", {touch_point});
        check(int32_t(rd32(touch_point)) == 320 + origin &&
                  int32_t(rd32(touch_point + 4)) == 240 + origin,
              "host client cursor is converted to screen coordinates at origin %d", origin);
        call_import(c, "USER32.dll", "ScreenToClient", {hwnd, touch_point});
        check(rd32(touch_point) == 320 && rd32(touch_point + 4) == 240,
              "touch round trip returns the requested client point");
        host_post_message(hwnd, 0x0200, 0, (240u << 16) | 320u);
        call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 0x0200, 0x0200, 1});
        check(int32_t(rd32(msgbuf + 20)) == 320 + origin &&
                  int32_t(rd32(msgbuf + 24)) == 240 + origin &&
                  rd32(msgbuf + 12) == ((240u << 16) | 320u),
              "MSG.pt is in screen pixels while mouse lParam stays in client pixels");
    }
    call_import(c, "USER32.dll", "SetWindowPos", {hwnd, 0, 0, 0, 640, 480, 0});

    uint32_t rc = scratch_block(16);
    call_import(c, "USER32.dll", "GetClientRect", {hwnd, rc});
    check(rd32(rc + 8) == 640 && rd32(rc + 12) == 480, "GetClientRect -> %ux%u", rd32(rc + 8),
          rd32(rc + 12));
    // A pointer game converts the cursor it polled with GetCursorPos into
    // client coordinates; a window at the origin maps a point onto itself.
    uint32_t pt = scratch_block(8);
    wr32(pt, 123);
    wr32(pt + 4, 45);
    check(call_import(c, "USER32.dll", "ScreenToClient", {hwnd, pt}) == 1 && rd32(pt) == 123 &&
              rd32(pt + 4) == 45,
          "ScreenToClient through a window at the origin");
    check(call_import(c, "USER32.dll", "GetActiveWindow", {}) == hwnd,
          "GetActiveWindow is the main window");
    check(call_import(c, "USER32.dll", "SetFocus", {hwnd}) == hwnd,
          "SetFocus returns the window that had focus");
    check(call_import(c, "USER32.dll", "GetMenu", {hwnd}) == 0, "GetMenu: no menu");
    check(call_import(c, "USER32.dll", "IsIconic", {hwnd}) == 0, "IsIconic: never minimised");
    check(call_import(c, "USER32.dll", "SetForegroundWindow", {hwnd}) == 1, "SetForegroundWindow");
    check(call_import(c, "USER32.dll", "SetActiveWindow", {hwnd}) == hwnd,
          "SetActiveWindow returns the previous");
    check(call_import(c, "USER32.dll", "OpenIcon", {hwnd}) == 1, "OpenIcon");
    check(call_import(c, "USER32.dll", "FindWindowA", {0, 0}) == 0,
          "FindWindowA finds no other instance");
    check(call_import(c, "USER32.dll", "WaitMessage", {}) == 1, "WaitMessage returns");
    uint32_t work = scratch_block(16);
    check(call_import(c, "USER32.dll", "SystemParametersInfoA", {48, 0, work, 0}) == 1 &&
              rd32(work + 8) == 640 && rd32(work + 12) == 480,
          "SPI_GETWORKAREA is the window's client area");
    check(call_import(c, "USER32.dll", "SystemParametersInfoA", {0x2000, 0, 0, 0}) == 0,
          "unknown SPI actions fail");
    // The input gate is not linked here; use the same user32 cursor bridge
    // that host input delivery uses before posting WM_MOUSEMOVE.
    host_set_cursor_pos(200, 100);
    host_post_message(hwnd, 0x0200, 0, 0);
    call_import(c, "USER32.dll", "PeekMessageA", {msgbuf, hwnd, 0x0200, 0x0200, 1});
    check(call_import(c, "USER32.dll", "GetMessagePos", {}) == ((100u << 16) | 200u),
          "GetMessagePos packs y:x");
    check(call_import(c, "USER32.dll", "GetMessageTime", {}) <=
              call_import(c, "KERNEL32.dll", "GetTickCount", {}),
          "GetMessageTime is on the tick clock");

    check(call_import(c, "USER32.dll", "SetWindowLongA", {hwnd, 0, 0x1111}) == 0,
          "SetWindowLongA on the first extra dword");
    check(call_import(c, "USER32.dll", "GetWindowLongA", {hwnd, 0}) == 0x1111,
          "GetWindowLongA reads it back");
    check(call_import(c, "USER32.dll", "GetWindowLongA", {hwnd, 0xfffffffcu}) == wndproc,
          "GWL_WNDPROC reads the window procedure");

    uint32_t msg = scratch_block(28);
    check(call_import(c, "USER32.dll", "PeekMessageA", {msg, 0, 0, 0, 0}) == 0,
          "PeekMessageA on an empty queue returns FALSE");
    host_post_message(hwnd, 0x0201 /* WM_LBUTTONDOWN */, 1, 0x00320064);
    check(call_import(c, "USER32.dll", "PeekMessageA", {msg, 0, 0, 0, 1}) == 1,
          "PeekMessageA(PM_REMOVE) returns the posted message");
    check(rd32(msg + 4) == 0x0201 && rd32(msg + 12) == 0x00320064,
          "the message and lParam survived the round trip");
    check(call_import(c, "USER32.dll", "PeekMessageA", {msg, 0, 0, 0, 1}) == 0,
          "the queue is empty again");

    call_import(c, "USER32.dll", "PostMessageA", {hwnd, 0x0100, 0x41, 0});
    check(call_import(c, "USER32.dll", "GetMessageA", {msg, 0, 0, 0}) == 1,
          "GetMessageA returns the posted WM_KEYDOWN");
    host_set_key_state(0x10, false);
    check(call_import(c, "USER32.dll", "TranslateMessage", {msg}) == 1,
          "TranslateMessage synthesised a WM_CHAR");
    check(call_import(c, "USER32.dll", "PeekMessageA", {msg, 0, 0, 0, 1}) == 1 &&
              rd32(msg + 4) == 0x0102 && rd32(msg + 8) == 'a',
          "the WM_CHAR carries 'a'");

    // Message filters, and an empty queue reports the documented error rather
    // than a message the system never sent.
    host_post_message(hwnd, 0x0201, 0, 0);
    check(call_import(c, "USER32.dll", "PeekMessageA", {msg, 0, 0x0100, 0x0109, 1}) == 0,
          "PeekMessageA with a keyboard filter skips the mouse message");
    check(call_import(c, "USER32.dll", "PeekMessageA", {msg, 0, 0x0200, 0x0209, 1}) == 1,
          "the mouse filter matches it");
    check(call_import(c, "USER32.dll", "GetMessageA", {msg, 0, 0, 0}) == 0xffffffffu,
          "GetMessageA on an empty queue with no host returns -1");

    uint32_t clip = scratch_block(16);
    wr32(clip + 0, 10);
    wr32(clip + 4, 20);
    wr32(clip + 8, 110);
    wr32(clip + 12, 220);
    check(call_import(c, "USER32.dll", "ClipCursor", {clip}) == 1, "ClipCursor");
    uint32_t back = scratch_block(16);
    check(call_import(c, "USER32.dll", "GetClipCursor", {back}) == 1 && rd32(back + 8) == 110,
          "GetClipCursor returns the clip rectangle");
    call_import(c, "USER32.dll", "ClipCursor", {0});
    check(call_import(c, "USER32.dll", "ShowCursor", {0}) == 0xffffffffu,
          "ShowCursor(FALSE) drops the display count to -1");
    call_import(c, "USER32.dll", "ShowCursor", {1});

    host_set_key_state(0x41, true);
    check(call_import(c, "USER32.dll", "GetAsyncKeyState", {0x41}) == 0x8000,
          "GetAsyncKeyState sees the host key");
    host_set_key_state(0x41, false);
    // A message box nobody can click is answered with the default button of the
    // set the caller asked for, never with a button that set does not contain.
    check(call_import(c, "USER32.dll", "MessageBoxA",
                      {0, put_str("text"), put_str("caption"), 0}) == 1,
          "MessageBoxA(MB_OK) returns IDOK");
    check(call_import(c, "USER32.dll", "MessageBoxA", {0, put_str("t"), put_str("c"), 4}) == 6,
          "MB_YESNO returns IDYES, not IDOK");
    check(call_import(c, "USER32.dll", "MessageBoxA", {0, put_str("t"), put_str("c"), 4 | 0x100}) ==
              7,
          "MB_YESNO | MB_DEFBUTTON2 returns IDNO");
    check(call_import(c, "USER32.dll", "MessageBoxA", {0, put_str("t"), put_str("c"), 2}) == 3,
          "MB_ABORTRETRYIGNORE returns IDABORT");
    check(call_import(c, "USER32.dll", "MessageBoxA", {0, put_str("t"), put_str("c"), 1 | 0x100}) ==
              2,
          "MB_OKCANCEL | MB_DEFBUTTON2 returns IDCANCEL");
}

// A window procedure that answers WM_PAINT the way a real one does: BeginPaint
// then EndPaint, which is what validates the update region.
static uint32_t g_painted = 0;
static void fake_painting_wndproc(X86 *c) {
    uint32_t hwnd = arg(c, 0), msg = arg(c, 1);
    // Anything but WM_PAINT is answered TRUE, so WM_NCCREATE does not cancel
    // the creation this procedure is being used for.
    if (msg != 0x000f) {
        set_eax(c, 1);
        return;
    }
    ++g_painted;
    uint32_t ps = scratch_block(64);
    uint32_t entry_esp = c->r[R_ESP];
    uint32_t bp = imports_resolve("USER32.dll", "BeginPaint");
    uint32_t ep = imports_resolve("USER32.dll", "EndPaint");
    uint32_t sp = entry_esp;
    sp -= 4;
    wr32(sp, ps);
    sp -= 4;
    wr32(sp, hwnd);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, bp);
    sp = entry_esp;
    sp -= 4;
    wr32(sp, ps);
    sp -= 4;
    wr32(sp, hwnd);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, ep);
    c->r[R_ESP] = entry_esp;
    set_eax(c, 0);
}

// A window procedure that handles nothing itself and passes everything to
// DefWindowProc, which is what an ordinary WNDPROC does with WM_NCCREATE.
static void fake_defproc_wndproc(X86 *c) {
    uint32_t a[4] = {arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3)};
    uint32_t tramp = imports_resolve("USER32.dll", "DefWindowProcA");
    uint32_t entry_esp = c->r[R_ESP];
    uint32_t sp = entry_esp;
    for (int i = 3; i >= 0; --i) {
        sp -= 4;
        wr32(sp, a[i]);
    }
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, tramp);
    c->r[R_ESP] = entry_esp;
}

// A thread body that clobbers callee-saved registers and returns normally.
static void fake_clobbering_thread(X86 *c) {
    c->r[R_EBX] = 0x11111111;
    c->r[R_ESI] = 0x22222222;
    c->r[R_EDI] = 0x33333333;
    c->r[R_EBP] = 0x44444444;
    set_eax(c, 0x5150);
}

// A thread body that clobbers registers and leaves through ExitThread.
static void fake_exiting_thread(X86 *c) {
    c->r[R_EBX] = 0xdeadbeef;
    c->r[R_EBP] = 0xfeedface;
    uint32_t tramp = imports_resolve("KERNEL32.dll", "ExitThread");
    uint32_t esp = c->r[R_ESP];
    esp -= 4;
    wr32(esp, 0x1234); // exit code
    esp -= 4;
    wr32(esp, 0x00401000); // return address ExitThread never uses
    c->r[R_ESP] = esp;
    imports_dispatch(c, tramp); // longjmps out of the thread
}

// Polls a thread the way the game's own creator at 0052d580 does: call
// GetExitCodeThread until it stops reporting STILL_ACTIVE, with a bound so a
// thread that never runs fails the test instead of hanging it. One poll is a
// scheduling point but promises nothing about WHICH thread ran, exactly as on
// Windows, so a caller that wants a particular thread has to keep asking.
static uint32_t poll_exit_code(X86 *c, uint32_t th, uint32_t pcode, int max_polls) {
    for (int i = 0; i < max_polls; ++i) {
        call_import(c, "KERNEL32.dll", "GetExitCodeThread", {th, pcode});
        if (rd32(pcode) != 0x103)
            return rd32(pcode);
    }
    return rd32(pcode);
}

// ---------------------------------------------------------------------------
// Scheduling contracts. These are the behaviours the cooperative scheduler
// exists for, and none of them is visible from a body that just runs and
// returns: a service thread that never finishes, a wait that has to consume
// real time, a suspend count that has to be unwound as many times as it was
// wound, and TLS that has to be per thread.
// ---------------------------------------------------------------------------
static uint32_t g_service_loops = 0;
static uint32_t g_service_event = 0;
static uint32_t g_service_stop = 0;

// A thread shaped like the game's DirectInput workers: it never returns, it
// waits on an event with a short timeout, and it counts its passes. It only
// makes progress if something reschedules it.
static void fake_service_thread(X86 *c) {
    uint32_t waitfn = imports_resolve("KERNEL32.dll", "WaitForSingleObject");
    while (!g_service_stop) {
        ++g_service_loops;
        uint32_t esp = c->r[R_ESP];
        uint32_t sp = esp;
        sp -= 4;
        wr32(sp, 20); // 20 ms timeout
        sp -= 4;
        wr32(sp, g_service_event);
        sp -= 4;
        wr32(sp, 0x00401000);
        c->r[R_ESP] = sp;
        imports_dispatch(c, waitfn);
        c->r[R_ESP] = esp;
    }
    set_eax(c, 0x5e12);
}

// A worker for the ExitProcess check: it counts passes and waits briefly, so
// it only advances when something schedules it. Shaped like fake_service_thread
// but with its own counter, because the exit check runs in a child process
// that must attribute every pass to this worker alone.
static uint32_t g_exit_worker_loops = 0;
static uint32_t g_exit_worker_event = 0;

static void fake_exit_worker(X86 *c) {
    uint32_t waitfn = imports_resolve("KERNEL32.dll", "WaitForSingleObject");
    for (;;) {
        ++g_exit_worker_loops;
        uint32_t esp = c->r[R_ESP];
        uint32_t sp = esp;
        sp -= 4;
        wr32(sp, 5); // 5 ms timeout
        sp -= 4;
        wr32(sp, g_exit_worker_event);
        sp -= 4;
        wr32(sp, 0x00401000);
        c->r[R_ESP] = sp;
        imports_dispatch(c, waitfn);
        c->r[R_ESP] = esp;
    }
}

// Reads one TLS slot, writes its own marker, reads it back, and reports
// whether the slot was private to it.
static uint32_t g_tls_index = 0;
static uint32_t g_tls_seen_before[4] = {0, 0, 0, 0};
static uint32_t g_tls_read_back[4] = {0, 0, 0, 0};
static uint32_t g_tls_next_slot = 0;
static uint32_t g_image_tls_blocks[4] = {};
static bool g_image_tls_initialized[4] = {};

static void fake_tls_thread(X86 *c) {
    uint32_t slot = g_tls_next_slot++;
    const LoaderTls &tls = loader_tls();
    if (slot < 4 && tls.index < TLS_SLOTS && tls.raw_end > tls.raw_start) {
        uint32_t block = rd32(rd32(c->fs_base + 0x2c) + 4 * tls.index);
        g_image_tls_blocks[slot] = block;
        g_image_tls_initialized[slot] =
            block && memcmp(g_mem + block, g_mem + tls.raw_start, tls.raw_end - tls.raw_start) == 0;
        if (block)
            g_mem[block] ^= (uint8_t)(slot + 1);
    }
    uint32_t get = imports_resolve("KERNEL32.dll", "TlsGetValue");
    uint32_t set = imports_resolve("KERNEL32.dll", "TlsSetValue");
    uint32_t esp = c->r[R_ESP], sp;

    sp = esp;
    sp -= 4;
    wr32(sp, g_tls_index);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, get);
    c->r[R_ESP] = esp;
    if (slot < 4)
        g_tls_seen_before[slot] = c->r[R_EAX];

    sp = esp;
    sp -= 4;
    wr32(sp, 0xd00d0000u + slot);
    sp -= 4;
    wr32(sp, g_tls_index);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, set);
    c->r[R_ESP] = esp;

    sp = esp;
    sp -= 4;
    wr32(sp, g_tls_index);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, get);
    c->r[R_ESP] = esp;
    if (slot < 4)
        g_tls_read_back[slot] = c->r[R_EAX];
    set_eax(c, 0);
}

static double wall_seconds() {
    return (double)os_monotonic_ns() * 1e-9;
}

// Takes a critical section, records the order, holds it across a Sleep so the
// other thread must really block, then leaves it.
static uint32_t g_cs_addr = 0;
static char g_cs_order[16] = {0};
static uint32_t g_cs_order_n = 0;
static void cs_note(char ch) {
    if (g_cs_order_n < sizeof g_cs_order - 1)
        g_cs_order[g_cs_order_n++] = ch;
}
static void fake_critsec_thread(X86 *c) {
    uint32_t enter = imports_resolve("KERNEL32.dll", "EnterCriticalSection");
    uint32_t leave = imports_resolve("KERNEL32.dll", "LeaveCriticalSection");
    uint32_t sleep = imports_resolve("KERNEL32.dll", "Sleep");
    uint32_t esp = c->r[R_ESP], sp;
    sp = esp;
    sp -= 4;
    wr32(sp, g_cs_addr);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, enter);
    c->r[R_ESP] = esp;
    cs_note('B');
    sp = esp;
    sp -= 4;
    wr32(sp, 20);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, sleep);
    c->r[R_ESP] = esp;
    cs_note('b');
    sp = esp;
    sp -= 4;
    wr32(sp, g_cs_addr);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, leave);
    c->r[R_ESP] = esp;
    set_eax(c, 0);
}

// Waits on one event and records which waiter it was.
static uint32_t g_shared_event = 0;
static uint32_t g_wait_results[4] = {0, 0, 0, 0};
static uint32_t g_wait_done = 0;
static void fake_waiter_thread(X86 *c) {
    uint32_t waitfn = imports_resolve("KERNEL32.dll", "WaitForSingleObject");
    uint32_t esp = c->r[R_ESP];
    uint32_t sp = esp;
    sp -= 4;
    wr32(sp, 400);
    sp -= 4;
    wr32(sp, g_shared_event);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, waitfn);
    c->r[R_ESP] = esp;
    if (g_wait_done < 4)
        g_wait_results[g_wait_done++] = c->r[R_EAX];
    set_eax(c, 0);
}

// Takes a mutex and ends without releasing it.
static uint32_t g_abandon_mutex = 0;
static void fake_abandoning_thread(X86 *c) {
    uint32_t waitfn = imports_resolve("KERNEL32.dll", "WaitForSingleObject");
    uint32_t esp = c->r[R_ESP];
    uint32_t sp = esp;
    sp -= 4;
    wr32(sp, 0);
    sp -= 4;
    wr32(sp, g_abandon_mutex);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, waitfn);
    c->r[R_ESP] = esp;
    set_eax(c, 0);
}

// Calls ExitProcess from a worker. The main thread owns the landing pad, so
// this has to reach it however deeply it is blocked.
static uint32_t g_exiting_started = 0;
static void fake_exitprocess_thread(X86 *c) {
    ++g_exiting_started;
    uint32_t fn = imports_resolve("KERNEL32.dll", "ExitProcess");
    uint32_t esp = c->r[R_ESP];
    esp -= 4;
    wr32(esp, 0x2b);
    esp -= 4;
    wr32(esp, 0x00401000);
    c->r[R_ESP] = esp;
    imports_dispatch(c, fn); // does not return
    set_eax(c, 0);
}

// A worker that sleeps a little and then posts the message a filtered
// GetMessageA is waiting for. It only ever runs if GetMessageA yields.
static uint32_t g_post_hwnd = 0;
static uint32_t g_posted_from_worker = 0;
static void fake_posting_thread(X86 *c) {
    uint32_t sleepfn = imports_resolve("KERNEL32.dll", "Sleep");
    uint32_t esp = c->r[R_ESP];
    uint32_t sp = esp;
    sp -= 4;
    wr32(sp, 20);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, sleepfn);
    c->r[R_ESP] = esp;
    host_post_message(g_post_hwnd, 0x0201, 7, 9);
    g_posted_from_worker = 1;
    set_eax(c, 0);
}

// The message waiter a real host installs: service the loop, then report
// whether anything is queued. It also rescues the test after an absurd number
// of calls, so a GetMessageA that busy-loops fails the test instead of hanging
// it.
static uint32_t g_waiter_calls = 0;
static uint32_t g_waiter_rescued = 0;
static bool test_message_waiter() {
    if (++g_waiter_calls > 200000 && !g_waiter_rescued) {
        g_waiter_rescued = 1;
        host_post_message(g_post_hwnd, 0x0201, 7, 9);
    }
    return host_messages_pending();
}

// A thread shaped like the DirectInput service threads: register nothing, just
// wait on an event for a long time and record how it was released. It only
// ever finishes if something signals the event.
static uint32_t g_hostsig_event = 0;
static uint32_t g_hostsig_result = 0xdeadbeef;
static uint32_t g_hostsig_running = 0;
static void fake_host_waiter_thread(X86 *c) {
    g_hostsig_running = 1;
    uint32_t waitfn = imports_resolve("KERNEL32.dll", "WaitForSingleObject");
    uint32_t esp = c->r[R_ESP];
    uint32_t sp = esp;
    sp -= 4;
    wr32(sp, 5000);
    sp -= 4;
    wr32(sp, g_hostsig_event);
    sp -= 4;
    wr32(sp, 0x00401000);
    c->r[R_ESP] = sp;
    imports_dispatch(c, waitfn);
    c->r[R_ESP] = esp;
    g_hostsig_result = c->r[R_EAX];
    set_eax(c, 0x1234);
}

// Signals the event from a genuine host thread, the way an AppKit event
// handler would, while the guest is running.
static void *host_signal_thread(void *arg) {
    os_sleep_us(30 * 1000);
    guest_event_signal_from_host((uint32_t)(uintptr_t)arg);
    return nullptr;
}

// Stands in for a windowed host's idle waiter: it services nothing, sleeps its
// slice and returns, which is the shape the real one has. Counting the calls
// shows the run thread really went through it rather than round it.
static uint32_t g_idle_calls = 0;
static double g_idle_total = 0.0;
static int test_idle_waiter(double seconds) {
    ++g_idle_calls;
    g_idle_total += seconds;
    os_sleep_us((uint64_t)(seconds * 1e6));
    return 0;
}

// Repeated short handoffs model the input workers woken by pointer motion.
// An AppKit wait cannot hear a scheduler condition-variable broadcast, so
// sleeping in that host callback adds a full slice to each completed handoff.
static uint32_t g_pointer_handoffs = 0;
static void fake_pointer_handoff_thread(X86 *c) {
    for (unsigned i = 0; i < 100; ++i) {
        ++g_pointer_handoffs;
        host_guest_yield();
    }
    set_eax(c, 0);
}

// A window procedure that polls a non-blocking import while its window is
// being created, and records whether the service thread ran meanwhile.
static uint32_t g_creation_service_passes = 0xffffffffu;
static void creation_polling_wndproc(X86 *c) {
    if (arg(c, 1) == 0x81) { // WM_NCCREATE
        const uint32_t before = g_service_loops;
        const double t0 = wall_seconds();
        while (wall_seconds() - t0 < 0.05)
            call_import(c, "KERNEL32.dll", "GetTickCount", {});
        g_creation_service_passes = g_service_loops - before;
    }
    set_eax(c, 1);
}

// ---------------------------------------------------------------------------
// Thread and handle creation against parked workers (run 23,
// review/run22/snd-crash.md). A worker parked in Sleep wakes under g_sched_m
// WITHOUT the baton and reads the scheduler's thread list and the handle table
// (sched_wait_locked, sched_expire_locked), so the baton holder must not change
// those containers outside that lock. Built with ThreadSanitizer
// (harness/r23/tsan_sched.sh, `runtime_tests --sched-race`) the unlocked push
// is reported as a data race; in an ordinary build this checks that every
// thread still ends.
// ---------------------------------------------------------------------------
static uint32_t g_race_stop = 0; // guest-side: ordered by the baton handoff
static uint32_t g_race_sleeps = 0;
static void race_sleeper_thread(X86 *c) {
    uint32_t sleepfn = imports_resolve("KERNEL32.dll", "Sleep");
    while (!g_race_stop) {
        ++g_race_sleeps;
        uint32_t esp = c->r[R_ESP];
        uint32_t sp = esp;
        sp -= 4;
        wr32(sp, 1);
        sp -= 4;
        wr32(sp, 0x00401000);
        c->r[R_ESP] = sp;
        imports_dispatch(c, sleepfn);
        c->r[R_ESP] = esp;
    }
    set_eax(c, 0x51ee);
}
static void race_quick_thread(X86 *c) {
    set_eax(c, 0x0c0c);
}
static std::atomic<int> g_race_signal_stop{0};
static std::atomic<int> g_race_signals{0};
// Wakes every parked worker over and over, the way the audio host and the
// deadline ticker do at start-up.
static void *race_signal_thread(void *arg) {
    while (!g_race_signal_stop.load()) {
        guest_event_signal_from_host((uint32_t)(uintptr_t)arg);
        g_race_signals.fetch_add(1);
        os_sleep_us(100);
    }
    return nullptr;
}

// Walks the scheduler's containers under its lock, back to back, as woken
// workers do: the reader TSan needs beside every unlocked change (run 23: the
// real wakes are too rare inside the microseconds between a push and the next
// lock to show the race reliably).
static std::atomic<int> g_race_census_stop{0};
static std::atomic<uint32_t> g_race_census_walks{0};
static void *race_census_thread(void *) {
    while (!g_race_census_stop.load()) {
        sched_census_locked_walk();
        g_race_census_walks.fetch_add(1);
    }
    return nullptr;
}

static void test_sched_spawn_race(X86 *c) {
    section("thread and handle creation against parked workers");
    uint32_t pcode = scratch_block(4);
    uint32_t sleeper = imports_alloc_trampoline("test", "race_sleeper", race_sleeper_thread, 1);
    uint32_t quick = imports_alloc_trampoline("test", "race_quick", race_quick_thread, 1);
    uint32_t ev = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 0, 0, 0});
    g_race_stop = 0;
    uint32_t sleepers[3];
    for (uint32_t &h : sleepers)
        h = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, sleeper, 0, 0, 0});
    for (int i = 0; i < 20 && g_race_sleeps < 6; ++i)
        call_import(c, "KERNEL32.dll", "Sleep", {2});
    check(g_race_sleeps >= 3, "three sleepers are parked in Sleep (%u passes)", g_race_sleeps);

    OsThread *sig = os_thread_create(race_signal_thread, (void *)(uintptr_t)ev, 0);
    OsThread *census = os_thread_create(race_census_thread, nullptr, 0);
    // 48 threads: the list grows through 4, 8, 16 and 32 entries, each a
    // reallocation before the fix; 8 events made and closed per thread.
    const int kThreads = 48, kEvents = 8;
    std::vector<uint32_t> quick_handles;
    uint32_t made = 0, closed = 0;
    for (int i = 0; i < kThreads; ++i) {
        uint32_t th = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, quick, 0, 0, 0});
        made += th != 0;
        quick_handles.push_back(th);
        for (int k = 0; k < kEvents; ++k) {
            uint32_t e = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, 0});
            closed += e && call_import(c, "KERNEL32.dll", "CloseHandle", {e}) != 0;
        }
    }
    check(made == (uint32_t)kThreads, "created %u of %d threads while the workers were woken",
          made, kThreads);
    check(closed == (uint32_t)(kThreads * kEvents), "made and closed %u of %d events", closed,
          kThreads * kEvents);
    g_race_signal_stop.store(1);
    os_thread_join(sig);
    g_race_census_stop.store(1);
    os_thread_join(census);
    check(g_race_census_walks.load() > 0, "a host thread walked the thread list and handle table %u times meanwhile",
          g_race_census_walks.load());
    check(g_race_signals.load() > 0, "the host thread signalled %d times", g_race_signals.load());

    g_race_stop = 1;
    // On wall-clock time, not a poll count: a sleeper needs its 1 ms Sleep to
    // end before it sees the stop, and counted polls run out sooner.
    auto exit_code_within = [&](uint32_t h, double seconds) {
        const double t0 = wall_seconds();
        uint32_t code = 0x103;
        while (wall_seconds() - t0 < seconds) {
            call_import(c, "KERNEL32.dll", "GetExitCodeThread", {h, pcode});
            code = rd32(pcode);
            if (code != 0x103)
                break;
            call_import(c, "KERNEL32.dll", "Sleep", {1});
        }
        return code;
    };
    uint32_t sleepers_done = 0, quick_done = 0;
    for (uint32_t h : sleepers) {
        sleepers_done += exit_code_within(h, 2.0) == 0x51ee;
        call_import(c, "KERNEL32.dll", "CloseHandle", {h});
    }
    for (uint32_t h : quick_handles) {
        quick_done += exit_code_within(h, 2.0) == 0x0c0c;
        call_import(c, "KERNEL32.dll", "CloseHandle", {h});
    }
    check(sleepers_done == 3, "the sleepers ended (%u of 3)", sleepers_done);
    check(quick_done == (uint32_t)kThreads, "every created thread ran and ended (%u of %d)",
          quick_done, kThreads);
    call_import(c, "KERNEL32.dll", "CloseHandle", {ev});
}

static void test_scheduling(X86 *c) {
    section("cooperative scheduling contracts");
    uint32_t pcode = scratch_block(4);

    // --- a service thread makes progress without an explicit yield ---------
    g_service_event = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, 0});
    g_service_loops = 0;
    g_service_stop = 0;
    uint32_t svc = imports_alloc_trampoline("test", "service_thread", fake_service_thread, 1);
    uint32_t th = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, svc, 0, 0, 0});
    check(th != 0, "started a service thread that never returns");
    check(g_service_loops == 0, "it has not run yet");

    // Only GetTickCount, which is not a blocking call and never yielded before
    // the bounded checkpoint existed. The service thread must still advance.
    uint32_t before = g_service_loops;
    double t0 = wall_seconds();
    while (wall_seconds() - t0 < 0.25)
        call_import(c, "KERNEL32.dll", "GetTickCount", {});
    check(g_service_loops > before,
          "polling GetTickCount alone let the service thread run (%u passes)", g_service_loops);

    // --- an atomic stretch keeps the baton through import checkpoints ------
    before = g_service_loops;
    sched_atomic_enter(c->r[R_ESP]);
    t0 = wall_seconds();
    while (wall_seconds() - t0 < 0.05)
        call_import(c, "KERNEL32.dll", "GetTickCount", {});
    check(g_service_loops == before, "no other thread ran inside an atomic stretch");
    // A guest exception unwinding above the stretch ends it.
    sched_atomic_unwind_to_esp(c->r[R_ESP] + 4);
    t0 = wall_seconds();
    while (wall_seconds() - t0 < 0.25)
        call_import(c, "KERNEL32.dll", "GetTickCount", {});
    check(g_service_loops > before, "unwinding above the stretch ended it");

    // --- window creation is such a stretch, messages and all ---------------
    {
        uint32_t proc = imports_alloc_trampoline("test", "creation_polling_wndproc",
                                                 creation_polling_wndproc, 4);
        uint32_t cls = put_str("AtomicCreate"), wc = scratch_block(40);
        wr32(wc + 4, proc);
        wr32(wc + 36, cls);
        call_import(c, "USER32.dll", "RegisterClassA", {wc});
        g_creation_service_passes = 0xffffffffu;
        uint32_t hwnd = call_import(c, "USER32.dll", "CreateWindowExA",
                                    {0, cls, cls, 0, 0, 0, 64, 64, 0, 0, 0, 0});
        check(hwnd != 0 && g_creation_service_passes == 0,
              "no other thread ran while CreateWindowExA sent WM_NCCREATE (%u passes)",
              g_creation_service_passes);
        before = g_service_loops;
        t0 = wall_seconds();
        while (wall_seconds() - t0 < 0.25)
            call_import(c, "KERNEL32.dll", "GetTickCount", {});
        check(g_service_loops > before, "and the stretch ended with the call");
        call_import(c, "USER32.dll", "DestroyWindow", {hwnd});
    }

    // --- an unsignalled wait consumes real time and reports a timeout ------
    uint32_t ev = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, 0});
    t0 = wall_seconds();
    uint32_t r = call_import(c, "KERNEL32.dll", "WaitForSingleObject", {ev, 60});
    double waited = wall_seconds() - t0;
    check(r == 0x102, "an unsignalled wait with a timeout reports WAIT_TIMEOUT");
    check(waited >= 0.05, "and it waited for it (%.0f ms)", waited * 1000.0);

    // --- a zero timeout answers from the current state, without waiting ----
    t0 = wall_seconds();
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {ev, 0}) == 0x102,
          "a zero timeout on an unsignalled object returns WAIT_TIMEOUT at once");
    check(wall_seconds() - t0 < 0.02, "and does not block");

    // --- a signalled object releases the waiter without waiting it out -----
    call_import(c, "KERNEL32.dll", "SetEvent", {ev});
    t0 = wall_seconds();
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {ev, 5000}) == 0,
          "a signalled event satisfies the wait");
    check(wall_seconds() - t0 < 0.05, "immediately, not after the timeout");

    // --- Sleep really sleeps ----------------------------------------------
    t0 = wall_seconds();
    call_import(c, "KERNEL32.dll", "Sleep", {60});
    double slept = wall_seconds() - t0;
    check(slept >= 0.05, "Sleep(60) suspended the caller for the interval (%.0f ms)",
          slept * 1000.0);

    g_service_stop = 1;
    call_import(c, "KERNEL32.dll", "SetEvent", {g_service_event});
    check(poll_exit_code(c, th, pcode, 200) == 0x5e12,
          "the service thread saw the stop request and ended");

    // --- nested suspension -------------------------------------------------
    uint32_t fn = imports_alloc_trampoline("test", "guest_callback", fake_guest_fn, 4);
    g_callback_hits = 0;
    uint32_t th2 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, fn, 0x77, 4, 0});
    check(call_import(c, "KERNEL32.dll", "SuspendThread", {th2}) == 1,
          "SuspendThread on a CREATE_SUSPENDED thread reports count 1");
    check(call_import(c, "KERNEL32.dll", "ResumeThread", {th2}) == 2,
          "the first ResumeThread reports count 2");
    for (int i = 0; i < 8; ++i)
        call_import(c, "KERNEL32.dll", "GetTickCount", {});
    check(g_callback_hits == 0, "still suspended after one resume of two");
    check(call_import(c, "KERNEL32.dll", "ResumeThread", {th2}) == 1,
          "the second ResumeThread reports count 1");
    check(poll_exit_code(c, th2, pcode, 64) == 0x600d && g_callback_hits == 1,
          "and the thread ran once the count reached zero");

    // --- TLS is per thread -------------------------------------------------
    g_tls_index = call_import(c, "KERNEL32.dll", "TlsAlloc", {});
    check(g_tls_index != 0xffffffffu, "TlsAlloc gave index %u", g_tls_index);
    check(g_tls_index != loader_tls().index, "TlsAlloc skips the image's reserved slot");
    call_import(c, "KERNEL32.dll", "TlsSetValue", {g_tls_index, 0x11111111});
    g_tls_next_slot = 0;
    uint32_t tlsfn = imports_alloc_trampoline("test", "tls_thread", fake_tls_thread, 1);
    uint32_t ta = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, tlsfn, 0, 0, 0});
    uint32_t tb = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, tlsfn, 0, 0, 0});
    poll_exit_code(c, ta, pcode, 64);
    poll_exit_code(c, tb, pcode, 64);
    const LoaderTls &tls = loader_tls();
    if (tls.index < TLS_SLOTS && tls.raw_end > tls.raw_start) {
        uint32_t main_block = rd32(TLS_BASE + 4 * tls.index);
        check(g_image_tls_initialized[0] && g_image_tls_initialized[1],
              "both new threads start with the image's TLS raw data");
        check(g_image_tls_blocks[0] != g_image_tls_blocks[1] &&
                  g_image_tls_blocks[0] != main_block && g_image_tls_blocks[1] != main_block,
              "the main thread and both workers have distinct image TLS blocks");
        check(memcmp(g_mem + main_block, g_mem + tls.raw_start, tls.raw_end - tls.raw_start) == 0,
              "worker TLS writes leave the main thread's image TLS data intact");
    }
    check(g_tls_seen_before[0] == 0 && g_tls_seen_before[1] == 0,
          "each thread's slot started at zero, not at the creator's value");
    check(g_tls_read_back[0] == 0xd00d0000u && g_tls_read_back[1] == 0xd00d0001u,
          "and each read back its own value");
    check(call_import(c, "KERNEL32.dll", "TlsGetValue", {g_tls_index}) == 0x11111111,
          "the creator's value is untouched by either");
    call_import(c, "KERNEL32.dll", "TlsFree", {g_tls_index});

    // --- a critical section excludes another thread ------------------------
    uint32_t cs = scratch_block(24);
    call_import(c, "KERNEL32.dll", "InitializeCriticalSection", {cs});
    call_import(c, "KERNEL32.dll", "EnterCriticalSection", {cs});
    call_import(c, "KERNEL32.dll", "EnterCriticalSection", {cs});
    check(rd32(cs + 8) == 2, "entering twice recurses rather than deadlocking");
    check(rd32(cs + 12) != 0, "and records an owner");
    call_import(c, "KERNEL32.dll", "LeaveCriticalSection", {cs});
    check(rd32(cs + 8) == 1, "one leave drops one level");
    call_import(c, "KERNEL32.dll", "LeaveCriticalSection", {cs});
    check(rd32(cs + 12) == 0 && rd32(cs + 4) == 0xffffffffu,
          "the last leave releases it and restores LockCount = -1");
    check(call_import(c, "KERNEL32.dll", "TryEnterCriticalSection", {cs}) == 1,
          "TryEnterCriticalSection takes a free section");
    call_import(c, "KERNEL32.dll", "LeaveCriticalSection", {cs});
    call_import(c, "KERNEL32.dll", "DeleteCriticalSection", {cs});

    // --- a contended critical section really blocks -------------------------
    g_cs_addr = scratch_block(24);
    g_cs_order_n = 0;
    memset(g_cs_order, 0, sizeof g_cs_order);
    call_import(c, "KERNEL32.dll", "InitializeCriticalSection", {g_cs_addr});
    uint32_t csfn = imports_alloc_trampoline("test", "critsec_thread", fake_critsec_thread, 1);
    uint32_t cst = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, csfn, 0, 0, 0});
    call_import(c, "KERNEL32.dll", "GetTickCount", {}); // let it take the lock
    for (int i = 0; i < 4 && g_cs_order_n == 0; ++i) {
        call_import(c, "KERNEL32.dll", "Sleep", {5});
    }
    check(g_cs_order_n >= 1 && g_cs_order[0] == 'B', "the worker took the critical section first");
    cs_note('A');
    call_import(c, "KERNEL32.dll", "EnterCriticalSection", {g_cs_addr});
    cs_note('a');
    check(strcmp(g_cs_order, "BAba") == 0,
          "this thread blocked until the worker left it (order %s)", g_cs_order);
    call_import(c, "KERNEL32.dll", "LeaveCriticalSection", {g_cs_addr});
    poll_exit_code(c, cst, pcode, 200);

    // --- one signal releases exactly one of several waiters -----------------
    g_shared_event = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 0, 0, 0}); // auto-reset
    g_wait_done = 0;
    uint32_t wfn = imports_alloc_trampoline("test", "waiter_thread", fake_waiter_thread, 1);
    uint32_t wa = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, wfn, 0, 0, 0});
    uint32_t wb = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, wfn, 0, 0, 0});
    for (int i = 0; i < 4; ++i)
        call_import(c, "KERNEL32.dll", "GetTickCount", {});
    check(g_wait_done == 0, "both waiters are blocked on the auto-reset event");
    call_import(c, "KERNEL32.dll", "SetEvent", {g_shared_event});
    for (int i = 0; i < 40 && g_wait_done < 1; ++i)
        call_import(c, "KERNEL32.dll", "Sleep", {5});
    check(g_wait_done == 1 && g_wait_results[0] == 0,
          "one SetEvent released exactly one waiter, with WAIT_OBJECT_0");
    call_import(c, "KERNEL32.dll", "SetEvent", {g_shared_event});
    poll_exit_code(c, wa, pcode, 400);
    poll_exit_code(c, wb, pcode, 400);
    check(g_wait_done == 2, "the second signal released the other one");

    // --- a mutex its owner never released is abandoned ----------------------
    g_abandon_mutex = call_import(c, "KERNEL32.dll", "CreateMutexA", {0, 0, 0});
    uint32_t abfn =
        imports_alloc_trampoline("test", "abandoning_thread", fake_abandoning_thread, 1);
    uint32_t abt = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, abfn, 0, 0, 0});
    poll_exit_code(c, abt, pcode, 200);
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {g_abandon_mutex, 0}) == 0x80,
          "a mutex whose owner ended reports WAIT_ABANDONED to the next waiter");
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {g_abandon_mutex, 0}) == 0,
          "and only to the first: it is owned normally after that");
    call_import(c, "KERNEL32.dll", "ReleaseMutex", {g_abandon_mutex});
    call_import(c, "KERNEL32.dll", "ReleaseMutex", {g_abandon_mutex});
    call_import(c, "KERNEL32.dll", "CloseHandle", {g_abandon_mutex});

    // --- a mutex is owned, recursively -------------------------------------
    uint32_t mx = call_import(c, "KERNEL32.dll", "CreateMutexA", {0, 1, 0});
    check(mx != 0, "created a mutex owned by its creator");
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {mx, 0}) == 0,
          "the owner re-enters it without blocking");
    check(call_import(c, "KERNEL32.dll", "ReleaseMutex", {mx}) == 1, "released once");
    check(call_import(c, "KERNEL32.dll", "ReleaseMutex", {mx}) == 1, "released twice");
    check(call_import(c, "KERNEL32.dll", "ReleaseMutex", {mx}) == 0,
          "releasing a mutex this thread no longer owns fails");
    call_import(c, "KERNEL32.dll", "CloseHandle", {mx});

    // --- GetMessageA waits without starving the thread that will post -------
    // A queued message the filter rejects keeps the waiter answering "yes,
    // there is something", so a loop that only yields when the waiter says no
    // spins forever and the worker that would post the matching message never
    // gets the baton.
    {
        g_post_hwnd = host_main_window();
        g_posted_from_worker = 0;
        g_waiter_calls = 0;
        g_waiter_rescued = 0;
        host_set_message_waiter(test_message_waiter);

        // Queued, and outside the filter this GetMessageA will use.
        host_post_message(g_post_hwnd, 0x0400, 0, 0);
        check(host_messages_pending(), "an unmatched message is queued");

        uint32_t pfn = imports_alloc_trampoline("test", "posting_thread", fake_posting_thread, 1);
        call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, pfn, 0, 0, 0});

        uint32_t msg = scratch_block(28);
        uint32_t r = call_import(c, "USER32.dll", "GetMessageA", {msg, 0, 0x0200, 0x0209});
        check(r == 1 && rd32(msg + 4) == 0x0201 && rd32(msg + 8) == 7,
              "GetMessageA waited and returned the matching message");
        check(g_posted_from_worker == 1, "the worker got the baton and posted it");
        check(g_waiter_rescued == 0,
              "GetMessageA yielded rather than spinning on the unmatched message "
              "(%u waiter calls)",
              g_waiter_calls);
        check(host_messages_pending(), "and the unmatched message is still queued, not swallowed");

        // Drain it so later tests see an empty queue.
        call_import(c, "USER32.dll", "PeekMessageA", {msg, 0, 0, 0, 1});
        host_set_message_waiter(nullptr);
    }

    // --- deadlines are exact, not rounded to some coarser tick --------------
    // A hundred Sleep(1) calls must take about a hundred milliseconds. If any
    // wait in the scheduler rounded up to a poll interval, this is where it
    // would show: at a 200 ms poll the same loop would take twenty seconds.
    // The DirectInput workers are alive and waiting 200 ms at a time while
    // this runs, which is exactly the situation that would quantise it.
    {
        g_service_event = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, 0});
        g_service_loops = 0;
        g_service_stop = 0;
        uint32_t svc2 = imports_alloc_trampoline("test", "service_thread2", fake_service_thread, 1);
        uint32_t bg = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, svc2, 0, 0, 0});

        double t0 = wall_seconds();
        for (int i = 0; i < 100; ++i)
            call_import(c, "KERNEL32.dll", "Sleep", {1});
        double took = wall_seconds() - t0;
        check(took < 0.300, "100 x Sleep(1) took %.0f ms, so deadlines are not rounded to a poll",
              took * 1000.0);
        check(took >= 0.050, "and it did sleep rather than returning at once (%.0f ms)",
              took * 1000.0);

        g_service_stop = 1;
        call_import(c, "KERNEL32.dll", "SetEvent", {g_service_event});
        poll_exit_code(c, bg, pcode, 400);
    }

    // --- a host thread can release a blocked guest thread --------------------
    // This is the DirectInput path: the game's service threads wait on an
    // event the host signals when input arrives. The host has no baton, so it
    // may not touch the handle table; the signal is queued and applied by the
    // next thread to enter the scheduler.
    {
        g_hostsig_event = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 0, 0, 0});
        g_hostsig_result = 0xdeadbeef;
        g_hostsig_running = 0;
        uint32_t hfn =
            imports_alloc_trampoline("test", "host_waiter_thread", fake_host_waiter_thread, 1);
        uint32_t ht = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, hfn, 0, 0, 0});

        // Let it reach the wait.
        for (int i = 0; i < 20 && !g_hostsig_running; ++i)
            call_import(c, "KERNEL32.dll", "Sleep", {5});
        check(g_hostsig_running == 1, "the waiter thread reached its wait");
        check(g_hostsig_result == 0xdeadbeef, "and is still in it");

        OsThread *sig = os_thread_create(host_signal_thread, (void *)(uintptr_t)g_hostsig_event, 0);

        // Poll on wall-clock time, not on a count: 4000 counted polls run out
        // in under a millisecond, long before the host thread has signalled.
        double t0 = wall_seconds();
        uint32_t code = 0x103;
        while (wall_seconds() - t0 < 2.0) {
            call_import(c, "KERNEL32.dll", "GetExitCodeThread", {ht, pcode});
            code = rd32(pcode);
            if (code != 0x103)
                break;
            call_import(c, "KERNEL32.dll", "Sleep", {5});
        }
        double took = wall_seconds() - t0;
        check(code == 0x1234, "the thread finished");
        os_thread_join(sig);
        check(g_hostsig_result == 0,
              "its wait returned WAIT_OBJECT_0, released by the host signal");
        check(took < 2.0,
              "and it was released when the signal landed, not by its 5 s timeout "
              "(%.0f ms)",
              took * 1000.0);
    }

    // --- short input handoffs must wake the main thread immediately --------
    {
        g_pointer_handoffs = 0;
        uint32_t fn =
            imports_alloc_trampoline("test", "pointer_handoffs", fake_pointer_handoff_thread, 1);
        uint32_t worker = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, fn, 0, 0, 0});
        g_idle_calls = 0;
        g_idle_total = 0;
        host_set_idle_waiter(test_idle_waiter);
        const double started = wall_seconds();
        for (unsigned i = 0; i < 1000 && g_pointer_handoffs < 100; ++i)
            host_guest_yield();
        const double elapsed = wall_seconds() - started;
        host_set_idle_waiter(nullptr);
        check(g_pointer_handoffs == 100, "all 100 pointer worker handoffs ran");
        check(g_idle_calls > 0, "host events were serviced while waiting for the worker");
        check(g_idle_total == 0,
              "baton handoffs requested no uninterruptible host sleep "
              "(requested %.1f ms, elapsed %.1f ms)",
              g_idle_total * 1000, elapsed * 1000);
        poll_exit_code(c, worker, pcode, 400);
    }

    // --- a host signal wakes the RUN thread, on both waiting paths ----------
    // The run thread is the one that services the window system, so it is the
    // one whose wait matters most. It has to come back when the signal lands
    // on either path: the condition variable when no host is attached, and the
    // sliced host waiter when one is.
    for (int with_host = 0; with_host < 2; ++with_host) {
        g_idle_calls = 0;
        g_idle_total = 0.0;
        if (with_host)
            host_set_idle_waiter(test_idle_waiter);

        uint32_t ev = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 0, 0, 0});
        OsThread *sig = os_thread_create(host_signal_thread, (void *)(uintptr_t)ev, 0);

        double t0 = wall_seconds();
        uint32_t r = call_import(c, "KERNEL32.dll", "WaitForSingleObject", {ev, 5000});
        double took = wall_seconds() - t0;
        os_thread_join(sig);

        check(r == 0, "%s: the run thread's wait was satisfied by the host signal",
              with_host ? "with a host waiter" : "with no host waiter");
        check(took < 1.0,
              "%s: and it came back when the signal landed, not on its "
              "5 s timeout (%.0f ms)",
              with_host ? "with a host waiter" : "with no host waiter", took * 1000.0);
        if (with_host)
            check(g_idle_calls > 0, "the run thread went through the host waiter (%u calls)",
                  g_idle_calls);
        host_set_idle_waiter(nullptr);
    }

    // --- a worker's ExitProcess reaches a main thread blocked indefinitely --
    // Only the main thread can perform the exit, because the landing pad is on
    // its stack. An INFINITE wait must not swallow it.
    {
        uint32_t dead = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, 0});
        uint32_t xfn =
            imports_alloc_trampoline("test", "exitprocess_thread", fake_exitprocess_thread, 1);
        g_exiting_started = 0;
        call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, xfn, 0, 0, 0});
        bool came_back = false;
        if (setjmp(*process_exit_jmp()) == 0) {
            // Nothing will ever signal this event; only the pending exit can
            // end the wait.
            call_import(c, "KERNEL32.dll", "WaitForSingleObject", {dead, 0xffffffffu});
        } else {
            came_back = true;
        }
        check(came_back, "an INFINITE wait was ended by a worker's ExitProcess");
        check(g_exiting_started == 1, "and the worker is the one that asked for it");
        check(process_exited() && process_exit_code() == 0x2b, "the exit code came through");
    }
}

static uint32_t thunk_hits, thunk_return, thunk_ecx, thunk_arg, thunk_arg0;
static void thunk_probe(X86 *c) {
    ++thunk_hits;
    thunk_return = rd32(c->r[R_ESP]);
    thunk_ecx = c->r[R_ECX];
    thunk_arg = arg(c, 1);
    thunk_arg0 = arg(c, 0);
    set_eax(c, 0x600d);
}

static void test_guest_thunks(X86 *c) {
    section("runtime-generated guest thunks");
    uint32_t code = heap_alloc(256, true);
    uint32_t probe = imports_alloc_trampoline("test", "thunk_probe", thunk_probe, 0);
    uint32_t sp = c->r[R_ESP];
    thunk_hits = 0;
    wr8(code, 0xe8);
    wr32(code + 1, probe - (code + 5));
    wr32(code + 5, 0x12345678);
    wr32(code + 9, 0x87654321);
    recomp_call(c, code);
    check(thunk_hits == 1 && thunk_return == code + 5,
          "heap CALL reaches trampoline with return pointing to its record");
    check(c->r[R_ESP] == sp, "CALL trampoline consumes only its pushed return");
    c->r[R_ESP] = sp;

    // The shared Delphi stub pops the record address before tail dispatch.
    wr32(code + 1, 32 - 5);
    wr8(code + 32, 0x59);
    wr8(code + 33, 0xe9);
    wr32(code + 34, probe - (code + 38));
    c->r[R_ESP] = sp - 4;
    wr32(sp - 4, g_fake_ret);
    recomp_call(c, code);
    check(thunk_hits == 2 && thunk_ecx == code + 5 && thunk_return == g_fake_ret,
          "CALL/POP/JMP preserves the outer return and supplies the record in ECX");
    c->r[R_ESP] = sp;

    wr8(code + 64, 0xe9);
    wr32(code + 65, probe - (code + 69));
    c->r[R_ESP] = sp - 4;
    wr32(sp - 4, g_fake_ret);
    recomp_call(c, code + 64);
    check(thunk_hits == 3 && thunk_return == g_fake_ret && c->r[R_ESP] == sp,
          "heap JMP tail-dispatches without pushing another return");
    c->r[R_ESP] = sp;

    uint32_t cls = put_str("ThunkWindow"), wc = scratch_block(40);
    uint32_t wndprobe = imports_alloc_trampoline("test", "thunk_wndproc", thunk_probe, 4);
    wr32(wc + 4, imports_resolve("USER32.dll", "DefWindowProcW"));
    wr32(wc + 36, cls);
    call_import(c, "USER32.dll", "RegisterClassA", {wc});
    uint32_t hwnd = call_import(c, "USER32.dll", "CreateWindowExA",
                                {0, cls, cls, 0, 0, 0, 64, 64, 0, 0, IMAGE_BASE, 0});
    wr32(code + 34, wndprobe - (code + 38));
    call_import(c, "USER32.dll", "SetWindowLongW", {hwnd, uint32_t(-4), code});
    uint32_t hits = thunk_hits;
    uint32_t result = call_import(c, "USER32.dll", "SendMessageW", {hwnd, 0x8001, 7, 9});
    check(result == 0x600d && thunk_hits == hits + 1 && thunk_arg == 0x8001 &&
              thunk_ecx == code + 5,
          "SendMessageW executes the heap WNDPROC through the shared dispatch path");
    call_import(c, "USER32.dll", "DestroyWindow", {hwnd});

    // A loop must be bounded, in both of the places that walk guest code.
    // recomp_run_thunk's prober gives up on `jmp $` after its sixteen steps;
    // the interpreter then decodes it cleanly - the JMP is the routine's last
    // instruction and nothing branches past it - and only its step budget
    // ends the run. Before that budget existed this call never returned. The
    // loop writes nothing, so the guard word below the stack also says the
    // run stopped where it stood rather than walking anywhere.
    wr8(code + 96, 0xeb);
    wr8(code + 97, 0xfe);
    c->r[R_ESP] = sp - 4;
    wr32(sp - 4, g_fake_ret);
    wr32(sp - 8, 0xaabbccdd);
    hits = thunk_hits;
    recomp_unknown_call(c, code + 96);
    check(thunk_hits == hits && c->r[R_ESP] == sp && c->eip == g_fake_ret &&
              rd32(sp - 8) == 0xaabbccdd && strstr(interp_last_error(), "no RET within") != nullptr,
          "a heap routine that jumps to itself stops on the step budget");
    c->r[R_ESP] = sp;

    // An unsuccessful prefix must not leave a PUSH. recomp_run_thunk follows
    // the leading `push imm32` for real, then stops on fld [esi+4], and has
    // to undo the word it wrote; the interpreter refuses the routine outright
    // for the same fld, since it decodes all of it before running any of it,
    // so it adds no writes of its own. The guard word says both held.
    wr8(code + 96, 0x68);
    wr32(code + 97, 0x11223344);
    wr8(code + 101, 0xd9);
    wr8(code + 102, 0x46);
    wr8(code + 103, 0x04);
    wr8(code + 104, 0xc3);
    c->r[R_ESP] = sp - 4;
    wr32(sp - 4, g_fake_ret);
    wr32(sp - 8, 0xaabbccdd);
    hits = thunk_hits;
    recomp_unknown_call(c, code + 96);
    check(thunk_hits == hits && c->r[R_ESP] == sp && rd32(sp - 8) == 0xaabbccdd,
          "an undecodable routine leaves no speculative PUSH before the unknown-call fallback");
    c->r[R_ESP] = sp;
    // The observed audio-bank prefix is outside the small thunk prober, but
    // the production unknown-call path must still execute it in the existing
    // integer interpreter. Verify its helper call, write, RET and saved ESI.
    const uint8_t bank[] = {0x56, 0x8b, 0x74, 0x24, 0x08, 0x56, 0xe8, 0,    0,   0,
                            0,    0x89, 0x46, 0x6c, 0x83, 0xc4, 0x04, 0x5e, 0xc3};
    const uint32_t block = heap_alloc(128, true);
    memcpy(gm_ptr(code + 160), bank, sizeof bank);
    wr32(code + 167, probe - (code + 171));
    const uint32_t saved_esi = c->r[R_ESI];
    c->r[R_ESI] = 0x11223344;
    c->r[R_ESP] = sp - 8;
    wr32(sp - 8, g_fake_ret);
    wr32(sp - 4, block);
    hits = thunk_hits;
    recomp_unknown_call(c, code + 160);
    check(thunk_hits == hits + 1 && thunk_arg0 == block && rd32(block + 0x6c) == 0x600d,
          "a thunk fast-path miss still executes the bank helper and parameter write");
    check(c->r[R_ESP] == sp - 4 && c->eip == g_fake_ret && c->r[R_ESI] == 0x11223344 &&
              interp_last_error()[0] == 0,
          "bank interpreter fallback returns normally and preserves stack and ESI");
    c->r[R_ESP] = sp;
    c->r[R_ESI] = saved_esi;
    heap_free(block);
    // Exercise the remaining opcode forms from a stack-resident thunk.
    uint32_t at = STACK_LIMIT + 0x100;
    uint8_t ops[] = {0xb8, 0,    0,    0,    0,    0x89, 0xc2, 0x8b, 0xca,
                     0xeb, 0x01, 0xcc, 0xff, 0x25, 0,    0,    0,    0};
    memcpy(g_mem + at, ops, sizeof ops);
    wr32(at + 1, 0x76543210);
    wr32(at + 14, code + 128);
    wr32(code + 128, probe);
    c->r[R_ESP] = sp - 4;
    wr32(sp - 4, g_fake_ret);
    hits = thunk_hits;
    recomp_call(c, at);
    check(thunk_hits == hits + 1 && thunk_ecx == 0x76543210 && c->r[R_EDX] == 0x76543210 &&
              c->r[R_ESP] == sp,
          "stack thunk executes MOV immediate/register, short JMP and indirect memory JMP");
    c->r[R_ESP] = sp;
    heap_free(code);
}

static void test_callbacks(X86 *c) {
    section("guest callbacks and threads");
    uint32_t fn = imports_alloc_trampoline("test", "guest_callback", fake_guest_fn, 4);
    check(fn != 0, "registered a stand-in guest function at %08x", fn);

    // A WNDPROC reached through DispatchMessageA.
    uint32_t clsname = put_str("CallbackWnd");
    uint32_t wc = scratch_block(40);
    wr32(wc + 4, fn);
    wr32(wc + 36, clsname);
    call_import(c, "USER32.dll", "RegisterClassA", {wc});
    g_callback_hits = 0;
    uint32_t hwnd = call_import(c, "USER32.dll", "CreateWindowExA",
                                {0, clsname, put_str("cb"), 0, 0, 0, 320, 200, 0, 0, 0x400000, 0});
    check(hwnd != 0, "created a window whose WNDPROC is the stand-in");
    check(g_callback_hits == 2 && g_callback_args[1] == 0x0001,
          "CreateWindowExA sent WM_NCCREATE then WM_CREATE to the WNDPROC");

    g_callback_hits = 0;
    uint32_t msg = scratch_block(28);
    // Creation geometry is queued separately from these synchronous callbacks.
    while (call_import(c, "USER32.dll", "PeekMessageA", {msg, hwnd, 0x0003, 0x0005, 1})) {
    }
    wr32(msg + 0, hwnd);
    wr32(msg + 4, 0x8001); // WM_APP+1: WM_TIMER lParam names a callback now
    wr32(msg + 8, 7);
    wr32(msg + 12, 0x1234);
    uint32_t esp_before = c->r[R_ESP];
    uint32_t result = call_import(c, "USER32.dll", "DispatchMessageA", {msg});
    check(g_callback_hits == 1 && g_callback_args[1] == 0x8001 && g_callback_args[2] == 7 &&
              g_callback_args[3] == 0x1234,
          "DispatchMessageA passed hwnd, message, wParam, lParam");
    check(result == 0x600d, "DispatchMessageA returned the WNDPROC result");
    check(c->r[R_ESP] == esp_before, "the callback left ESP where it was");
    call_import(c, "USER32.dll", "DestroyWindow", {hwnd});

    // CreateThread starts a cooperative thread: it returns first and the body
    // runs when something yields the baton, which is what Windows does and
    // what the game's own creator at 0052d580 assumes when it polls.
    g_callback_hits = 0;
    uint32_t th = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, fn, 0x2b, 0, 0});
    check(th != 0, "CreateThread returned a thread handle");
    check(g_callback_hits == 0,
          "the body has not run yet: CreateThread returns to its caller first");
    uint32_t pcode = scratch_block(4);
    check(poll_exit_code(c, th, pcode, 32) == 0x600d && g_callback_hits == 1 &&
              g_callback_args[0] == 0x2b,
          "polling GetExitCodeThread let it run with its parameter and reported its return value");
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {th, 0}) == 0,
          "a finished thread object is signalled");

    g_callback_hits = 0;
    uint32_t th2 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, fn, 0x2c, 4, 0});
    check(g_callback_hits == 0, "CREATE_SUSPENDED did not run the body");
    check(call_import(c, "KERNEL32.dll", "GetExitCodeThread", {th2, pcode}) == 1 &&
              rd32(pcode) == 0x103,
          "a thread that has not finished reports STILL_ACTIVE");
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {th2, 0}) == 0x102,
          "and waiting on it times out rather than succeeding");
    check(call_import(c, "KERNEL32.dll", "ResumeThread", {th2}) == 1,
          "ResumeThread reports the previous suspend count");
    check(g_callback_hits == 0, "ResumeThread only makes it runnable; nothing has yielded yet");
    check(poll_exit_code(c, th2, pcode, 32) == 0x600d && g_callback_hits == 1,
          "polling after ResumeThread let it run to completion");
    check(call_import(c, "KERNEL32.dll", "ResumeThread", {th2}) == 0,
          "a second ResumeThread reports no suspend count");

    // Multimedia timer callbacks fire through the same path. A fake clock keeps
    // the test independent of how long the host takes to get here. The timer
    // service thread is off so host_pump_timers is the path under test.
    setenv("RECOMP_MM_TIMER_THREAD", "0", 1);
    g_callback_hits = 0;
    g_fake_time = 1000;
    host_set_time_source(fake_clock);
    uint32_t id = call_import(c, "WINMM.dll", "timeSetEvent", {10, 0, fn, 0x99, 0});
    check(id != 0, "timeSetEvent(10 ms, one-shot) -> id %u", id);
    host_pump_timers(c);
    check(g_callback_hits == 0, "the timer does not fire before its delay elapses");
    g_fake_time = 1011;
    host_pump_timers(c);
    check(g_callback_hits == 1 && g_callback_args[0] == id && g_callback_args[2] == 0x99,
          "host_pump_timers called the guest timer callback with its id and user data");
    host_pump_timers(c);
    check(g_callback_hits == 1, "the one-shot timer did not fire twice");

    uint32_t pid = call_import(c, "WINMM.dll", "timeSetEvent", {10, 0, fn, 0, 1});
    g_fake_time = 1030;
    host_pump_timers(c);
    g_fake_time = 1045;
    host_pump_timers(c);
    check(g_callback_hits == 3, "a periodic timer fired on each elapsed period");
    call_import(c, "WINMM.dll", "timeKillEvent", {pid});
    g_fake_time = 1100;
    host_pump_timers(c);
    check(g_callback_hits == 3, "timeKillEvent stopped it");

    // TIME_CALLBACK_EVENT_SET: the "callback" is an event handle to signal.
    uint32_t ev = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, 0});
    g_callback_hits = 0;
    uint32_t eid = call_import(c, "WINMM.dll", "timeSetEvent", {5, 0, ev, 0, 0x10});
    g_fake_time = 1200;
    host_pump_timers(c);
    check(g_callback_hits == 0, "an event-mode timer does not call the handle as code");
    check(call_import(c, "KERNEL32.dll", "WaitForSingleObject", {ev, 0}) == 0,
          "it signalled the event instead");
    call_import(c, "WINMM.dll", "timeKillEvent", {eid});
    host_set_time_source(nullptr);

    // A thread has its own register file and its own guest stack, so whatever
    // the body does to the callee-saved registers is invisible to the creator.
    uint32_t clob =
        imports_alloc_trampoline("test", "clobbering_thread", fake_clobbering_thread, 1);
    c->r[R_EBX] = 0xb0;
    c->r[R_ESI] = 0x51;
    c->r[R_EDI] = 0xd1;
    c->r[R_EBP] = 0xbb;
    X86 pre = *c;
    uint32_t th4 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, clob, 0, 0, 0});
    check(poll_exit_code(c, th4, pcode, 32) == 0x5150,
          "the body's return value became the thread's exit code");
    check(c->r[R_EBX] == pre.r[R_EBX] && c->r[R_ESI] == pre.r[R_ESI] &&
              c->r[R_EDI] == pre.r[R_EDI] && c->r[R_EBP] == pre.r[R_EBP] &&
              c->r[R_ESP] == pre.r[R_ESP],
          "a thread body that returns normally leaves the creator's registers alone");
    check(c->fs_base == pre.fs_base, "and the creator keeps its own TEB");

    uint32_t th5 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, clob, 0, 4, 0});
    pre = *c;
    call_import(c, "KERNEL32.dll", "ResumeThread", {th5});
    check(poll_exit_code(c, th5, pcode, 32) == 0x5150, "so does a body started by ResumeThread");
    check(c->r[R_EBX] == pre.r[R_EBX] && c->r[R_ESI] == pre.r[R_ESI] &&
              c->r[R_EDI] == pre.r[R_EDI] && c->r[R_EBP] == pre.r[R_EBP],
          "and it too leaves the creator's registers alone");

    // Painting follows the window's update region, not the clock. Showing a
    // hidden window invalidates it; UpdateWindow then paints synchronously,
    // once, and BeginPaint validates it again.
    g_callback_hits = 0;
    uint32_t pwnd =
        call_import(c, "USER32.dll", "CreateWindowExA",
                    {0, clsname, put_str("paint"), 0, 0, 0, 320, 200, 0, 0, 0x400000, 0});
    g_callback_hits = 0;
    check(call_import(c, "USER32.dll", "UpdateWindow", {pwnd}) == 1 && g_callback_hits == 0,
          "UpdateWindow on a window with an empty update region paints nothing");
    call_import(c, "USER32.dll", "ShowWindow", {pwnd, 1});
    g_callback_hits = 0;
    check(call_import(c, "USER32.dll", "UpdateWindow", {pwnd}) == 1 && g_callback_hits == 1 &&
              g_callback_args[1] == 0x000f,
          "showing a window invalidates it and UpdateWindow sends WM_PAINT synchronously");
    // This WNDPROC never calls BeginPaint, so the region is still dirty: only
    // BeginPaint or ValidateRect clears it, and validating before the handler
    // ran would lose the paint for a window whose procedure ignores WM_PAINT.
    g_callback_hits = 0;
    check(call_import(c, "USER32.dll", "UpdateWindow", {pwnd}) == 1 && g_callback_hits == 1,
          "a WNDPROC that ignores WM_PAINT leaves the region dirty and is asked again");
    while (call_import(c, "USER32.dll", "PeekMessageA", {msg, pwnd, 0x0003, 0x0005, 1})) {
    }
    call_import(c, "USER32.dll", "DestroyWindow", {pwnd});

    // A procedure that does call BeginPaint validates it, so the next
    // UpdateWindow has nothing to do.
    uint32_t paintproc =
        imports_alloc_trampoline("test", "painting_wndproc", fake_painting_wndproc, 4);
    uint32_t pcls = put_str("PaintWnd");
    uint32_t wc3 = scratch_block(40);
    wr32(wc3 + 4, paintproc);
    wr32(wc3 + 36, pcls);
    call_import(c, "USER32.dll", "RegisterClassA", {wc3});
    g_painted = 0;
    // WS_VISIBLE in the style shows the window as part of creation.
    uint32_t vwnd =
        call_import(c, "USER32.dll", "CreateWindowExA",
                    {0, pcls, put_str("v"), 0x10000000, 0, 0, 64, 64, 0, 0, 0x400000, 0});
    check(vwnd != 0 && host_window_visible(vwnd),
          "a window created with WS_VISIBLE is visible without a separate ShowWindow");
    check(call_import(c, "USER32.dll", "UpdateWindow", {vwnd}) == 1 && g_painted == 1,
          "and it is dirty, so UpdateWindow paints it");
    check(call_import(c, "USER32.dll", "UpdateWindow", {vwnd}) == 1 && g_painted == 1,
          "BeginPaint validated the region, so the next UpdateWindow paints nothing");
    call_import(c, "USER32.dll", "InvalidateRect", {vwnd, 0, 0});
    check(call_import(c, "USER32.dll", "UpdateWindow", {vwnd}) == 1 && g_painted == 2,
          "InvalidateRect makes the next UpdateWindow paint again");
    while (call_import(c, "USER32.dll", "PeekMessageA", {msg, vwnd, 0x0003, 0x0005, 1})) {
    }
    call_import(c, "USER32.dll", "InvalidateRect", {vwnd, 0, 0});
    host_post_message(vwnd, 0x113, 99, 0);
    check(call_import(c, "USER32.dll", "PeekMessageW", {msg, vwnd, 0, 0, 1}) == 1 &&
              rd32(msg + 4) == 0x113,
          "queued timer precedes synthesized paint");
    check(call_import(c, "USER32.dll", "PeekMessageW", {msg, vwnd, 0x100, 0x109, 1}) == 0,
          "keyboard filter excludes pending paint");
    for (uint32_t remove : {0u, 0u, 1u})
        check(call_import(c, "USER32.dll", "PeekMessageW", {msg, vwnd, 0, 0, remove}) == 1 &&
                  rd32(msg) == vwnd && rd32(msg + 4) == 0xf,
              "pending paint is synthesized without validating the region (flags=%u)", remove);
    uint32_t ps = scratch_block(64);
    check(call_import(c, "USER32.dll", "BeginPaint", {vwnd, ps}) != 0 &&
              call_import(c, "USER32.dll", "EndPaint", {vwnd, ps}) == 1,
          "BeginPaint validates and EndPaint releases the DC");
    check(call_import(c, "USER32.dll", "PeekMessageW", {msg, vwnd, 0, 0, 1}) == 0,
          "validated window produces no second paint");
    call_import(c, "USER32.dll", "ShowWindow", {vwnd, 0});
    call_import(c, "USER32.dll", "InvalidateRect", {vwnd, 0, 0});
    check(call_import(c, "USER32.dll", "PeekMessageW", {msg, vwnd, 0xf, 0xf, 1}) == 0,
          "hidden window does not synthesize paint");
    call_import(c, "USER32.dll", "ShowWindow", {vwnd, 1});
    check(call_import(c, "USER32.dll", "GetMessageW", {msg, vwnd, 0xf, 0xf}) == 1 &&
              rd32(msg + 4) == 0xf,
          "GetMessage also synthesizes paint for a shown window");
    call_import(c, "USER32.dll", "UpdateWindow", {vwnd});
    call_import(c, "USER32.dll", "SetWindowPos", {vwnd, 0, 0, 0, 72, 72, 6});
    check(call_import(c, "USER32.dll", "PeekMessageW", {msg, vwnd, 0xf, 0xf, 1}) == 1,
          "resizing invalidates the visible client area");
    call_import(c, "USER32.dll", "UpdateWindow", {vwnd});
    while (call_import(c, "USER32.dll", "PeekMessageW", {msg, vwnd, 3, 5, 1})) {
    }
    call_import(c, "USER32.dll", "DestroyWindow", {vwnd});
    pwnd = vwnd;

    // The message waiter is asked only when nothing matches, and its answer is
    // taken at face value, so it has to be true only when something arrived.
    check(!host_messages_pending(), "the queue is empty");
    host_post_message(0, 0x0400, 0, 0);
    check(host_messages_pending(), "and not empty once something is posted");
    uint32_t drain = scratch_block(28);
    call_import(c, "USER32.dll", "PeekMessageA", {drain, 0, 0, 0, 1});
    check(!host_messages_pending(), "PeekMessage with PM_REMOVE drained it");

    // A window procedure that delegates to DefWindowProc must not have its
    // window creation cancelled.
    uint32_t defproc = imports_alloc_trampoline("test", "defproc_wndproc", fake_defproc_wndproc, 4);
    uint32_t defcls = put_str("DefProcWnd");
    uint32_t wc2 = scratch_block(40);
    wr32(wc2 + 4, defproc);
    wr32(wc2 + 36, defcls);
    call_import(c, "USER32.dll", "RegisterClassA", {wc2});
    uint32_t dhwnd = call_import(c, "USER32.dll", "CreateWindowExA",
                                 {0, defcls, put_str("d"), 0, 0, 0, 64, 64, 0, 0, 0x400000, 0});
    check(dhwnd != 0, "CreateWindowExA succeeds when WM_NCCREATE goes to DefWindowProc");
    check(call_import(c, "USER32.dll", "DefWindowProcA", {dhwnd, 0x0081, 0, 0}) == 1,
          "DefWindowProcA answers WM_NCCREATE with TRUE");
    while (call_import(c, "USER32.dll", "PeekMessageA", {msg, dhwnd, 0x0003, 0x0005, 1})) {
    }
    call_import(c, "USER32.dll", "DestroyWindow", {dhwnd});

    // WM_QUIT reaches the guest whatever the filter says.
    uint32_t qmsg = scratch_block(28);
    host_post_message(0, 0x0012 /* WM_QUIT */, 0, 0);
    check(call_import(c, "USER32.dll", "PeekMessageA", {qmsg, dhwnd, 0x0200, 0x0209, 1}) == 1 &&
              rd32(qmsg + 4) == 0x0012,
          "a message filter never suppresses WM_QUIT");

    // ExitThread ends its own thread and nothing else.
    uint32_t exiting = imports_alloc_trampoline("test", "exiting_thread", fake_exiting_thread, 1);
    X86 before = *c;
    uint32_t th3 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, exiting, 0, 0, 0});
    check(poll_exit_code(c, th3, pcode, 32) == 0x1234, "ExitThread recorded the exit code");
    check(c->r[R_ESP] == before.r[R_ESP] && c->r[R_EBX] == before.r[R_EBX] &&
              c->r[R_EBP] == before.r[R_EBP],
          "the creator's ESP, EBX and EBP survived ExitThread");
}

// _setjmp / _longjmp, driven the way generated code will drive them.
static void test_intrinsics(X86 *c) {
    section("_setjmp / _longjmp intrinsics");
    uint32_t buf = scratch_block(64);
    uint32_t esp0 = c->r[R_ESP];

    // call _setjmp(buf): the caller pushes the argument, CALL pushes the return
    // address, so ESP points at the return address on entry.
    uint32_t sp = esp0 - 4;
    wr32(sp, buf);
    sp -= 4;
    wr32(sp, g_fake_ret);
    c->r[R_ESP] = sp;

    jmp_buf *env = recomp_setjmp_prepare(c);
    int v = setjmp(*env);
    recomp_setjmp_return(c, v);
    if (v == 0) {
        check(c->r[R_EAX] == 0, "the first _setjmp return is 0");
        check(c->r[R_ESP] == esp0 - 4, "it popped the return address and left the argument");
        // Deeper code calls _longjmp(buf, 7).
        uint32_t lp = c->r[R_ESP] - 0x40;
        lp -= 4;
        wr32(lp, 7);
        lp -= 4;
        wr32(lp, buf);
        lp -= 4;
        wr32(lp, g_fake_ret);
        c->r[R_ESP] = lp;
        c->r[R_EBX] = 0xbadbad; // clobbered, must be restored by the longjmp
        recomp_longjmp(c);
        check(false, "recomp_longjmp returned, which it must never do");
    }
    check(v == 7 && c->r[R_EAX] == 7, "_longjmp(buf, 7) made _setjmp return 7");
    check(c->r[R_ESP] == esp0 - 4, "the guest stack pointer is back at the _setjmp call");
    check(c->r[R_EBX] != 0xbadbad, "the guest registers were restored from the jmp_buf");
    check(rd32(buf) == 0x4d504f50u, "the guest jmp_buf carries the runtime marker");

    // The single-call form must not silently work: it has to abort, because a
    // host setjmp taken there would belong to a frame that has already
    // returned. Checked in a child so this process survives.
    // The child re-runs this binary with --child-setjmp-abort (see main), which
    // builds the same frame and calls recomp_setjmp; an abort reads as 134.
    fflush(stdout);
    char exe[4096];
    check(os_exe_path(exe, sizeof exe) == 0, "the test knows its own path");
    const char *child_argv[] = {exe, "--child-setjmp-abort", nullptr};
    int64_t pid = 0;
    int code = -1;
    check(os_spawn(child_argv, &pid) == 0 && os_wait(pid, &code) == 0 && code == 134,
          "the single-call _setjmp intrinsic aborts instead of pretending to work (exit %d)", code);

    c->r[R_ESP] = esp0;
}

// A call the runtime cannot deliver must still consume the pushed return
// address, or every frame after it is displaced by four bytes.
static void test_undeliverable_calls(X86 *c) {
    section("undeliverable calls behave as a RET");
    uint32_t esp0 = c->r[R_ESP];

    uint32_t sp = esp0 - 4;
    wr32(sp, 0x00401234);
    c->r[R_ESP] = sp;
    recomp_unknown_call(c, 0x00abcdef);
    check(c->r[R_ESP] == esp0 && c->eip == 0x00401234 && c->r[R_EAX] == 0,
          "recomp_unknown_call popped the return address and resumed at %08x", c->eip);

    sp = esp0 - 4;
    wr32(sp, 0x00405678);
    c->r[R_ESP] = sp;
    recomp_unknown_call(c, 0x0fdfff00); // the callback return sentinel
    check(c->r[R_ESP] == esp0 && c->eip == 0x00405678,
          "so does a call to the callback return sentinel");

    sp = esp0 - 4;
    wr32(sp, 0x00409abc);
    c->r[R_ESP] = sp;
    recomp_shim_call(c, 0x0ff00000 + 16 * 4000); // in range, never allocated
    check(c->r[R_ESP] == esp0 && c->eip == 0x00409abc && c->r[R_EAX] == 0,
          "so does a shim call to an unallocated trampoline");

    c->r[R_ESP] = esp0;
}

static void test_startup_apis(X86 *c) {
    section("startup API calling conventions and failures");
    check(call_import(c, "KERNEL32.dll", "GetSystemDefaultLCID", {}) == 0x409,
          "system locale matches the virtual Windows locale");
    uint32_t out = scratch_block(32), size = scratch_block(4);
    wr32(size, 2);
    wr32(out, 0xabababab);
    check(call_import(c, "ADVAPI32.dll", "GetUserNameA", {out, size}) == 0 && rd32(size) == 7 &&
              rd32(out) == 0xabababab && get_last_error() == 122,
          "user name reports required size without truncating");
    check(call_import(c, "ADVAPI32.dll", "GetUserNameA", {out, size}) == 1 &&
              gm_str(out) == "Player" && rd32(size) == 7,
          "user name includes terminator in returned size");
    check(call_import(c, "ADVAPI32.dll", "GetUserNameA", {out, 0}) == 0 && get_last_error() == 87,
          "user name rejects an invalid size pointer");
    wr32(out, 0xdeadbeef);
    check(call_import(c, "AVIFIL32.dll", "AVIFileOpenA", {out, 0, 0, 0}) == 0x80040154u &&
              rd32(out) == 0,
          "unavailable AVI codec returns failure and no interface");
    check(call_import(c, "_INMM.dll", "mciSendCommandA", {0, 0x803, 0, out}) == 266,
          "MCI forwarding reports no device and preserves the caller's stack");
    for (const char *name : {"_missing@0", "_missing@8", "_missing@16"}) {
        uint32_t tramp = imports_alloc_trampoline("abi-test.dll", name, nullptr, ARGC_UNKNOWN);
        uint8_t count = name[9] == '0' ? 0 : name[9] == '8' ? 2 : 4;
        check(imports_argc(tramp) == count, "decorated stdcall arity: %s", name);
        if (count == 0)
            call_import(c, "abi-test.dll", name, {});
        else if (count == 2)
            call_import(c, "abi-test.dll", name, {1, 2});
        else
            call_import(c, "abi-test.dll", name, {1, 2, 3, 4});
    }
    uint32_t cdecl = imports_alloc_trampoline("abi-test.dll", "_explicit@8", nullptr, ARGC_CDECL);
    check(imports_argc(cdecl) == ARGC_CDECL, "explicit signature overrides decorated spelling");
}

// These deliberately unknown trampolines test rejection, not the game's import
// coverage. Run them after that coverage assertion so they cannot contaminate it.
static void test_rejected_import_decorations() {
    section("rejected import decorations");
    for (const char *name : {"_bad@3", "_bad@", "_bad@8x", "_bad@99999999999", "@fast@8"}) {
        uint32_t tramp = imports_alloc_trampoline("abi-test.dll", name, nullptr, ARGC_UNKNOWN);
        check(imports_argc(tramp) == ARGC_UNKNOWN, "reject ambiguous decoration: %s", name);
    }
}

static void test_registry(X86 *c) {
    section("registry round trip");
    printf("  using %s\n", registry_path().c_str());
    os_unlink(registry_path().c_str());
    registry_load();

    // Roots exist even with an empty profile. Null/empty subkeys reopen the
    // predefined handle for both encodings; nonexistent children still fail.
    uint32_t root_out = scratch_block(4), empty = scratch_block(4);
    wr32(empty, 0);
    for (const char *api : {"RegOpenKeyExA", "RegOpenKeyExW"}) {
        for (uint32_t name : {0u, empty}) {
            wr32(root_out, 0);
            check(call_import(c, "ADVAPI32.dll", api, {0x80000002u, name, 0, 0x20019, root_out}) ==
                          0 &&
                      rd32(root_out) == 0x80000002u,
                  "%s null/empty subkey reopens HKLM without stored values", api);
            call_import(c, "ADVAPI32.dll", "RegCloseKey", {rd32(root_out)});
        }
    }
    check(call_import(c, "ADVAPI32.dll", "RegOpenKeyExA",
                      {0x80000002u, put_str("MissingRootChild"), 0, 0, root_out}) == 2,
          "a missing root child still fails");
    check(call_import(c, "ADVAPI32.dll", "RegOpenKeyExA", {0x12345678u, 0, 0, 0, root_out}) == 6,
          "an empty subkey does not validate an invalid handle");

    uint32_t sub = put_str("Software\\RecompTests\\Registry");
    uint32_t phk = scratch_block(4), pdisp = scratch_block(4);
    uint32_t rc = call_import(c, "ADVAPI32.dll", "RegCreateKeyExA",
                              {0x80000002u, sub, 0, 0, 0, 0xf003f, 0, phk, pdisp});
    check(rc == 0, "RegCreateKeyExA(HKLM\\Software\\RecompTests\\Registry) -> %u", rc);
    uint32_t hk = rd32(phk);
    check(rd32(pdisp) == 1, "the key was created, not opened");

    uint32_t vname = put_str("InstallPath");
    uint32_t vdata = put_str(RECOMP_GUEST_ROOT);
    check(call_import(c, "ADVAPI32.dll", "RegSetValueExA",
                      {hk, vname, 0, 1, vdata, sizeof(RECOMP_GUEST_ROOT)}) == 0,
          "RegSetValueExA(REG_SZ)");
    uint32_t dname = put_str("Detail");
    uint32_t ddata = scratch_block(4);
    wr32(ddata, 3);
    check(call_import(c, "ADVAPI32.dll", "RegSetValueExA", {hk, dname, 0, 4, ddata, 4}) == 0,
          "RegSetValueExA(REG_DWORD)");
    check(call_import(c, "ADVAPI32.dll", "RegCloseKey", {hk}) == 0, "RegCloseKey");

    OsStat st{};
    check(os_stat(registry_path().c_str(), &st) == 0 && st.size > 0, "%s was written (%lld bytes)",
          registry_path().c_str(), (long long)st.size);

    // Reload from disk and read the values back.
    registry_load();
    uint32_t phk2 = scratch_block(4);
    check(call_import(c, "ADVAPI32.dll", "RegOpenKeyExA", {0x80000002u, sub, 0, 0x20019, phk2}) ==
              0,
          "RegOpenKeyExA after reload");
    uint32_t hk2 = rd32(phk2);
    uint32_t ptype = scratch_block(4), pbuf = scratch_block(sizeof(RECOMP_GUEST_ROOT) + 64),
             pcb = scratch_block(4);
    wr32(pcb, sizeof(RECOMP_GUEST_ROOT) + 64);
    check(call_import(c, "ADVAPI32.dll", "RegQueryValueExA", {hk2, vname, 0, ptype, pbuf, pcb}) ==
              0,
          "RegQueryValueExA(InstallPath)");
    check(rd32(ptype) == 1 && gm_str(pbuf) == RECOMP_GUEST_ROOT,
          "value survived the round trip: type %u \"%s\"", rd32(ptype), gm_str(pbuf).c_str());
    wr32(pcb, sizeof(RECOMP_GUEST_ROOT) + 64);
    check(call_import(c, "ADVAPI32.dll", "RegQueryValueExA", {hk2, dname, 0, ptype, pbuf, pcb}) ==
              0,
          "RegQueryValueExA(Detail)");
    check(rd32(ptype) == 4 && rd32(pbuf) == 3, "the DWORD round tripped as %u", rd32(pbuf));

    uint32_t mixed_key = put_str("SOFTWARE\\recomptests\\REGISTRY");
    uint32_t phk3 = scratch_block(4);
    check(call_import(c, "ADVAPI32.dll", "RegOpenKeyExA",
                      {0x80000002u, mixed_key, 0, 0x20019, phk3}) == 0,
          "the key opens under a different case");
    wr32(pcb, sizeof(RECOMP_GUEST_ROOT) + 64);
    check(call_import(c, "ADVAPI32.dll", "RegQueryValueExA",
                      {rd32(phk3), put_str("installpath"), 0, ptype, pbuf, pcb}) == 0 &&
              gm_str(pbuf) == RECOMP_GUEST_ROOT,
          "so does the value name");
    call_import(c, "ADVAPI32.dll", "RegCloseKey", {rd32(phk3)});

    uint32_t missing = put_str("NoSuchValue");
    wr32(pcb, sizeof(RECOMP_GUEST_ROOT) + 64);
    check(call_import(c, "ADVAPI32.dll", "RegQueryValueExA", {hk2, missing, 0, ptype, pbuf, pcb}) ==
              2,
          "a missing value reports ERROR_FILE_NOT_FOUND");
    call_import(c, "ADVAPI32.dll", "RegCloseKey", {hk2});
}

// Every import in the PE must have a trampoline, and the stack discipline must
// hold for a zero-argument and a multi-argument shim.
static void test_import_coverage(X86 *c) {
    section("import coverage");
    // The application upgrades the loader's logging-only IAT entries by
    // registering DX after loading. Coverage must include those modules too.
    dx_register_shims();
    ExpectedImage expect;
    std::string err;
    if (check(pefile_sections(expect, err), "read PE imports for coverage: %s", err.c_str())) {
        std::set<uint32_t> trampolines, data;
        for (const auto &import : expect.imports) {
            uint32_t value = rd32(import.slot);
            if (imports_is_trampoline(value))
                trampolines.insert(value);
            else
                data.insert(value);
        }
        const std::set<std::string> delphi_dlls = {
            "oleaut32.dll", "advapi32.dll", "version.dll",  "comctl32.dll", "winspool.drv",
            "netapi32.dll", "msvcrt.dll",   "shfolder.dll", "shell32.dll",  "ole32.dll"};
        uint32_t checked = 0, missing = 0, user32_checked = 0, user32_missing = 0;
        for (const auto &import : expect.imports) {
            std::string dll = import.dll;
            std::transform(dll.begin(), dll.end(), dll.begin(),
                           [](unsigned char ch) { return char(std::tolower(ch)); });
            if (dll == "user32.dll") {
                ++user32_checked;
                if (imports_argc(rd32(import.slot)) == ARGC_UNKNOWN)
                    ++user32_missing;
            }
            // CoCreateInstance belongs to DX, which this runtime-only binary
            // does not link. Only the six COM additions are part of this task.
            const std::set<std::string> com_additions = {"OleInitialize",  "OleUninitialize",
                                                         "CoInitializeEx", "CoTaskMemAlloc",
                                                         "CoTaskMemFree",  "IsEqualGUID"};
            if (dll == "ole32.dll" && !com_additions.count(import.name))
                continue;
            if (delphi_dlls.count(dll)) {
                ++checked;
                if (imports_argc(rd32(import.slot)) == ARGC_UNKNOWN)
                    ++missing;
            }
        }
        check(user32_missing == 0, "USER32 argument counts: %u imports checked, %u unknown",
              user32_checked, user32_missing);
        check(missing == 0, "Delphi DLL argument counts: %u imports checked, %u unknown", checked,
              missing);
        check(imports_count() >= trampolines.size(),
              "%u trampolines allocated; the IAT references %zu distinct trampolines",
              imports_count(), trampolines.size());
        check(imports_data_count() >= data.size(),
              "%u data symbols registered; the IAT references %zu distinct data symbols",
              imports_data_count(), data.size());
    }

    uint32_t esp_before = c->r[R_ESP];
    call_import(c, "KERNEL32.dll", "GetVersion", {});
    check(c->r[R_ESP] == esp_before, "a 0-argument shim leaves ESP unchanged");
    call_import(c, "KERNEL32.dll", "WideCharToMultiByte", {0, 0, 0, 0, 0, 0, 0, 0});
    check(c->r[R_ESP] == esp_before, "an 8-argument shim pops all its arguments");

    // A logging-only import still balances the stack.
    call_import(c, "WINMM.dll", "midiOutShortMsg", {0, 0});
    check(c->r[R_ESP] == esp_before, "a logging-only shim pops its arguments too");

    // Representative previously unknown APIs: three, six and nine arguments.
    // No networking/device support is asserted; only the production ABI boundary.
    call_import(c, "KERNEL32.dll", "WaitCommEvent", {0, 0, 0});
    call_import(c, "WS2_32.dll", "ord20", {0, 0, 0, 0, 0, 0});
    call_import(c, "TAPI32.dll", "lineOpen", {0, 0, 0, 0, 0, 0, 0, 0, 0});
    check(c->r[R_ESP] == esp_before,
          "unavailable serial, Winsock ordinal and TAPI calls preserve the caller stack");

    // WS2_32 byte order (ordinals 8, 9, 14, 15 and by name; NFS Underground): swapped values, one argument popped.
    check(call_import(c, "WS2_32.dll", "ord8", {0x11223344u}) == 0x44332211u, "htonl (ordinal 8) swaps four bytes");
    check(call_import(c, "WS2_32.dll", "ord9", {0xabcd1234u}) == 0x3412u, "htons (ordinal 9) swaps the low two");
    check(call_import(c, "WS2_32.dll", "ntohl", {0x44332211u}) == 0x11223344u, "ntohl is htonl");
    check(call_import(c, "WS2_32.dll", "ord15", {0x3412u}) == 0x1234u, "ntohs (ordinal 15) is htons");
    check(c->r[R_ESP] == esp_before, "the byte-order shims pop their argument");

    uint32_t impl = 0, log_only = 0, unknown = 0;
    imports_coverage(&impl, &log_only, &unknown);
    check(impl + log_only == imports_count(), "%u implemented, %u logging-only", impl, log_only);
    check(unknown == 0, "%u imports have an unknown argument count", unknown);
    if (unknown) {
        // Reuse the runtime's classification rather than duplicating its shim
        // registry in this test. Limit the diagnostic to the first 40 names.
        FILE *coverage = tmpfile();
        if (check(coverage != nullptr, "opened import-coverage diagnostic buffer")) {
            imports_dump_coverage(coverage);
            rewind(coverage);
            char line[1024];
            bool names = false;
            unsigned printed = 0;
            printf("  first %u unknown dll!name pairs:\n", std::min(unknown, 40u));
            while (fgets(line, sizeof line, coverage)) {
                if (strncmp(line, "imports with an unknown stdcall argument count", 45) == 0) {
                    names = true;
                } else if (names) {
                    if (line[0] != ' ')
                        break;
                    if (printed++ < 40)
                        fputs(line, stdout);
                }
            }
            check(printed == unknown, "coverage diagnostic names all %u unknown imports", unknown);
            fclose(coverage);
        }
    }
    imports_dump_stats(stdout);
}

// ---------------------------------------------------------------------------
// The mod seams. No mods module is linked here, so the weak defaults must be
// exactly what an unmodded build does, and the file seam must be driven by a
// resolver the test installs itself.
// ---------------------------------------------------------------------------
static std::vector<std::pair<std::string, int>> g_seam_calls;
static std::string g_seam_root;

static int test_resolver(const char *relative, int op, char *out, size_t out_len) {
    g_seam_calls.push_back({relative, op});
    // Reads come from a "mod" directory, everything that writes from a
    // "profile" directory: that is the shape the overlay has, and it is what
    // the shim's operation classification has to produce.
    std::string dir = (op == WIN32_FILE_READ || op == WIN32_FILE_LIST) ? g_seam_root + "/read"
                                                                       : g_seam_root + "/write";
    std::string full = dir + "/" + relative;
    if (op == WIN32_FILE_READ) {
        OsStat st;
        if (os_stat(full.c_str(), &st) != 0)
            return 0;
    }
    if (full.size() + 1 > out_len)
        return 0;
    memcpy(out, full.c_str(), full.size() + 1);
    return 1;
}

static void test_lister(const char *dir, void (*emit)(void *, const char *, const char *),
                        void *ctx) {
    std::string host = g_seam_root + "/read/" + dir;
    emit(ctx, "one.txt", (host + "/one.txt").c_str());
    emit(ctx, "two.txt", (host + "/two.txt").c_str());
}

// Runs `fn` on a real guest thread and waits for it to end. The scheduler only
// lets a guest thread run while this one is at a yield point, so polling
// GetExitCodeThread is both the wait and the thing that lets it run.
static uint32_t g_run_on_guest_slot = 0;
static void (*g_run_on_guest_fn)() = nullptr;
static void run_on_guest_body(X86 *c) {
    if (g_run_on_guest_fn)
        g_run_on_guest_fn();
    set_eax(c, 1);
}

static void run_on_guest_thread(X86 *c, void (*fn)()) {
    g_run_on_guest_fn = fn;
    if (!g_run_on_guest_slot)
        g_run_on_guest_slot =
            imports_alloc_trampoline("test", "run_on_guest", run_on_guest_body, 1);
    uint32_t pcode = scratch + 0xc00;
    uint32_t th =
        call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, g_run_on_guest_slot, 0, 0, 0});
    poll_exit_code(c, th, pcode, 4096);
    call_import(c, "KERNEL32.dll", "CloseHandle", {th});
    g_run_on_guest_fn = nullptr;
}

// ExitProcess on the main thread ends every other guest thread. Windows runs
// no further user code on them, and the guest has just freed what they were
// working with, so a worker resuming afterwards runs against torn-down state.
// Run in a child: performing the exit is not something this process recovers
// from. The child returns 0 when no worker advanced after the exit, 3 when one
// did, and 2 when its own setup failed.
static int child_exit_stops_workers() {
    mem_init();
    imports_init();
    if (!loader_load(nullptr))
        return 2;
    X86 *c = loader_context();
    g_exit_worker_loops = 0;
    g_exit_worker_event = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, 0});
    uint32_t fn = imports_alloc_trampoline("test", "exit_worker", fake_exit_worker, 1);
    if (!call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, fn, 0, 0, 0}))
        return 2;
    // Let it get going, so "it did not advance" means the exit stopped it and
    // not that it never started.
    double t0 = wall_seconds();
    while (g_exit_worker_loops < 2 && wall_seconds() - t0 < 2.0)
        call_import(c, "KERNEL32.dll", "GetTickCount", {});
    if (g_exit_worker_loops < 2)
        return 2;

    if (setjmp(*process_exit_jmp()) == 0) {
        call_import(c, "KERNEL32.dll", "ExitProcess", {0});
        return 2; // ExitProcess must not return
    }
    if (!process_exited())
        return 2;
    // The run thread retires and the shutdown drive runs, exactly as a host's
    // teardown does. Nothing here may put the worker back on a guest
    // instruction, so its counter must stand still.
    uint32_t after_exit = g_exit_worker_loops;
    sched_run_thread_finished();
    bool stopped = sched_drive_until_stopped(2.0);
    sched_drive_release();
    t0 = wall_seconds();
    while (wall_seconds() - t0 < 0.25)
        os_sleep_us(1000);
    if (g_exit_worker_loops != after_exit)
        return 3;           // guest code ran on the worker after the exit
    return stopped ? 0 : 4; // the worker must also END, not just stop running
}

static void test_exit_process_stops_workers(X86 *c) {
    section("ExitProcess stops guest workers");
    (void)c;
    fflush(stdout);
    char exe[4096];
    check(os_exe_path(exe, sizeof exe) == 0, "the test knows its own path");
    const char *child_argv[] = {exe, "--child-exit-stops-workers", nullptr};
    int64_t pid = 0;
    int code = -1;
    check(os_spawn(child_argv, &pid) == 0 && os_wait(pid, &code) == 0 && code == 0,
          "after ExitProcess no guest code ran on a worker and every worker ended "
          "(child exit %d)",
          code);
}

static void test_mod_seams(X86 *c) {
    section("mod seams");

    // The weak defaults are no-ops that report "nothing installed".
    check(mods_load_all(), "mods_load_all defaults to success with no module");
    check(std::string(mods_active_callback_desc()).empty(),
          "no active mod callback without the module");
    check(!mods_input_key(0x44, 0x79, 1), "input is never consumed without the module");
    uint8_t *px = nullptr;
    uint32_t bytes = 0;
    check(!mods_texture_override(1, 2, 2, 0, &px, &bytes),
          "no texture override without the module");
    mods_hooks_unwind_to_esp(0); // must not crash

    // The file seam classifies each operation, and a write never resolves
    // through the read tier.
    g_seam_root = "build/recomp/seam-test";
    remove_tree(g_seam_root);
    mkdir_p(g_seam_root + "/read/data");
    mkdir_p(g_seam_root + "/write/data");
    FILE *f = fopen((g_seam_root + "/read/data/shared.txt").c_str(), "wb");
    fputs("read tier", f);
    fclose(f);
    win32_set_file_ops(test_resolver, test_lister);

    g_seam_calls.clear();
    std::string r = win32_host_path_op("data\\shared.txt", WIN32_FILE_READ);
    check(r == g_seam_root + "/read/data/shared.txt", "a read resolves through the read tier");
    check(g_seam_calls.size() == 1 && g_seam_calls[0].first == "data/shared.txt",
          "the resolver sees a normalised, separator-free relative path");

    std::string w = win32_host_path_op("data\\shared.txt", WIN32_FILE_WRITE);
    check(w == g_seam_root + "/write/data/shared.txt",
          "a write resolves through the write tier, not the read tier");

    // CreateFileA with GENERIC_WRITE and OPEN_EXISTING opens through the READ
    // tier and defers the write classification to the first WriteFile. A game
    // opens its archives read/write and never writes them; classifying the
    // open as a write copied every archive into the profile. The write tier is
    // resolved when a byte is actually written, and the handle continues at
    // the offset it had reached; the read tier is never written.
    g_seam_calls.clear();
    uint32_t name = put_str("data\\shared.txt");
    uint32_t hw = call_import(c, "KERNEL32.dll", "CreateFileA",
                              {name, 0xC0000000u, 0, 0, 3 /*OPEN_EXISTING*/, 0, 0});
    check(hw != 0xffffffffu && !g_seam_calls.empty() &&
              g_seam_calls.back().second == WIN32_FILE_READ,
          "read/write OPEN_EXISTING opens through the read tier");
    uint32_t rbuf = scratch + 0x900, got = scratch + 0x9f0;
    check(call_import(c, "KERNEL32.dll", "ReadFile", {hw, rbuf, 4, got, 0}) == 1 &&
              rd32(got) == 4 && memcmp(g_mem + rbuf, "read", 4) == 0,
          "and reads the read tier's bytes");
    g_seam_calls.clear();
    uint32_t wdata = put_str("WXYZ");
    check(call_import(c, "KERNEL32.dll", "WriteFile", {hw, wdata, 4, got, 0}) == 1 &&
              rd32(got) == 4,
          "the first WriteFile on it succeeds");
    check(!g_seam_calls.empty() && g_seam_calls.back().second == WIN32_FILE_WRITE,
          "and is what resolves the write tier");
    call_import(c, "KERNEL32.dll", "CloseHandle", {hw});
    {
        FILE *wf = fopen((g_seam_root + "/write/data/shared.txt").c_str(), "rb");
        check(wf != nullptr, "the write tier now holds the file");
        if (wf) {
            char b[8] = {0};
            fseek(wf, 4, SEEK_SET);
            size_t n = fread(b, 1, 4, wf);
            fclose(wf);
            check(n == 4 && memcmp(b, "WXYZ", 4) == 0,
                  "the bytes landed at the offset the handle had reached");
        }
        FILE *rf = fopen((g_seam_root + "/read/data/shared.txt").c_str(), "rb");
        char rb[16] = {0};
        size_t rn = rf ? fread(rb, 1, 9, rf) : 0;
        if (rf)
            fclose(rf);
        check(rn == 9 && memcmp(rb, "read tier", 9) == 0, "the read tier is untouched");
    }
    // A write-only OPEN_EXISTING that never writes touches no tier either.
    g_seam_calls.clear();
    uint32_t hwo = call_import(c, "KERNEL32.dll", "CreateFileA",
                               {name, 0x40000000u, 0, 0, 3 /*OPEN_EXISTING*/, 0, 0});
    check(hwo != 0xffffffffu && g_seam_calls.back().second == WIN32_FILE_READ,
          "write-only OPEN_EXISTING is classified at the first write, not the open");
    call_import(c, "KERNEL32.dll", "CloseHandle", {hwo});

    g_seam_calls.clear();
    call_import(c, "KERNEL32.dll", "DeleteFileA", {name});
    check(!g_seam_calls.empty() && g_seam_calls.back().second == WIN32_FILE_DELETE,
          "DeleteFileA is classified as a delete");

    // FindFirstFile takes the host path from the lister, so a match living in
    // another tier still reports the right metadata.
    uint32_t pattern = put_str("data\\*.txt");
    uint32_t data = scratch + 0x800;
    uint32_t h = call_import(c, "KERNEL32.dll", "FindFirstFileA", {pattern, data});
    check(h != 0xffffffffu, "FindFirstFileA found a match through the lister");
    check(gm_str(data + 44) == std::string("one.txt"), "the first match is the lister's");
    check(call_import(c, "KERNEL32.dll", "FindNextFileA", {h, data}) == 1 &&
              gm_str(data + 44) == std::string("two.txt"),
          "the second match follows");
    call_import(c, "KERNEL32.dll", "FindClose", {h});

    win32_set_file_ops(nullptr, nullptr);
    check(win32_host_path("data\\shared.txt").empty() ||
              win32_host_path("data\\shared.txt").find(win32_game_dir()) == 0,
          "with no resolver the shim resolves in the game directory as before");

    // With no resolver installed, a rename or a copy to a filename that does
    // not exist yet has to keep working: that is the ordinary case, and the
    // seam is not allowed to change it. Both go through the game directory.
    {
        std::string dir = win32_game_dir();
        std::string src = dir + "/seam-src.tmp";
        FILE *sf = fopen(src.c_str(), "wb");
        check(sf != nullptr, "created a source file in the game directory");
        if (sf) {
            fputs("payload", sf);
            fclose(sf);
        }
        win32_invalidate_dir_cache();
        os_unlink((dir + "/seam-copy.tmp").c_str());
        os_unlink((dir + "/seam-moved.tmp").c_str());

        uint32_t from = put_str("seam-src.tmp");
        uint32_t cto = put_str("seam-copy.tmp");
        check(call_import(c, "KERNEL32.dll", "CopyFileA", {from, cto, 0}) == 1,
              "unmodded CopyFileA creates a destination that did not exist");
        OsStat st{};
        check(os_stat((dir + "/seam-copy.tmp").c_str(), &st) == 0, "and the copy is really there");

        win32_invalidate_dir_cache();
        uint32_t mto = put_str("seam-moved.tmp");
        check(call_import(c, "KERNEL32.dll", "MoveFileA", {from, mto}) == 1,
              "unmodded MoveFileA renames to a destination that did not exist");
        check(os_stat((dir + "/seam-moved.tmp").c_str(), &st) == 0,
              "and the renamed file is really there");

        os_unlink((dir + "/seam-copy.tmp").c_str());
        os_unlink((dir + "/seam-moved.tmp").c_str());
        os_unlink((dir + "/seam-src.tmp").c_str());
        win32_invalidate_dir_cache();
    }

    // Finding 4: the guest root is the empty string to BOTH callbacks. A "."
    // component is exactly what the contract says the resolver never sees.
    {
        static std::vector<std::string> seen_dirs;
        seen_dirs.clear();
        g_seam_calls.clear();
        win32_set_file_ops(
            [](const char *rel, int op, char *out, size_t n) -> int {
                g_seam_calls.push_back({rel, op});
                std::string full = g_seam_root + "/read/" + rel;
                if (full.size() + 1 > n)
                    return 0;
                memcpy(out, full.c_str(), full.size() + 1);
                return 1;
            },
            [](const char *dir, void (*emit)(void *, const char *, const char *), void *ctx) {
                seen_dirs.push_back(dir);
                emit(ctx, "root.txt", (g_seam_root + "/read/root.txt").c_str());
            });
        win32_host_path_op(RECOMP_GUEST_ROOT, WIN32_FILE_READ);
        check(!g_seam_calls.empty() && g_seam_calls.back().first.empty(),
              "the guest root reaches the resolver as the empty string");
        uint32_t rootpat = put_str("*.txt");
        uint32_t rh = call_import(c, "KERNEL32.dll", "FindFirstFileA", {rootpat, data});
        check(seen_dirs.size() == 1 && seen_dirs[0].empty(),
              "and the lister is given the same empty root, not \".\"");
        if (rh != 0xffffffffu)
            call_import(c, "KERNEL32.dll", "FindClose", {rh});
        win32_set_file_ops(nullptr, nullptr);
    }

    // Scheduler coordination. This test's own thread never entered the
    // scheduler, so it must say it is not a guest thread - which is the whole
    // point, because t_self would have called it thread 0.
    check(!sched_is_guest_thread(), "a plain host thread is not a guest thread");
    static bool inside = false, baton_inside = false;
    run_on_guest_thread(c, [] {
        inside = sched_is_guest_thread();
        baton_inside = sched_holds_baton();
    });
    check(inside, "a registered guest thread says so");
    check(baton_inside, "and it holds the baton while it runs");
    check(sched_guest_threads_stopped(), "and it has finished by the time we look");

    sched_registry_lock();
    sched_registry_unlock();
    static int pumped = 0;
    sched_set_checkpoint([] { ++pumped; });
    guest_sleep_ms(0); // a yield point
    check(pumped > 0, "the scheduler ran the registry checkpoint at a yield");
    sched_set_checkpoint(nullptr);

    // A finished guest thread announces its exit exactly once, which is what
    // unwinds whatever it left on the mod layer's invocation stack.
    static int exits = 0;
    static bool stopped_during_exit = true;
    static bool guest_during_exit = false, baton_during_exit = false;
    exits = 0;
    sched_set_thread_exit_observer([] {
        ++exits;
        // The whole ordering requirement, asserted from inside the exit: this
        // thread has NOT published completion yet, so a plugin unload cannot
        // have started underneath code that is still running, and it still
        // holds the baton, so this unwind is still mutually exclusive with
        // every other guest thread.
        stopped_during_exit = sched_guest_threads_stopped();
        guest_during_exit = sched_is_guest_thread();
        baton_during_exit = sched_holds_baton();
    });
    run_on_guest_thread(c, [] {});
    check(exits == 1, "a finished guest thread announced its exit once");
    check(!stopped_during_exit, "the exit ran before the thread was published as finished");
    check(guest_during_exit && baton_during_exit,
          "and while it still held the baton as a guest thread");
    sched_set_thread_exit_observer(nullptr);

    // The locked form answers the same question from under the registry lock,
    // which is the one place the ordinary form cannot be asked: both take the
    // scheduler's single non-recursive mutex, so asking there would hang. This
    // test would not return at all if that were still the only form.
    sched_set_guest_thread(true);
    check(sched_holds_baton(), "the run thread holds the baton");
    sched_registry_lock();
    bool locked_answer = sched_holds_baton_locked();
    sched_registry_unlock();
    check(locked_answer, "sched_holds_baton_locked answers under the registry lock, and returns");
    sched_set_guest_thread(false);
    // Under the lock again: this is the only way the function may ever be
    // called, and asking it unlocked would be the very misuse the locked form
    // exists to avoid - masked here by the non-guest short-circuit, which
    // returns before it would have touched anything.
    sched_registry_lock();
    bool not_guest = sched_holds_baton_locked();
    sched_registry_unlock();
    check(!not_guest, "and it agrees with the plain form for a thread that is not a guest thread");

    check(std::string(loader_exe_sha256()) == std::string(LOADER_EXPECTED_SHA256),
          "the loader reports the digest of the image it actually mapped");
}

// ---------------------------------------------------------------------------
// Queued input is runnable work, not something to sleep through.
//
// A host cannot apply input decoded during a scheduler idle slice: another
// guest thread may hold the baton. So it queues, and the queue is drained by
// the host's tick - which the guest reaches through its own clock read. A
// guest BLOCKED on an event never reads its clock, so if the thing that would
// signal that event is sitting in the queue, the wait and the drain are each
// waiting for the other and the thread waits out its whole timeout.
//
// The scheduler therefore drains the queue itself, from the one point where
// nothing else is runnable and so nothing else is in guest code.
// ---------------------------------------------------------------------------
static uint32_t g_wake_event = 0;
static int g_drain_calls = 0;
static bool g_input_queued = false;
static bool fake_input_pending() {
    return g_input_queued;
}
static void fake_input_drain() {
    ++g_drain_calls;
    g_input_queued = false;
    win32_signal_event(g_wake_event, false);
}

static void test_queued_input_wakes_a_blocked_wait(X86 *c) {
    section("queued input is runnable work");
    g_wake_event = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 0, 0, 0});
    check(g_wake_event != 0, "an auto-reset event to wait on");

    g_input_queued = true;
    g_drain_calls = 0;
    sched_set_input_queue(fake_input_pending, fake_input_drain);

    // Nothing else will signal this. Only the queued "input" will, and only if
    // the scheduler decides to drain it rather than sleep.
    double t0 = wall_seconds();
    uint32_t r = call_import(c, "KERNEL32.dll", "WaitForSingleObject", {g_wake_event, 5000});
    double waited = wall_seconds() - t0;

    check(r == 0, "the wait was satisfied rather than timing out (%u)", r);
    check(g_drain_calls >= 1, "the scheduler drained the queue itself");
    check(waited < 1.0, "and it woke in %.0f ms, not by waiting out 5000", waited * 1000.0);

    sched_set_input_queue(nullptr, nullptr);
}

// ---------------------------------------------------------------------------
// The run thread's ending hands the baton on.
//
// A worker runs only when the thread holding the baton gives it up. The run
// thread is never spawned by the scheduler, so nothing announced its ending:
// clearing the thread-local guest flag says nothing about the baton, and a
// worker waiting for it waited for a thread that had already stopped running
// guest code. A host polling for quiescence then polled until its bound
// expired and reported a hang that was really a handover that never happened.
//
// This runs LAST. It retires the main thread, and nothing that drives guest
// code should follow it.
// ---------------------------------------------------------------------------
// A worker that blocks on a finite wait. Nothing but an expiring deadline can
// wake it, which is exactly what a plain polling loop cannot provide once the
// run thread has been retired.
static uint32_t g_drive_slot = 0;
static volatile int g_drive_ran = 0;

static void drive_sleeper(X86 *c) {
    guest_sleep_ms(20);
    ++g_drive_ran;
    set_eax(c, 0x5150);
}

// A worker that sleeps for longer than the first drive's timeout, so the
// second drive begins with it already parked inside guest_block.
static uint32_t g_parked_slot = 0;
static volatile int g_parked_ran = 0;

static void parked_sleeper(X86 *c) {
    guest_sleep_ms(400);
    ++g_parked_ran;
    set_eax(c, 0x5151);
}

// A worker whose deadline expires while another worker is running. It must not
// take the baton back for itself when it wakes: it waits for the handoff.
static uint32_t g_short_slot = 0;
static volatile int g_short_ran = 0;

static void short_sleeper(X86 *c) {
    guest_sleep_ms(2);
    ++g_short_ran;
    set_eax(c, 0x5152);
}

// A worker that stays running for a while, checking throughout that it is
// still the only holder and that nothing expired deadlines underneath it.
static volatile int g_solo_violations = 0;
static volatile int g_solo_running = 0;
static volatile int g_solo_slices = 0;

static void solo_worker(X86 *c) {
    g_solo_running = 1;
    for (int i = 0; i < 200; ++i) {
        // Under the scheduler's own lock, so the answer cannot be torn.
        sched_registry_lock();
        bool mine = sched_holds_baton_locked();
        sched_registry_unlock();
        if (!mine)
            ++g_solo_violations;
        ++g_solo_slices;
        os_sleep_us(200);
    }
    g_solo_running = 0;
    set_eax(c, 0x5010);
}

static void test_run_thread_finished(X86 *c) {
    section("the run thread's ending");
    sched_set_guest_thread(true);

    // A thread that never ran guest code must not be able to retire the run
    // thread on its behalf: t_self defaults to 0 everywhere, so without the
    // pthread check this call from another thread would retire thread 0.
    std::thread([] { sched_run_thread_finished(); }).join();
    check(sched_holds_baton(),
          "a foreign thread cannot retire the run thread: this one still holds "
          "the baton");

    // The unconditional unwind touches only the calling thread's own frames,
    // so it is safe anywhere and is what a teardown path uses when its thread
    // never registered with the scheduler.
    std::thread([] { sched_run_thread_unwind_frames(); }).join();
    check(true, "the unconditional unwind runs on an unregistered thread");

    uint32_t fn = imports_alloc_trampoline("test", "guest_callback", fake_guest_fn, 4);
    g_callback_hits = 0;
    uint32_t th = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, fn, 0x5a, 0, 0});
    check(th != 0, "created a worker (%08x)", th);
    check(!sched_guest_threads_stopped(), "which has not finished yet");

    // Nothing below drives the scheduler: no shim call, no clock read, no
    // wait. The worker cannot run while this thread holds the baton, and this
    // thread is not going to ask for anything that would give it up.
    sched_run_thread_finished();
    bool stopped = false;
    for (int i = 0; i < 1000 && !stopped; ++i) {
        os_sleep_us(1000);
        stopped = sched_guest_threads_stopped();
    }
    check(stopped, "the worker finished once the run thread handed the baton on");

    // With the run thread retired, a worker that BLOCKS cannot be helped by
    // polling: the baton would go to a thread that has finished and nothing
    // would expire its deadline, so a twenty-millisecond sleep would never
    // end. Driving is the primitive that does both.
    g_drive_ran = 0;
    if (!g_drive_slot)
        g_drive_slot = imports_alloc_trampoline("test", "drive_sleeper", drive_sleeper, 1);
    uint32_t th2 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, g_drive_slot, 0, 0, 0});
    check(th2 != 0, "started a worker that blocks on a finite sleep");
    double t0 = wall_seconds();
    bool drove = sched_drive_until_stopped(2.0);
    double took = wall_seconds() - t0;
    check(drove, "driving stopped it even though the run thread had retired");
    // It comes back still registered and still holding the baton, because the
    // caller's next act is to run the mods' exit handlers and those are
    // ordinary mod code: an exit that removes its own hook needs the baton,
    // and without it the removal would be queued on the one thread left to
    // apply the queue.
    check(sched_holds_baton(), "and comes back holding the baton, for the exits");
    sched_drive_release();
    check(!sched_holds_baton(), "which it gives up when the drive is released");
    check(g_drive_ran == 1, "and it really ran its start routine (%d)", g_drive_ran);
    check(took < 1.5, "without waiting out the timeout (%.0f ms)", took * 1000.0);

    // A worker that runs for a while with the driver active alongside it. The
    // driver may not hand its baton to anyone else while it runs, and may not
    // expire deadlines underneath it: expiry satisfies waits and releases
    // mutexes the running thread may be inside, and the scheduler's mutex does
    // not protect those.
    g_solo_violations = 0;
    g_solo_slices = 0;
    static uint32_t solo_slot = 0;
    if (!solo_slot)
        solo_slot = imports_alloc_trampoline("test", "solo_worker", solo_worker, 1);
    uint32_t th3 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, solo_slot, 0, 0, 0});
    check(th3 != 0, "started a worker that runs for a while");
    check(sched_drive_until_stopped(5.0), "the driver ran it to completion");
    check(g_solo_slices > 0, "and it really ran (%d slices)", g_solo_slices);
    check(g_solo_violations == 0, "it held the baton alone throughout (%d moments it did not)",
          g_solo_violations);
    sched_drive_release();
    call_import(c, "KERNEL32.dll", "CloseHandle", {th3});

    // A worker parked in guest_block when the drive starts. The first drive
    // gives it time to get there and then times out; the second begins with it
    // already parked, which is the state the old driver misread - it treated
    // `blocked` as parked and handed the baton away while a worker was still
    // between releasing the mutex and parking.
    g_parked_ran = 0;
    if (!g_parked_slot)
        g_parked_slot = imports_alloc_trampoline("test", "parked_sleeper", parked_sleeper, 1);
    uint32_t th4 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, g_parked_slot, 0, 0, 0});
    check(th4 != 0, "started a worker that sleeps longer than a short drive");
    check(!sched_drive_until_stopped(0.05),
          "a drive that ends before the worker does returns false");
    check(g_parked_ran == 0, "and the worker is still parked, not finished");
    // Releasing after a timeout must not park the baton on this thread. It is
    // retired and will never yield again, so a worker blocked behind it would
    // be stranded for good; the baton goes to a worker that can use it or to
    // nobody, and a later drive picks it up.
    sched_drive_release();
    // The precondition it names is the caller's, not the scheduler's: the
    // worker is fine, the timeout was simply shorter than the sleep.
    check(sched_drive_until_stopped(3.0), "a second drive finds it parked and finishes it");
    check(g_parked_ran == 1, "and it ran to the end (%d)", g_parked_ran);
    sched_drive_release();
    call_import(c, "KERNEL32.dll", "CloseHandle", {th4});

    // A worker whose deadline expires while another one is running. The
    // sleeper wakes in two milliseconds, long before the runner is done, and
    // must wait for the ordinary handoff: taking the baton on waking put it
    // beside the runner and both executed guest code at once.
    g_solo_violations = 0;
    g_solo_slices = 0;
    g_short_ran = 0;
    if (!g_short_slot)
        g_short_slot = imports_alloc_trampoline("test", "short_sleeper", short_sleeper, 1);
    uint32_t th5 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, solo_slot, 0, 0, 0});
    uint32_t th6 = call_import(c, "KERNEL32.dll", "CreateThread", {0, 0, g_short_slot, 0, 0, 0});
    check(th5 != 0 && th6 != 0, "started a long runner and a short sleeper");
    check(sched_drive_until_stopped(5.0), "the driver ran both to completion");
    check(g_short_ran == 1, "the sleeper woke and finished (%d)", g_short_ran);
    check(g_solo_slices > 0, "the runner really ran (%d slices)", g_solo_slices);
    check(g_solo_violations == 0,
          "and it held the baton alone throughout, expiry or no expiry (%d "
          "moments it did not)",
          g_solo_violations);
    sched_drive_release();
    call_import(c, "KERNEL32.dll", "CloseHandle", {th5});
    call_import(c, "KERNEL32.dll", "CloseHandle", {th6});

    // Everything is stopped, so asking again is true and immediate.
    t0 = wall_seconds();
    check(sched_drive_until_stopped(2.0), "driving an already-stopped scheduler is true");
    check(wall_seconds() - t0 < 0.5, "and immediate");
    sched_drive_release();
    call_import(c, "KERNEL32.dll", "CloseHandle", {th2});
    check(g_callback_hits == 1, "and it really ran its start routine");

    // Saying it twice is not two endings.
    sched_run_thread_finished();
    check(sched_guest_threads_stopped(), "saying it again changes nothing");
}

// ---------------------------------------------------------------------------
// Input arriving while a guest thread is parked wakes it.
//
// A thread with nothing runnable to hand the baton to parks on the scheduler's
// condition with a deadline of up to a second, draining queued input first.
// Input queued AFTER it parked left it asleep with the input already in hand,
// so a keypress could take a second to be seen for no reason but the slice it
// slept through. sched_input_arrived is what ends that sleep.
// ---------------------------------------------------------------------------
static volatile int g_late_input_queued = 0;
static volatile int g_late_input_drained = 0;
static uint32_t g_late_event = 0;

static bool late_input_pending() {
    return g_late_input_queued > g_late_input_drained;
}

static void late_input_drain() {
    g_late_input_drained = g_late_input_queued;
    // What the host's drain really does: turn input into something the guest
    // is waiting for. Here that is the event, so the wait below ends exactly
    // when the drain runs and the elapsed time is the measurement.
    win32_signal_event(g_late_event, false);
}

static void test_input_wakes_a_parked_thread(X86 *c) {
    section("input arriving wakes a parked guest thread");
    sched_set_guest_thread(true);
    sched_set_input_queue(late_input_pending, late_input_drain);

    g_late_input_queued = 0;
    g_late_input_drained = 0;
    g_late_event = call_import(c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, 0});
    check(g_late_event != 0, "created an event only the drain will signal");

    // A host thread that queues input a little after this thread has parked.
    double queued_at = 0.0;
    std::thread announcer([&] {
        os_sleep_us(80000); // long enough to be parked
        queued_at = wall_seconds();
        g_late_input_queued = 1;
        sched_input_arrived();
    });

    // Nothing else is runnable, so this parks, and only the drain can end it.
    double t0 = wall_seconds();
    uint32_t r = call_import(c, "KERNEL32.dll", "WaitForSingleObject", {g_late_event, 5000});
    double ended = wall_seconds();
    announcer.join();

    check(r == 0, "the wait was satisfied rather than timing out (%u)", r);
    check(g_late_input_drained == 1, "the queued input was drained");
    check(ended - t0 < 2.0, "well inside the 5000 ms deadline (%.0f ms)", (ended - t0) * 1000.0);
    // The point of the announcement: the gap between input arriving and the
    // parked thread acting on it is a slice, not the second it would sleep.
    if (queued_at > 0.0)
        check(ended - queued_at < 0.5, "and within a slice of the input arriving (%.0f ms)",
              (ended - queued_at) * 1000.0);

    sched_set_input_queue(nullptr, nullptr);
    call_import(c, "KERNEL32.dll", "CloseHandle", {g_late_event});
    sched_set_guest_thread(false);
}

static void child_setjmp_abort(X86 *c) {
    // The single-call _setjmp form: a jmp_buf pushed, a fake return address,
    // no enclosing call. It has to abort.
    uint32_t buf2 = scratch_block(64);
    uint32_t sp = c->r[R_ESP] - 4;
    wr32(sp, buf2);
    sp -= 4;
    wr32(sp, g_fake_ret);
    c->r[R_ESP] = sp;
    recomp_setjmp(c);
    os_exit_immediately(0); // reached only if it failed to abort
}

// Wide file writes use an isolated profile seam, never the developer's inputs.
static std::vector<int> g_wide_file_ops;
static const std::string g_wide_root = "build/recomp/wide-profile-test";
static int wide_resolver(const char *relative, int op, char *out, size_t cap) {
    g_wide_file_ops.push_back(op);
    std::string path = g_wide_root + "/" + relative;
    OsStat st{};
    if (op == WIN32_FILE_READ && os_stat(path.c_str(), &st) != 0)
        return 0;
    if (path.size() + 1 > cap)
        return 0;
    memcpy(out, path.c_str(), path.size() + 1);
    return 1;
}

static std::string g_wide_enum_text;
static uint32_t g_wide_enum_calls = 0;
static void wide_enum_callback(X86 *c) {
    ++g_wide_enum_calls;
    g_wide_enum_text = gm_wstr(arg(c, 0));
    set_eax(c, 1);
}

// Both version probes consume four x86 slots; the condition mask uses EDX:EAX.
static uint64_t call_condition_mask(X86 *c, uint64_t mask, uint32_t type, uint32_t condition) {
    uint32_t saved = c->r[R_ESP], sp = saved - 20;
    wr32(sp, g_fake_ret);
    wr32(sp + 4, (uint32_t)mask);
    wr32(sp + 8, (uint32_t)(mask >> 32));
    wr32(sp + 12, type);
    wr32(sp + 16, condition);
    c->r[R_ESP] = sp;
    uint32_t tramp = imports_resolve("KERNEL32.dll", "VerSetConditionMask");
    check(tramp != 0 && imports_dispatch(c, tramp), "VerSetConditionMask dispatches");
    check(c->r[R_ESP] == saved, "VerSetConditionMask cleans all four stdcall argument slots");
    c->r[R_ESP] = saved;
    return ((uint64_t)c->r[R_EDX] << 32) | c->r[R_EAX];
}

static void test_windows_version(X86 *c) {
    section("configured Windows version");
    const bool nt = RECOMP_WINDOWS_PLATFORM == 2;
    const uint32_t build =
        nt ? RECOMP_WINDOWS_BUILD
           : (RECOMP_WINDOWS_MAJOR << 24) | (RECOMP_WINDOWS_MINOR << 16) | RECOMP_WINDOWS_BUILD;
    const uint32_t sp = RECOMP_WINDOWS_MAJOR == 6 && RECOMP_WINDOWS_MINOR == 1 ? 1 : 0;
    const char *csd = sp ? "Service Pack 1" : nt ? "" : " A ";
    uint32_t packed = call_import(c, "KERNEL32.dll", "GetVersion", {});
    check(packed == ((nt ? RECOMP_WINDOWS_BUILD << 16 : 0xc0000000u) | (RECOMP_WINDOWS_MINOR << 8) |
                     RECOMP_WINDOWS_MAJOR),
          "GetVersion packs the configured version and NT/9x platform");
    uint32_t p = scratch_block(300);
    for (bool wide : {false, true}) {
        const char *api = wide ? "GetVersionExW" : "GetVersionExA";
        uint32_t base = wide ? 276 : 148;
        for (uint32_t size : {base, base + 8}) {
            memset(g_mem + p, 0xa5, 300);
            wr32(p, size);
            check(call_import(c, "KERNEL32.dll", api, {p}) == 1 && rd32(p) == size &&
                      rd32(p + 4) == RECOMP_WINDOWS_MAJOR && rd32(p + 8) == RECOMP_WINDOWS_MINOR &&
                      rd32(p + 12) == build && rd32(p + 16) == RECOMP_WINDOWS_PLATFORM &&
                      (wide ? gm_wstr(p + 20) : gm_str(p + 20)) == csd &&
                      rd32(p + size) == 0xa5a5a5a5,
                  "%s fills size %u without overwriting its guard", api, size);
            if (size > base)
                check(rd16(p + base) == sp && rd16(p + base + 2) == 0 && rd16(p + base + 4) == 0 &&
                          rd8(p + base + 6) == 1 && rd8(p + base + 7) == 0,
                      "%s EX reports service pack and workstation product type", api);
        }
        wr32(p, base - 1);
        check(call_import(c, "KERNEL32.dll", api, {p}) == 0 &&
                  call_import(c, "KERNEL32.dll", "GetLastError", {}) == 87,
              "%s rejects an invalid structure size", api);
    }
    wr32(p, 284);
    call_import(c, "KERNEL32.dll", "GetVersionExW", {p});
    auto verify = [&](uint32_t types, uint64_t mask) {
        return call_import(c, "KERNEL32.dll", "VerifyVersionInfoW",
                           {p, types, (uint32_t)mask, (uint32_t)(mask >> 32)});
    };
    uint64_t equal = 0;
    for (uint32_t bit : {1u, 2u, 4u, 8u, 16u, 32u, 128u})
        equal = call_condition_mask(c, equal, bit, 1);
    check(verify(0xbf, equal) == 1, "VerifyVersionInfoW agrees with all GetVersionExW fields");
    wr32(p + 12, build + 1);
    check(verify(4, equal) == 0 && call_import(c, "KERNEL32.dll", "GetLastError", {}) == 1150,
          "VerifyVersionInfoW rejects a different build with ERROR_OLD_WIN_VERSION");
    for (uint32_t op = 1; op <= 5; ++op) {
        uint64_t mask = call_condition_mask(c, 0, 4, op);
        check(verify(4, mask) == (op == 4 || op == 5),
              "VerifyVersionInfoW compares a newer build with operator %u", op);
    }
    // Major/minor/service-pack comparison is hierarchical: an older major wins
    // regardless of its larger minor and service-pack values.
    wr32(p + 4, RECOMP_WINDOWS_MAJOR - 1);
    wr32(p + 8, 255);
    wr16(p + 276, 99);
    uint64_t ge = 0;
    for (uint32_t bit : {1u, 2u, 16u, 32u})
        ge = call_condition_mask(c, ge, bit, 3);
    check(verify(0x33, ge) == 1, "VerifyVersionInfoW compares version tuples hierarchically");
    wr32(p + 4, RECOMP_WINDOWS_MAJOR);
    wr32(p + 8, RECOMP_WINDOWS_MINOR);
    check(verify(0x33, ge) == 0,
          "VerifyVersionInfoW rejects a newer service pack at equal major/minor");
    wr32(p + 8, RECOMP_WINDOWS_MINOR + 1);
    uint64_t mixed = call_condition_mask(c, 0, 2, 1);
    mixed = call_condition_mask(c, mixed, 1, 4);
    check(verify(3, mixed) == 1,
          "VerifyVersionInfoW allows equal major with a separate minor comparison");
    wr32(p + 8, RECOMP_WINDOWS_MINOR - 1);
    mixed = call_condition_mask(c, 0, 2, 3);
    mixed = call_condition_mask(c, mixed, 1, 1);
    check(verify(3, mixed) == 0, "VerifyVersionInfoW retains a lower-field equality condition");
    mixed = call_condition_mask(c, 0, 2, 3);
    mixed = call_condition_mask(c, mixed, 1, 4);
    check(verify(3, mixed) == 1, "VerifyVersionInfoW keeps the major comparison direction");
    wr16(p + 280, 1);
    check(verify(0x40, call_condition_mask(c, 0, 0x40, 6)) == 0 &&
              verify(0x40, call_condition_mask(c, 0, 0x40, 7)) == 0,
          "VerifyVersionInfoW applies suite AND/OR to the workstation suite mask");
    check(verify(2, 0) == 0 && call_import(c, "KERNEL32.dll", "GetLastError", {}) == 87,
          "VerifyVersionInfoW rejects a missing condition");
}

static std::vector<std::string> g_resource_names;
static uint32_t g_resource_module, g_resource_type, g_resource_param;
static bool g_resource_stop = false;
static void resource_enum_callback(X86 *c) {
    g_resource_module = arg(c, 0);
    g_resource_type = arg(c, 1);
    g_resource_param = arg(c, 3);
    uint32_t name = arg(c, 2);
    g_resource_names.push_back(name <= 0xffff ? "#" + std::to_string(name) : gm_wstr(name));
    set_eax(c, g_resource_stop ? 0 : 1);
}
// A minimal version tree independent of the selected game's resources.
// Only mapped guest bytes change, and RAII restores them before later tests.
class VersionResourceFixture {
    uint32_t root = 0;
    std::vector<uint8_t> saved;

  public:
    explicit VersionResourceFixture(bool present = true) {
        uint32_t image = loader_image_base(), opt = image + rd32(image + 0x3c) + 24;
        root = image + rd32(opt + 112);
        if (!rd32(opt + 112)) { // UG1's Speed.exe has no resource section
            printf("  [skip] image has no resource directory (version fixture)\n");
            ++g_skips;
            return;
        }
        if (!check(rd32(opt + 116) >= 0x200 && gm_valid(root, 0x200),
                   "mapped resource directory can hold the version fixture"))
            return;
        saved.assign(g_mem + root, g_mem + root + 0x200);
        memset(g_mem + root, 0, 0x200);
        if (!present)
            return;
        wr16(root + 14, 1);
        wr32(root + 16, 16); // RT_VERSION
        wr32(root + 20, 0x80000020);
        wr16(root + 0x2e, 1);
        wr32(root + 0x30, 1);
        wr32(root + 0x34, 0x80000040);
        wr16(root + 0x4e, 1);
        wr32(root + 0x50, 0x409);
        wr32(root + 0x54, 0x60);
        wr32(root + 0x60, root + 0x100 - image);
        wr32(root + 0x64, 92);
        uint32_t data = root + 0x100;
        wr16(data, 92);
        wr16(data + 2, 52); // binary VS_FIXEDFILEINFO
        gm_put_wstr(data + 6, "VS_VERSION_INFO", 16);
        wr32(data + 40, 0xfeef04bd);
        wr32(data + 44, 0x00010000);
        wr32(data + 48, 0x00010002); // synthetic version 1.2.3.4
        wr32(data + 52, 0x00030004);
    }
    ~VersionResourceFixture() {
        if (!saved.empty())
            memcpy(g_mem + root, saved.data(), saved.size());
    }
    bool valid() const {
        return !saved.empty();
    }
    VersionResourceFixture(const VersionResourceFixture &) = delete;
    VersionResourceFixture &operator=(const VersionResourceFixture &) = delete;
};

static void test_kernel32_wide() {
    X86 c;
    loader_init_context(&c);
    const uint32_t s = 0x00300000, fd = s + 0x1000;
    section("kernel32 wide files");
    gm_put_wstr(s, RECOMP_EXECUTABLE, 128);
    uint32_t attrs = call_import(&c, "KERNEL32.dll", "GetFileAttributesW", {s});
    check(attrs != 0 && attrs != 0xffffffffu && !(attrs & 0x10), "GetFileAttributesW(exe) = %08x",
          attrs);
    uint32_t h = call_import(&c, "KERNEL32.dll", "CreateFileW", {s, 0x80000000u, 1, 0, 3, 0x80, 0});
    check(h != 0 && h != 0xffffffffu, "CreateFileW opens the executable");
    if (h && h != 0xffffffffu)
        call_import(&c, "KERNEL32.dll", "CloseHandle", {h});
    memset(g_mem + fd, 0xa5, 600);
    gm_put_wstr(s, "*.exe", 128);
    uint32_t fh = call_import(&c, "KERNEL32.dll", "FindFirstFileW", {s, fd});
    check(fh != 0 && fh != 0xffffffffu, "FindFirstFileW(*.exe)");
    if (fh && fh != 0xffffffffu) {
        std::set<std::string> names;
        do {
            std::string found = gm_wstr(fd + 44, 260);
            check(found.size() > 4 && os_strcasecmp(found.c_str() + found.size() - 4, ".exe") == 0,
                  "wide find record: %s", found.c_str());
            check(names.insert(found).second, "wide enumeration advances");
            check(rd32(fd + 592) == 0xa5a5a5a5, "WIN32_FIND_DATAW ends at byte 592");
        } while (names.size() < 100 && call_import(&c, "KERNEL32.dll", "FindNextFileW", {fh, fd}));
        call_import(&c, "KERNEL32.dll", "FindClose", {fh});
    }
    gm_put_wstr(s, "folder\\caf\xc3\xa9.txt", 128);
    uint32_t n = call_import(&c, "KERNEL32.dll", "GetFullPathNameW", {s, 256, fd, fd + 600});
    check(n > 0 && gm_wstr(rd32(fd + 600)) == "caf\xc3\xa9.txt",
          "GetFullPathNameW returns a UTF-16 file-part pointer");
    uint32_t need = call_import(&c, "KERNEL32.dll", "GetFullPathNameW", {s, 0, 0, 0});
    check(need == n + 1 &&
              call_import(&c, "KERNEL32.dll", "GetFullPathNameW", {s, 2, fd, 0}) == need,
          "GetFullPathNameW reports required units including NUL on a short buffer");
    gm_put_wstr(s, RECOMP_EXECUTABLE, 128);
    check(call_import(&c, "KERNEL32.dll", "GetFileAttributesExW", {s, 0, fd}) == 1 &&
              rd32(fd + 32) > 0,
          "GetFileAttributesExW includes file size");
    memset(g_mem + fd, 0xa5, 42);
    check(call_import(&c, "KERNEL32.dll", "GetSystemDirectoryW", {fd, 20}) == 19 &&
              gm_wstr(fd) == "C:\\Windows\\System32" && rd16(fd + 38) == 0 &&
              rd16(fd + 40) == 0xa5a5,
          "GetSystemDirectoryW writes System32 as UTF-16 and counts characters without NUL");
    memset(g_mem + fd, 0xa5, 42);
    check(call_import(&c, "KERNEL32.dll", "GetSystemDirectoryW", {fd, 19}) == 20 &&
              rd16(fd) == 0xa5a5 && rd16(fd + 38) == 0xa5a5,
          "GetSystemDirectoryW reports space including NUL when only the text fits");
    check(call_import(&c, "KERNEL32.dll", "GetSystemDirectoryW", {0, 0}) == 20 &&
              call_import(&c, "KERNEL32.dll", "GetSystemDirectoryW", {fd, 0}) == 20 &&
              rd16(fd) == 0xa5a5,
          "GetSystemDirectoryW supports size queries without writing a buffer");
    check(call_import(&c, "KERNEL32.dll", "GetDriveTypeW", {0}) == 3, "GetDriveTypeW");
    check(call_import(&c, "KERNEL32.dll", "GetLogicalDriveStringsW", {5, fd}) == 4 &&
              gm_wstr(fd) == "C:\\" && rd16(fd + 8) == 0,
          "GetLogicalDriveStringsW double terminates");
    check(call_import(&c, "KERNEL32.dll", "GetVolumeInformationW",
                      {0, fd, 64, fd + 128, fd + 132, fd + 136, fd + 140, 64}) == 1 &&
              gm_wstr(fd + 140) == "FAT32",
          "GetVolumeInformationW");
    check(call_import(&c, "KERNEL32.dll", "GetDiskFreeSpaceW", {0, fd, fd + 4, fd + 8, fd + 12}) ==
                  1 &&
              rd32(fd) && rd32(fd + 4) && rd32(fd + 8) <= rd32(fd + 12),
          "GetDiskFreeSpaceW geometry");
    check(call_import(&c, "KERNEL32.dll", "GetDiskFreeSpaceA",
                      {0, fd + 16, fd + 20, fd + 24, fd + 28}) == 1 &&
              memcmp(g_mem + fd, g_mem + fd + 16, 16) == 0,
          "GetDiskFreeSpaceA and W report the same virtual disk geometry");
    gm_put_wstr(s, "C:", 8);
    check(call_import(&c, "KERNEL32.dll", "QueryDosDeviceW", {s, fd, 128}) > 0 &&
              !gm_wstr(fd).empty(),
          "QueryDosDeviceW(C:)");
    remove_tree(g_wide_root);
    mkdir_p(g_wide_root);
    win32_set_file_ops(wide_resolver, nullptr);
    gm_put_wstr(s, "caf\xc3\xa9.txt", 128);
    h = call_import(&c, "KERNEL32.dll", "CreateFileW", {s, 0x40000000u, 0, 0, 2, 0x80, 0});
    check(h && h != 0xffffffffu, "CreateFileW creates a Unicode path in the write tier");
    if (h && h != 0xffffffffu)
        call_import(&c, "KERNEL32.dll", "CloseHandle", {h});
    gm_put_wstr(s + 256, "copy.txt", 128);
    check(call_import(&c, "KERNEL32.dll", "CopyFileW", {s, s + 256, 1}) == 1, "CopyFileW");
    check(call_import(&c, "KERNEL32.dll", "SetFileAttributesW", {s, 0x80}) == 1,
          "SetFileAttributesW");
    check(call_import(&c, "KERNEL32.dll", "DeleteFileW", {s}) == 1, "DeleteFileW");
    gm_put_wstr(s, "empty", 128);
    check(call_import(&c, "KERNEL32.dll", "CreateDirectoryW", {s, 0}) == 1, "CreateDirectoryW");
    check(call_import(&c, "KERNEL32.dll", "RemoveDirectoryW", {s}) == 1 &&
              g_wide_file_ops.back() == WIN32_FILE_DELETE,
          "RemoveDirectoryW uses the delete tier");
    section("kernel32 wide profile strings");
    gm_put_wstr(s, "Settings", 64);
    gm_put_wstr(s + 128, "Windowed", 64);
    gm_put_wstr(s + 256, "1", 64);
    gm_put_wstr(s + 384, "wide-test.ini", 64);
    check(call_import(&c, "KERNEL32.dll", "WritePrivateProfileStringW",
                      {s, s + 128, s + 256, s + 384}) == 1,
          "WritePrivateProfileStringW");
    gm_put_wstr(s + 512, "0", 64);
    n = call_import(&c, "KERNEL32.dll", "GetPrivateProfileStringW",
                    {s, s + 128, s + 512, s + 0x800, 64, s + 384});
    check(n == 1 && gm_wstr(s + 0x800) == "1",
          "GetPrivateProfileStringW reads back value from write tier");

    check(std::find(g_wide_file_ops.begin(), g_wide_file_ops.end(), WIN32_FILE_WRITE) !=
              g_wide_file_ops.end(),
          "profile update uses the write seam");
    gm_put_wstr(s, "settings", 64);
    gm_put_wstr(s + 128, "windowed", 64);
    gm_put_wstr(s + 256, "caf\xc3\xa9 \xf0\x9f\x98\x80", 64);
    check(call_import(&c, "KERNEL32.dll", "WritePrivateProfileStringW",
                      {s, s + 128, s + 256, s + 384}) == 1,
          "profile replaces a case-insensitive key");
    n = call_import(&c, "KERNEL32.dll", "GetPrivateProfileStringW",
                    {s, s + 128, s + 512, s + 0x800, 64, s + 384});
    check(n == 7 && gm_wstr(s + 0x800) == gm_wstr(s + 256),
          "profile value preserves Unicode and returns UTF-16 units");
    wr32(s + 0x804, 0xa5a5a5a5);
    check(call_import(&c, "KERNEL32.dll", "GetPrivateProfileStringW",
                      {s, s + 128, s + 512, s + 0x800, 2, s + 384}) == 1 &&
              gm_wstr(s + 0x800) == "c" && rd32(s + 0x804) == 0xa5a5a5a5,
          "profile truncation stays in capacity");
    n = call_import(&c, "KERNEL32.dll", "GetPrivateProfileStringW",
                    {s, 0, 0, s + 0x800, 64, s + 384});
    check(n == 9 && os_strcasecmp(gm_wstr(s + 0x800).c_str(), "Windowed") == 0 &&
              rd16(s + 0x812) == 0,
          "profile enumerates keys with double NUL");
    check(call_import(&c, "KERNEL32.dll", "WritePrivateProfileStringW", {s, s + 128, 0, s + 384}) ==
              1,
          "profile deletes a key");
    n = call_import(&c, "KERNEL32.dll", "GetPrivateProfileStringW",
                    {s, s + 128, s + 512, s + 0x800, 64, s + 384});
    check(n == 1 && gm_wstr(s + 0x800) == "0", "missing profile key uses the default");
    win32_set_file_ops(nullptr, nullptr);
    remove_tree(g_wide_root);

    section("kernel32 wide synchronisation and mappings");
    gm_put_wstr(s, "wide-event", 64);
    uint32_t event = call_import(&c, "KERNEL32.dll", "CreateEventW", {0, 1, 1, s});
    check(event != 0 && call_import(&c, "KERNEL32.dll", "WaitForSingleObject", {event, 0}) == 0,
          "CreateEventW initial state");
    if (event)
        call_import(&c, "KERNEL32.dll", "CloseHandle", {event});
    gm_put_wstr(s, "wide-mutex", 64);
    uint32_t mutex = call_import(&c, "KERNEL32.dll", "CreateMutexW", {0, 1, s});
    uint32_t opened = call_import(&c, "KERNEL32.dll", "OpenMutexW", {0x1f0001, 0, s});
    check(mutex && opened, "OpenMutexW finds the named mutex");
    if (mutex)
        call_import(&c, "KERNEL32.dll", "CloseHandle", {mutex});
    check(opened && call_import(&c, "KERNEL32.dll", "ReleaseMutex", {opened}) == 1,
          "closing the original handle preserves an open named mutex");
    if (opened)
        call_import(&c, "KERNEL32.dll", "CloseHandle", {opened});
    check(call_import(&c, "KERNEL32.dll", "OpenMutexW", {0, 0, s}) == 0,
          "OpenMutexW fails after the last handle closes");
    gm_put_str(s + 256, "shared-event", 64);
    gm_put_wstr(s, "shared-event", 64);
    uint32_t aevent = call_import(&c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, s + 256});
    uint32_t wevent = call_import(&c, "KERNEL32.dll", "CreateEventW", {0, 0, 1, s});
    check(wevent && get_last_error() == 183, "A and W share a named event");
    call_import(&c, "KERNEL32.dll", "SetEvent", {aevent});
    check(wevent && call_import(&c, "KERNEL32.dll", "WaitForSingleObject", {wevent, 0}) == 0,
          "wide handle observes the ANSI event signal");
    call_import(&c, "KERNEL32.dll", "CloseHandle", {aevent});
    if (wevent)
        call_import(&c, "KERNEL32.dll", "CloseHandle", {wevent});
    gm_put_wstr(s, RECOMP_EXECUTABLE, 128);
    h = call_import(&c, "KERNEL32.dll", "CreateFileW", {s, 0x80000000u, 1, 0, 3, 0, 0});
    gm_put_wstr(s + 256, "wide-mapping", 64);
    uint32_t mapping =
        call_import(&c, "KERNEL32.dll", "CreateFileMappingW", {h, 0, 2, 0, 0, s + 256});
    check(mapping != 0, "CreateFileMappingW opens an executable mapping");
    if (mapping) {
        uint32_t view = call_import(&c, "KERNEL32.dll", "MapViewOfFile", {mapping, 4, 0, 0, 64});
        check(view && rd16(view) == 0x5a4d, "wide mapping shares the ANSI file-backed body");
        if (view)
            call_import(&c, "KERNEL32.dll", "UnmapViewOfFile", {view});
        call_import(&c, "KERNEL32.dll", "CloseHandle", {mapping});
    }
    if (h && h != 0xffffffffu)
        call_import(&c, "KERNEL32.dll", "CloseHandle", {h});

    section("kernel32 wide text and time");
    gm_put_wstr(s, "caf\xc3\xa9", 64);
    gm_put_wstr(s + 128, " \xf0\x9f\x98\x80", 64);
    check(call_import(&c, "KERNEL32.dll", "lstrlenW", {s}) == 4 &&
              call_import(&c, "KERNEL32.dll", "lstrlenW", {0}) == 0,
          "lstrlenW counts UTF-16 units");
    check(call_import(&c, "KERNEL32.dll", "lstrcatW", {s, s + 128}) == s &&
              gm_wstr(s) == "caf\xc3\xa9 \xf0\x9f\x98\x80",
          "lstrcatW appends Unicode");
    check(call_import(&c, "KERNEL32.dll", "lstrlenW", {s}) == 7,
          "lstrlenW counts both halves of a surrogate pair");
    check(call_import(&c, "KERNEL32.dll", "FormatMessageW", {0x1000, 0, 5, 0, fd, 64, 0}) == 7 &&
              gm_wstr(fd) == "Error 5",
          "FormatMessageW system code");
    check(call_import(&c, "KERNEL32.dll", "FormatMessageW", {0x1000, 0, 5, 0, fd, 2, 0}) == 0,
          "FormatMessageW refuses a short buffer");
    wr32(fd, 0);
    check(call_import(&c, "KERNEL32.dll", "FormatMessageW", {0x1100, 0, 87, 0, fd, 0, 0}) == 8 &&
              gm_wstr(rd32(fd)) == "Error 87",
          "FormatMessageW allocates a LocalFree-compatible buffer");
    if (rd32(fd))
        call_import(&c, "KERNEL32.dll", "LocalFree", {rd32(fd)});
    call_import(&c, "KERNEL32.dll", "OutputDebugStringW", {s});
    const uint64_t ft = (11644473600ull + 946782246ull) * 10000000ull + 1230000ull;
    wr32(s, (uint32_t)ft);
    wr32(s + 4, (uint32_t)(ft >> 32));
    check(call_import(&c, "KERNEL32.dll", "FileTimeToLocalFileTime", {s, s + 16}) == 1 &&
              rd32(s) == rd32(s + 16) && rd32(s + 4) == rd32(s + 20),
          "FileTimeToLocalFileTime identity");
    check(call_import(&c, "KERNEL32.dll", "FileTimeToSystemTime", {s, fd}) == 1 &&
              rd16(fd) == 2000 && rd16(fd + 2) == 1 && rd16(fd + 4) == 0 && rd16(fd + 6) == 2 &&
              rd16(fd + 8) == 3 && rd16(fd + 10) == 4 && rd16(fd + 12) == 6 && rd16(fd + 14) == 123,
          "FileTimeToSystemTime UTC fields and milliseconds");
    // And back again: the pair has to round-trip, or a game that converts a
    // time to do arithmetic on it and converts the answer back drifts.
    check(call_import(&c, "KERNEL32.dll", "SystemTimeToFileTime", {fd, s + 16}) == 1 &&
              rd32(s + 16) == rd32(s) && rd32(s + 20) == rd32(s + 4),
          "SystemTimeToFileTime inverts it, milliseconds included");
    wr16(fd + 2, 13); // a month that does not exist
    check(call_import(&c, "KERNEL32.dll", "SystemTimeToFileTime", {fd, s + 16}) == 0,
          "SystemTimeToFileTime refuses a month outside 1..12");
    wr16(fd + 2, 1);
    check(call_import(&c, "KERNEL32.dll", "GetDateFormatW", {0x409, 0, fd, 0, s + 128, 64}) == 11 &&
              gm_wstr(s + 128) == "2000-01-02",
          "GetDateFormatW fixed ISO picture");
    check(call_import(&c, "KERNEL32.dll", "GetDateFormatW", {0x409, 0, fd, 0, 0, 0}) == 11,
          "GetDateFormatW size includes terminator");
    check(call_import(&c, "KERNEL32.dll", "FileTimeToDosDateTime", {s, s + 32, s + 34}) == 1 &&
              rd16(s + 32) == ((20 << 9) | (1 << 5) | 2) &&
              rd16(s + 34) == ((3 << 11) | (4 << 5) | 3),
          "FileTimeToDosDateTime packs fields");
    wr32(s, 0);
    wr32(s + 4, 0);
    check(call_import(&c, "KERNEL32.dll", "FileTimeToDosDateTime", {s, s + 32, s + 34}) == 0,
          "DOS time rejects years before 1980");
    section("kernel32 wide locale");
    check(call_import(&c, "KERNEL32.dll", "GetThreadLocale", {}) == 0x0409, "GetThreadLocale");
    check(call_import(&c, "KERNEL32.dll", "GetUserDefaultUILanguage", {}) == 0x0409,
          "GetUserDefaultUILanguage");

    check(call_import(&c, "KERNEL32.dll", "SetThreadLocale", {0x0411}) == 1 &&
              call_import(&c, "KERNEL32.dll", "GetThreadLocale", {}) == 0x0411,
          "SetThreadLocale stores the process-wide LCID");
    call_import(&c, "KERNEL32.dll", "SetThreadLocale", {0x0409});
    check(call_import(&c, "KERNEL32.dll", "GetSystemDefaultUILanguage", {}) == 0x0409,
          "GetSystemDefaultUILanguage");
    check(call_import(&c, "KERNEL32.dll", "IsDBCSLeadByteEx", {1252, 0x81}) == 0,
          "IsDBCSLeadByteEx");
    check(call_import(&c, "KERNEL32.dll", "GetConsoleCP", {}) == 437 &&
              call_import(&c, "KERNEL32.dll", "GetConsoleOutputCP", {}) == 437,
          "console code pages");
    memset(g_mem + fd, 0xa5, 548);
    check(call_import(&c, "KERNEL32.dll", "GetCPInfoExW", {0, 0, fd}) == 1 && rd32(fd) == 1 &&
              rd32(fd + 20) == 1252 && rd16(fd + 18) == '?' && !gm_wstr(fd + 24).empty() &&
              rd32(fd + 544) == 0xa5a5a5a5,
          "CPINFOEXW fields and bounds");
    uint32_t enum_cb = imports_alloc_trampoline("test", "wide_enum", wide_enum_callback, 1);
    g_wide_enum_calls = 0;
    check(call_import(&c, "KERNEL32.dll", "EnumSystemLocalesW", {enum_cb, 1}) == 1 &&
              g_wide_enum_calls == 1 && g_wide_enum_text == "00000409",
          "EnumSystemLocalesW calls the guest once");
    g_wide_enum_calls = 0;
    check(call_import(&c, "KERNEL32.dll", "EnumCalendarInfoW", {enum_cb, 0x409, 0xffffffffu, 1}) ==
                  1 &&
              g_wide_enum_calls == 1 && g_wide_enum_text == "1",
          "EnumCalendarInfoW calls the guest once");

    section("kernel32 wide process and version");
    uint32_t cmd_a = call_import(&c, "KERNEL32.dll", "GetCommandLineA", {});
    uint32_t cmd_w = call_import(&c, "KERNEL32.dll", "GetCommandLineW", {});
    check(cmd_w && gm_wstr(cmd_w) == gm_str(cmd_a) &&
              call_import(&c, "KERNEL32.dll", "GetCommandLineW", {}) == cmd_w,
          "GetCommandLineW widens the stable ANSI answer");
    memset(g_mem + fd, 0xa5, 72);
    call_import(&c, "KERNEL32.dll", "GetStartupInfoW", {fd});
    check(rd32(fd) == 68 && rd32(fd + 64) == 0 && rd32(fd + 68) == 0xa5a5a5a5,
          "GetStartupInfoW writes exactly 68 bytes");
    check(call_import(&c, "KERNEL32.dll", "VerifyVersionInfoW", {fd, 0, 0, 0}) == 0,
          "VerifyVersionInfoW rejects an empty request");
    check(call_condition_mask(&c, 0x8000000000001234ull, 0x80, 5) ==
              (0x8000000000001234ull | (5ull << 21)),
          "VerSetConditionMask preserves EDX:EAX and the three-bit condition field");
    check(call_import(&c, "KERNEL32.dll", "GetCurrentProcessId", {}) == 1, "GetCurrentProcessId");
    check(call_import(&c, "KERNEL32.dll", "IsDebuggerPresent", {}) == 0 &&
              call_import(&c, "KERNEL32.dll", "SwitchToThread", {}) == 0,
          "debugger and switch probes");
    check(call_import(&c, "KERNEL32.dll", "MulDiv", {5, 1, 2}) == 3 &&
              call_import(&c, "KERNEL32.dll", "MulDiv", {(uint32_t)-5, 1, 2}) == (uint32_t)-3,
          "MulDiv rounds signed halves away from zero");
    check(call_import(&c, "KERNEL32.dll", "MulDiv", {0x7fffffff, 2, 1}) == 0xffffffffu &&
              call_import(&c, "KERNEL32.dll", "MulDiv", {1, 2, 0}) == 0xffffffffu,
          "MulDiv reports overflow and divide by zero");
    check(call_import(&c, "KERNEL32.dll", "VirtualProtect", {s, 4096, 4, fd}) == 1 &&
              rd32(fd) == 0x40,
          "VirtualProtect returns the previous protection");
    for (const auto &region : std::vector<std::pair<uint32_t, uint32_t>>{
             {loader_image_base(), 0x1000000}, {HEAP_BASE, 0x20000}, {STACK_LIMIT, 0x20000}}) {
        memset(g_mem + fd, 0xa5, 32);
        check(call_import(&c, "KERNEL32.dll", "VirtualQuery", {region.first + 123, fd, 28}) == 28 &&
                  rd32(fd + 4) == region.first && rd32(fd + 12) >= 4096 &&
                  rd32(fd + 16) == 0x1000 && rd32(fd + 24) == region.second &&
                  rd32(fd + 28) == 0xa5a5a5a5,
              "VirtualQuery classifies arena region %08x", region.first);
    }
    check(call_import(&c, "KERNEL32.dll", "VirtualQueryEx", {0xffffffffu, s, fd, 28}) == 28 &&
              rd32(fd + 16) == 0x10000,
          "VirtualQueryEx reports an unassigned arena range as free");
    check(call_import(&c, "KERNEL32.dll", "VirtualQuery", {GUEST_SIZE, fd, 28}) == 0 &&
              call_import(&c, "KERNEL32.dll", "VirtualQuery", {s, fd, 27}) == 0,
          "VirtualQuery rejects invalid addresses and short records");
    check(call_import(&c, "KERNEL32.dll", "SetErrorMode", {1}) == 0 &&
              call_import(&c, "KERNEL32.dll", "SetErrorMode", {0}) == 1,
          "SetErrorMode preserves the existing previous-mode behavior");
    gm_put_wstr(s, "Wide-Atom", 64);
    uint32_t atom = call_import(&c, "KERNEL32.dll", "GlobalAddAtomW", {s});
    gm_put_wstr(s, "wide-atom", 64);
    check(atom >= 0xc000 && call_import(&c, "KERNEL32.dll", "GlobalFindAtomW", {s}) == atom,
          "global wide atoms are case-insensitive");
    check(call_import(&c, "KERNEL32.dll", "GlobalAddAtomW", {s}) == atom,
          "GlobalAddAtomW retains an existing atom");
    call_import(&c, "KERNEL32.dll", "GlobalDeleteAtom", {atom});
    check(call_import(&c, "KERNEL32.dll", "GlobalFindAtomW", {s}) == atom,
          "atom remains until all references are deleted");
    call_import(&c, "KERNEL32.dll", "GlobalDeleteAtom", {atom});
    check(call_import(&c, "KERNEL32.dll", "GlobalFindAtomW", {s}) == 0,
          "GlobalDeleteAtom removes the last reference");
    event = call_import(&c, "KERNEL32.dll", "CreateEventW", {0, 1, 1, 0});
    wr32(fd, event);
    check(call_import(&c, "KERNEL32.dll", "WaitForMultipleObjectsEx", {1, fd, 0, 0, 1}) == 0,
          "WaitForMultipleObjectsEx uses the existing wait body");
    call_import(&c, "KERNEL32.dll", "CloseHandle", {event});
    section("kernel32 wide resources");
    uint32_t resource_cb =
        imports_alloc_trampoline("test", "resource_enum", resource_enum_callback, 4);
    {
        VersionResourceFixture version;
        if (version.valid()) {
            uint32_t r = call_import(&c, "KERNEL32.dll", "FindResourceW", {0, 1, 16});
            check(r != 0, "FindResourceW(VS_VERSION_INFO)");
            uint32_t size = call_import(&c, "KERNEL32.dll", "SizeofResource", {0, r});
            uint32_t data = call_import(&c, "KERNEL32.dll", "LoadResource", {0, r});
            check(size > 0x34 && data != 0 && gm_valid(data, size) &&
                      rd32(data + 40) == 0xfeef04bdu,
                  "the loaded resource is a VS_VERSIONINFO (size %u)", size);

            check(call_import(&c, "KERNEL32.dll", "LockResource", {data}) == data,
                  "LockResource preserves the guest address");
            check(call_import(&c, "KERNEL32.dll", "FreeResource", {data}) == 0,
                  "FreeResource leaves image-backed resources loaded");
            gm_put_wstr(s, "#16", 64);
            gm_put_wstr(s + 128, "#1", 64);
            check(r && call_import(&c, "KERNEL32.dll", "FindResourceW",
                                   {loader_image_base(), s + 128, s}) == r,
                  "resource integer strings resolve like IDs");
            check(call_import(&c, "KERNEL32.dll", "FindResourceW", {0, 0xffff, 16}) == 0,
                  "missing resource returns zero");
            g_resource_names.clear();
            check(call_import(&c, "KERNEL32.dll", "EnumResourceNamesW",
                              {0, 16, resource_cb, 0x1234}) == 1 &&
                      std::find(g_resource_names.begin(), g_resource_names.end(), "#1") !=
                          g_resource_names.end() &&
                      g_resource_type == 16 && g_resource_param == 0x1234,
                  "EnumResourceNamesW passes names, type and caller data to the guest");
        }
    }
    {
        VersionResourceFixture missing(false);
        if (missing.valid())
            check(call_import(&c, "KERNEL32.dll", "FindResourceW", {0, 1, 16}) == 0,
                  "an image without RT_VERSION has no version resource");
    }
    // Replace only guest-memory directory bytes temporarily, then restore them.
    // The real image stays pinned on disk; this fixture exercises names and
    // corrupt offsets that need not occur in a particular game's resources.
    uint32_t image = loader_image_base(), opt = image + rd32(image + 0x3c) + 24;
    uint32_t root = image + rd32(opt + 112), directory_size = rd32(opt + 116);
    if (!rd32(opt + 112)) {
        printf("  [skip] image has no resource directory (synthetic fixture)\n");
        ++g_skips;
    } else if (check(directory_size >= 0x300 && gm_valid(root, directory_size),
              "resource directory can hold the synthetic fixture")) {
        std::vector<uint8_t> saved(g_mem + root, g_mem + root + 0x300);
        memset(g_mem + root, 0, 0x300);
        wr16(root + 12, 1);
        wr16(root + 14, 1);
        wr32(root + 16, 0x80000100);
        wr32(root + 20, 0x80000040);
        wr32(root + 24, 10);
        wr32(root + 28, 0x80000040);
        wr16(root + 0x4c, 1);
        wr16(root + 0x4e, 1);
        wr32(root + 0x50, 0x80000120);
        wr32(root + 0x54, 0x80000080);
        wr32(root + 0x58, 7);
        wr32(root + 0x5c, 0x800000a0);
        wr16(root + 0x8e, 1);
        wr32(root + 0x90, 0x409);
        wr32(root + 0x94, 0xc0);
        wr16(root + 0xae, 1);
        wr32(root + 0xb0, 0x409);
        wr32(root + 0xb4, 0xd0);
        wr32(root + 0xc0, root + 0x200 - image);
        wr32(root + 0xc4, 4);
        wr32(root + 0xd0, root + 0x210 - image);
        wr32(root + 0xd4, 4);
        wr16(root + 0x100, 4);
        gm_put_wstr(root + 0x102, "TYPE", 5);
        wr16(root + 0x120, 6);
        gm_put_wstr(root + 0x122, "Name \xce\xa9", 7);
        wr32(root + 0x200, 0x12345678);
        wr32(root + 0x210, 0xabcdef01);
        gm_put_wstr(s, "TYPE", 64);
        gm_put_wstr(s + 128, "Name \xce\xa9", 64);
        uint32_t named = call_import(&c, "KERNEL32.dll", "FindResourceW", {0, s + 128, s});
        check(named == root + 0xc0 &&
                  call_import(&c, "KERNEL32.dll", "LoadResource", {0, named}) == root + 0x200,
              "named UTF-16 resources resolve type, name and language");
        check(call_import(&c, "KERNEL32.dll", "FindResourceW", {0, 7, 10}) == root + 0xd0,
              "integer resource directory entries resolve");
        g_resource_names.clear();
        g_resource_stop = false;
        check(call_import(&c, "KERNEL32.dll", "EnumResourceNamesW",
                          {image, s, resource_cb, 0x5678}) == 1 &&
                  g_resource_names == std::vector<std::string>{"Name \xce\xa9", "#7"} &&
                  g_resource_module == image && g_resource_type == s && g_resource_param == 0x5678,
              "resource enumeration handles mixed string and integer names");
        g_resource_names.clear();
        g_resource_stop = true;
        call_import(&c, "KERNEL32.dll", "EnumResourceNamesW", {0, s, resource_cb, 0});
        check(g_resource_names.size() == 1, "resource enumeration stops when the guest asks");
        g_resource_stop = false;
        wr32(root + 0xc0, loader_image_size() - 2);
        check(call_import(&c, "KERNEL32.dll", "LoadResource", {0, named}) == 0 &&
                  call_import(&c, "KERNEL32.dll", "SizeofResource", {0, named}) == 0,
              "resource payload cannot cross the image boundary");
        wr32(root + 20, 0x80000000u | (directory_size - 8));
        check(call_import(&c, "KERNEL32.dll", "FindResourceW", {0, s + 128, s}) == 0,
              "truncated resource directory is rejected");
        wr32(root + 16, 0x80000000u | (directory_size - 1));
        check(call_import(&c, "KERNEL32.dll", "FindResourceW", {0, s + 128, s}) == 0,
              "truncated resource name is rejected");
        check(call_import(&c, "KERNEL32.dll", "LoadResource", {0, GUEST_SIZE - 8}) == 0,
              "invalid resource handles are rejected");
        memcpy(g_mem + root, saved.data(), saved.size());
    }
}

// Exercise ownership and the x86 Automation layouts, including embedded NULs.
static void test_delphi_automation() {
    section("Delphi Automation ownership");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00310000, v = s + 0x100, copy = v + 16;
    wr16(s, 'a');
    wr16(s + 2, 0);
    wr16(s + 4, 'b');
    uint32_t b = call_import(&c, "OLEAUT32.dll", "SysAllocStringLen", {s, 3});
    check(b && rd32(b - 4) == 6 && rd16(b + 4) == 'b' && rd16(b + 6) == 0,
          "BSTR preserves embedded NUL and byte length");
    wr32(s + 16, b);
    check(call_import(&c, "OLEAUT32.dll", "SysReAllocStringLen", {s + 16, b, 2}) == 1,
          "BSTR reallocation accepts its own source");
    b = rd32(s + 16);
    check(b && rd32(b - 4) == 4 && rd16(b) == 'a', "BSTR reallocation updates length");
    memset(g_mem + v, 0xcc, 32);
    call_import(&c, "OLEAUT32.dll", "VariantInit", {v});
    call_import(&c, "OLEAUT32.dll", "VariantInit", {copy});
    check(rd32(v) == 0 && rd32(v + 12) == 0, "VariantInit zeros all 16 bytes");
    wr16(v, 8);
    wr32(v + 8, b);
    check(call_import(&c, "OLEAUT32.dll", "VariantCopy", {copy, v}) == 0 && rd32(copy + 8) != b &&
              rd32(copy + 8) && rd32(rd32(copy + 8) - 4) == 4,
          "VariantCopy deep copies BSTR");
    call_import(&c, "OLEAUT32.dll", "VariantClear", {v});
    check(!b || !heap_owns(b - 4), "VariantClear releases owned BSTR");
    call_import(&c, "OLEAUT32.dll", "VariantClear", {copy});
    wr16(v, 3);
    wr32(v + 8, (uint32_t)-42);
    check(call_import(&c, "OLEAUT32.dll", "VariantChangeType", {v, v, 0, 8}) == 0 && rd16(v) == 8 &&
              gm_wstr(rd32(v + 8)) == "-42",
          "VariantChangeType I4 to BSTR in place");
    check(call_import(&c, "OLEAUT32.dll", "VariantChangeType", {copy, v, 0, 5}) == 0 &&
              rd16(copy) == 5 && rd64(copy + 8) == 0xc045000000000000ull,
          "VariantChangeType BSTR to R8");
    check(call_import(&c, "OLEAUT32.dll", "VariantChangeType", {copy, copy, 0, 11}) == 0 &&
              rd16(copy + 8) == 0xffff,
          "VariantChangeType numeric true is VARIANT_TRUE");
    check(call_import(&c, "OLEAUT32.dll", "VariantChangeType", {copy, v, 0, 9}) == 0x80020005u,
          "VariantChangeType unsupported type mismatch");
    call_import(&c, "OLEAUT32.dll", "VariantClear", {v});
    wr16(v, 0x4003);
    wr32(v + 8, s + 24);
    wr32(s + 24, 1234);
    check(call_import(&c, "OLEAUT32.dll", "VariantCopyInd", {copy, v}) == 0 && rd16(copy) == 3 &&
              rd32(copy + 8) == 1234,
          "VariantCopyInd dereferences I4");
    call_import(&c, "OLEAUT32.dll", "VariantClear", {v});
    check(rd32(s + 24) == 1234, "clearing BYREF leaves the referent alone");
    wr32(s, 3);
    wr32(s + 4, (uint32_t)-2);
    uint32_t a = call_import(&c, "OLEAUT32.dll", "SafeArrayCreate", {3, 1, s});
    check(a && rd16(a) == 1 && rd32(a + 4) == 4 && rd32(a + 16) == 3 &&
              rd32(a + 20) == (uint32_t)-2,
          "SAFEARRAY x86 header includes its first bound in 24 bytes");
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayGetLBound", {a, 1, s + 8}) == 0 &&
              rd32(s + 8) == (uint32_t)-2,
          "SafeArrayGetLBound signed bound");
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayGetUBound", {a, 1, s + 8}) == 0 &&
              rd32(s + 8) == 0,
          "SafeArrayGetUBound");
    wr32(s + 8, (uint32_t)-1);
    wr32(s + 12, 9876);
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayPutElement", {a, s + 8, s + 12}) == 0 &&
              call_import(&c, "OLEAUT32.dll", "SafeArrayGetElement", {a, s + 8, s + 16}) == 0 &&
              rd32(s + 16) == 9876,
          "SafeArray element round trip at a negative index");
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayPtrOfIndex", {a, s + 8, s + 16}) == 0 && a &&
              rd32(s + 16) == rd32(a + 12) + 4,
          "SafeArrayPtrOfIndex returns guest data address");
    wr32(s + 8, 1);
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayGetElement", {a, s + 8, s + 16}) == 0x8002000bu,
          "SafeArray bounds failure");
    // A two-dimensional array of variants, the shape the map loader asks for:
    // [0..3, 1..3], twelve elements, the first dimension the outermost. The
    // two extents differ so a transposed layout cannot pass.
    uint32_t bounds = s + 0x40, idx = s + 0x60, out = s + 0x80;
    wr32(bounds, 4);
    wr32(bounds + 4, 0);
    wr32(bounds + 8, 3);
    wr32(bounds + 12, 1);
    uint32_t m = call_import(&c, "OLEAUT32.dll", "SafeArrayCreate", {12, 2, bounds});
    check(m && rd16(m) == 2 && rd32(m + 4) == 16 && heap_size(rd32(m + 12)) >= 12 * 16,
          "SafeArrayCreate allocates two dimensions worth of variants");
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayGetLBound", {m, 1, out}) == 0 &&
              rd32(out) == 0 &&
              call_import(&c, "OLEAUT32.dll", "SafeArrayGetUBound", {m, 1, out}) == 0 &&
              rd32(out) == 3,
          "dimension one is the bound the caller gave first");
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayGetLBound", {m, 2, out}) == 0 &&
              rd32(out) == 1 &&
              call_import(&c, "OLEAUT32.dll", "SafeArrayGetUBound", {m, 2, out}) == 0 &&
              rd32(out) == 3,
          "dimension two is the bound it gave second");
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayGetLBound", {m, 3, out}) == 0x8002000bu,
          "a dimension the array does not have is rejected");
    wr32(idx, 2);
    wr32(idx + 4, 3);
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayPtrOfIndex", {m, idx, out}) == 0 &&
              rd32(out) == rd32(m + 12) + (2 * 3 + 2) * 16,
          "element address folds both indices, the last varying fastest");
    wr32(idx, 0);
    wr32(idx + 4, 1);
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayPtrOfIndex", {m, idx, out}) == 0 &&
              rd32(out) == rd32(m + 12),
          "the lower corner is the first element");
    wr32(idx, 4);
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayPtrOfIndex", {m, idx, out}) == 0x8002000bu,
          "an index past the first dimension is rejected");
    wr32(idx, 0);
    wr32(idx + 4, 4);
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayPtrOfIndex", {m, idx, out}) == 0x8002000bu,
          "an index past the second dimension is rejected");
    wr32(idx, 2);
    wr32(idx + 4, 3);
    wr16(v, 3);
    wr32(v + 8, 4242);
    check(call_import(&c, "OLEAUT32.dll", "SafeArrayPutElement", {m, idx, v}) == 0 &&
              call_import(&c, "OLEAUT32.dll", "SafeArrayGetElement", {m, idx, out}) == 0 &&
              rd16(out) == 3 && rd32(out + 8) == 4242,
          "variant element round trips through two indices");
    call_import(&c, "OLEAUT32.dll", "VariantClear", {out});
    wr16(v, 0x200c);
    wr32(v + 8, m);
    check(call_import(&c, "OLEAUT32.dll", "VariantClear", {v}) == 0 && (!m || !heap_owns(m)),
          "VariantClear releases a multi-dimensional array");

    wr16(v, 0x2003);
    wr32(v + 8, a);
    check(call_import(&c, "OLEAUT32.dll", "VariantClear", {v}) == 0 && (!a || !heap_owns(a)),
          "VariantClear releases SAFEARRAY storage");
    wr32(s, 0xdeadbeef);
    check(call_import(&c, "OLEAUT32.dll", "GetErrorInfo", {0, s}) == 1 && rd32(s) == 0,
          "GetErrorInfo clears output and returns S_FALSE");
}

// The byte forms of the case-mapping and string-type calls, which a Delphi
// runtime built this decade uses to build its ANSI tables from inside a unit
// initialization. An import the kit does not know is called with its
// arguments left on the stack; two calls per byte value was enough to pop
// the unit-init loop's counter back as garbage and stop the walk with more
// than half the units - the PNG reader among them - never initialized.
static void test_ansi_case_and_string_types() {
    section("ANSI case mapping and string types");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00312000, out = s + 0x40;
    memcpy(g_mem + s, "Hello, World! \xe9", 15);
    uint32_t esp = c.r[R_ESP];
    check(call_import(&c, "USER32.dll", "CharUpperBuffA", {s, 13}) == 13 &&
              memcmp(g_mem + s, "HELLO, WORLD! \xe9", 15) == 0,
          "CharUpperBuffA maps the counted bytes in place and returns the count");
    check(call_import(&c, "USER32.dll", "CharLowerBuffA", {s, 5}) == 5 &&
              memcmp(g_mem + s, "hello, WORLD!", 13) == 0,
          "CharLowerBuffA maps only the counted prefix");
    check(c.r[R_ESP] == esp, "both are stdcall with two arguments: the stack is level");
    memcpy(g_mem + s, "a1 ", 3);
    check(call_import(&c, "KERNEL32.dll", "GetStringTypeExA", {0x409, 1, s, 3, out}) == 1 &&
              (rd16(out) & 0x2) && (rd16(out + 2) & 0x4) && (rd16(out + 4) & 0x8),
          "GetStringTypeExA classifies through the locale-first layout");
    wr16(s, 'A');
    wr16(s + 2, '7');
    check(call_import(&c, "KERNEL32.dll", "GetStringTypeExW", {0x409, 1, s, 2, out}) == 1 &&
              (rd16(out) & 0x1) && (rd16(out + 2) & 0x4),
          "GetStringTypeExW shifts past the locale and classifies the rest");
    check(call_import(&c, "KERNEL32.dll", "FlushInstructionCache", {0xffffffff, 0, 0}) == 1 &&
              c.r[R_ESP] == esp,
          "FlushInstructionCache succeeds and clears its three arguments");
}

static void test_delphi_registry_version() {
    section("Delphi wide registry, version and COM");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00320000, out = s + 0x800;
    gm_put_wstr(s, "Software\\RecompWideTest", 128);
    check(call_import(&c, "ADVAPI32.dll", "RegCreateKeyExW",
                      {0x80000001u, s, 0, 0, 0, 0, 0, out, 0}) == 0,
          "create wide registry fixture");
    uint32_t h = rd32(out);
    gm_put_wstr(s + 256, "Unicode", 64);
    gm_put_wstr(s + 512, "café\U0001f600", 64);
    check(call_import(&c, "ADVAPI32.dll", "RegSetValueExW", {h, s + 256, 0, 1, s + 512, 14}) == 0,
          "write UTF-16 registry value with surrogate pair");
    wr32(out, 0);
    check(call_import(&c, "ADVAPI32.dll", "RegQueryValueExW", {h, s + 256, 0, out + 4, 0, out}) ==
                  0 &&
              rd32(out) == 14 && rd32(out + 4) == 1,
          "wide registry size is UTF-16 bytes including NUL");
    wr32(out, 12);
    wr32(out + 16, 0xcccccccc);
    check(call_import(&c, "ADVAPI32.dll", "RegQueryValueExW", {h, s + 256, 0, 0, out + 16, out}) ==
                  234 &&
              rd32(out) == 14 && rd32(out + 16) == 0xcccccccc,
          "short registry buffer reports size without writing");
    wr32(out, 64);
    check(call_import(&c, "ADVAPI32.dll", "RegQueryValueExW", {h, s + 256, 0, 0, out + 16, out}) ==
                  0 &&
              gm_wstr(out + 16) == "café\U0001f600",
          "wide registry read round trips Unicode");
    gm_put_str(s + 768, "Unicode", 64);
    wr32(out, 64);
    check(call_import(&c, "ADVAPI32.dll", "RegQueryValueExA", {h, s + 768, 0, 0, out + 16, out}) ==
                  0 &&
              gm_str(out + 16) == "café\U0001f600",
          "ANSI registry reads UTF-8 backing value");
    wr32(out, 64);
    wr32(out + 4, 64);
    check(call_import(&c, "ADVAPI32.dll", "RegEnumValueW",
                      {h, 0, out + 128, out, 0, 0, out + 16, out + 4}) == 0 &&
              gm_wstr(out + 128) == "Unicode" && rd32(out) == 7 && rd32(out + 4) == 14,
          "RegEnumValueW counts name units and data bytes separately");
    check(call_import(&c, "ADVAPI32.dll", "RegEnumValueW", {h, 1, out + 128, out, 0, 0, 0, 0}) ==
              259,
          "registry value enumeration ends with NO_MORE_ITEMS");
    gm_put_wstr(s + 1024, "Child", 64);
    call_import(&c, "ADVAPI32.dll", "RegCreateKeyExW", {h, s + 1024, 0, 0, 0, 0, 0, out + 8, 0});
    uint32_t child = rd32(out + 8);
    wr32(out, 64);
    check(call_import(&c, "ADVAPI32.dll", "RegEnumKeyExW", {h, 0, out + 128, out, 0, 0, 0, 0}) ==
                  0 &&
              gm_wstr(out + 128) == "Child" && rd32(out) == 5,
          "RegEnumKeyExW immediate children");
    check(call_import(&c, "ADVAPI32.dll", "RegQueryInfoKeyW",
                      {h, 0, 0, 0, out, out + 4, 0, out + 8, out + 12, out + 16, 0, 0}) == 0 &&
              rd32(out) == 1 && rd32(out + 4) == 5 && rd32(out + 8) == 1 && rd32(out + 16) == 14,
          "RegQueryInfoKeyW reports child and value maxima");
    check(call_import(&c, "ADVAPI32.dll", "RegOpenKeyExW", {h, s + 1024, 0, 0, out}) == 0,
          "RegOpenKeyExW shares keys");
    call_import(&c, "ADVAPI32.dll", "RegCloseKey", {rd32(out)});
    call_import(&c, "ADVAPI32.dll", "RegCloseKey", {child});
    check(call_import(&c, "ADVAPI32.dll", "RegDeleteKeyW", {h, s + 1024}) == 0 &&
              call_import(&c, "ADVAPI32.dll", "RegDeleteValueW", {h, s + 256}) == 0 &&
              call_import(&c, "ADVAPI32.dll", "RegFlushKey", {h}) == 0,
          "wide registry deletion and flush");
    call_import(&c, "ADVAPI32.dll", "RegCloseKey", {h});
    call_import(&c, "ADVAPI32.dll", "RegDeleteKeyW", {0x80000001u, s});
    for (auto &entry : std::vector<std::pair<const char *, unsigned>>{{"RegConnectRegistryW", 3},
                                                                      {"RegLoadKeyW", 3},
                                                                      {"RegUnLoadKeyW", 2},
                                                                      {"RegSaveKeyW", 3},
                                                                      {"RegRestoreKeyW", 3},
                                                                      {"RegReplaceKeyW", 4}})
        check(call_import(&c, "ADVAPI32.dll", entry.first, std::vector<uint32_t>(entry.second)) ==
                  5,
              "%s denies unsupported external registry operations", entry.first);
    gm_put_wstr(s, RECOMP_EXECUTABLE, 128);
    uint32_t size = call_import(&c, "VERSION.dll", "GetFileVersionInfoSizeW", {s, out});
    if (size) {
        uint32_t block = heap_alloc(size, true);
        call_import(&c, "VERSION.dll", "GetFileVersionInfoW", {s, 0, size, block});
        gm_put_wstr(s + 256, "\\VarFileInfo\\Translation", 128);
        check(call_import(&c, "VERSION.dll", "VerQueryValueW", {block, s + 256, out, out + 4}) ==
                      1 &&
                  rd32(out + 4) >= 4,
              "VerQueryValueW translation table");
        uint32_t lang = rd32(rd32(out));
        char path[128];
        snprintf(path, sizeof path, "\\StringFileInfo\\%04x%04x\\FileVersion", lang & 0xffff,
                 lang >> 16);
        gm_put_wstr(s + 256, path, 128);
        gm_put_str(s + 512, path, 128);
        check(call_import(&c, "VERSION.dll", "VerQueryValueW", {block, s + 256, out, out + 4}) ==
                      1 &&
                  !gm_wstr(rd32(out)).empty(),
              "VerQueryValueW FileVersion string");
        std::string version = gm_wstr(rd32(out));
        check(call_import(&c, "VERSION.dll", "VerQueryValueA", {block, s + 512, out, out + 4}) ==
                      1 &&
                  gm_str(rd32(out)) == version,
              "VerQueryValueA from the same block");
        check(call_import(&c, "VERSION.dll", "VerQueryValueW", {block, s + 256, out, out + 4}) ==
                      1 &&
                  gm_wstr(rd32(out)) == version,
              "ANSI query preserves wide version data");
        heap_free(block);
    }
    check(call_import(&c, "OLE32.dll", "CoInitializeEx", {0, 2}) == 0, "CoInitializeEx S_OK");
    call_import(&c, "OLE32.dll", "OleUninitialize", {});
    uint32_t p = call_import(&c, "OLE32.dll", "CoTaskMemAlloc", {16});
    check(p && heap_owns(p), "CoTaskMemAlloc is guest heap storage");
    memset(g_mem + s, 0x5a, 32);
    check(call_import(&c, "OLE32.dll", "IsEqualGUID", {s, s + 16}) == 1,
          "IsEqualGUID all 16 bytes equal");
    wr8(s + 31, 0);
    check(call_import(&c, "OLE32.dll", "IsEqualGUID", {s, s + 16}) == 0,
          "IsEqualGUID compares final byte");
    call_import(&c, "OLE32.dll", "CoTaskMemFree", {p});
    check(!p || !heap_owns(p), "CoTaskMemFree releases storage");
}

static void test_delphi_misc() {
    section("Delphi absent services and C ABI");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00330000;
    wr32(s, 0xdeadbeef);
    wr32(s + 4, 0xdeadbeef);
    check(call_import(&c, "WINSPOOL.DRV", "EnumPrintersW", {2, 0, 2, 0, 0, s, s + 4}) == 1 &&
              rd32(s) == 0 && rd32(s + 4) == 0,
          "EnumPrintersW clears needed and returned counts");
    check(call_import(&c, "WINSPOOL.DRV", "GetDefaultPrinterW", {0, s}) == 0 &&
              get_last_error() == 2,
          "GetDefaultPrinterW reports FILE_NOT_FOUND");
    wr32(s, 0xdeadbeef);
    check(call_import(&c, "WINSPOOL.DRV", "OpenPrinterW", {0, s, 0}) == 0 && rd32(s) == 0,
          "OpenPrinterW produces no printer handle");
    check(call_import(&c, "WINSPOOL.DRV", "ClosePrinter", {0}) == 1 &&
              call_import(&c, "WINSPOOL.DRV", "DocumentPropertiesW", {0, 0, 0, 0, 0, 0}) ==
                  0xffffffffu,
          "absent printer close and document properties");
    wr32(s, 0xdeadbeef);
    check(call_import(&c, "NETAPI32.dll", "NetWkstaGetInfo", {0, 100, s}) == 50 && rd32(s) == 0 &&
              imports_argc(imports_resolve("NETAPI32.dll", "NetWkstaGetInfo")) == 3,
          "NetWkstaGetInfo uses the documented three-argument ABI and clears output");
    check(call_import(&c, "NETAPI32.dll", "NetApiBufferFree", {0}) == 0, "NetApiBufferFree");
    memset(g_mem + s, 0x33, 32);
    check(call_import(&c, "msvcrt.dll", "memset", {s + 1, 0xab, 3}) == s + 1 && rd8(s) == 0x33 &&
              rd8(s + 1) == 0xab && rd8(s + 4) == 0x33,
          "cdecl memset writes the requested span");
    check(call_import(&c, "msvcrt.dll", "memcpy", {s + 16, s, 5}) == s + 16 &&
              !memcmp(g_mem + s, g_mem + s + 16, 5),
          "cdecl memcpy copies bytes and returns destination");
    check(imports_argc(imports_resolve("msvcrt.dll", "memcpy")) == ARGC_CDECL &&
              imports_argc(imports_resolve("msvcrt.dll", "memset")) == ARGC_CDECL,
          "C runtime imports leave argument cleanup to caller");
    call_import(&c, "SHELL32.dll", "SHGetSpecialFolderPathA", {0, s, 5, 0});
    std::string documents = gm_str(s);
    for (uint32_t csidl : {5u, 26u, 28u, 35u})
        check(call_import(&c, "SHFOLDER.dll", "SHGetFolderPathW", {0, csidl, 0, 0, s + 512}) == 0 &&
                  gm_wstr(s + 512) == documents,
              "SHGetFolderPathW(%u) shares the guest documents path", csidl);
    check(call_import(&c, "SHFOLDER.dll", "SHGetFolderPathW", {0, 0x26, 0, 0, s + 512}) ==
              0x80070057u,
          "SHGetFolderPathW rejects unsupported folders");
    check(call_import(&c, "SHELL32.dll", "Shell_NotifyIconW", {0, 0}) == 1,
          "Shell_NotifyIconW accepts notifications");
}

static void test_delphi_controls() {
    section("Delphi image lists and flat scroll bars");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00340000;
    uint32_t il = call_import(&c, "COMCTL32.dll", "ImageList_Create", {2, 2, 0x20, 4, 4});
    check(il && call_import(&c, "COMCTL32.dll", "ImageList_GetImageCount", {il}) == 0,
          "initial capacity does not set image count");
    memset(g_mem + s, 0, 40);
    wr32(s, 40);
    wr32(s + 4, 4);
    wr32(s + 8, (uint32_t)-2);
    wr16(s + 12, 1);
    wr16(s + 14, 32);
    uint32_t bitmap = call_import(&c, "GDI32.dll", "CreateDIBSection", {0, s, 0, s + 64, 0, 0});
    uint32_t pixels = rd32(s + 64);
    for (uint32_t i = 0; i < 8; ++i)
        wr32(pixels + 4 * i, i % 4 < 2 ? 0xffff0000 : 0xff00ff00);
    check(call_import(&c, "COMCTL32.dll", "ImageList_Add", {il, bitmap, 0}) == 0 &&
              call_import(&c, "COMCTL32.dll", "ImageList_GetImageCount", {il}) == 2,
          "ImageList_Add splits strips");
    uint32_t dc = call_import(&c, "GDI32.dll", "CreateCompatibleDC", {0});
    call_import(&c, "GDI32.dll", "SelectObject", {dc, bitmap});
    check(call_import(&c, "COMCTL32.dll", "ImageList_Draw", {il, 1, dc, 0, 0, 0}) == 1 &&
              rd32(pixels) == 0xff00ff00,
          "ImageList_Draw writes DC backing pixels");
    check(call_import(&c, "COMCTL32.dll", "ImageList_DrawEx",
                      {il, 0, dc, 1, 0, 1, 1, 0xffffffffu, 0, 0}) == 1 &&
              rd32(pixels + 4) == 0xffff0000 && rd32(pixels + 16) == 0xff00ff00,
          "ImageList_DrawEx bounds");
    check(call_import(&c, "COMCTL32.dll", "ImageList_GetImageInfo", {il, 1, s + 128}) == 1 &&
              rd32(s + 128) && rd32(s + 152) - rd32(s + 144) == 2,
          "ImageList_GetImageInfo bitmap rectangle");
    call_import(&c, "GDI32.dll", "GetObjectA", {rd32(s + 128), 24, s + 256});
    uint32_t shared_bits = rd32(s + 276);
    wr32(shared_bits + 8, 0xff0000ff);
    check(call_import(&c, "COMCTL32.dll", "ImageList_Draw", {il, 1, dc, 0, 0, 0}) == 1 &&
              rd32(pixels) == 0xff0000ff,
          "image info bitmap edits remain visible to drawing");
    check(call_import(&c, "COMCTL32.dll", "ImageList_GetIconSize", {il, s + 128, s + 132}) == 1 &&
              rd32(s + 128) == 2 && rd32(s + 132) == 2,
          "ImageList_GetIconSize");
    check(call_import(&c, "COMCTL32.dll", "ImageList_SetBkColor", {il, 0x123456}) == 0xffffffffu &&
              call_import(&c, "COMCTL32.dll", "ImageList_GetBkColor", {il}) == 0x123456,
          "image list background state");
    uint32_t icon = call_import(&c, "COMCTL32.dll", "ImageList_GetIcon", {il, 0, 0});
    check(icon && call_import(&c, "COMCTL32.dll", "ImageList_ReplaceIcon",
                              {il, 0xffffffffu, icon}) == 2,
          "image list icon round trip");
    call_import(&c, "USER32.dll", "DestroyIcon", {icon});
    check(call_import(&c, "COMCTL32.dll", "ImageList_Replace", {il, 1, bitmap, 0}) == 1 &&
              call_import(&c, "COMCTL32.dll", "ImageList_Copy", {il, 2, il, 1, 0}) == 1,
          "image replace and copy");
    check(call_import(&c, "COMCTL32.dll", "ImageList_Remove", {il, 1}) == 1 &&
              call_import(&c, "COMCTL32.dll", "ImageList_GetImageCount", {il}) == 2,
          "remove compacts image list");
    check(call_import(&c, "COMCTL32.dll", "ImageList_SetImageCount", {il, 4}) == 1 &&
              call_import(&c, "COMCTL32.dll", "ImageList_GetImageCount", {il}) == 4,
          "grow image count");
    check(call_import(&c, "COMCTL32.dll", "ImageList_SetOverlayImage", {il, 0, 1}) == 1,
          "set overlay");
    check(call_import(&c, "COMCTL32.dll", "ImageList_BeginDrag", {il, 0, 1, 1}) == 1 &&
              call_import(&c, "COMCTL32.dll", "ImageList_DragEnter", {0, 10, 20}) == 1 &&
              call_import(&c, "COMCTL32.dll", "ImageList_DragMove", {30, 40}) == 1,
          "drag image state");
    check(call_import(&c, "COMCTL32.dll", "ImageList_GetDragImage", {s + 128, s + 136}) &&
              rd32(s + 128) == 30 && rd32(s + 132) == 40 && rd32(s + 136) == 1,
          "drag position and hotspot");
    call_import(&c, "COMCTL32.dll", "ImageList_DragShowNolock", {0});
    call_import(&c, "COMCTL32.dll", "ImageList_DragLeave", {0});
    call_import(&c, "COMCTL32.dll", "ImageList_EndDrag", {});
    check(call_import(&c, "COMCTL32.dll", "ImageList_GetDragImage", {0, 0}) == 0, "drag release");
    check(call_import(&c, "COMCTL32.dll", "ImageList_SetIconSize", {il, 3, 4}) == 1 &&
              call_import(&c, "COMCTL32.dll", "ImageList_GetImageCount", {il}) == 0,
          "resize discards images");
    check(call_import(&c, "COMCTL32.dll", "ImageList_Read", {0}) == 0 &&
              call_import(&c, "COMCTL32.dll", "ImageList_Write", {il, 0}) == 0,
          "image list persistence fails");
    check(call_import(&c, "COMCTL32.dll", "ImageList_LoadImageW", {0, 0xffff, 2, 4, 0, 0, 0}) == 0,
          "image list missing resource fails");
    check(call_import(&c, "COMCTL32.dll", "ImageList_Destroy", {il}) == 1 &&
              call_import(&c, "COMCTL32.dll", "ImageList_Destroy", {il}) == 0,
          "destroy invalidates image list");
    uint32_t masked = call_import(&c, "COMCTL32.dll", "ImageList_Create", {2, 2, 0x21, 1, 1});
    call_import(&c, "COMCTL32.dll", "ImageList_Add", {masked, bitmap, 0});
    check(call_import(&c, "COMCTL32.dll", "ImageList_GetImageInfo", {masked, 0, s + 128}) == 1 &&
              rd32(s + 132) != 0 &&
              call_import(&c, "GDI32.dll", "GetObjectA", {rd32(s + 132), 24, s + 256}) == 24 &&
              rd16(s + 274) == 1,
          "masked image list exposes an owned monochrome mask bitmap");
    wr32(s + 512, 1);
    wr32(s + 516, 0);
    wr32(s + 520, 0);
    wr32(s + 524, 0);
    wr32(s + 528, bitmap);
    uint32_t indirect = call_import(&c, "USER32.dll", "CreateIconIndirect", {s + 512});
    check(indirect &&
              call_import(&c, "COMCTL32.dll", "ImageList_ReplaceIcon", {masked, 0, indirect}) == 0,
          "ImageList_ReplaceIcon reads copied CreateIconIndirect pixels");
    call_import(&c, "USER32.dll", "DestroyIcon", {indirect});
    call_import(&c, "COMCTL32.dll", "ImageList_Destroy", {masked});
    std::vector<ResourceName> names;
    if (resource_names(2, &names) && !names.empty()) {
        const auto &name = names.front();
        uint32_t id = name.id;
        if (name.is_string) {
            gm_put_wstr(s + 512, name.name, 256);
            id = s + 512;
        }
        uint32_t bytes = 0, data = resource_data(resource_find(2, id), &bytes);
        uint32_t loaded =
            call_import(&c, "COMCTL32.dll", "ImageList_LoadImageW",
                        {loader_image_base(), id, rd32(data + 4), 1, 0xffffffffu, 0, 0});
        check(loaded && call_import(&c, "COMCTL32.dll", "ImageList_GetImageCount", {loaded}) == 1,
              "ImageList_LoadImageW decodes an image-backed bitmap resource");
        call_import(&c, "COMCTL32.dll", "ImageList_Destroy", {loaded});
    }
    call_import(&c, "GDI32.dll", "DeleteDC", {dc});
    call_import(&c, "GDI32.dll", "DeleteObject", {bitmap});
    std::string saved_seam_root = g_seam_root;
    g_seam_root = "build/recomp/image-list-file-test";
    mkdir_p(g_seam_root + "/read");
    memset(g_mem + s, 0, 74);
    wr16(s, 0x4d42);
    wr32(s + 2, 74);
    wr32(s + 10, 70); // bfOffBits includes a 16-byte gap.
    wr32(s + 14, 40);
    wr32(s + 18, 1);
    wr32(s + 22, (uint32_t)-1);
    wr16(s + 26, 1);
    wr16(s + 28, 32);
    wr32(s + 70, 0xff1256ab);
    FILE *bmp_file = fopen((g_seam_root + "/read/gap.bmp").c_str(), "wb");
    check(bmp_file && fwrite(g_mem + s, 1, 74, bmp_file) == 74, "write isolated BMP fixture");
    if (bmp_file)
        fclose(bmp_file);
    win32_set_file_ops(test_resolver, nullptr);
    gm_put_wstr(s + 512, "gap.bmp", 64);
    uint32_t file_list = call_import(&c, "COMCTL32.dll", "ImageList_LoadImageW",
                                     {0, s + 512, 1, 1, 0xffffffffu, 0, 0x10});
    check(file_list &&
              call_import(&c, "COMCTL32.dll", "ImageList_GetImageInfo", {file_list, 0, s + 128}) ==
                  1 &&
              call_import(&c, "GDI32.dll", "GetObjectA", {rd32(s + 128), 24, s + 256}) == 24 &&
              rd32(rd32(s + 276)) == 0xff1256ab,
          "ImageList_LoadImageW honors BMP pixel offset through file overlay");
    call_import(&c, "COMCTL32.dll", "ImageList_Destroy", {file_list});
    win32_set_file_ops(nullptr, nullptr);
    remove_tree(g_seam_root);
    g_seam_root = saved_seam_root;
    check(call_import(&c, "COMCTL32.dll", "InitializeFlatSB", {1}) == 1, "InitializeFlatSB");
    wr32(s, 28);
    wr32(s + 4, 7);
    wr32(s + 8, 0);
    wr32(s + 12, 100);
    wr32(s + 16, 10);
    wr32(s + 20, 35);
    check(call_import(&c, "COMCTL32.dll", "FlatSB_SetScrollInfo", {1, 0, s, 1}) == 35 &&
              call_import(&c, "COMCTL32.dll", "FlatSB_GetScrollPos", {1, 0}) == 35,
          "flat scrollbar range page position");
    check(call_import(&c, "COMCTL32.dll", "FlatSB_SetScrollPos", {1, 0, 200, 0}) == 35 &&
              call_import(&c, "COMCTL32.dll", "FlatSB_GetScrollPos", {1, 0}) == 91,
          "flat scrollbar clamp and previous position");
    wr32(s + 4, 0x17);
    check(call_import(&c, "COMCTL32.dll", "FlatSB_GetScrollInfo", {1, 0, s}) == 1 &&
              rd32(s + 20) == 91,
          "flat scrollbar query fields");
    check(call_import(&c, "COMCTL32.dll", "FlatSB_SetScrollProp", {1, 1, 16, 0}) == 1 &&
              call_import(&c, "COMCTL32.dll", "_TrackMouseEvent", {s}) == 1,
          "flat properties and tracking");
}

static void test_display_settings() {
    section("display mode enumeration with uninitialized dmSize");
    X86 c{};
    loader_init_context(&c);
    const uint32_t p = 0x00310000;
    uint32_t width = 1024, height = 768, bpp = 32;
    ddraw_display_mode(&width, &height, &bpp);
    check(ddraw_set_modes("1024x768x16"), "set a single offered display mode");
    for (bool wide : {true, false}) {
        const char *name = wide ? "EnumDisplaySettingsW" : "EnumDisplaySettingsA";
        uint32_t size = wide ? 220 : 156, size_offset = wide ? 68 : 36;
        // DEVMODEW also widens dmFormName: display fields start at 168, not 104.
        uint32_t display = wide ? 168 : 104;
        for (uint32_t input_size : {0u, 4u, size}) {
            memset(g_mem + p, 0xa5, size + 4);
            wr16(p + size_offset, uint16_t(input_size));
            check(call_import(&c, "USER32.dll", name, {0, 0, p}) == 1 &&
                      rd16(p + size_offset) == size && rd16(p + size_offset + 2) == 0 &&
                      rd32(p + display) == 16 && rd32(p + display + 4) == 1024 &&
                      rd32(p + display + 8) == 768 && rd32(p + display + 12) == 0 &&
                      rd32(p + display + 16) == 60 && rd32(p + size) == 0xa5a5a5a5,
                  "%s accepts dmSize=%u and fills only the standard record", name, input_size);
        }
        for (uint32_t mode : {0xffffffffu, 0xfffffffeu})
            check(call_import(&c, "USER32.dll", name, {0, mode, p}) == 1 &&
                      rd32(p + display + 4) == width && rd32(p + display + 8) == height,
                  "%s current/registry mode %08x", name, mode);
        check(call_import(&c, "USER32.dll", name, {0, 1, p}) == 0,
              "%s ends enumeration after mode zero", name);
    }
    check(ddraw_set_modes("800x600x16,1280x720x16"), "set two offered display modes");
    for (const char *name : {"EnumDisplaySettingsW", "EnumDisplaySettingsA"}) {
        uint32_t display = strcmp(name, "EnumDisplaySettingsW") == 0 ? 168 : 104;
        check(call_import(&c, "USER32.dll", name, {0, 0, p}) == 1 && rd32(p + display) == 16 &&
                  rd32(p + display + 4) == 800 && rd32(p + display + 8) == 600,
              "%s enumerates the first offered mode, independently of the desktop", name);
        check(call_import(&c, "USER32.dll", name, {0, 1, p}) == 1 && rd32(p + display) == 16 &&
                  rd32(p + display + 4) == 1280 && rd32(p + display + 8) == 720,
              "%s enumerates a second mode supported by DirectDraw", name);
        check(call_import(&c, "USER32.dll", name, {0, 2, p}) == 0,
              "%s stops after the full offered list", name);
    }
    ddraw_reset_modes();
}

// The pinned frame clock makes message timers deterministic without host sleeps.
static void test_user32_vcl() {
    section("wide windows and VCL model");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00300000;
    uint32_t defproc = imports_resolve("USER32.dll", "DefWindowProcW");
    memset(g_mem + s, 0, 40);
    wr32(s + 4, defproc);
    gm_put_wstr(s + 0x100, "TVclTestWindow", 32);
    wr32(s + 36, s + 0x100);
    uint32_t atom = call_import(&c, "USER32.dll", "RegisterClassW", {s});
    check(atom != 0, "RegisterClassW");
    gm_put_wstr(s + 0x200, "Wide \xce\xa9\xf0\x9f\x98\x80", 32);
    uint32_t hwnd =
        call_import(&c, "USER32.dll", "CreateWindowExW",
                    {0, s + 0x100, s + 0x200, 0x00cf0000u, 0, 0, 800, 600, 0, 0, IMAGE_BASE, 0});
    check(hwnd != 0, "CreateWindowExW -> %08x", hwnd);
    check(call_import(&c, "USER32.dll", "IsWindowUnicode", {hwnd}) == 1, "a W window is Unicode");
    check(call_import(&c, "USER32.dll", "GetWindowTextW", {hwnd, s + 0x300, 32}) == 8 &&
              gm_wstr(s + 0x300) == "Wide \xce\xa9\xf0\x9f\x98\x80",
          "GetWindowTextW counts UTF-16 units");
    check(call_import(&c, "USER32.dll", "SendMessageW", {hwnd, 0xd, 32, s + 0x300}) == 8,
          "WM_GETTEXT reaches DefWindowProcW");
    gm_put_str(s + 0x600, "changed", 16);
    check(call_import(&c, "USER32.dll", "SendMessageA", {hwnd, 0xc, 0, s + 0x600}) == 1 &&
              call_import(&c, "USER32.dll", "GetWindowTextW", {hwnd, s + 0x300, 32}) == 7 &&
              gm_wstr(s + 0x300) == "changed",
          "A text is converted for a W procedure");
    check(call_import(&c, "USER32.dll", "GetClassInfoW", {IMAGE_BASE, atom, s + 0x700}) == 1 &&
              rd32(s + 0x704) == defproc,
          "GetClassInfoW accepts the class atom");
    gm_put_wstr(s + 0x400, "prop", 8);
    check(call_import(&c, "USER32.dll", "SetPropW", {hwnd, s + 0x400, 0x1234}) == 1 &&
              call_import(&c, "USER32.dll", "GetPropW", {hwnd, s + 0x400}) == 0x1234,
          "window properties");
    host_set_time_source_pinned(100, 20);
    check(call_import(&c, "USER32.dll", "SetTimer", {hwnd, 7, 10, 0}) == 7, "SetTimer");
    host_pinned_clock_advance();
    uint32_t msg = s + 0x500;
    // Creation queues WM_MOVE/WM_SIZE, so ask specifically for the timer.
    check(call_import(&c, "USER32.dll", "PeekMessageW", {msg, hwnd, 0x113, 0x113, 1}) == 1 &&
              rd32(msg + 4) == 0x113 && rd32(msg + 8) == 7 && rd32(msg + 16) == 120,
          "WM_TIMER 7 is queued after the deadline with its timestamp");
    check(call_import(&c, "USER32.dll", "KillTimer", {hwnd, 7}) == 1, "KillTimer");
    host_clear_time_source();
    check(call_import(&c, "USER32.dll", "GetSysColor", {15}) == 0x00f0f0f0u, "COLOR_BTNFACE");
    check(call_import(&c, "USER32.dll", "DestroyWindow", {hwnd}) == 1, "DestroyWindow");
    while (call_import(&c, "USER32.dll", "PeekMessageA", {msg, 0, 0, 0, 1})) {
    }
}

static uint32_t g_vcl_timer_calls, g_vcl_enum_calls;
static void vcl_timer_callback(X86 *c) {
    check(arg(c, 1) == 0x113 && arg(c, 2) == 9 && arg(c, 3) == 220, "timer callback arguments");
    ++g_vcl_timer_calls;
    set_eax(c, 0);
}
static void vcl_enum_callback(X86 *c) {
    check(arg(c, 1) == 0x5678, "window enumeration lParam");
    ++g_vcl_enum_calls;
    set_eax(c, 1);
}
static uint32_t vcl_wait_calls;
static bool vcl_wait_once() {
    ++vcl_wait_calls;
    return false;
}
static uint32_t vcl_late_hwnd = 0;
// Posts a message on its third call only: a WaitMessage must keep waiting
// until then.
static bool vcl_wait_posts_late() {
    if (++vcl_wait_calls == 3)
        host_post_message(vcl_late_hwnd, 0x8003, 0, 0);
    return vcl_wait_calls >= 3;
}
static void test_user32_window_model() {
    section("window model lifetime, callbacks and waits");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00310000, msg = s + 0x500;
    gm_put_wstr(s, "TVclTestWindow", 32);
    uint32_t hwnd = call_import(&c, "USER32.dll", "CreateWindowExW",
                                {0, s, 0, 0x10000000, 10, 20, 200, 100, 0, 0, IMAGE_BASE, 0});
    uint32_t child = call_import(&c, "USER32.dll", "CreateWindowExW",
                                 {0, s, 0, 0x50000000, 3, 4, 50, 30, hwnd, 42, IMAGE_BASE, 0});
    check(child && call_import(&c, "USER32.dll", "GetParent", {child}) == hwnd &&
              call_import(&c, "USER32.dll", "IsChild", {hwnd, child}) == 1,
          "child parent relationship");
    check(call_import(&c, "USER32.dll", "GetDlgCtrlID", {child}) == 42, "child control ID");
    uint32_t popup = call_import(&c, "USER32.dll", "CreateWindowExW",
                                 {0, s, 0, 0x90000000u, 0, 0, 40, 30, hwnd, 0, IMAGE_BASE, 0});
    call_import(&c, "USER32.dll", "ShowOwnedPopups", {hwnd, 0});
    check(call_import(&c, "USER32.dll", "IsWindowVisible", {popup}) == 0,
          "ShowOwnedPopups hides owned windows");
    call_import(&c, "USER32.dll", "ShowOwnedPopups", {hwnd, 1});
    check(call_import(&c, "USER32.dll", "IsWindowVisible", {popup}) == 1,
          "ShowOwnedPopups restores only windows it hid");
    check(call_import(&c, "USER32.dll", "SetWindowRgn", {0xdead, 0, 0}) == 0,
          "SetWindowRgn rejects an invalid window");

    wr32(s + 0x100, 0);
    wr32(s + 0x104, 0);
    call_import(&c, "USER32.dll", "MapWindowPoints", {child, 0, s + 0x100, 1});
    check(rd32(s + 0x100) == 13 && rd32(s + 0x104) == 24, "nested client origin maps to screen");
    call_import(&c, "USER32.dll", "ScreenToClient", {child, s + 0x100});
    check(rd32(s + 0x100) == 0 && rd32(s + 0x104) == 0,
          "ScreenToClient uses the complete parent chain");
    wr32(s + 0x100, 0);
    wr32(s + 0x104, 0);
    call_import(&c, "USER32.dll", "ClientToScreen", {child, s + 0x100});
    check(rd32(s + 0x100) == 13 && rd32(s + 0x104) == 24,
          "ClientToScreen uses the complete parent chain");

    check(call_import(&c, "USER32.dll", "SetParent", {hwnd, child}) == 0 &&
              call_import(&c, "USER32.dll", "GetParent", {hwnd}) == 0,
          "parent cycles are rejected");
    uint32_t cb = imports_alloc_trampoline("test", "vcl_enum", vcl_enum_callback, 2);
    g_vcl_enum_calls = 0;
    check(call_import(&c, "USER32.dll", "EnumChildWindows", {hwnd, cb, 0x5678}) == 1 &&
              g_vcl_enum_calls == 1,
          "enumerate children through guest dispatch");
    check(call_import(&c, "USER32.dll", "EnableWindow", {child, 0}) == 0 &&
              call_import(&c, "USER32.dll", "IsWindowEnabled", {child}) == 0 &&
              call_import(&c, "USER32.dll", "EnableWindow", {child, 1}) == 1,
          "EnableWindow returns previous disabled state");
    call_import(&c, "USER32.dll", "SetFocus", {child});
    check(call_import(&c, "USER32.dll", "GetFocus", {}) == child, "focus state");
    call_import(&c, "USER32.dll", "SetCapture", {child});
    check(call_import(&c, "USER32.dll", "GetCapture", {}) == child &&
              call_import(&c, "USER32.dll", "ReleaseCapture", {}) == 1 &&
              call_import(&c, "USER32.dll", "GetCapture", {}) == 0,
          "capture state");
    call_import(&c, "USER32.dll", "ShowWindow", {hwnd, 2});
    check(call_import(&c, "USER32.dll", "IsIconic", {hwnd}) == 1, "minimized state");
    call_import(&c, "USER32.dll", "ShowWindow", {hwnd, 3});
    wr32(s + 0x200, 44);
    check(call_import(&c, "USER32.dll", "GetWindowPlacement", {hwnd, s + 0x200}) == 1 &&
              rd32(s + 0x208) == 3 && call_import(&c, "USER32.dll", "IsZoomed", {hwnd}) == 1,
          "placement and maximized state");
    check(call_import(&c, "USER32.dll", "CloseWindow", {hwnd}) == 1 &&
              call_import(&c, "USER32.dll", "IsIconic", {hwnd}) == 1 &&
              call_import(&c, "USER32.dll", "IsWindow", {hwnd}) == 1,
          "CloseWindow minimizes and preserves the window");
    call_import(&c, "USER32.dll", "ShowWindow", {hwnd, 9});
    check(call_import(&c, "USER32.dll", "IsIconic", {hwnd}) == 0,
          "restoring a closed window clears minimized state");
    uint32_t desktop = call_import(&c, "USER32.dll", "GetDesktopWindow", {});
    check(desktop && call_import(&c, "USER32.dll", "GetWindowRect", {desktop, s + 0x300}) == 1 &&
              rd32(s + 0x308) == call_import(&c, "USER32.dll", "GetSystemMetrics", {0}),
          "desktop rectangle matches display");
    check(call_import(&c, "USER32.dll", "MonitorFromWindow", {hwnd, 0}) == 1, "single monitor");
    wr32(s + 0x400, 104);
    check(call_import(&c, "USER32.dll", "GetMonitorInfoW", {1, s + 0x400}) == 1 &&
              rd32(s + 0x40c) == rd32(s + 0x308),
          "monitor geometry");
    while (call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0, 0, 1})) {
        call_import(&c, "USER32.dll", "DispatchMessageW", {msg});
    }
    host_set_time_source_pinned(200, 20);
    cb = imports_alloc_trampoline("test", "vcl_timer", vcl_timer_callback, 4);
    g_vcl_timer_calls = 0;
    call_import(&c, "USER32.dll", "SetTimer", {hwnd, 9, 10, cb});
    host_pinned_clock_advance();
    check(call_import(&c, "USER32.dll", "PeekMessageW", {msg, hwnd, 0x113, 0x113, 0}) == 1 &&
              g_vcl_timer_calls == 0,
          "timer callback waits for dispatch");
    check(call_import(&c, "USER32.dll", "PeekMessageW", {msg, hwnd, 0x113, 0x113, 1}) == 1,
          "peek does not duplicate timer");
    call_import(&c, "USER32.dll", "DispatchMessageW", {msg});
    check(g_vcl_timer_calls == 1 &&
              call_import(&c, "USER32.dll", "PeekMessageW", {msg, hwnd, 0x113, 0x113, 1}) == 0,
          "callback runs once through recomp_call");
    call_import(&c, "USER32.dll", "KillTimer", {hwnd, 9});
    host_set_time_source_pinned(0xfffffff0u, 20);
    uint32_t generated_timer = call_import(&c, "USER32.dll", "SetTimer", {hwnd, 0, 10, 0});
    host_pinned_clock_advance();
    check(generated_timer &&
              call_import(&c, "USER32.dll", "PeekMessageW", {msg, hwnd, 0x113, 0x113, 1}) == 1 &&
              rd32(msg + 8) == generated_timer,
          "zero-ID timer returns its queued ID across clock wrap");
    check(call_import(&c, "USER32.dll", "KillTimer", {hwnd, generated_timer}) == 1,
          "generated timer can be killed by its returned ID");

    host_clear_time_source();
    host_set_cursor_pos(21, 34);
    host_post_message(hwnd, 0x8002, 0, 0);
    call_import(&c, "USER32.dll", "PeekMessageW", {msg, hwnd, 0x8002, 0x8002, 1});
    host_set_cursor_pos(55, 89);
    check(call_import(&c, "USER32.dll", "GetMessagePos", {}) == ((34u << 16) | 21u),
          "GetMessagePos retains the retrieved message coordinates");
    gm_put_str(s + 0x700, "unsafe queued text", 32);
    check(call_import(&c, "USER32.dll", "PostMessageA", {hwnd, 0xc, 0, s + 0x700}) == 0,
          "posted text pointers are rejected instead of escaping a synchronous call");
    host_set_message_waiter(vcl_wait_once);
    vcl_wait_calls = 0;
    check(call_import(&c, "USER32.dll", "MsgWaitForMultipleObjects", {2, 0, 0, 0, 0}) == 0x102 &&
              vcl_wait_calls == 1,
          "empty wait pumps once then times out");
    host_post_message(hwnd, 0x8001, 0, 0);
    check(call_import(&c, "USER32.dll", "MsgWaitForMultipleObjectsEx", {2, 0, 0, 0, 0}) == 2 &&
              vcl_wait_calls == 1,
          "queued message returns count without pumping");
    call_import(&c, "USER32.dll", "WaitMessage", {});
    check(vcl_wait_calls == 2, "WaitMessage pumps once");
    while (call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0, 0, 1}))
        ;
    host_set_message_waiter(vcl_wait_posts_late);
    vcl_late_hwnd = hwnd;
    vcl_wait_calls = 0;
    call_import(&c, "USER32.dll", "WaitMessage", {});
    check(vcl_wait_calls == 3 &&
              call_import(&c, "USER32.dll", "PeekMessageW", {msg, hwnd, 0x8003, 0x8003, 1}) == 1,
          "WaitMessage waits until a message is queued");
    host_set_message_waiter(vcl_wait_once);
    // A signalled handle answers before any message, in both argument orders;
    // an unsignalled one leaves the message, then the timeout. This is the
    // wait Delphi's TThread.WaitFor makes for a thread's handle.
    {
        call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0, 0, 1}); // drain
        while (call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0, 0, 1}))
            ;
        uint32_t idle = call_import(&c, "KERNEL32.dll", "CreateEventA", {0, 1, 0, 0});
        uint32_t fired = call_import(&c, "KERNEL32.dll", "CreateEventA", {0, 1, 1, 0});
        wr32(s + 0x780, idle);
        wr32(s + 0x784, fired);
        check(call_import(&c, "USER32.dll", "MsgWaitForMultipleObjects",
                          {2, s + 0x780, 0, 1000, 0x40}) == 1,
              "a signalled handle is WAIT_OBJECT_0 + its index");
        check(call_import(&c, "USER32.dll", "MsgWaitForMultipleObjectsEx",
                          {2, s + 0x780, 1000, 0x40, 0}) == 1,
              "the Ex form takes the handles in the same place");
        check(call_import(&c, "USER32.dll", "MsgWaitForMultipleObjectsEx",
                          {2, s + 0x780, 0, 0x40, 1}) == 0x102,
              "MWMO_WAITALL waits for both");
        host_post_message(hwnd, 0x8001, 0, 0);
        check(call_import(&c, "USER32.dll", "MsgWaitForMultipleObjects",
                          {1, s + 0x780, 0, 0, 0xff}) == 1,
              "an unsignalled handle leaves the queued message");
        call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0, 0, 1});
        check(call_import(&c, "USER32.dll", "MsgWaitForMultipleObjects",
                          {1, s + 0x780, 0, 0, 0xff}) == 0x102,
              "and then the timeout");
        call_import(&c, "KERNEL32.dll", "CloseHandle", {idle});
        call_import(&c, "KERNEL32.dll", "CloseHandle", {fired});
    }
    host_set_message_waiter(nullptr);
    host_set_key_state(65, true);
    check(call_import(&c, "USER32.dll", "GetKeyboardState", {s + 0x600}) == 1 &&
              g_mem[s + 0x641] == 0x80,
          "keyboard state uses host input bridge");
    host_set_key_state(65, false);
    call_import(&c, "USER32.dll", "DestroyWindow", {hwnd});
    check(call_import(&c, "USER32.dll", "IsWindow", {popup}) == 0,
          "owner destruction retires owned popups");
    check(call_import(&c, "USER32.dll", "IsWindow", {child}) == 0 &&
              call_import(&c, "USER32.dll", "GetFocus", {}) == 0,
          "parent destruction retires children and focus");
    while (call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0, 0, 1})) {
    }
}

// The host has screen coordinates; USER32 owns hit testing, activation and capture.
static void test_host_mouse_routing() {
    section("host mouse routing across VCL windows");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00313000, msg = s + 0x200;
    static uint32_t activations, activation_top, activation_data, activate_result;
    activations = 0;
    activate_result = 1;
    uint32_t proc = imports_alloc_trampoline(
        "TEST", "MouseWindowProc",
        [](X86 *cc) {
            if (arg(cc, 1) == 0x21) {
                ++activations;
                activation_top = arg(cc, 2);
                activation_data = arg(cc, 3);
                set_eax(cc, activate_result);
            } else
                set_eax(cc, arg(cc, 1) == 0x81 ? 1 : 0);
        },
        4);
    memset(g_mem + s, 0, 40);
    wr32(s + 4, proc);
    gm_put_wstr(s + 0x100, "MouseRouting", 32);
    wr32(s + 36, s + 0x100);
    call_import(&c, "USER32.dll", "RegisterClassW", {s});
    auto window = [&](uint32_t ex, uint32_t style, int x, int y, uint32_t parent = 0) {
        return call_import(&c, "USER32.dll", "CreateWindowExW",
                           {ex, s + 0x100, 0, style, uint32_t(x), uint32_t(y), 100, 100, parent, 0,
                            IMAGE_BASE, 0});
    };
    uint32_t top = window(8, 0x10000000, 100, 120);
    uint32_t ordinary = window(0, 0x10000000, 110, 130);
    uint32_t hidden = window(8, 0, 100, 120);
    uint32_t disabled = window(8, 0x18000000, 100, 120);
    call_import(&c, "USER32.dll", "SetActiveWindow", {ordinary});
    auto take = [&](uint32_t message, uint32_t mk, int x, int y, uint32_t expected, int cx,
                    int cy) {
        host_post_mouse_message(message, mk, x, y);
        uint32_t got = call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0x200, 0x209, 1});
        check(got == 1 && rd32(msg) == expected && rd32(msg + 4) == message &&
                  rd32(msg + 8) == mk && int16_t(rd32(msg + 12)) == cx &&
                  int16_t(rd32(msg + 12) >> 16) == cy && int32_t(rd32(msg + 20)) == x &&
                  int32_t(rd32(msg + 24)) == y,
              "mouse %x goes to %08x at client (%d,%d), preserving MK flags and screen point",
              message, expected, cx, cy);
    };
    take(0x201, 0xd, 125, 150, top, 25, 30);
    check(activations == 1 && activation_top == top && activation_data == ((0x201u << 16) | 1) &&
              call_import(&c, "USER32.dll", "GetActiveWindow", {}) == top,
          "inactive topmost receives WM_MOUSEACTIVATE before its press");
    take(0x202, 0, 125, 150, top, 25, 30);
    check(activations == 1, "release does not activate again");
    uint32_t child = window(0, 0x50000000, 10, 12, top);
    take(0x200, 2, 125, 150, child, 15, 18);
    // The game's rendered frame supplies client points, unlike the virtual
    // desktop events above. Pumping a message must not undo the cursor's
    // conversion to screen pixels or offset the routed click a second time.
    for (uint32_t message : {0x200u, 0x201u, 0x202u}) {
        host_set_client_cursor_pos(top, 25, 30);
        host_post_client_mouse_message(top, message, 0, 25, 30);
        check(call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0x200, 0x209, 1}) == 1 &&
                  rd32(msg) == child && rd32(msg + 12) == ((18u << 16) | 15u) &&
                  rd32(msg + 20) == 125 && rd32(msg + 24) == 150,
              "host client mouse message reaches child at the matching point");
        call_import(&c, "USER32.dll", "GetCursorPos", {s + 0x300});
        check(rd32(s + 0x300) == 125 && rd32(s + 0x304) == 150,
              "mouse routing preserves the converted screen cursor position");
    }
    host_set_key_state(0x10, true);
    host_set_key_state(0x11, true);
    host_post_mouse_message(0x200, 0, 125, 150);
    check(call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0x200, 0x209, 1}) == 1 &&
              rd32(msg + 8) == 0xc,
          "host mouse messages include held Shift and Control MK flags");
    host_set_key_state(0x10, false);
    host_set_key_state(0x11, false);
    call_import(&c, "USER32.dll", "SetCapture", {child});
    take(0x204, 2, 50, 60, child, -60, -72);
    take(0x205, 0, 50, 60, child, -60, -72);
    call_import(&c, "USER32.dll", "ReleaseCapture", {});
    take(0x201, 1, 125, 150, child, 15, 18);
    call_import(&c, "USER32.dll", "EnableWindow", {top, 0});
    take(0x201, 1, 125, 150, ordinary, 15, 20);
    call_import(&c, "USER32.dll", "EnableWindow", {top, 1});
    call_import(&c, "USER32.dll", "ShowWindow", {hidden, 5});
    take(0x201, 1, 125, 150, hidden, 25, 30);
    call_import(&c, "USER32.dll", "SetWindowPos", {top, 0xffffffffu, 0, 0, 0, 0, 3});
    take(0x201, 1, 125, 150, child, 15, 18);
    activate_result = 4; // MA_NOACTIVATEANDEAT consumes the press.
    call_import(&c, "USER32.dll", "SetActiveWindow", {ordinary});
    host_post_mouse_message(0x201, 1, 125, 150);
    check(call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0x200, 0x209, 1}) == 0 &&
              call_import(&c, "USER32.dll", "GetActiveWindow", {}) == ordinary,
          "WM_MOUSEACTIVATE can refuse activation and eat the press");
    // Keyboard input goes to the focus, which is not the window the mouse is
    // over and not the first window created. A VCL application's first window
    // is the invisible application one, so a host that posted keystrokes
    // there typed into nothing.
    {
        call_import(&c, "USER32.dll", "SetFocus", {ordinary});
        host_post_key_message(0x0100, 0x41, 0x1e0001);
        check(call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0x100, 0x109, 1}) == 1 &&
                  rd32(msg) == ordinary && rd32(msg + 4) == 0x0100 && rd32(msg + 8) == 0x41,
              "a key goes to the focus window");
        call_import(&c, "USER32.dll", "SetFocus", {child});
        host_post_key_message(0x0102, 'x', 0x2d0001);
        check(call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0x100, 0x109, 1}) == 1 &&
                  rd32(msg) == child && rd32(msg + 8) == 'x',
              "and follows the focus when it moves, character messages included");
        // With no focus at all it falls back rather than dropping the key.
        call_import(&c, "USER32.dll", "SetFocus", {0});
        host_post_key_message(0x0101, 0x41, 0xc01e0001);
        check(call_import(&c, "USER32.dll", "PeekMessageW", {msg, 0, 0x100, 0x109, 1}) == 1 &&
                  rd32(msg) != 0,
              "and reaches some window when nothing holds the focus");
    }
    for (uint32_t w : {top, ordinary, hidden, disabled})
        call_import(&c, "USER32.dll", "DestroyWindow", {w});
}

static void test_user32_services() {
    section("menus, scrollbars, clipboard, resources and drawing");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00320000;
    uint32_t menu = call_import(&c, "USER32.dll", "CreateMenu", {}),
             sub = call_import(&c, "USER32.dll", "CreatePopupMenu", {});
    gm_put_wstr(s, "Item \xce\xa9", 32);
    check(menu && sub &&
              call_import(&c, "USER32.dll", "InsertMenuW", {menu, 0xffffffffu, 0x410, sub, s}) == 1,
          "insert Unicode popup menu");
    memset(g_mem + s + 0x100, 0, 48);
    wr32(s + 0x100, 48);
    wr32(s + 0x104, 0x43);
    wr32(s + 0x110, 77);
    wr32(s + 0x124, s);
    check(call_import(&c, "USER32.dll", "InsertMenuItemW", {sub, 0, 1, s + 0x100}) == 1 &&
              call_import(&c, "USER32.dll", "GetSubMenu", {menu, 0}) == sub &&
              call_import(&c, "USER32.dll", "GetMenuItemID", {sub, 0}) == 77,
          "menu item IDs and submenu handles");
    check(call_import(&c, "USER32.dll", "GetMenuStringW", {menu, 0, s + 0x200, 32, 0x400}) == 6 &&
              gm_wstr(s + 0x200) == "Item \xce\xa9",
          "menu text round trip");
    check(call_import(&c, "USER32.dll", "CheckMenuItem", {sub, 77, 8}) == 0 &&
              (call_import(&c, "USER32.dll", "GetMenuState", {sub, 77, 0}) & 8),
          "checked menu state");
    wr32(s + 0x104, 0x42);
    wr32(s + 0x124, s + 0x200);
    wr32(s + 0x128, 32);
    check(call_import(&c, "USER32.dll", "GetMenuItemInfoW", {sub, 77, 0, s + 0x100}) == 1 &&
              rd32(s + 0x128) == 6 && gm_wstr(s + 0x200) == "Item \xce\xa9",
          "MENUITEMINFOW guest layout and text units");
    check(call_import(&c, "USER32.dll", "RemoveMenu", {menu, 0, 0x400}) == 1 &&
              call_import(&c, "USER32.dll", "GetMenuItemCount", {sub}) == 1,
          "RemoveMenu retains submenu ownership");
    call_import(&c, "USER32.dll", "InsertMenuW", {menu, 0xffffffffu, 0x410, sub, s});
    check(call_import(&c, "USER32.dll", "DeleteMenu", {menu, 0, 0x400}) == 1 &&
              call_import(&c, "USER32.dll", "GetMenuItemCount", {sub}) == 0xffffffffu,
          "DeleteMenu destroys attached submenu");
    call_import(&c, "USER32.dll", "DestroyMenu", {menu});
    check(call_import(&c, "USER32.dll", "SetScrollRange", {123, 1, 10, 80, 0}) == 1 &&
              call_import(&c, "USER32.dll", "GetScrollRange", {123, 1, s + 0x300, s + 0x304}) ==
                  1 &&
              rd32(s + 0x300) == 10 && rd32(s + 0x304) == 80,
          "plain scrollbar range");
    call_import(&c, "USER32.dll", "SetScrollPos", {123, 1, 30, 0});
    check(call_import(&c, "COMCTL32.dll", "FlatSB_GetScrollPos", {123, 1}) == 30,
          "plain and flat scrollbar state is shared");
    wr32(s + 0x400, 28);
    wr32(s + 0x404, 6);
    wr32(s + 0x410, 10);
    wr32(s + 0x414, 100);
    check(call_import(&c, "USER32.dll", "SetScrollInfo", {123, 1, s + 0x400, 0}) == 71 &&
              call_import(&c, "COMCTL32.dll", "FlatSB_GetScrollPos", {123, 1}) == 71,
          "shared scrollbar page clamping");
    gm_put_wstr(s, "Runtime.Format", 32);
    uint32_t format = call_import(&c, "USER32.dll", "RegisterClipboardFormatW", {s});
    check(format >= 0xc000 &&
              call_import(&c, "USER32.dll", "RegisterClipboardFormatW", {s}) == format,
          "clipboard format registration is stable");
    uint32_t message = call_import(&c, "USER32.dll", "RegisterWindowMessageW", {s});
    check(message >= 0xc000 &&
              call_import(&c, "USER32.dll", "RegisterWindowMessageW", {s}) == message,
          "registered window message is stable");
    call_import(&c, "USER32.dll", "OpenClipboard", {0});
    call_import(&c, "USER32.dll", "EmptyClipboard", {});
    uint32_t data = heap_alloc(16, true);
    check(call_import(&c, "USER32.dll", "SetClipboardData", {format, data}) == data &&
              call_import(&c, "USER32.dll", "GetClipboardData", {format}) == data &&
              call_import(&c, "USER32.dll", "IsClipboardFormatAvailable", {format}) == 1,
          "clipboard data round trip");
    call_import(&c, "USER32.dll", "EmptyClipboard", {});
    check(call_import(&c, "USER32.dll", "IsClipboardFormatAvailable", {format}) == 0,
          "EmptyClipboard clears formats");
    call_import(&c, "USER32.dll", "CloseClipboard", {});
    uint32_t hook = call_import(&c, "USER32.dll", "SetWindowsHookExW", {3, 1, 0, 0});
    check(hook && call_import(&c, "USER32.dll", "UnhookWindowsHookEx", {hook}) == 1 &&
              call_import(&c, "USER32.dll", "UnhookWindowsHookEx", {hook}) == 0,
          "hook handle lifetime");
    wr8(s, 1);
    wr16(s + 2, 65);
    wr16(s + 4, 77);
    uint32_t accel = call_import(&c, "USER32.dll", "CreateAcceleratorTableW", {s, 1});
    check(accel && call_import(&c, "USER32.dll", "DestroyAcceleratorTable", {accel}) == 1,
          "accelerator table copies six-byte guest entries");
    // Resource string lookup must keep the length prefix out of the text.
    std::vector<ResourceName> names;
    bool checked = false;
    if (resource_names(6, &names))
        for (const auto &name : names) {
            if (name.is_string)
                continue;
            uint32_t bytes = 0, p = resource_data(resource_find(6, name.id), &bytes),
                     end = p + bytes;
            for (uint32_t i = 0; p && i < 16 && p + 2 <= end; ++i) {
                uint32_t n = rd16(p);
                p += 2;
                if (n > (end - p) / 2)
                    break;
                if (n) {
                    uint32_t id = (name.id - 1) * 16 + i;
                    check(call_import(&c, "USER32.dll", "LoadStringW",
                                      {IMAGE_BASE, id, s + 0x500, 0}) == n &&
                              rd32(s + 0x500) == p,
                          "LoadStringW zero-capacity resource pointer");
                    check(call_import(&c, "USER32.dll", "LoadStringW",
                                      {IMAGE_BASE, id, s + 0x600, 4}) == std::min(n, 3u) &&
                              rd16(s + 0x600 + std::min(n, 3u) * 2) == 0,
                          "LoadStringW short buffer terminates");
                    checked = true;
                    break;
                }
                p += n * 2;
            }
            if (checked)
                break;
        }
    if (!checked) {
        printf("  [skip] image has no nonempty string resource\n");
        ++g_skips;
    }
    // Draw a system brush and a constructed icon into an actual guest DIB.
    uint32_t hdr = s + 0x800;
    memset(g_mem + hdr, 0, 40);
    wr32(hdr, 40);
    wr32(hdr + 4, 4);
    wr32(hdr + 8, 0xfffffffcu);
    wr16(hdr + 12, 1);
    wr16(hdr + 14, 32);
    uint32_t bmp = call_import(&c, "GDI32.dll", "CreateDIBSection", {0, hdr, 0, s + 0x900, 0, 0}),
             bits = rd32(s + 0x900);
    uint32_t dc = call_import(&c, "GDI32.dll", "CreateCompatibleDC", {0});
    call_import(&c, "GDI32.dll", "SelectObject", {dc, bmp});
    wr32(s + 0xa00, 0);
    wr32(s + 0xa04, 0);
    wr32(s + 0xa08, 4);
    wr32(s + 0xa0c, 4);
    uint32_t brush = call_import(&c, "USER32.dll", "GetSysColorBrush", {15});
    check(brush && call_import(&c, "USER32.dll", "FillRect", {dc, s + 0xa00, brush}) == 1 &&
              (rd32(bits) & 0xffffff) == 0xf0f0f0,
          "FillRect paints a system color brush into the DIB");
    uint32_t before_focus = rd32(bits);
    check(call_import(&c, "USER32.dll", "DrawFocusRect", {dc, s + 0xa00}) == 1 &&
              rd32(bits) != before_focus &&
              call_import(&c, "USER32.dll", "DrawFocusRect", {dc, s + 0xa00}) == 1 &&
              rd32(bits) == before_focus,
          "focus rectangle XOR restores pixels on second draw");
    memset(g_mem + s + 0xb00, 0, 16);
    for (uint32_t i = 0; i < 4; ++i)
        wr32(s + 0xb20 + i * 4, 0xff0000ff);
    uint32_t icon =
        call_import(&c, "USER32.dll", "CreateIcon", {0, 2, 2, 1, 32, s + 0xb00, s + 0xb20});
    check(icon && call_import(&c, "USER32.dll", "DrawIcon", {dc, 0, 0, icon}) == 1 &&
              (rd32(bits) & 0xffffff) == 0xff,
          "DrawIcon paints owned pixels");
    check(call_import(&c, "USER32.dll", "GetIconInfo", {icon, s + 0xc00}) == 1 &&
              rd32(s + 0xc10) != 0,
          "GetIconInfo returns caller-owned bitmaps");
    call_import(&c, "GDI32.dll", "DeleteObject", {rd32(s + 0xc0c)});
    call_import(&c, "GDI32.dll", "DeleteObject", {rd32(s + 0xc10)});
    call_import(&c, "USER32.dll", "DestroyIcon", {icon});
    call_import(&c, "GDI32.dll", "DeleteDC", {dc});
    call_import(&c, "GDI32.dll", "DeleteObject", {bmp});
}

// Auxiliary modules from game.toml [modules.aux.*]: the loader mapped each at
// its configured base, LoadLibrary hands that base out as the handle and
// GetProcAddress answers from the module's own export directory. A game with
// no auxiliary modules exercises only the empty registry.
// Exercise lookup with synthetic PE export data, including data exports,
// holes, forwarders and malformed RVAs. No translated routines are called.
static void test_pe_exports() {
    section("PE code and data exports");
    LoaderModule m;
    m.base = 0x00310000;
    m.size = 0x1000;
    m.export_rva = 0x100;
    m.export_size = 0x100;
    memset(g_mem + m.base, 0, m.size);
    uint32_t dir = m.base + m.export_rva;
    wr32(dir + 16, 7);
    wr32(dir + 20, 3);
    wr32(dir + 24, 2);
    wr32(dir + 28, 0x200);
    wr32(dir + 32, 0x220);
    wr32(dir + 36, 0x230);
    wr32(m.base + 0x200, 0x400);
    wr32(m.base + 0x204, 0x500);
    wr32(m.base + 0x220, 0x300);
    wr32(m.base + 0x224, 0x310);
    wr16(m.base + 0x230, 0);
    wr16(m.base + 0x232, 1);
    gm_put_str(m.base + 0x300, "Code", 16);
    gm_put_str(m.base + 0x310, "Data", 16);
    check(loader_module_export(m, "Code") == m.base + 0x400,
          "code export resolves to its guest address");
    uint32_t data = loader_module_export(m, "Data");
    check(data == m.base + 0x500, "data export resolves into the image too");
    if (data) {
        wr32(data, 0x12345678);
        check(rd32(m.base + 0x500) == 0x12345678, "data export shares storage with its image");
    }
    check(loader_module_export(m, "code") == 0 && loader_module_export(m, "Missing") == 0,
          "export names are case sensitive and missing exports fail");
    check(loader_module_export_ordinal(m, 7) == m.base + 0x400 &&
              loader_module_export_ordinal(m, 8) == m.base + 0x500,
          "nonzero ordinal base resolves code and data");
    check(loader_module_export_ordinal(m, 6) == 0 && loader_module_export_ordinal(m, 9) == 0 &&
              loader_module_export_ordinal(m, 10) == 0,
          "ordinal underflow, holes and overflow fail");
    wr32(m.base + 0x200, 0x150);
    check(loader_module_export(m, "Code") == 0 && loader_module_export_ordinal(m, 7) == 0,
          "unsupported forwarders are not returned as code");
    wr32(m.base + 0x200, m.size);
    check(loader_module_export(m, "Code") == 0 && loader_module_export_ordinal(m, 7) == 0,
          "out-of-image exports fail");
    wr32(dir + 20, 0x40000001);
    check(loader_module_export(m, "Code") == 0 && loader_module_export_ordinal(m, 7) == 0,
          "oversized address tables fail without arithmetic wrap");
    m.export_rva = m.size - 20;
    check(loader_module_export(m, "Code") == 0 && loader_module_export_ordinal(m, 7) == 0,
          "truncated export directories fail");
}

namespace dll_lifetime_test {
constexpr uint32_t entry = 0x0e110100;
LoaderModule *mapped;
uint32_t attaches, detaches;
bool fail_attach;

void dll_entry(X86 *c) {
    uint32_t reason = arg(c, 1);
    check(arg(c, 0) == mapped->base && arg(c, 2) == 0,
          "DllMain receives the module base and null reserved argument");
    if (reason == 1) {
        ++attaches;
        check(memcmp(g_mem + mapped->base, mapped->initial_image.data(), mapped->size) == 0,
              "attach starts with pristine globals, PE headers and patched imports");
        wr32(mapped->base + 0x20, 0x12345678);
        c->r[R_EAX] = fail_attach ? 0 : 1;
    } else {
        ++detaches;
        check(reason == 0 && rd32(mapped->base + 0x20) == 0x12345678,
              "detach runs before the loaded module's state is discarded");
        wr32(mapped->base + 0x20, 0xbad);
        c->r[R_EAX] = 1;
    }
    c->eip = rd32(c->r[R_ESP]);
    c->r[R_ESP] += 16; // stdcall DllMain(base, reason, reserved)
    recomp_return(c);
}
} // namespace dll_lifetime_test

// Replace only an auxiliary entry with a synthetic translated DllMain. The
// real pinned PE mapping and real KERNEL32 imports exercise module lifetimes
// without executing game initialization in the stub-linked runtime suite.
static void test_dll_lifetime() {
    section("DLL unload and reload");
    using namespace dll_lifetime_test;
    if (!loader_module_count()) {
        ++g_skips;
        printf("  [skip] no configured auxiliary DLL\n");
        return;
    }
    mapped = loader_module_named(loader_module(0)->name.c_str());
    LoaderModule saved = *mapped;
    static const uint32_t addresses[] = {entry};
    static void (*const functions[])(X86 *) = {dll_entry};
    static RecompHookFn hooks[1]{};
    static uint8_t hooked[1]{};
    static const RecompModule module = {"dll-lifetime-test",
                                        entry,
                                        entry + 1,
                                        addresses,
                                        1,
                                        functions,
                                        hooks,
                                        hooked,
                                        nullptr,
                                        0,
                                        nullptr};
    recomp_module_register(&module);
    mapped->entry = entry;
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00300000, esp = c.r[R_ESP];
    gm_put_str(s, mapped->name.c_str(), 128);
    gm_put_wstr(s + 128, mapped->name.c_str(), 128);
    check(call_import(&c, "KERNEL32.dll", "GetModuleHandleA", {s}) == 0 && attaches == 0,
          "lookup of a mapped but unloaded DLL does not initialize it");
    fail_attach = true;
    check(call_import(&c, "KERNEL32.dll", "LoadLibraryA", {s}) == 0 &&
              call_import(&c, "KERNEL32.dll", "GetLastError", {}) == 1114 && attaches == 1 &&
              detaches == 1 && !mapped->attached && !mapped->load_refs,
          "failed initialization detaches and reports ERROR_DLL_INIT_FAILED");
    fail_attach = false;
    for (uint32_t cycle = 0; cycle < 3; ++cycle) {
        uint32_t before_attach = attaches, before_detach = detaches;
        uint32_t h = call_import(&c, "KERNEL32.dll", "LoadLibraryA", {s});
        check(h == mapped->base && attaches == before_attach + 1,
              "load cycle %u initializes the DLL exactly once", cycle);
        check(call_import(&c, "KERNEL32.dll", "LoadLibraryW", {s + 128}) == h &&
                  attaches == before_attach + 1,
              "a second load acquires a reference without initializing again");
        check(call_import(&c, "KERNEL32.dll", "GetModuleHandleA", {s}) == h,
              "handle lookup finds the attached DLL");
        check(call_import(&c, "KERNEL32.dll", "FreeLibrary", {h}) == 1 &&
                  detaches == before_detach && mapped->attached,
              "first release keeps a multiply loaded DLL alive");
        check(call_import(&c, "KERNEL32.dll", "FreeLibrary", {h}) == 1 &&
                  detaches == before_detach + 1 && !mapped->attached,
              "final release detaches once; handle lookup acquired no reference");
        check(call_import(&c, "KERNEL32.dll", "GetModuleHandleW", {s + 128}) == 0 &&
                  call_import(&c, "KERNEL32.dll", "FreeLibrary", {h}) == 0 &&
                  detaches == before_detach + 1,
              "unloaded DLL is absent and cannot be detached twice");
    }
    gm_put_str(s + 384, RECOMP_EXECUTABLE, 128);
    uint32_t before_attach = attaches, before_detach = detaches;
    check(call_import(&c, "KERNEL32.dll", "LoadLibraryA", {s + 384}) == loader_image_base() &&
              call_import(&c, "KERNEL32.dll", "FreeLibrary", {loader_image_base()}) == 0 &&
              loader_module_named(RECOMP_EXECUTABLE)->attached && attaches == before_attach &&
              detaches == before_detach,
          "main executable cannot be reinitialized or unloaded as a DLL");
    check(c.r[R_ESP] == esp && c.eip == g_fake_ret,
          "nested DllMain callbacks preserve the import caller's stack and return");
    *mapped = saved;
    memcpy(g_mem + mapped->base, mapped->initial_image.data(), mapped->initial_image.size());
}

static void test_auxiliary_modules() {
    section("auxiliary modules");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00300000;
    const LoaderModule *main = loader_module_named(RECOMP_EXECUTABLE);
    check(main && main->base == loader_image_base() && main->attached,
          "main executable participates in module lookups without DLL initialization");
    gm_put_str(s, RECOMP_EXECUTABLE, 128);
    check(call_import(&c, "KERNEL32.dll", "GetModuleHandleA", {s}) == loader_image_base(),
          "main executable's configured name returns its image handle");
    check(loader_module_count() == RECOMP_AUX_MODULE_COUNT, "%u auxiliary modules mapped",
          loader_module_count());
    for (uint32_t i = 0; const LoaderModule *m = loader_module(i); ++i) {
        gm_put_wstr(s, m->name.c_str(), 64);
        gm_put_str(s + 128, m->name.c_str(), 64);
        uint32_t h = call_import(&c, "KERNEL32.dll", "LoadLibraryW", {s});
        check(h == m->base, "LoadLibraryW(%s) -> %08x, the configured base %08x", m->name.c_str(),
              h, m->base);
        check(call_import(&c, "KERNEL32.dll", "GetModuleHandleA", {s + 128}) == h,
              "GetModuleHandleA(%s) shares the handle", m->name.c_str());
        check(call_import(&c, "KERNEL32.dll", "IsBadCodePtr", {m->base}) == 0,
              "the module's base is code");
        check(rd16(m->base) == 0x5a4d, "PE headers mapped at %08x", m->base);
        // Compare every import from the main executable against that image's
        // export table. In particular, a data import must not be a trampoline.
        uint32_t opt = m->base + rd32(m->base + 0x3c) + 24;
        uint32_t imports = rd32(opt + 104), main_imports = 0;
        bool bound = true;
        for (uint32_t d = m->base + imports; imports && rd32(d + 12); d += 20) {
            if (os_strcasecmp(gm_str(m->base + rd32(d + 12)).c_str(), RECOMP_EXECUTABLE))
                continue;
            uint32_t original = rd32(d), iat = rd32(d + 16);
            for (uint32_t n = 0; original && rd32(m->base + original + n * 4); ++n) {
                uint32_t t = rd32(m->base + original + n * 4);
                uint32_t expected =
                    !main ? 0
                    : (t & 0x80000000u)
                        ? loader_module_export_ordinal(*main, t & 0xffff)
                        : loader_module_export(*main, gm_str(m->base + t + 2).c_str());
                bound &= expected && rd32(m->base + iat + n * 4) == expected;
                ++main_imports;
            }
        }
        check(bound, "%s binds %u imports directly to main-image exports", m->name.c_str(),
              main_imports);
        gm_put_str(s + 256, "no-such-export", 64);
        check(call_import(&c, "KERNEL32.dll", "GetProcAddress", {h, s + 256}) == 0,
              "GetProcAddress(%s, no-such-export) -> 0", m->name.c_str());
        if (!m->export_rva)
            continue;
        uint32_t dir = m->base + m->export_rva;
        uint32_t nnames = rd32(dir + 24), names = rd32(dir + 32);
        for (uint32_t n = 0; n < nnames && n < 4; ++n) {
            std::string name = gm_str(m->base + rd32(m->base + names + 4 * n), 260);
            gm_put_str(s + 256, name.c_str(), 128);
            uint32_t a = call_import(&c, "KERNEL32.dll", "GetProcAddress", {h, s + 256});
            check(a >= m->base && a < m->base + m->size &&
                      a == loader_module_export(*m, name.c_str()),
                  "GetProcAddress(%s, %s) -> %08x inside the module", m->name.c_str(), name.c_str(),
                  a);
        }
        check(recomp_module_containing(m->base) == nullptr ||
                  recomp_module_containing(m->base)->base == m->base,
              "a registered translation of %s agrees on its base", m->name.c_str());
    }
}

static void test_delphi_dlls() {
    section("Delphi DLLs");
    X86 c;
    loader_init_context(&c);
    uint32_t s = 0x00300000;
    // BSTR: length prefix in bytes, text, terminator.
    gm_put_wstr(s, "hello", 16);
    uint32_t b = call_import(&c, "OLEAUT32.dll", "SysAllocStringLen", {s, 5});
    check(b != 0 && rd32(b - 4) == 10 && gm_wstr(b) == "hello", "SysAllocStringLen");
    check(call_import(&c, "OLEAUT32.dll", "SysFreeString", {b}) == 0, "SysFreeString");
    // Registry through the W API, read back through the A one.
    uint32_t key = s + 0x100, hkey_out = s + 0x200;
    gm_put_wstr(key, "Software\\RecompTest", 64);
    check(call_import(&c, "ADVAPI32.dll", "RegCreateKeyExW",
                      {0x80000001u, key, 0, 0, 0, 0xf003f, 0, hkey_out, 0}) == 0,
          "RegCreateKeyExW");
    uint32_t hk = rd32(hkey_out);
    gm_put_wstr(s + 0x300, "Name", 16);
    gm_put_wstr(s + 0x400, "value", 16);
    check(call_import(&c, "ADVAPI32.dll", "RegSetValueExW", {hk, s + 0x300, 0, 1, s + 0x400, 12}) ==
              0,
          "RegSetValueExW");
    gm_put_str(s + 0x500, "Name", 16);
    wr32(s + 0x600, 64);
    check(call_import(&c, "ADVAPI32.dll", "RegQueryValueExA",
                      {hk, s + 0x500, 0, 0, s + 0x700, s + 0x600}) == 0 &&
              gm_str(s + 0x700) == "value",
          "RegQueryValueExA reads what RegSetValueExW wrote");
    {
        VersionResourceFixture version;
        if (version.valid()) {
            // version.dll W over a controlled resource, even if this game has none.
            gm_put_wstr(s, RECOMP_EXECUTABLE, 128);
            uint32_t size = call_import(&c, "VERSION.dll", "GetFileVersionInfoSizeW", {s, 0});
            check(size > 0, "GetFileVersionInfoSizeW = %u", size);
            check(call_import(&c, "VERSION.dll", "GetFileVersionInfoW", {s, 0, size, s + 0x1000}) ==
                      1,
                  "GetFileVersionInfoW");
            gm_put_wstr(s + 0x800, "\\", 8);
            check(call_import(&c, "VERSION.dll", "VerQueryValueW",
                              {s + 0x1000, s + 0x800, s + 0x900, s + 0x904}) == 1 &&
                      rd32(rd32(s + 0x900)) == 0xfeef04bdu,
                  "VerQueryValueW(\\) finds VS_FIXEDFILEINFO");
            check(rd32(s + 0x904) == 52 && rd32(rd32(s + 0x900) + 8) == 0x00010002 &&
                      rd32(rd32(s + 0x900) + 12) == 0x00030004,
                  "wide version query returns the fixture's length and version");
        }
    }
    {
        VersionResourceFixture missing(false);
        if (missing.valid()) {
            gm_put_wstr(s, RECOMP_EXECUTABLE, 128);
            check(call_import(&c, "VERSION.dll", "GetFileVersionInfoSizeW", {s, 0}) == 0 &&
                      get_last_error() == 1813,
                  "wide version size reports the absent resource");
            check(call_import(&c, "VERSION.dll", "GetFileVersionInfoW", {s, 0, 256, s + 0x1000}) ==
                          0 &&
                      get_last_error() == 1813,
                  "wide version copy reports the absent resource");
        }
    }
    // The rest answer as documented for a machine with nothing attached.
    wr32(s + 0xa00, 0);
    check(call_import(&c, "WINSPOOL.DRV", "EnumPrintersW", {2, 0, 2, 0, 0, s + 0xa04, s + 0xa00}) ==
                  1 &&
              rd32(s + 0xa00) == 0,
          "EnumPrintersW: no printers");
    check(call_import(&c, "NETAPI32.dll", "NetWkstaGetInfo", {0, 100, s + 0xb00}) == 50,
          "NetWkstaGetInfo: not supported");
    check(call_import(&c, "OLE32.dll", "OleInitialize", {0}) == 0, "OleInitialize");
    uint32_t il = call_import(&c, "COMCTL32.dll", "ImageList_Create", {16, 16, 0x20, 4, 4});
    check(il != 0 && call_import(&c, "COMCTL32.dll", "ImageList_GetImageCount", {il}) == 0,
          "ImageList_Create");
    call_import(&c, "COMCTL32.dll", "ImageList_Destroy", {il});
    call_import(&c, "ADVAPI32.dll", "RegCloseKey", {hk});
}

static void test_import_return_trace() {
    section("verbose import return values");
    char dir[] = "build/recomp/import-trace-XXXXXX", exe[4096];
    if (!check(os_mkdtemp(dir) == 0, "created an import trace directory"))
        return;
    std::string path = std::string(dir) + "/returns.log";
    check(os_exe_path(exe, sizeof exe) == 0, "import trace knows its executable");
    const char *args[] = {exe, "--child-import-trace", path.c_str(), nullptr};
    int64_t pid = 0;
    int code = -1;
    check(os_spawn(args, &pid) == 0 && os_wait(pid, &code) == 0 && code == 0,
          "import trace child exits cleanly (exit %d)", code);
    std::string text;
    if (FILE *log = fopen(path.c_str(), "r")) {
        char line[1024];
        while (fgets(line, sizeof line, log))
            text += line;
        fclose(log);
    }
    check(text.find("<- KERNEL32.dll!GetCurrentProcess (eax=ffffffff)") != std::string::npos,
          "verbose trace records the actual nonzero return value");
    check(text.find("<- USER32.dll!IsWindow (eax=00000000)") != std::string::npos,
          "verbose trace records a FALSE return value");
    remove_tree(dir);
}

int main(int argc, char **argv) {
    const bool child = argc > 1 && strcmp(argv[1], "--child-setjmp-abort") == 0;
    if (child) {
        freopen(os_null_device(), "w", stdout);
        freopen(os_null_device(), "w", stderr);
    }
    os_setenv("RECOMP_REGISTRY", "build/recomp/registry-test.json");
    if (!recomp_env("LOG"))
        os_setenv("RECOMP_LOG", "1");
    // The suite loads the developer's game image; without one (the kit's
    // stub game, a checkout with no original/) there is nothing to test.
    if (FILE *image = fopen(RECOMP_DEVELOPER_EXE, "rb"))
        fclose(image);
    else {
        printf("runtime_tests: no game image at %s; the game-backed checks were skipped\n",
               RECOMP_DEVELOPER_EXE);
        return 0;
    }

    if (argc == 2 && strcmp(argv[1], "--child-exit-stops-workers") == 0)
        return child_exit_stops_workers();
    if (argc == 3 && strcmp(argv[1], "--child-import-trace") == 0) {
        if (!freopen(argv[2], "w", stderr))
            return 2;
        os_setenv("RECOMP_LOG", "2");
        mem_init();
        imports_init();
        X86 c;
        loader_init_context(&c);
        call_import(&c, "KERNEL32.dll", "GetCurrentProcess", {});
        call_import(&c, "USER32.dll", "IsWindow", {0});
        mem_shutdown();
        return g_failures ? 1 : 0;
    }
    if (argc == 3 && strcmp(argv[1], "--child-heap-refusal") == 0) {
        if (!freopen(argv[2], "w", stderr))
            return 2;
        mem_init();
        imports_init();
        if (!loader_load(nullptr))
            return 3;
        X86 *c = loader_context();
        loader_init_context(c);
        c->r[R_EAX] = 0x13579bdf;
        c->r[R_EDI] = 0x2468ace0;
        c->r[R_EBP] = c->r[R_ESP] - 64;
        wr32(c->r[R_EBP], 0);
        wr32(c->r[R_EBP] + 4, loader_image_base() + 0x1234);
        c->r[R_ESP] -= 96;
        wr32(c->r[R_ESP], loader_image_base() + 0x2345);
        return heap_alloc(0xffffffffu) == 0 ? 0 : 4;
    }

    if (argc == 2 && strcmp(argv[1], "--sched-race") == 0) {
        // Only the scheduler race check: the ThreadSanitizer build runs this.
        mem_init();
        imports_init();
        if (!loader_load(nullptr))
            return 2;
        X86 *c = loader_context();
        loader_init_context(c);
        scratch = 0x0ee00000;
        test_sched_spawn_race(c);
        printf("\n%d sched-race checks, %d failures\n", g_checks, g_failures);
        return g_failures ? 1 : 0;
    }
    if (argc == 2 && strcmp(argv[1], "--thunks-only") == 0) {
        mem_init();
        imports_init();
        if (!loader_load(nullptr))
            return 2;
        X86 *c = loader_context();
        loader_init_context(c);
        scratch = 0x0ee00000;
        test_guest_thunks(c);
        printf("\n%d thunk checks, %d failures\n", g_checks, g_failures);
        mem_shutdown();
        return g_failures ? 1 : 0;
    }

    test_loader();
    test_cpuid_features();
    test_discovery_recorder();
    test_pe_exports();
    test_dll_lifetime();
    test_auxiliary_modules();
    test_resumable_stacks();
    if (argc == 2 && strcmp(argv[1], "--startup-contracts") == 0) {
        // A new port can validate mapping and registry contracts before its
        // remaining platform APIs or optional resources are supported.
        scratch = 0x0ee00000;
        test_startup_apis(loader_context());
        test_registry(loader_context());
        printf("\n%d startup checks, %d failures, %d skipped\n", g_checks, g_failures, g_skips);
        return g_failures ? 1 : 0;
    }
    scratch = 0x0ee00000;
    test_startup_apis(loader_context());
    test_import_return_trace();
    test_modules_and_wide();
    test_preferred_ui_languages();
    test_session_notification_service_unavailable();
    test_buffered_paint_unavailable();
    test_propvariant_clear();
    test_media_foundation_unavailable();
    test_kernel32_wide();
    test_delphi_dlls();
    test_delphi_automation();
    test_ansi_case_and_string_types();
    test_delphi_registry_version();
    test_delphi_misc();
    test_delphi_controls();
    test_display_settings();
    test_user32_vcl();
    test_user32_window_model();
    test_host_mouse_routing();
    test_user32_services();
    X86 *c = loader_context();
    if (child)
        child_setjmp_abort(c);
    scratch = 0x0ee00000; // scratch area below the stack, inside the arena

    test_allocator();
    test_heap_guard();
    test_heap_readonly();
    test_heap_kit_span();
    test_heap_shims(c);
    test_memory_shims_2(c);
    test_files(c);
    test_version_resource(c);
    test_pinned_clock(c);
    test_guest_pause(c);
    test_cadence_trace(c);
    test_misc_shims(c);
    test_windows_version(c);
    test_boot_shims(c);
    test_gdi_and_com(c);
    test_cxx_throw_description(c);
    test_native_draw_waits(c);
    test_midi(c);
    test_windows(c);
    test_synchronous_geometry(c);
    test_guest_thunks(c);
    test_callbacks(c);
    test_scheduling(c);
    test_sched_spawn_race(c);
    test_exit_process_stops_workers(c);
    test_mod_seams(c);
    test_input_wakes_a_parked_thread(c);
    test_intrinsics(c);
    test_undeliverable_calls(c);
    test_registry(c);
    test_import_coverage(c);
    test_rejected_import_decorations();
    test_queued_input_wakes_a_blocked_wait(c);
    // Last: it retires the main thread.
    test_run_thread_finished(c);

    printf("\n%d checks, %d failures, %d skipped\n", g_checks, g_failures, g_skips);
    return g_failures ? 1 : 0;
}
