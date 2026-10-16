// UTF-16 window entry points share the ANSI window and message model. Temporary
// conversion buffers live in the guest arena and are freed after synchronous callbacks.
#include "user32_internal.h"
#include "resources.h"
#include "memory.h"
#include "gdi_image.h"
#include <algorithm>
#include <cctype>
#include <cstring>

namespace user32 {
uint32_t wide_units(const std::string &s) {
    uint32_t n = 0;
    for (unsigned char ch : s)
        if ((ch & 0xc0) != 0x80)
            n += ch >= 0xf0 ? 2 : 1;
    return n;
}
uint32_t put_text(uint32_t out, uint32_t cap, const std::string &text, bool wide) {
    if (!out || !cap || cap > 0x1000000 || !gm_valid(out, cap * (wide ? 2 : 1)))
        return 0;
    return wide ? gm_put_wstr(out, text, cap) : gm_put_str(out, text.c_str(), cap);
}
void alias_ansi(X86 *c, const char *name) {
    for (size_t i = 0; i < g_user32_shim_count; ++i)
        if (!strcmp(g_user32_shims[i].name, name)) {
            g_user32_shims[i].fn(c);
            return;
        }
    set_eax(c, 0);
}
// Both APIs deliver text in the recipient's encoding. Pointer messages stay
// synchronous; their conversion buffers cannot escape into the posted queue.
uint32_t deliver_message(X86 *c, uint32_t hwnd, uint32_t msg, uint32_t wp, uint32_t lp, bool wide) {
    Window *w = find_window(hwnd);
    if (!w)
        return 0;
    bool target_wide = w->unicode;
    if (wide == target_wide || (msg != 0xc && msg != 0xd))
        return host_dispatch_to_wndproc(c, hwnd, msg, wp, lp);
    if (msg == 0xc) {
        std::string text = wide ? gm_wstr(lp) : gm_str(lp);
        uint32_t cap = uint32_t(text.size()) + 1;
        uint32_t tmp = heap_alloc(cap * 2, true);
        if (!tmp)
            return 0;
        put_text(tmp, cap, text, target_wide);
        uint32_t result = host_dispatch_to_wndproc(c, hwnd, msg, wp, tmp);
        heap_free(tmp);
        return result;
    }
    if (!wp || wp > 0x1000000 || !lp)
        return 0;
    uint32_t tmp = heap_alloc(wp * 4, true);
    if (!tmp)
        return 0;
    host_dispatch_to_wndproc(c, hwnd, msg, wp, tmp);
    std::string text = target_wide ? gm_wstr(tmp, wp) : gm_str(tmp, wp);
    uint32_t result = put_text(lp, wp, text, wide);
    heap_free(tmp);
    return result;
}
void send_message(X86 *c, bool wide) {
    set_eax(c, deliver_message(c, arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3), wide));
}
void display_rect(uint32_t out) {
    uint32_t w = 1024, h = 768, bpp = 32;
    win32_display_mode(&w, &h, &bpp);
    wr32(out, 0);
    wr32(out + 4, 0);
    wr32(out + 8, w);
    wr32(out + 12, h);
}
} // namespace user32

namespace {
using namespace user32;
void register_w(X86 *c) {
    register_class_named(c, true, 0);
}
void create_w(X86 *c) {
    create_window_named(c, true);
}
void def_w(X86 *c) {
    def_window_proc(c, true);
}
void send_w(X86 *c) {
    send_message(c, true);
}
void send_timeout(X86 *c) {
    uint32_t out = arg(c, 6), hwnd = arg(c, 0);
    send_w(c);
    if (out && gm_valid(out, 4))
        wr32(out, c->r[R_EAX]);
    set_eax(c, find_window(hwnd) != nullptr);
}
void def_frame(X86 *c) {
    uint32_t fn = imports_resolve("USER32.dll", "DefWindowProcW");
    set_eax(c, guest_call(c, fn, arg(c, 0), arg(c, 2), arg(c, 3), arg(c, 4)));
}
void call_proc(X86 *c) {
    set_eax(c, guest_call(c, arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4)));
}
void unregister_w(X86 *c) {
    auto it = classes().find(class_key(arg(c, 0), true));
    if (it == classes().end()) {
        set_eax(c, 0);
        return;
    }
    uint32_t atom = it->second.atom;
    for (const auto &kv : windows()) {
        if (kv.second.cls == class_key(atom)) {
            set_last_error(1412);
            set_eax(c, 0);
            return;
        }
    }
    for (auto i = classes().begin(); i != classes().end();)
        if (i->second.atom == atom)
            i = classes().erase(i);
        else
            ++i;
    set_eax(c, 1);
}
void class_info(X86 *c) {
    ensure_system_classes();
    auto it = classes().find(class_key(arg(c, 1), true));
    uint32_t p = arg(c, 2);
    if (it == classes().end() || !p || !gm_valid(p, 40)) {
        set_eax(c, 0);
        return;
    }
    const auto &w = it->second;
    uint32_t values[] = {w.style, w.wndproc, w.cls_extra, w.wnd_extra, w.hinstance,
                         w.hicon, w.hcursor, w.hbrush,    w.menu_id,   arg(c, 1)};
    for (uint32_t i = 0; i < 10; ++i)
        wr32(p + 4 * i, values[i]);
    set_eax(c, 1);
}
void class_long(X86 *c, bool set) {
    Window *w = find_window(arg(c, 0));
    auto it = w ? classes().find(w->cls) : classes().end();
    if (it == classes().end()) {
        set_eax(c, 0);
        return;
    }
    auto update = [&](WndClass &cl) -> uint32_t {
        uint32_t *v = nullptr;
        int32_t index = int32_t(arg(c, 1));
        switch (index) {
        case -8:
            v = &cl.hbrush;
            break;
        case -12:
            v = &cl.hcursor;
            break;
        case -14:
        case -34:
            v = &cl.hicon;
            break;
        case -16:
            v = &cl.hinstance;
            break;
        case -18:
            v = &cl.wnd_extra;
            break;
        case -20:
            v = &cl.cls_extra;
            break;
        case -24:
            v = &cl.wndproc;
            break;
        case -26:
            v = &cl.style;
            break;
        case -32:
            return cl.atom;
        default:
            if (index >= 0 && size_t(index / 4) < cl.extra.size())
                v = &cl.extra[index / 4];
        }
        uint32_t old = v ? *v : 0;
        if (v && set)
            *v = arg(c, 2);
        return old;
    };
    uint32_t old = update(it->second), atom = it->second.atom;
    if (set)
        for (auto &kv : classes())
            if (kv.second.atom == atom)
                update(kv.second);
    set_eax(c, old);
}
void get_class_long(X86 *c) {
    class_long(c, false);
}
void set_class_long(X86 *c) {
    class_long(c, true);
}
void get_text(X86 *c) {
    set_eax(c, deliver_message(c, arg(c, 0), 0xd, arg(c, 2), arg(c, 1), true));
}
void set_text(X86 *c) {
    set_eax(c, deliver_message(c, arg(c, 0), 0xc, 0, arg(c, 1), true));
}
void class_name(X86 *c) {
    Window *w = find_window(arg(c, 0));
    auto it = w ? classes().find(w->cls) : classes().end();
    set_eax(c, put_text(arg(c, 1), arg(c, 2), it == classes().end() ? "" : it->second.name, true));
}
void find_named(X86 *c, bool ex) {
    uint32_t parent = ex ? arg(c, 0) : 0, after = ex ? arg(c, 1) : 0;
    uint32_t cls = arg(c, ex ? 2 : 0), title = arg(c, ex ? 3 : 1);
    std::string key = class_key(cls, true);
    auto ci = classes().find(key);
    if (ci != classes().end())
        key = class_key(ci->second.atom);
    std::string text = gm_wstr(title);
    for (const auto &kv : windows()) {
        const auto &w = kv.second;
        if (w.hwnd <= after || w.parent != parent || (cls && key != w.cls))
            continue;
        if (title && w.title_utf8 != text)
            continue;
        set_eax(c, w.hwnd);
        return;
    }
    set_eax(c, 0);
}
void find_w(X86 *c) {
    find_named(c, false);
}
void find_ex_w(X86 *c) {
    find_named(c, true);
}
// RT_STRING blocks contain sixteen WORD-length-prefixed UTF-16 strings.
void load_string(X86 *c) {
    uint32_t id = arg(c, 1) & 0xffff, out = arg(c, 2), cap = arg(c, 3), bytes = 0;
    uint32_t p = resource_data(resource_find(6, (id >> 4) + 1), &bytes), end = p + bytes;
    for (uint32_t i = 0; p && i < 16; ++i) {
        if (end - p < 2)
            break;
        uint32_t n = rd16(p);
        p += 2;
        if (n > (end - p) / 2)
            break;
        if (i == (id & 15)) {
            if (!out)
                break;
            if (!cap) {
                if (!gm_valid(out, 4))
                    break;
                wr32(out, p);
                set_eax(c, n);
                return;
            }
            n = std::min(n, cap - 1);
            if (!gm_valid(out, (n + 1) * 2))
                break;
            memmove(g_mem + out, g_mem + p, n * 2);
            wr16(out + n * 2, 0);
            set_eax(c, n);
            return;
        }
        p += n * 2;
    }
    set_eax(c, 0);
}
void load_bitmap(X86 *c) {
    uint32_t bytes = 0, p = resource_data(resource_find(2, arg(c, 1)), &bytes);
    GdiImage image;
    set_eax(c, p && gdi_decode_image(p, bytes, &image) ? gdi_image_bitmap(image) : 0);
}
void load_icon(X86 *c) {
    if (!arg(c, 0)) {
        alias_ansi(c, "LoadIconA");
        return;
    }
    uint32_t bytes = 0, p = resource_data(resource_find(14, arg(c, 1)), &bytes);
    if (!p || bytes < 20 || !rd16(p + 4)) {
        set_eax(c, 0);
        return;
    }
    p = resource_data(resource_find(3, rd16(p + 18)), &bytes);
    GdiImage image;
    // Icon DIB heights include the AND mask. Decode the XOR plane at half height.
    if (!p || bytes < 40 || rd32(p) < 40) {
        set_eax(c, 0);
        return;
    }
    uint32_t copy = heap_alloc(bytes, true);
    if (!copy) {
        set_eax(c, 0);
        return;
    }
    memcpy(g_mem + copy, g_mem + p, bytes);
    wr32(copy + 8, rd32(copy + 8) / 2);
    bool ok = gdi_decode_image(copy, bytes, &image);
    heap_free(copy);
    set_eax(c, ok ? gdi_create_icon(image) : 0);
}
void draw_text(X86 *c) {
    set_eax(c, gdi::draw_text(arg(c, 0), arg(c, 1), arg(c, 2), arg(c, 3), arg(c, 4)));
}
void draw_text_ex(X86 *c) {
    uint32_t p = arg(c, 5);
    if (p && gm_valid(p, 20) && rd32(p) == 20)
        wr32(p + 16, wide_units(gm_wstr(arg(c, 1), arg(c, 2) == 0xffffffffu ? 0x8000 : arg(c, 2))));
    draw_text(c);
}
uint16_t case_unit(uint16_t ch, bool upper) {
    if (upper) {
        if ((ch >= 'a' && ch <= 'z') || (ch >= 0xe0 && ch <= 0xfe && ch != 0xf7) ||
            (ch >= 0x3b1 && ch <= 0x3cb && ch != 0x3c2) || (ch >= 0x430 && ch <= 0x44f))
            return ch - 32;
        if (ch == 0x3c2)
            return 0x3a3;
    } else if ((ch >= 'A' && ch <= 'Z') || (ch >= 0xc0 && ch <= 0xde && ch != 0xd7) ||
               (ch >= 0x391 && ch <= 0x3ab && ch != 0x3a2) || (ch >= 0x410 && ch <= 0x42f))
        return ch + 32;
    return ch;
}
void char_case(X86 *c, bool upper, bool buffer) {
    uint32_t p = arg(c, 0);
    if (!buffer && p < 0x10000) {
        set_eax(c, case_unit(uint16_t(p), upper));
        return;
    }
    uint32_t n = buffer ? arg(c, 1) : 0x8000, i = 0;
    for (; i < n && p && gm_valid(p + i * 2, 2); ++i) {
        uint16_t ch = rd16(p + i * 2);
        if (!buffer && !ch)
            break;
        wr16(p + i * 2, case_unit(ch, upper));
    }
    set_eax(c, buffer ? i : p);
}
void upper(X86 *c) {
    char_case(c, true, false);
}
void lower(X86 *c) {
    char_case(c, false, false);
}
void upper_buff(X86 *c) {
    char_case(c, true, true);
}
void lower_buff(X86 *c) {
    char_case(c, false, true);
}
// The byte forms. A Delphi runtime builds its ANSI case tables at startup by
// running every byte value through these two, from inside a unit's
// initialization section - and an import the kit does not know is called
// with its arguments left on the stack, since nothing can say how many there
// were. Two calls a byte, eight bytes a call, and the register the unit-init
// loop keeps its count in is popped back as garbage: the walk stops there,
// and every unit after it - the PNG reader included - never initializes.
void case_bytes(X86 *c, bool upper) {
    uint32_t p = arg(c, 0), n = arg(c, 1), i = 0;
    for (; i < n && p && gm_valid(p + i, 1); ++i)
        wr8(p + i, (uint8_t)case_unit(rd8(p + i), upper));
    set_eax(c, i);
}
void upper_buff_a(X86 *c) {
    case_bytes(c, true);
}
// CharUpperA/CharLowerA(lpsz): a pointer whose high word is zero is one character, converted and returned in
// place of the pointer; otherwise the NUL-terminated string is converted in place and the pointer returned.
void case_string_a(X86 *c, bool upper) {
    uint32_t p = arg(c, 0);
    if (!(p >> 16)) {
        set_eax(c, (uint32_t)case_unit(p & 0xff, upper) & 0xff);
        return;
    }
    for (uint32_t i = 0; gm_valid(p + i, 1); ++i) {
        uint8_t ch = rd8(p + i);
        if (!ch)
            break;
        wr8(p + i, (uint8_t)case_unit(ch, upper));
    }
    set_eax(c, p);
}
void upper_a(X86 *c) {
    case_string_a(c, true);
}
void lower_a(X86 *c) {
    case_string_a(c, false);
}
void lower_buff_a(X86 *c) {
    case_bytes(c, false);
}
void next_char(X86 *c) {
    uint32_t p = arg(c, 0);
    set_eax(c, p && rd16(p) ? p + 2 : p);
}
void map_key(X86 *c) {
    // Set 1 scan codes for the US layout used by the host input gate.
    static const uint8_t letters[26] = {30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50,
                                        49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45, 21, 44};
    uint32_t code = arg(c, 0), kind = arg(c, 1), result = 0;
    auto scan = [&](uint32_t vk) -> uint32_t {
        if (vk >= 'A' && vk <= 'Z')
            return letters[vk - 'A'];
        if (vk >= '1' && vk <= '9')
            return vk - '1' + 2;
        switch (vk) {
        case '0':
            return 11;
        case 8:
            return 14;
        case 9:
            return 15;
        case 13:
            return 28;
        case 16:
            return 42;
        case 17:
            return 29;
        case 18:
            return 56;
        case 27:
            return 1;
        case 32:
            return 57;
        default:
            return 0;
        }
    };
    if (kind == 0 || kind == 4)
        result = scan(code);
    else if (kind == 1 || kind == 3) {
        for (uint32_t i = 1; i < 256; ++i)
            if (scan(i) == code) {
                result = i;
                break;
            }
    } else if (kind == 2 && code >= 32 && code <= 126)
        result = uint32_t(std::toupper(int(code)));
    set_eax(c, result);
}
void key_name(X86 *c) {
    uint32_t scan = (arg(c, 0) >> 16) & 255, vk = 0;
    uint32_t args[] = {scan, 1};
    vk = guest_call(c, imports_resolve("USER32.dll", "MapVirtualKeyW"), args, 2);
    std::string text = vk >= 32 && vk < 127 ? std::string(1, char(vk))
                                            : (vk == 13   ? "Enter"
                                               : vk == 27 ? "Esc"
                                               : vk == 9  ? "Tab"
                                                          : "");
    set_eax(c, put_text(arg(c, 1), arg(c, 2), text, true));
}
void layout_name(X86 *c) {
    set_eax(c, put_text(arg(c, 0), 9, "00000409", true) == 8);
}
void load_layout(X86 *c) {
    set_eax(c, 0x04090409);
}
void spi_w(X86 *c) {
    if (arg(c, 0) == 48 && arg(c, 2) && gm_valid(arg(c, 2), 16)) {
        display_rect(arg(c, 2));
        set_eax(c, 1);
    } else
        alias_ansi(c, "SystemParametersInfoA");
}
// Some callers leave dmSize uninitialized. Enumeration supplies the complete
// standard DEVMODE, without reading that field or writing driver-private bytes.
void enum_settings(X86 *c, bool wide) {
    uint32_t p = arg(c, 2), mode = arg(c, 1);
    uint32_t size = wide ? 220 : 156, header = wide ? 64 : 32;
    uint32_t display = wide ? 168 : 104;
    if (!p || !gm_valid(p, size)) {
        set_eax(c, 0);
        return;
    }
    uint32_t w = 1024, h = 768, bpp = 32;
    if (mode >= 0xfffffffeu) {
        win32_display_mode(&w, &h, &bpp);
    } else if (!ddraw_enum_display_mode(mode, &w, &h, &bpp)) {
        set_eax(c, 0);
        return;
    }
    memset(g_mem + p, 0, size);
    put_text(p, 32, "DISPLAY1", wide);
    wr16(p + header, 0x401);
    wr16(p + header + 4, uint16_t(size));
    wr32(p + header + 8, 0x5c0000);
    wr32(p + display, bpp);
    wr32(p + display + 4, w);
    wr32(p + display + 8, h);
    wr32(p + display + 16, 60);
    set_eax(c, 1);
}
void enum_settings_w(X86 *c) {
    enum_settings(c, true);
}
void enum_settings_a(X86 *c) {
    enum_settings(c, false);
}
void enum_devices(X86 *c) {
    uint32_t p = arg(c, 2);
    if (arg(c, 1) || !p || !gm_valid(p, 840) || rd32(p) < 840) {
        set_eax(c, 0);
        return;
    }
    memset(g_mem + p + 4, 0, 836);
    gm_put_wstr(p + 4, "\\\\.\\DISPLAY1", 32);
    gm_put_wstr(p + 68, "Runtime display", 128);
    wr32(p + 324, 5);
    set_eax(c, 1);
}
void monitor_info(X86 *c) {
    uint32_t p = arg(c, 1);
    if (arg(c, 0) != 1 || !p || !gm_valid(p, 40) || rd32(p) < 40) {
        set_eax(c, 0);
        return;
    }
    display_rect(p + 4);
    display_rect(p + 20);
    wr32(p + 36, 1);
    if (rd32(p) >= 104 && gm_valid(p, 104))
        gm_put_wstr(p + 40, "\\\\.\\DISPLAY1", 32);
    set_eax(c, 1);
}
void dialog_message(X86 *c) {
    set_eax(c, 0);
}
const ImportShim shims[] = {
#define W(name, n, fn) {"USER32.dll", name, n, fn}
    W("RegisterClassW", 1, register_w),
    W("UnregisterClassW", 2, unregister_w),
    W("GetClassInfoW", 3, class_info),
    W("CreateWindowExW", 12, create_w),
    W("DefWindowProcW", 4, def_w),
    W("DefFrameProcW", 5, def_frame),
    W("DefMDIChildProcW", 4, def_w),
    W("CallWindowProcW", 5, call_proc),
    W("PeekMessageW", 5, peek_message),
    W("DispatchMessageW", 1, dispatch_message),
    W("SendMessageW", 4, send_w),
    W("SendMessageTimeoutW", 7, send_timeout),
    W("GetClassLongW", 2, get_class_long),
    W("SetClassLongW", 3, set_class_long),
    W("GetWindowTextW", 3, get_text),
    W("SetWindowTextW", 2, set_text),
    W("GetClassNameW", 3, class_name),
    W("FindWindowW", 2, find_w),
    W("FindWindowExW", 4, find_ex_w),
    W("LoadBitmapW", 2, load_bitmap),
    W("LoadIconW", 2, load_icon),
    W("LoadStringW", 4, load_string),
    W("DrawTextW", 5, draw_text),
    W("DrawTextExW", 6, draw_text_ex),
    W("CharUpperW", 1, upper),
    W("CharLowerW", 1, lower),
    W("CharUpperBuffW", 2, upper_buff),
    W("CharLowerBuffW", 2, lower_buff),
    W("CharUpperBuffA", 2, upper_buff_a),
    W("CharUpperA", 1, upper_a),
    W("CharLowerA", 1, lower_a),
    W("CharLowerBuffA", 2, lower_buff_a),
    W("CharNextW", 1, next_char),
    W("MapVirtualKeyW", 2, map_key),
    W("GetKeyNameTextW", 3, key_name),
    W("GetKeyboardLayoutNameW", 1, layout_name),
    W("LoadKeyboardLayoutW", 2, load_layout),
    W("SystemParametersInfoW", 4, spi_w),
    W("EnumDisplaySettingsW", 3, enum_settings_w),
    W("EnumDisplaySettingsA", 3, enum_settings_a),
    W("EnumDisplayDevicesW", 4, enum_devices),
    W("GetMonitorInfoW", 2, monitor_info),
    W("IsDialogMessageW", 2, dialog_message),
    W("IsDialogMessageA", 2, dialog_message),
#undef W
};
} // namespace
void user32_wide_register() {
    imports_register(shims, sizeof(shims) / sizeof(shims[0]));
    // These structures and scalar arguments have identical A/W layouts.
    const char *names[][2] = {{"PostMessageA", "PostMessageW"},
                              {"SetWindowLongA", "SetWindowLongW"},
                              {"GetWindowLongA", "GetWindowLongW"},
                              {"LoadCursorA", "LoadCursorW"},
                              {"GetMessageA", "GetMessageW"}};
    for (const auto &names_pair : names) {
        for (size_t i = 0; i < g_user32_shim_count; ++i)
            if (!strcmp(names_pair[0], g_user32_shims[i].name)) {
                ImportShim shim = g_user32_shims[i];
                shim.name = names_pair[1];
                imports_register(&shim, 1);
                break;
            }
    }
}
