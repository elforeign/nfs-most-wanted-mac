#include "compositor.h"
#ifdef POPM_COMPOSITOR_TEST_UI_DOUBLE
#include "tests/compositor_ui_double.h"
#elif __has_include("ui_layer.h")
#include "ui_layer.h"
#else
#include "ui_frame_contract.h"
#endif

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <vector>

namespace {
using Registry = std::map<uint64_t, Anchor>;
std::mutex registry_mutex;
Registry anchors;
std::mutex pointer_mutex;
bool pointer_valid = false;
int32_t pointer_x = 0, pointer_y = 0;
struct CompositeRect {
    double x, y, w, h;
};

Registry registry_snapshot() {
    std::lock_guard lock(registry_mutex);
    return anchors;
}
bool valid(const CompositorInput *in) {
    return in && in->guest_w > 0 && in->guest_h > 0 && in->drawable_w > 0 && in->drawable_h > 0;
}
int8_t default_axis(int p, int extent, int domain) {
    const double centre = double(p) + double(extent) / 2;
    if (std::abs(centre - double(domain) / 2) <= 8)
        return 0;
    return centre <= double(domain) / 2 ? -1 : 1;
}
double anchored_axis(int p, int domain, int drawable, int scale, int8_t a) {
    if (a < 0)
        return double(p) * scale;
    if (a > 0)
        return drawable - (double(domain) - p) * scale;
    return (drawable - double(domain) * scale) / 2 + double(p) * scale;
}
CompositeRect whole_rect(const CompositorInput &in, bool integer) {
    const double scale =
        integer ? compositor_ui_scale(in.drawable_h, in.guest_h, in.scale_override)
                : std::min(double(in.drawable_w) / in.guest_w, double(in.drawable_h) / in.guest_h);
    const double w = in.guest_w * scale, h = in.guest_h * scale;
    return {(in.drawable_w - w) / 2, (in.drawable_h - h) / 2, w, h};
}
// Enhanced gameplay with Wide view off on a drawable wider than the guest's
// aspect. Its world was projected for the guest's width, so it is shown boxed
// at that aspect, with its HUD and input mapped through the same box, the way
// Classic shows it.
bool boxed_scene(const CompositorInput &in) {
    return in.narrow && in.cls == HOST_SCREEN_GAMEPLAY && !in.legacy && !in.classic &&
           int64_t(in.drawable_w) * in.guest_h > int64_t(in.drawable_h + 1) * in.guest_w;
}
CompositeRect pixel_rect(CompositeRect r) {
    const double x = std::round(r.x), y = std::round(r.y);
    return {x, y, std::round(r.x + r.w) - x, std::round(r.y + r.h) - y};
}
// The 640x480 settings page at the UI scale, but never larger than the
// drawable: a saved scale that overflows would push the page's own UI scale
// row off screen. A drawable under 640x480 fits the page instead.
CompositeRect settings_page_rect(const CompositorInput &in) {
    auto page = in;
    if ((in.settings_page_w != 640 || in.settings_page_h != 480) && in.settings_page_w > 0 &&
        in.settings_page_h > 0) {
        page.guest_w = in.settings_page_w;
        page.guest_h = in.settings_page_h;
        return pixel_rect(whole_rect(page, false));
    }
    page.guest_w = 640;
    page.guest_h = 480;
    const int fits = std::min(in.drawable_w / 640, in.drawable_h / 480);
    if (fits < 1)
        return pixel_rect(whole_rect(page, false));
    page.scale_override =
        std::min(compositor_ui_scale(in.drawable_h, 480, in.scale_override), fits);
    return pixel_rect(whole_rect(page, true));
}
std::atomic<uint64_t> g_settings_page_drawn{0};
CompositeRect drawn_settings_page_rect(const CompositorInput &in) {
    const CompositeRect r = settings_page_rect(in);
    auto field = [](double v) { return uint64_t(uint16_t(std::clamp(int(v), 0, 0xffff))); };
    g_settings_page_drawn.store(field(r.x) | field(r.y) << 16 | field(r.w) << 32 | field(r.h) << 48,
                                std::memory_order_relaxed);
    return r;
}
CompositeRect mapped_element_rect(const CompositorInput &in, const UiElement &e,
                                  const Registry &registry) {
    if (e.is_cursor) {
        std::lock_guard lock(pointer_mutex);
        if (pointer_valid) {
            double sx, sy;
            if (!in.legacy && !in.classic && in.cls == HOST_SCREEN_GAMEPLAY && !boxed_scene(in))
                sx = sy = compositor_ui_scale(in.drawable_h, in.guest_h, in.scale_override);
            else {
                const auto whole =
                    whole_rect(in, !in.legacy && !in.classic && in.cls == HOST_SCREEN_MENU);
                sx = whole.w / in.guest_w;
                sy = whole.h / in.guest_h;
            }
            return {double(pointer_x), double(pointer_y), e.w * sx, e.h * sy};
        }
    }
    if (in.legacy || in.classic || in.cls != HOST_SCREEN_GAMEPLAY || boxed_scene(in)) {
        const auto whole = whole_rect(in, !in.legacy && !in.classic && in.cls == HOST_SCREEN_MENU);
        const double sx = whole.w / in.guest_w, sy = whole.h / in.guest_h;
        return {whole.x + e.x * sx, whole.y + e.y * sy, e.w * sx, e.h * sy};
    }
    const int scale = compositor_ui_scale(in.drawable_h, in.guest_h, in.scale_override);
    if (e.is_cursor) {
        if (std::isfinite(in.scene.scale_x) && in.scene.scale_x > 0 &&
            std::isfinite(in.scene.scale_y) && in.scene.scale_y > 0 &&
            std::isfinite(in.scene.offset_x) && std::isfinite(in.scene.offset_y)) {
            return {e.x * double(in.scene.scale_x) + in.scene.offset_x,
                    e.y * double(in.scene.scale_y) + in.scene.offset_y, double(e.w) * scale,
                    double(e.h) * scale};
        }
        const auto whole = whole_rect(in, true);
        return {whole.x + double(e.x) * scale, whole.y + double(e.y) * scale, double(e.w) * scale,
                double(e.h) * scale};
    }
    const auto found = registry.find(e.id);
    const Anchor a = found == registry.end() ? compositor_default_anchor(&e, in.guest_w, in.guest_h)
                                             : found->second;
    return {anchored_axis(e.x, in.guest_w, in.drawable_w, scale, a.h),
            anchored_axis(e.y, in.guest_h, in.drawable_h, scale, a.v), double(e.w) * scale,
            double(e.h) * scale};
}
CompositeRect element_rect(const CompositorInput &in, const UiElement &e,
                           const Registry &registry) {
    // Use the same integral edges for rasterization and the published layout,
    // including odd drawable dimensions and fractional scene-mapped cursors.
    return pixel_rect(mapped_element_rect(in, e, registry));
}
std::vector<const UiElement *> ordered(const UiFrame *ui) {
    std::vector<const UiElement *> result;
    if (ui)
        for (const auto &e : ui->elements)
            result.push_back(&e);
    std::stable_sort(result.begin(), result.end(),
                     [](const auto *a, const auto *b) { return a->last_seq < b->last_seq; });
    return result;
}

enum Blend { Opaque, Straight, Premultiplied };
struct Quad {
    float rect[4], uv[4];
    float drawable[2];
    uint32_t opaque, pad;
};
// One pipeline per blend convention and target format; the device caches by
// the render state, so asking every compose is free.
gpu::Pipeline pipeline(gpu::Device *device, gpu::Format format, Blend blend) {
    gpu::RenderState state;
    state.color_format[0] = format;
    state.color_count = 1;
    state.blend_enabled = blend != Opaque;
    state.src_rgb = blend == Straight ? gpu::Blend::SrcAlpha : gpu::Blend::One;
    state.dst_rgb = gpu::Blend::OneMinusSrcAlpha;
    state.src_alpha = gpu::Blend::One;
    state.dst_alpha = gpu::Blend::OneMinusSrcAlpha;
    // Test86: an RGBA16F (HDR) target takes the variant that decodes sRGB and expands the scene.
    return device->render_pipeline(format == gpu::Format::RGBA16F ? "compositor_hdr" : "compositor", state);
}
struct Pass {
    gpu::Device *device;
    gpu::CommandBuffer cb;
    gpu::Format format;
    std::vector<gpu::Texture> transient; // destroyed when cb completes
};
// Encode a textured compositor quad with explicit destination, UVs and alpha convention.
// Reject empty or wholly offscreen rectangles before issuing draw commands.
void draw(Pass &pass, const CompositorInput &in, gpu::Texture tex, CompositeRect dst,
          CompositeRect uv, Blend blend, int hdr_mode = 0) {
    if (!tex || dst.w <= 0 || dst.h <= 0 || dst.x >= in.drawable_w || dst.y >= in.drawable_h ||
        dst.x + dst.w <= 0 || dst.y + dst.h <= 0)
        return;
    gpu::Pipeline p = pipeline(pass.device, pass.format, blend);
    if (!p)
        return;
    // pad: the Brightness exponent's bits for the game picture (Metal reads it as a float; 0 = unchanged, and the
    // Vulkan and WebGPU shaders do not read it).
    uint32_t brightness_bits = 0;
    if (blend == Opaque && in.brightness > 0.0f && std::fabs(in.brightness - 1.0f) > 1e-4f)
        memcpy(&brightness_bits, &in.brightness, sizeof brightness_bits);
    Quad q{{float(dst.x), float(dst.y), float(dst.w), float(dst.h)},
           {float(uv.x), float(uv.y), float(uv.w), float(uv.h)},
           {float(in.drawable_w), float(in.drawable_h)},
           uint32_t(blend == Opaque),
           brightness_bits};
    pass.device->set_pipeline(pass.cb, p);
    pass.device->set_bytes(pass.cb, gpu::Stage::Vertex, 0, &q, sizeof q);
    pass.device->set_bytes(pass.cb, gpu::Stage::Fragment, 0, &q, sizeof q);
    if (pass.format == gpu::Format::RGBA16F) { // Test86
        const float hdr[4] = {float(hdr_mode), std::max(1.0f, in.hdr_headroom), in.hdr_knee, in.hdr_desat};
        pass.device->set_bytes(pass.cb, gpu::Stage::Fragment, 1, hdr, sizeof hdr);
    }
    pass.device->set_texture(pass.cb, gpu::Stage::Fragment, 0, tex);
    pass.device->set_sampler(pass.cb, gpu::Stage::Fragment, 0, gpu::SamplerState{});
    pass.device->draw(pass.cb, gpu::Primitive::TriangleStrip, 0, 4);
}
gpu::Texture upload(Pass &pass, const UiElement &e) {
    if (e.w <= 0 || e.h <= 0)
        return {};
    const size_t n = size_t(e.w) * size_t(e.h);
    if (n > std::numeric_limits<size_t>::max() / 4 || e.rgba.size() < n * 4 || e.mask.size() < n)
        return {};
    std::vector<uint8_t> pixels(e.rgba.begin(), e.rgba.begin() + n * 4);
    for (size_t i = 0; i < n; ++i)
        if (!e.mask[i])
            pixels[4 * i + 3] = 0;
    gpu::Texture tex = pass.device->create_texture(
        {e.w, e.h, gpu::Format::RGBA8, gpu::UsageSampled | gpu::UsageCpu, 1});
    if (!tex) {
        fprintf(stderr, "compositor: UI texture allocation failed (%dx%d)\n", e.w, e.h);
        return {};
    }
    pass.device->upload(tex, {0, 0, e.w, e.h}, pixels.data(), e.w * 4);
    pass.transient.push_back(tex);
    return tex;
}

// An overlay was encoded in guest UI space. Crop it to its HUD owner and move
// that crop with the owner's anchor. Subtract newer overlapping source rects
// so one overlay pixel cannot be duplicated onto two independently moved HUDs.
void subtract(std::vector<CompositeRect> &pieces, CompositeRect b) {
    std::vector<CompositeRect> next;
    for (auto a : pieces) {
        const double x = std::max(a.x, b.x), y = std::max(a.y, b.y);
        const double r = std::min(a.x + a.w, b.x + b.w), bottom = std::min(a.y + a.h, b.y + b.h);
        if (x >= r || y >= bottom) {
            next.push_back(a);
            continue;
        }
        if (y > a.y)
            next.push_back({a.x, a.y, a.w, y - a.y});
        if (bottom < a.y + a.h)
            next.push_back({a.x, bottom, a.w, a.y + a.h - bottom});
        if (x > a.x)
            next.push_back({a.x, y, x - a.x, bottom - y});
        if (r < a.x + a.w)
            next.push_back({r, y, a.x + a.w - r, bottom - y});
    }
    pieces.swap(next);
}
} // namespace

bool compositor_settings_page_drawn(int *x, int *y, int *w, int *h) {
    const uint64_t v = g_settings_page_drawn.load(std::memory_order_relaxed);
    *x = int(v & 0xffff);
    *y = int(v >> 16 & 0xffff);
    *w = int(v >> 32 & 0xffff);
    *h = int(v >> 48 & 0xffff);
    return *w > 0 && *h > 0;
}
int compositor_ui_scale(int drawable_h, int guest_h, int override_) {
    return std::clamp(override_ ? override_ : drawable_h / std::max(1, guest_h), 1, 4);
}
Anchor compositor_default_anchor(const UiElement *e, int guest_w, int guest_h) {
    if (!e)
        return {-1, -1};
    return {default_axis(e->x, e->w, guest_w), default_axis(e->y, e->h, guest_h)};
}
void compositor_set_anchor(uint64_t id, Anchor a) {
    a.h = std::clamp<int>(a.h, -1, 1);
    a.v = std::clamp<int>(a.v, -1, 1);
    std::lock_guard lock(registry_mutex);
    anchors[id] = a;
}
void compositor_clear_anchor(uint64_t id) {
    std::lock_guard lock(registry_mutex);
    anchors.erase(id);
}
bool compositor_element_rect_on_drawable(const CompositorInput *in, uint64_t id, int *x, int *y,
                                         int *w, int *h) {
    if (!valid(in) || !in->ui)
        return false;
    const auto registry = registry_snapshot();
    for (const auto &e : in->ui->elements)
        if (e.id == id) {
            if (e.w <= 0 || e.h <= 0)
                return false;
            const auto r = element_rect(*in, e, registry);
            const double values[] = {r.x, r.y, r.w, r.h};
            for (double value : values)
                if (!std::isfinite(value) || value < std::numeric_limits<int>::min() ||
                    value > std::numeric_limits<int>::max())
                    return false;
            if (x)
                *x = int(std::lround(r.x));
            if (y)
                *y = int(std::lround(r.y));
            if (w)
                *w = int(std::lround(r.w));
            if (h)
                *h = int(std::lround(r.h));
            return true;
        }
    return false;
}
uint32_t compositor_element_ids(const UiFrame *ui, uint64_t *out, uint32_t max) {
    const auto elements = ordered(ui);
    const size_t count = std::min(elements.size(), size_t(UINT32_MAX));
    if (out)
        for (size_t i = 0; i < std::min(count, size_t(max)); ++i)
            out[i] = elements[i]->id;
    return uint32_t(count);
}
bool compositor_resolve_scene(CompositorSceneHistory *history, CompositorInput *in,
                              bool had_draws) {
    if (!history || !in)
        return false;
    const int domain = in->scene.domain_w > 0 ? in->scene.domain_w : in->guest_w;
    const bool changed = history->guest_w != in->guest_w || history->guest_h != in->guest_h ||
                         history->domain_w != domain || history->classic != in->classic;
    if (in->cls != HOST_SCREEN_GAMEPLAY || in->legacy || changed) {
        history->world = {};
        history->overlay = {};
        if (in->cls != HOST_SCREEN_GAMEPLAY || in->legacy)
            return false;
    }
    if (had_draws) {
        history->world = in->world;
        history->overlay = in->overlay;
        history->guest_w = in->guest_w;
        history->guest_h = in->guest_h;
        history->domain_w = domain;
        history->classic = in->classic;
        return false;
    }
    in->world = history->world;
    in->overlay = history->overlay;
    if (!history->world)
        return false;
    ++history->scene_reused;
    return true;
}

void compositor_compose(gpu::Device *device, const CompositorInput *in, gpu::Texture out,
                        gpu::CommandBuffer cb) {
    if (!device || !valid(in) || !out || !cb)
        return;
    const gpu::TextureDesc desc = device->describe(out);
    if (desc.width != in->drawable_w || desc.height != in->drawable_h)
        return;
    Pass pass{device, cb, desc.format, {}};
    gpu::RenderPass rp;
    rp.color_count = 1;
    rp.color[0].texture = out;
    rp.color[0].load = gpu::Load::Clear;
    rp.color[0].store = gpu::Store::Store;
    rp.color[0].clear[0] = rp.color[0].clear[1] = rp.color[0].clear[2] = 0;
    rp.color[0].clear[3] = 1;
    device->begin_render_pass(cb, rp);
    const CompositeRect full_uv{0, 0, 1, 1};
    auto finish = [&] {
        device->end_render_pass(cb);
        if (!pass.transient.empty()) {
            auto transient = std::make_shared<std::vector<gpu::Texture>>(std::move(pass.transient));
            device->on_complete(cb, [device, transient](gpu::CommandStatus, double) {
                for (gpu::Texture t : *transient)
                    device->destroy(t);
            });
        }
    };
    if (in->legacy) {
        draw(pass, *in, in->legacy_frame, pixel_rect(whole_rect(*in, false)), full_uv, Opaque,
             in->hdr_linear ? 2 : in->hdr_float ? (in->hdr_scene ? (in->hdr_pattern ? 5 : 3) : 4) : in->hdr_scene ? 1 : 0);   // item 10: 3/4 (5: TEST bars)
        if (in->settings_page)
            draw(pass, *in, in->settings_page, drawn_settings_page_rect(*in), full_uv, Straight);
        finish();
        return;
    }
    const auto elements = ordered(in->ui);
    const auto registry = registry_snapshot();
    if (in->cls == HOST_SCREEN_GAMEPLAY)
        draw(pass, *in, in->world,
             in->classic || boxed_scene(*in)
                 ? pixel_rect(whole_rect(*in, false))
                 : CompositeRect{0, 0, double(in->drawable_w), double(in->drawable_h)},
             full_uv, Opaque, in->hdr_scene ? 1 : 0);
    for (const auto *e : elements) {
        const auto dst = element_rect(*in, *e, registry);
        if (dst.x >= in->drawable_w || dst.y >= in->drawable_h || dst.x + dst.w <= 0 ||
            dst.y + dst.h <= 0)
            continue;
        draw(pass, *in, upload(pass, *e), dst, full_uv, Straight);
    }
    if (in->cls == HOST_SCREEN_GAMEPLAY && in->overlay) {
        for (size_t i = 0; i < elements.size(); ++i) {
            const auto &e = *elements[i];
            if (!e.is_hud || e.is_cursor || e.w <= 0 || e.h <= 0)
                continue;
            const double x = std::max(0, e.x), y = std::max(0, e.y);
            const double r = std::min(double(in->guest_w), double(e.x) + e.w);
            const double b = std::min(double(in->guest_h), double(e.y) + e.h);
            if (r <= x || b <= y)
                continue;
            std::vector<CompositeRect> pieces{{x, y, r - x, b - y}};
            for (size_t j = i + 1; j < elements.size(); ++j) {
                const auto &later = *elements[j];
                if (later.is_hud && !later.is_cursor && later.w > 0 && later.h > 0)
                    subtract(pieces,
                             {double(later.x), double(later.y), double(later.w), double(later.h)});
            }
            const auto dst = element_rect(*in, e, registry);
            const double sx = dst.w / e.w, sy = dst.h / e.h;
            for (auto part : pieces) {
                draw(pass, *in, in->overlay,
                     {dst.x + (part.x - e.x) * sx, dst.y + (part.y - e.y) * sy, part.w * sx,
                      part.h * sy},
                     {part.x / in->guest_w, part.y / in->guest_h, part.w / in->guest_w,
                      part.h / in->guest_h},
                     Premultiplied);
            }
        }
    }
    if (in->settings_page)
        draw(pass, *in, in->settings_page, drawn_settings_page_rect(*in), full_uv, Straight);
    finish();
}

void compositor_set_pointer_position(bool valid, int32_t x, int32_t y) {
    std::lock_guard lock(pointer_mutex);
    pointer_valid = valid;
    pointer_x = x;
    pointer_y = y;
}

LayoutSnapshot compositor_layout_snapshot(const CompositorInput *in) {
    LayoutSnapshot result;
    if (!valid(in))
        return result;
    result.drawable_w = in->drawable_w;
    result.drawable_h = in->drawable_h;
    result.guest_w = in->guest_w;
    result.guest_h = in->guest_h;
    result.ui_scale = compositor_ui_scale(in->drawable_h, in->guest_h, in->scale_override);
    result.scene = in->scene;
    result.cls = in->cls;
    result.scale_override = in->scale_override;
    result.legacy = in->legacy;
    result.classic = in->classic;
    result.narrow = in->narrow;
    if (in->legacy || in->classic || in->cls != HOST_SCREEN_GAMEPLAY || boxed_scene(*in) ||
        !std::isfinite(result.scene.scale_x) || result.scene.scale_x <= 0 ||
        !std::isfinite(result.scene.scale_y) || result.scene.scale_y <= 0 ||
        !std::isfinite(result.scene.offset_x) || !std::isfinite(result.scene.offset_y) ||
        result.scene.domain_w <= 0) {
        const auto r = whole_rect(*in, !in->legacy && !in->classic && in->cls == HOST_SCREEN_MENU);
        result.scene = {float(r.w / in->guest_w), float(r.h / in->guest_h), float(r.x), float(r.y),
                        in->guest_w};
    }
    const auto registry = registry_snapshot();
    if (in->ui)
        for (const auto &e : in->ui->elements) {
            if (e.w <= 0 || e.h <= 0)
                continue;
            const auto r = element_rect(*in, e, registry);
            const double values[] = {r.x, r.y, r.w, r.h};
            bool ok = r.w > 0 && r.h > 0;
            for (double value : values)
                ok &= std::isfinite(value) && value >= INT32_MIN && value <= INT32_MAX;
            if (ok)
                result.elements.push_back({e.id,
                                           e.last_seq,
                                           {e.x, e.y, e.w, e.h},
                                           {int(r.x), int(r.y), int(r.w), int(r.h)},
                                           e.is_cursor});
        }
    std::stable_sort(result.elements.begin(), result.elements.end(),
                     [](const auto &a, const auto &b) { return a.last_seq < b.last_seq; });
    return result;
}

LayoutSnapshot compositor_resize_layout(const LayoutSnapshot &layout, int w, int h) {
    if (w <= 0 || h <= 0 || layout.drawable_w <= 0 || layout.drawable_h <= 0)
        return layout;
    UiFrame ui{};
    for (const auto &e : layout.elements) {
        UiElement item{};
        item.id = e.id;
        item.last_seq = e.last_seq;
        item.x = e.guest.x;
        item.y = e.guest.y;
        item.w = e.guest.w;
        item.h = e.guest.h;
        item.is_cursor = e.is_cursor;
        ui.elements.push_back(std::move(item));
    }
    CompositorInput in{};
    in.ui = &ui;
    in.cls = layout.cls;
    in.guest_w = layout.guest_w;
    in.guest_h = layout.guest_h;
    in.drawable_w = w;
    in.drawable_h = h;
    in.scale_override = layout.scale_override;
    in.legacy = layout.legacy;
    in.classic = layout.classic;
    in.narrow = layout.narrow;
    in.scene = layout.scene;
    in.scene.scale_x *= double(w) / layout.drawable_w;
    in.scene.offset_x *= double(w) / layout.drawable_w;
    in.scene.scale_y *= double(h) / layout.drawable_h;
    in.scene.offset_y *= double(h) / layout.drawable_h;
    auto result = compositor_layout_snapshot(&in);
    result.frame_id = layout.frame_id;
    return result;
}
