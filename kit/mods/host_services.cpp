// host_services.cpp - the main-thread-only API group.
//
// register_menu_item adds entries that open a settings page and nothing else;
// arbitrary custom screens are the menu sub-project's. register_setting
// declares a setting the page renders, under its owner's mod id.
// texture_override_provider is a registration point: the default reports no
// override, and the upscaling pipeline is the HD texture sub-project's.
//
// WHY THESE THREE ARE MAIN-THREAD ONLY. They mutate registries the host reads
// while it is drawing, and they are the only API group whose effect is visible
// outside the guest. Everything else in the foundation is queued and applied
// at a scheduler checkpoint; these are not, so calling one from a worker is
// refused with POP_E_WRONG_THREAD rather than raced. The thread is whichever
// one called mods_host_set_main_thread, which the host does before the guest
// starts.
#include "mods_internal.h"
#include "display_settings.h"
#include "../runtime/win32.h"
#include "../platform/os.h"
#include "../dx/host_api.h"

#include <cmath>
#include <map>
#include <stdio.h>
#include <string>
#include <vector>

namespace {

struct Menu {
    uint32_t owner;
    std::string path, label;
    PopMenuFn cb;
    void *user;
    const char *desc; // interned: "mod <id> menu item <label>"
};
struct Provider {
    uint32_t owner;
    PopTextureProviderFn cb;
    void *user;
    const char *desc;
    PopTextureProviderExFn ex = nullptr;
};

std::map<uint64_t, uint32_t> anchor_owners; // newest writer owns cleanup

std::vector<Menu> &menus() {
    static std::vector<Menu> v;
    return v;
}
std::vector<Provider> &providers() {
    static std::vector<Provider> v;
    return v;
}

// How deep a texture-override pass is, and whether one asked for a removal it
// was not allowed to perform. See mods_texture_override.
int g_dispatching = 0;
bool g_removal_deferred = false;

void purge_dead_providers() {
    if (g_dispatching || !g_removal_deferred)
        return;
    g_removal_deferred = false;
    for (auto it = providers().begin(); it != providers().end();)
        it = (it->cb || it->ex) ? it + 1 : providers().erase(it);
}
OsThreadId g_main = 0;
bool g_have_main = false;

} // namespace

void mods_host_set_main_thread() {
    g_main = os_thread_self();
    g_have_main = true;
}

bool mods_host_on_main_thread() {
    return g_have_main && os_thread_self() == g_main;
}

PopModStatus mods_register_menu_item(uint32_t owner, const char *path, const char *label,
                                     PopMenuFn cb, void *user) {
    if (!mods_host_on_main_thread())
        return POP_E_WRONG_THREAD;
    if (!path || !label)
        return POP_E_INVAL;
    const PopModApi *api = mods_api_for(owner);
    char buf[128];
    snprintf(buf, sizeof buf, "mod %s menu item %s", api && api->mod_id ? api->mod_id : "?", label);
    menus().push_back({owner, path, label, cb, user, mods_intern_desc(buf)});
    return POP_OK;
}

PopModStatus mods_register_setting(uint32_t owner, const PopSettingDesc *d) {
    if (!mods_host_on_main_thread())
        return POP_E_WRONG_THREAD;
    if (!d || !d->key)
        return POP_E_INVAL;
    // The owner's id, not an empty string: a dynamically declared setting must
    // persist and reload under the mod that declared it.
    const PopModApi *api = mods_api_for(owner);
    mods_settings_declare(owner, api && api->mod_id ? api->mod_id : "", d->key, d->label, d->kind,
                          d->def, d->min, d->max);
    return POP_OK;
}

PopModStatus mods_texture_override_provider(uint32_t owner, PopTextureProviderFn cb, void *user) {
    if (!mods_host_on_main_thread())
        return POP_E_WRONG_THREAD;
    const PopModApi *api = mods_api_for(owner);
    char buf[96];
    snprintf(buf, sizeof buf, "mod %s texture provider", api && api->mod_id ? api->mod_id : "?");
    providers().push_back({owner, cb, user, mods_intern_desc(buf)});
    return POP_OK;
}

PopModStatus mods_texture_override_provider_ex(uint32_t owner, PopTextureProviderExFn cb,
                                               void *user) {
    if (!mods_host_on_main_thread())
        return POP_E_WRONG_THREAD;
    if (!cb)
        return POP_E_INVAL;
    auto status = mods_texture_override_provider(owner, nullptr, user);
    if (status == POP_OK)
        providers().back().ex = cb;
    return status;
}

// Ask texture providers for a valid replacement, tolerating registration/removal during callbacks.
// Providers added mid-pass are eligible on the next upload, preserving a bounded iteration.
static int texture_override(uint64_t hash64, int32_t w, int32_t h, int32_t format,
                            PopTextureReplacement *out, bool extended) {
    if (!out || w <= 0 || h <= 0 || w > 4096 || h > 4096)
        return 0;

    // Two things a provider is allowed to do to this list while it runs, and
    // the pass has to survive both.
    //
    // It may REGISTER another - a mod installing a fallback from inside its
    // first attempt is doing something reasonable - which appends and may
    // reallocate. So the pass walks by index over the live vector, re-reading
    // it each time, and stops at the count it started with: one registered
    // mid-pass is registered, and is asked at the next upload rather than this
    // one.
    //
    // It may also REMOVE one, its own or another mod's, and a snapshot would
    // then still hold a callback whose storage has been released - the first
    // version of this fixed the reallocation and left that. So removal during
    // a pass only marks entries dead, and the erase happens when the last pass
    // finishes. A dead entry is skipped rather than called.
    const size_t count = providers().size();
    ++g_dispatching;
    struct PassGuard {
        ~PassGuard() {
            --g_dispatching;
            purge_dead_providers();
        }
    } guard;
    for (size_t i = 0; i < count && i < providers().size(); ++i) {
        const Provider p = providers()[i]; // by value: the call may append
        if (!p.cb && (!extended || !p.ex))
            continue;
        PopTextureReplacement replacement{sizeof(PopTextureReplacement), w, h, w * 4, nullptr, 0};
        uint8_t *px = nullptr;
        uint32_t bytes = 0;
        const char *prev = mods_push_active_callback(p.desc);
        uint32_t depth = mods_view_depth();
        mods_view_push();
        int32_t took;
        if (p.ex)
            took = p.ex(mods_api_for(p.owner), hash64, w, h, format, &replacement, p.user);
        else {
            took = p.cb(mods_api_for(p.owner), hash64, w, h, format, &px, &bytes, p.user);
            replacement.pixels = px;
            replacement.bytes = bytes;
        }
        mods_view_truncate(depth);
        mods_pop_active_callback(prev);
        if (!took)
            continue;
        // Validate what came back: too little storage is a mod bug, not
        // something to read past the end of.
        const auto &r = replacement;
        if (r.size != sizeof(r) || !r.pixels || r.width <= 0 || r.height <= 0 || r.width > 4096 ||
            r.height > 4096 || r.pitch < int64_t(r.width) * 4 || r.bytes > 64ull * 1024 * 1024 ||
            r.bytes < uint64_t(r.pitch) * r.height ||
            int64_t(r.width) * h != int64_t(r.height) * w) {
            LOGW("mods: %s returned an invalid RGBA replacement; ignored", p.desc);
            continue;
        }
        *out = r;
        return 1;
    }
    return 0;
}

int mods_texture_override_ex(uint64_t hash, int32_t w, int32_t h, int32_t format,
                             PopTextureReplacement *out) {
    return texture_override(hash, w, h, format, out, true);
}
int mods_texture_override(uint64_t hash, int32_t w, int32_t h, int32_t format, uint8_t **out,
                          uint32_t *bytes) {
    if (!out || !bytes)
        return 0;
    PopTextureReplacement r{};
    if (!texture_override(hash, w, h, format, &r, false))
        return 0;
    *out = const_cast<uint8_t *>(r.pixels);
    *bytes = uint32_t(r.bytes);
    return 1;
}

uint32_t mods_menu_entry_count() {
    return (uint32_t)menus().size();
}

bool mods_menu_entry(uint32_t i, uint32_t *owner, const char **path, const char **label) {
    if (i >= menus().size())
        return false;
    if (owner)
        *owner = menus()[i].owner;
    if (path)
        *path = menus()[i].path.c_str();
    if (label)
        *label = menus()[i].label.c_str();
    return true;
}

PopModStatus mods_menu_activate(uint32_t i) {
    if (!mods_host_on_main_thread())
        return POP_E_WRONG_THREAD;
    if (i >= menus().size())
        return POP_E_RANGE;
    const Menu m = menus()[i]; // a copy: the callback may register more
    if (!m.cb)
        return POP_E_STATE;
    // A menu callback is a callback like any other: it gets its own snapshot
    // scope and its own attribution, so a crash inside one names it.
    const char *prev = mods_push_active_callback(m.desc);
    uint32_t depth = mods_view_depth();
    mods_view_push();
    m.cb(mods_api_for(m.owner), m.user);
    mods_view_truncate(depth);
    mods_pop_active_callback(prev);
    return POP_OK;
}

void mods_host_services_remove_all(uint32_t owner) {
    host_pad_rumble_pulse(owner, 0, 0, 0);
    for (auto it = anchor_owners.begin(); it != anchor_owners.end();) {
        if (it->second == owner) {
            host_display_anchor(it->first, 0, 0, 1);
            it = anchor_owners.erase(it);
        } else
            ++it;
    }
    for (auto it = menus().begin(); it != menus().end();)
        it = (it->owner == owner) ? menus().erase(it) : it + 1;
    if (g_dispatching) {
        // A pass is walking this list. Mark and let it finish; erasing under
        // it is what would hand a released callback to the next iteration.
        for (Provider &p : providers())
            if (p.owner == owner) {
                p.cb = nullptr;
                p.ex = nullptr;
                g_removal_deferred = true;
            }
        return;
    }
    for (auto it = providers().begin(); it != providers().end();)
        it = (it->owner == owner) ? providers().erase(it) : it + 1;
}

// Populate host-facing mod services with thread and value validation.
// Successful registrations retain ownership so teardown can revoke the calling mod resources.
extern "C" int d3d9_texture_tag_iface(uint32_t iface, uint64_t key); // dx/d3d9.cpp (Remaster A1)

void mods_fill_host_api(PopModApi *api) {
    api->d3d9_texture_tag = [](const PopModApi *, uint32_t tex_iface, uint64_t key) -> PopModStatus {
        if (!tex_iface)
            return POP_E_RANGE;
        return d3d9_texture_tag_iface(tex_iface, key) ? POP_OK : POP_E_NOTFOUND;
    };
    api->audio_output = [](const PopModApi *, uint32_t mode, uint32_t flags) -> PopModStatus {
        if (mode > POP_AUDIO_OUTPUT_SURROUND51 ||
            (flags & ~(POP_AUDIO_SWAP_CENTER_LFE | POP_AUDIO_DOLBY_DIGITAL)))
            return POP_E_RANGE;
        return host_audio_set_output(mode, flags) ? POP_OK : POP_E_NOTFOUND;
    };
    api->rumble_pulse = [](const PopModApi *api, uint16_t low, uint16_t high,
                           uint32_t ms) -> PopModStatus {
        if (ms > 5000)
            return POP_E_RANGE;
        int result = host_pad_rumble_pulse(api->mod_index, low, high, ms);
        return result > 0 ? POP_OK : result == 0 ? POP_E_NOTFOUND : POP_E_LIMIT;
    };
    api->set_anchor = [](const PopModApi *api, uint64_t id, int8_t h, int8_t v) -> PopModStatus {
        if (!mods_host_on_main_thread())
            return POP_E_WRONG_THREAD;
        if (h < -1 || h > 1 || v < -1 || v > 1)
            return POP_E_RANGE;
        PopModStatus status = host_display_anchor(id, h, v, 0);
        if (status == POP_OK)
            anchor_owners[id] = api->mod_index;
        return status;
    };
    api->clear_anchor = [](const PopModApi *, uint64_t id) -> PopModStatus {
        if (!mods_host_on_main_thread())
            return POP_E_WRONG_THREAD;
        PopModStatus status = host_display_anchor(id, 0, 0, 1);
        if (status == POP_OK)
            anchor_owners.erase(id);
        return status;
    };
    api->ui_elements = [](const PopModApi *, uint64_t *ids, uint32_t max) {
        return host_display_elements(ids, max);
    };
    api->pointer_box = [](const PopModApi *, float x0, float y0, float w, float h) -> PopModStatus {
        if (!(std::isfinite(x0) && std::isfinite(y0) && std::isfinite(w) && std::isfinite(h)) || w <= 0.01f ||
            h <= 0.01f || x0 < 0 || y0 < 0 || x0 + w > 1.001f || y0 + h > 1.001f)
            return POP_E_INVAL;
        host_display_set_ui_box(x0, y0, w, h);
        return POP_OK;
    };
    api->screen_size = [](const PopModApi *, uint32_t *w, uint32_t *h) -> PopModStatus {
        int32_t sw = 0, sh = 0;
        if (!w || !h)
            return POP_E_INVAL;
        if (!host_display_screen(&sw, &sh))
            return POP_E_NOTFOUND;
        *w = uint32_t(sw);
        *h = uint32_t(sh);
        return POP_OK;
    };
    api->host_aspect = [](const PopModApi *api) {
        // Wide View controls horizontal expansion. Keep core.display's
        // resolution/sky callbacks alive even when that option is off.
        const bool native_view =
            !mods_display_wide() && api && api->mod_id && !strcmp(api->mod_id, "core.display");
        return mods_display_classic() || native_view ? 4.0f / 3.0f : host_display_aspect();
    };
    api->set_scene_domain = [](const PopModApi *, uint32_t w, uint32_t h) -> PopModStatus {
        if (!sched_is_guest_thread())
            return POP_E_WRONG_THREAD;
        if (!w || !h || w > 32767 || h > 32767)
            return POP_E_RANGE;
        mods_display_scene_domain(w, h);
        return POP_OK;
    };
    api->display_transition = [](const PopModApi *, uint32_t *out) -> PopModStatus {
        if (!out)
            return POP_E_INVAL;
        *out = (uint32_t)host_display_epoch();
        return POP_OK;
    };

    api->register_menu_item = [](const PopModApi *a, const char *path, const char *label,
                                 PopMenuFn cb, void *user) {
        return mods_register_menu_item(a->mod_index, path, label, cb, user);
    };
    api->register_setting = [](const PopModApi *a, const PopSettingDesc *d) {
        return mods_register_setting(a->mod_index, d);
    };
    api->texture_override_provider = [](const PopModApi *a, PopTextureProviderFn cb, void *user) {
        return mods_texture_override_provider(a->mod_index, cb, user);
    };
    api->texture_override_provider_ex = [](const PopModApi *a, PopTextureProviderExFn cb,
                                           void *user) {
        return mods_texture_override_provider_ex(a->mod_index, cb, user);
    };
    // The page is host state the presenter reads while it draws, so opening it
    // is a host service and obeys the same rule as the other three: the thread
    // that called mods_host_set_main_thread, or nothing happens. Without this
    // a mod could open the page from a worker while the host was part-way
    // through drawing the frame the page is about to appear in.
    api->open_settings_page = [](const PopModApi *, const char *mod_id) {
        if (!mods_host_on_main_thread())
            return POP_E_WRONG_THREAD;
        return mods_page_open(mod_id);
    };
}
