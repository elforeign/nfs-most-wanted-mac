// The translated retail string renderer runs with fake font metrics and ordinary
// identity/scale matrix plumbing. No font atlas, Metal, or screenshot required.
static float metric_width, rendered_width;
void f_00585B50(Cpu *c) { c->eax = 0x80000; M32(0x80008) = 1; c->esp += 4; }
void f_0057E920(Cpu *c) { (void)c; abort(); } // explicit fallback must bypass localization
void f_00591A00(Cpu *c) { c->esp += 8; }
void f_00591AB0(Cpu *c) { c->eax = 0x81000; c->esp += 12; }
static void identity(uint32_t p) { memset(G + p, 0, 64); for (int i = 0; i < 4; ++i) MF32(p + i * 20) = 1; }
void f_004450C0(Cpu *c) { identity(arg(c, 0)); c->esp += 4; }
void f_005848B0(Cpu *c) {
    uint32_t matrix = arg(c, 1); identity(matrix);
    MF32(matrix) = MF32(arg(c, 0) + 0x38) * argf(c, 4);
    c->esp += 24;
}
void f_00586130(Cpu *c) { x87_push(c, metric_width); c->esp += 20; }
void f_0045ECF0(Cpu *c) {
    uint32_t dst = arg(c, 0), a = arg(c, 1), b = arg(c, 2);
    float result[16] = {0};
    for (int i = 0; i < 4; ++i) for (int j = 0; j < 4; ++j)
        for (int k = 0; k < 4; ++k) result[i * 4 + j] += MF32(a + (i * 4 + k) * 4) * MF32(b + (k * 4 + j) * 4);
    memcpy(G + dst, result, sizeof result); c->esp += 4;
}
void f_0059AC60(Cpu *c) { rendered_width = metric_width * MF32(arg(c, 3)); c->esp += 28; }
#include "trigopt_renderer.inc"
static float render_width(uint32_t object, float width, unsigned language) {
    metric_width = width; rendered_width = -1;
    M32(0x8f41c0) = language;
    Cpu c = {.esp = 0x880000, .ecx = 0x82000, .cw = 0x37f};
    M32(c.esp + 4) = object; M32(c.esp + 8) = 0; M32(c.esp + 12) = 0;
    f_005A4390(&c); CHECK(c.esp == 0x880010);
    return rendered_width;
}
