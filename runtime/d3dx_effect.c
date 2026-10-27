// d3dx_effect.c - ID3DXEffect / ID3DXEffectPool (d3dx9_26) for the game's embedded fx_2_0 effects.
//
// The binary layout follows tools/scripts/extract_pc_effects.py (parameters with typedefs, default values and
// annotations; techniques -> passes -> states; then the object table with strings and shader bytecode, and the
// resource table that marks states as shaders, parameter references or FXLC expressions). The game's state
// expressions are all `mov oc, c` (copies of parameter components), so FXLC evaluation is that one op.
//
// BeginPass applies the pass through the device's own COM methods (call_guest on its vtable), so render
// states, textures and shaders land in the same place as the game's direct calls. Shader constants come from
// each shader's CTAB: every constant named after an effect parameter is uploaded from that parameter.
#include <stdlib.h>

#include "host.h"

#define D3D_OK 0
#define D3DERR_INVALIDCALL 0x8876086Cu
#define E_FAIL 0x80004005u
#define HANDLE_BASE 0xF2000000u

// ---------------- data model ----------------
enum { PC_SCALAR, PC_VECTOR, PC_MATRIX_ROWS, PC_MATRIX_COLUMNS, PC_OBJECT, PC_STRUCT };
enum { PT_VOID, PT_BOOL, PT_INT, PT_FLOAT, PT_STRING, PT_TEXTURE, PT_TEXTURE1D, PT_TEXTURE2D, PT_TEXTURE3D, PT_TEXTURECUBE,
       PT_SAMPLER, PT_SAMPLER1D, PT_SAMPLER2D, PT_SAMPLER3D, PT_SAMPLERCUBE, PT_PIXELSHADER, PT_VERTEXSHADER };
typedef struct Param Param;
typedef struct State State;
struct State {
    uint32_t op, index;
    Param *value;               // typedef + constant value
    int kind;                   // 0 constant, 1 shader, 2 fxlc, 3 parameter reference
    char ref[64];               // kind 3: referenced parameter name
    uint32_t *fxlc; uint32_t fxlc_n;   // kind 2: bytecode
    uint32_t shader;            // kind 1: device shader object (guest), created on first use
    uint32_t *code; uint32_t code_n;
    // caches built on first use (parameters never move once the effect is parsed)
    int ref_done; Param *ref_param;     // kind 3: the referenced parameter
    struct FxlcPre *pre;                // kind 2: the parsed expression
    struct CtabEntry *ctab; int nctab, ctab_done;   // kind 1: the shader's CTAB, resolved to parameters
};
struct Param {
    uint32_t handle;
    char name[64], semantic[64];
    uint32_t cls, type, rows, cols, elements, member_count, bytes, flags;
    Param *members;             // elements or struct members
    int nmembers;
    uint8_t *data;              // numeric value (register order), or for objects: guest object/handle in data[0..3]
    Param *annotations;
    int nann;
    uint32_t object_id;
    State *states;              // sampler_state { ... }
    int nstates;
    uint32_t gname, gsemantic;  // guest copies for GetParameterDesc
    struct Effect *fx;
    Param *shared;              // pool-backed storage
};
typedef struct {
    uint32_t handle;
    char name[64];
    Param *annotations; int nann;
    State *states; int nstates;
    uint32_t gname;
    uint32_t vs_code, ps_code;  // guest copies of the shader bytecode (GetPassDesc)
    uint64_t applied;           // fx_epoch when the pass was last applied (BeginPass/CommitChanges)
} Pass;
typedef struct {
    uint32_t handle;
    char name[64];
    Param *annotations; int nann;
    Pass *passes; int npasses;
    uint32_t gname;
} Technique;
typedef struct { uint32_t id; uint8_t *data; uint32_t size; } Object;
typedef struct Effect {
    uint32_t guest, device;
    int refs;
    Param *params; int nparams;
    Technique *techs; int ntechs;
    Object *objs; int nobjs;
    Technique *cur;
    int in_pass;
    Pass *pass;
    uint32_t pool;
    char name[64];
    struct SavedState *saved;   // Begin .. End: the device states the technique sets, as they were (e_Begin)
    int nsaved, capsaved;
} Effect;
char diag_fx_pass[192];                 // "effect/technique/pass" of the pass being drawn, for the draw dumps

// handle table: every parameter, technique and pass of every effect
typedef struct { int kind; void *p; } HEntry;   // 1 param, 2 technique, 3 pass
static HEntry *htab;
static uint32_t nh, caph;
static uint32_t new_handle(int kind, void *p) {
    if (nh == caph) { caph = caph ? caph * 2 : 4096; htab = realloc(htab, caph * sizeof *htab); }
    htab[nh].kind = kind; htab[nh].p = p;
    return HANDLE_BASE + 4 * nh++;
}

// Parameter values carry a change stamp in a header in front of `data` (so parameters shared through the pool,
// which share `data`, share it too): every Set* bumps it, and CommitChanges re-applies only the states and
// constants whose parameters changed since the pass was last applied, as D3DX does.
// 64-bit: ~0.6 M stamps a second in a race wrapped a 32-bit epoch after ~2 h of play (audit 1; compares are ordered).
static uint64_t fx_epoch = 1;
uint64_t fx_epoch_now(void) { return fx_epoch; }
#define STAMP(d) (((uint64_t *)(void *)(d))[-1])
// test only: NFS_TEST_FX_EPOCH=<n> starts the epoch there (e.g. 4294967000: wraps 32 bits in the first frames)
static uint8_t *data_alloc(uint32_t n) {
    static int init;
    if (!init) { init = 1; const char *e = getenv("NFS_TEST_FX_EPOCH"); if (e) fx_epoch = strtoull(e, NULL, 10); }
    return (uint8_t *)calloc(1, n + 8) + 8;
}
static void data_free(uint8_t *d) { if (d) free(d - 8); }
static void touch(Param *p) {
    if (p->data) STAMP(p->data) = ++fx_epoch;
    for (int i = 0; i < p->nmembers; ++i) touch(&p->members[i]);
}
static uint64_t pstamp(const Param *p) {
    if (!p) return 0;
    uint64_t t = p->data ? STAMP(p->data) : 0;
    for (int i = 0; i < p->nmembers; ++i) { uint64_t u = pstamp(&p->members[i]); if (u > t) t = u; }
    return t;
}

// ---------------- parser ----------------
typedef struct { const uint8_t *b; uint32_t n; Effect *fx; } Blob;
static uint32_t d32(Blob *bl, uint32_t o) { uint32_t v = 0; if (o + 4 <= bl->n) memcpy(&v, bl->b + o, 4); return v; }
static void name_at(Blob *bl, uint32_t o, char *out, size_t cap) {
    uint32_t size = d32(bl, o);
    size_t k = 0;
    for (; k + 1 < cap && k < size && o + 4 + k < bl->n && bl->b[o + 4 + k]; ++k) out[k] = (char)bl->b[o + 4 + k];
    out[k] = 0;
}
static uint32_t typedef_at(Blob *bl, uint32_t ptr, Param *p, const Param *parent) {
    if (!parent) {
        p->type = d32(bl, ptr); p->cls = d32(bl, ptr + 4);
        uint32_t no = d32(bl, ptr + 8), so = d32(bl, ptr + 12);
        p->elements = d32(bl, ptr + 16);
        ptr += 20;
        name_at(bl, no, p->name, sizeof p->name);
        if (so) name_at(bl, so, p->semantic, sizeof p->semantic);
        if (p->cls == PC_VECTOR) { p->cols = d32(bl, ptr); p->rows = d32(bl, ptr + 4); ptr += 8; }
        else if (p->cls == PC_SCALAR || p->cls == PC_MATRIX_ROWS || p->cls == PC_MATRIX_COLUMNS) { p->rows = d32(bl, ptr); p->cols = d32(bl, ptr + 4); ptr += 8; }
        else if (p->cls == PC_STRUCT) { p->member_count = d32(bl, ptr); ptr += 4; }
        p->bytes = p->cls <= PC_MATRIX_COLUMNS ? 4 * p->rows * p->cols : 0;
    } else {
        p->type = parent->type; p->cls = parent->cls; p->rows = parent->rows; p->cols = parent->cols;
        p->member_count = parent->member_count; p->bytes = parent->cls <= PC_MATRIX_COLUMNS ? 4 * parent->rows * parent->cols : 0;
        snprintf(p->name, sizeof p->name, "%s", parent->name);
        snprintf(p->semantic, sizeof p->semantic, "%s", parent->semantic);
        p->elements = 0;
    }
    if (p->elements) {
        p->nmembers = (int)p->elements;
        p->members = calloc(p->elements, sizeof *p->members);
        uint32_t save = ptr, tot = 0;
        for (uint32_t i = 0; i < p->elements; ++i) { ptr = typedef_at(bl, save, &p->members[i], p); tot += p->members[i].bytes; }
        p->bytes = tot;
    } else if (p->member_count) {
        p->nmembers = (int)p->member_count;
        p->members = calloc(p->member_count, sizeof *p->members);
        for (uint32_t i = 0; i < p->member_count; ++i) { ptr = typedef_at(bl, ptr, &p->members[i], NULL); p->bytes += p->members[i].bytes; }
    }
    return ptr;
}
static uint32_t state_at(Blob *bl, uint32_t ptr, State *st);
// Reads the value for p from `off` (numeric data is inline there; objects are ids / state lists).
static uint32_t value_at(Blob *bl, Param *p, uint32_t off) {
    p->fx = bl->fx;
    if (p->elements || p->cls == PC_STRUCT) {
        uint32_t o = off;
        for (int i = 0; i < p->nmembers; ++i) {
            Param *m = &p->members[i];
            if (m->cls <= PC_MATRIX_COLUMNS || m->cls == PC_STRUCT || m->elements) { value_at(bl, m, o); o += m->bytes ? m->bytes : 4; }
            else o = value_at(bl, m, o);
        }
        return o;
    }
    if (p->cls <= PC_MATRIX_COLUMNS) {
        p->data = data_alloc(p->bytes ? p->bytes : 4);
        if (off + p->bytes <= bl->n) memcpy(p->data, bl->b + off, p->bytes);
        return off + p->bytes;
    }
    p->data = data_alloc(4);                      // objects: texture pointer / shader / string id
    if (p->type == PT_STRING || p->type == PT_PIXELSHADER || p->type == PT_VERTEXSHADER || (p->type >= PT_TEXTURE && p->type <= PT_TEXTURECUBE)) {
        p->object_id = d32(bl, off);
        return off + 4;
    }
    if (p->type >= PT_SAMPLER && p->type <= PT_SAMPLERCUBE) {
        p->nstates = (int)d32(bl, off);
        p->states = calloc((size_t)p->nstates + 1, sizeof *p->states);
        off += 4;
        for (int i = 0; i < p->nstates; ++i) off = state_at(bl, off, &p->states[i]);
        return off;
    }
    return off + 4;
}
static uint32_t state_at(Blob *bl, uint32_t ptr, State *st) {
    st->op = d32(bl, ptr); st->index = d32(bl, ptr + 4);
    uint32_t toff = d32(bl, ptr + 8), voff = d32(bl, ptr + 12);
    st->value = calloc(1, sizeof *st->value);
    typedef_at(bl, toff, st->value, NULL);
    value_at(bl, st->value, voff);
    return ptr + 16;
}
static uint32_t annotation_at(Blob *bl, uint32_t ptr, Param *a) {
    typedef_at(bl, d32(bl, ptr), a, NULL);
    value_at(bl, a, d32(bl, ptr + 4));
    return ptr + 8;
}
static void assign_handles(Param *p) {
    p->handle = new_handle(1, p);
    p->gname = gstr(p->name);
    p->gsemantic = p->semantic[0] ? gstr(p->semantic) : 0;
    for (int i = 0; i < p->nmembers; ++i) assign_handles(&p->members[i]);
    for (int i = 0; i < p->nann; ++i) assign_handles(&p->annotations[i]);
}

static Object *find_object(Effect *fx, uint32_t id) {
    for (int i = 0; i < fx->nobjs; ++i) if (fx->objs[i].id == id) return &fx->objs[i];
    return NULL;
}
static int parse_effect(Effect *fx, const uint8_t *data, uint32_t size) {
    if (size < 8 || d32(&(Blob){data, size, fx}, 0) != 0xFEFF0901) return 0;
    Blob bl = {data + 8, size - 8, fx};
    uint32_t ptr = d32(&(Blob){data, size, fx}, 4);
    fx->nparams = (int)d32(&bl, ptr);
    fx->ntechs = (int)d32(&bl, ptr + 4);
    ptr += 16;
    fx->params = calloc((size_t)fx->nparams + 1, sizeof *fx->params);
    for (int i = 0; i < fx->nparams; ++i) {
        Param *p = &fx->params[i];
        uint32_t toff = d32(&bl, ptr), voff = d32(&bl, ptr + 4), flags = d32(&bl, ptr + 8), nann = d32(&bl, ptr + 12);
        ptr += 16;
        p->nann = (int)nann;
        p->annotations = calloc(nann + 1, sizeof *p->annotations);
        for (uint32_t k = 0; k < nann; ++k) ptr = annotation_at(&bl, ptr, &p->annotations[k]);
        typedef_at(&bl, toff, p, NULL);
        p->flags = flags;
        value_at(&bl, p, voff);
    }
    fx->techs = calloc((size_t)fx->ntechs + 1, sizeof *fx->techs);
    for (int t = 0; t < fx->ntechs; ++t) {
        Technique *T = &fx->techs[t];
        name_at(&bl, d32(&bl, ptr), T->name, sizeof T->name);
        T->nann = (int)d32(&bl, ptr + 4);
        T->npasses = (int)d32(&bl, ptr + 8);
        ptr += 12;
        T->annotations = calloc((size_t)T->nann + 1, sizeof *T->annotations);
        for (int k = 0; k < T->nann; ++k) ptr = annotation_at(&bl, ptr, &T->annotations[k]);
        T->passes = calloc((size_t)T->npasses + 1, sizeof *T->passes);
        for (int pi = 0; pi < T->npasses; ++pi) {
            Pass *P = &T->passes[pi];
            name_at(&bl, d32(&bl, ptr), P->name, sizeof P->name);
            P->nann = (int)d32(&bl, ptr + 4);
            P->nstates = (int)d32(&bl, ptr + 8);
            ptr += 12;
            P->annotations = calloc((size_t)P->nann + 1, sizeof *P->annotations);
            for (int k = 0; k < P->nann; ++k) ptr = annotation_at(&bl, ptr, &P->annotations[k]);
            P->states = calloc((size_t)P->nstates + 1, sizeof *P->states);
            for (int k = 0; k < P->nstates; ++k) ptr = state_at(&bl, ptr, &P->states[k]);
        }
    }
    uint32_t nstr = d32(&bl, ptr), nres = d32(&bl, ptr + 4);
    ptr += 8;
    fx->objs = calloc(nstr + nres + 1, sizeof *fx->objs);
    for (uint32_t i = 0; i < nstr; ++i) {
        uint32_t id = d32(&bl, ptr), n = d32(&bl, ptr + 4);
        ptr += 8;
        Object *o = &fx->objs[fx->nobjs++];
        o->id = id; o->size = n; o->data = malloc(n + 1); memcpy(o->data, bl.b + ptr, n); o->data[n] = 0;
        ptr += (n + 3) & ~3u;
    }
    for (uint32_t i = 0; i < nres; ++i) {
        uint32_t ti = d32(&bl, ptr), idx = d32(&bl, ptr + 4), ei = d32(&bl, ptr + 8), si = d32(&bl, ptr + 12);
        uint32_t usage = d32(&bl, ptr + 16), n = d32(&bl, ptr + 20);
        ptr += 24;
        const uint8_t *d = bl.b + ptr;
        ptr += (n + 3) & ~3u;
        State *st = NULL;
        if (ti == 0xFFFFFFFF) {
            if (idx >= (uint32_t)fx->nparams) continue;
            Param *p = &fx->params[idx];
            if (ei != 0xFFFFFFFF && p->elements && ei < p->elements) p = &p->members[ei];
            if (si < (uint32_t)p->nstates) st = &p->states[si];
        } else if (ti < (uint32_t)fx->ntechs && idx < (uint32_t)fx->techs[ti].npasses && si < (uint32_t)fx->techs[ti].passes[idx].nstates) {
            st = &fx->techs[ti].passes[idx].states[si];
        }
        if (!st) continue;
        if (usage == 0) {
            if (st->value->type == PT_VERTEXSHADER || st->value->type == PT_PIXELSHADER) {
                st->kind = 1;
                st->code_n = n / 4; st->code = malloc(n); memcpy(st->code, d, n);
            } else {
                st->kind = 2;
                st->fxlc_n = n / 4; st->fxlc = malloc(n); memcpy(st->fxlc, d, n);
            }
        } else if (usage == 1) {
            st->kind = 3;
            snprintf(st->ref, sizeof st->ref, "%.*s", (int)(n < 63 ? n : 63), (const char *)d);
        }
    }
    // shader states that reference a shader object (not inline): take the bytecode from the object table
    for (int t = 0; t < fx->ntechs; ++t)
        for (int pi = 0; pi < fx->techs[t].npasses; ++pi)
            for (int k = 0; k < fx->techs[t].passes[pi].nstates; ++k) {
                State *st = &fx->techs[t].passes[pi].states[k];
                if (st->kind == 0 && (st->value->type == PT_VERTEXSHADER || st->value->type == PT_PIXELSHADER)) {
                    Object *o = find_object(fx, st->value->object_id);
                    if (o && o->size >= 4) { st->kind = 1; st->code_n = o->size / 4; st->code = (uint32_t *)o->data; }
                }
            }
    for (int i = 0; i < fx->nparams; ++i) assign_handles(&fx->params[i]);
    for (int t = 0; t < fx->ntechs; ++t) {
        Technique *T = &fx->techs[t];
        T->handle = new_handle(2, T);
        T->gname = gstr(T->name);
        for (int k = 0; k < T->nann; ++k) assign_handles(&T->annotations[k]);
        for (int pi = 0; pi < T->npasses; ++pi) {
            Pass *P = &T->passes[pi];
            P->handle = new_handle(3, P);
            P->gname = gstr(P->name);
            for (int k = 0; k < P->nann; ++k) assign_handles(&P->annotations[k]);
            for (int k = 0; k < P->nstates; ++k) {
                State *st = &P->states[k];
                if (st->kind != 1) continue;
                uint32_t g = galloc(4 * st->code_n);
                memcpy(G + g, st->code, 4 * st->code_n);
                if (st->value->type == PT_VERTEXSHADER) P->vs_code = g; else P->ps_code = g;
            }
        }
    }
    return 1;
}

// ---------------- pool (shared parameters) ----------------
typedef struct { char name[64]; uint32_t bytes; uint8_t *data; } Shared;
static Shared shared[256];
static int nshared;
static void share_params(Effect *fx) {
    for (int i = 0; i < fx->nparams; ++i) {
        Param *p = &fx->params[i];
        if (!(p->flags & 1) || p->cls > PC_MATRIX_COLUMNS || p->elements) continue;   // D3DX_PARAMETER_SHARED, numeric only
        Shared *s = NULL;
        for (int k = 0; k < nshared; ++k) if (!strcmp(shared[k].name, p->name) && shared[k].bytes == p->bytes) s = &shared[k];
        if (!s && nshared < 256) {
            s = &shared[nshared++];
            snprintf(s->name, sizeof s->name, "%s", p->name);
            s->bytes = p->bytes;
            s->data = p->data;
        }
        if (s) { if (p->data != s->data) data_free(p->data); p->data = s->data; }
    }
}

// ---------------- handles ----------------
static Param *find_param(Param *list, int n, const char *name) {
    for (int i = 0; i < n; ++i) if (!strcmp(list[i].name, name)) return &list[i];
    return NULL;
}
static Param *param_of(Effect *fx, uint32_t h) {
    if (h >= HANDLE_BASE && h < HANDLE_BASE + 4 * nh && htab[(h - HANDLE_BASE) / 4].kind == 1) return htab[(h - HANDLE_BASE) / 4].p;
    if (!h || h >= 0xF0000000u) return NULL;
    // a name: "a", "a.b" or "a[3]"
    const char *s = (const char *)G + h;
    char buf[128];
    snprintf(buf, sizeof buf, "%s", s);
    Param *list = fx->params, *p = NULL;
    int n = fx->nparams;
    for (char *tok = strtok(buf, "."); tok; tok = strtok(NULL, ".")) {
        char *br = strchr(tok, '[');
        int idx = -1;
        if (br) { *br = 0; idx = atoi(br + 1); }
        p = find_param(list, n, tok);
        if (!p) return NULL;
        if (idx >= 0) { if (!p->elements || idx >= (int)p->elements) return NULL; p = &p->members[idx]; }
        list = p->members; n = p->nmembers;
    }
    return p;
}
static Technique *tech_of(Effect *fx, uint32_t h) {
    if (h >= HANDLE_BASE && h < HANDLE_BASE + 4 * nh && htab[(h - HANDLE_BASE) / 4].kind == 2) return htab[(h - HANDLE_BASE) / 4].p;
    if (!h || h >= 0xF0000000u) return NULL;
    for (int i = 0; i < fx->ntechs; ++i) if (!strcmp(fx->techs[i].name, (const char *)G + h)) return &fx->techs[i];
    return NULL;
}
static Pass *pass_of(uint32_t h) {
    if (h >= HANDLE_BASE && h < HANDLE_BASE + 4 * nh && htab[(h - HANDLE_BASE) / 4].kind == 3) return htab[(h - HANDLE_BASE) / 4].p;
    return NULL;
}

// ---------------- values ----------------
// Numeric parameters keep their components in register order: rows of a MATRIX_ROWS, columns of a
// MATRIX_COLUMNS, as D3DX does. Integer and bool parameters hold ints.
static void set_floats(Param *p, const float *v, uint32_t n) {
    if (p->elements) {
        for (int i = 0; i < p->nmembers && n; ++i) {
            uint32_t k = p->members[i].rows * p->members[i].cols;
            set_floats(&p->members[i], v, n < k ? n : k);
            v += k; n = n > k ? n - k : 0;
        }
        return;
    }
    if (p->cls > PC_MATRIX_COLUMNS) return;
    uint32_t cnt = p->rows * p->cols;
    for (uint32_t i = 0; i < cnt && i < n; ++i) {
        uint32_t u;
        if (p->type == PT_FLOAT) memcpy(&u, &v[i], 4);
        else if (p->type == PT_BOOL) u = v[i] != 0.0f;
        else u = (uint32_t)(int32_t)v[i];
        memcpy(p->data + 4 * i, &u, 4);
    }
    if (p->data) STAMP(p->data) = ++fx_epoch;
}
static void get_floats(Param *p, float *v, uint32_t n) {
    if (p->elements) {
        for (int i = 0; i < p->nmembers && n; ++i) {
            uint32_t k = p->members[i].rows * p->members[i].cols;
            get_floats(&p->members[i], v, n < k ? n : k);
            v += k; n = n > k ? n - k : 0;
        }
        return;
    }
    if (p->cls > PC_MATRIX_COLUMNS) return;
    uint32_t cnt = p->rows * p->cols;
    for (uint32_t i = 0; i < cnt && i < n; ++i) {
        uint32_t u;
        memcpy(&u, p->data + 4 * i, 4);
        if (p->type == PT_FLOAT) memcpy(&v[i], &u, 4);
        else v[i] = (float)(int32_t)u;
    }
}
// m: a D3DXMATRIX (row-major 4x4) -> register order for p's rows/cols
static void set_matrix(Param *p, const float *m, int transpose) {
    float v[16];
    uint32_t r = p->rows, c = p->cols, k = 0;
    if (p->cls == PC_MATRIX_COLUMNS) { for (uint32_t j = 0; j < c; ++j) for (uint32_t i = 0; i < r; ++i) v[k++] = transpose ? m[j * 4 + i] : m[i * 4 + j]; }
    else { for (uint32_t i = 0; i < r; ++i) for (uint32_t j = 0; j < c; ++j) v[k++] = transpose ? m[j * 4 + i] : m[i * 4 + j]; }
    set_floats(p, v, k);
}
static void get_matrix(Param *p, float *m, int transpose) {
    float v[16] = {0};
    uint32_t r = p->rows, c = p->cols, k = 0;
    get_floats(p, v, r * c);
    memset(m, 0, 64);
    if (p->cls == PC_MATRIX_COLUMNS) { for (uint32_t j = 0; j < c; ++j) for (uint32_t i = 0; i < r; ++i) { float x = v[k++]; if (transpose) m[j * 4 + i] = x; else m[i * 4 + j] = x; } }
    else { for (uint32_t i = 0; i < r; ++i) for (uint32_t j = 0; j < c; ++j) { float x = v[k++]; if (transpose) m[j * 4 + i] = x; else m[i * 4 + j] = x; } }
}

// ---------------- applying passes ----------------
// Transient guest buffers for the device calls below (consumed synchronously): a reused ring, not the bump heap.
static uint32_t scratch(uint32_t n) {
    static uint32_t base, off;
    enum { SIZE = 1 << 16 };
    if (!base) base = galloc(SIZE);
    n = (n + 15) & ~15u;
    if (off + n > SIZE) off = 0;
    uint32_t a = base + off;
    off += n;
    return a;
}
static uint32_t dev_call(Cpu *c, Effect *fx, int slot, int n, const uint32_t *a) {
    uint32_t args[8] = {fx->device};
    for (int i = 0; i < n; ++i) args[i + 1] = a[i];
    return call_guest(c, M32(M32(fx->device) + 4 * (uint32_t)slot), n + 1, args);
}
enum { DEV_GetRenderState = 58, DEV_GetTextureStageState = 66, DEV_GetSamplerState = 68 };
enum { DEV_SetTransform = 44, DEV_SetMaterial = 49, DEV_SetLight = 51, DEV_LightEnable = 53, DEV_SetRenderState = 57,
       DEV_SetTexture = 65, DEV_SetTextureStageState = 67, DEV_SetSamplerState = 69, DEV_SetNPatchMode = 79, DEV_SetFVF = 89,
       DEV_CreateVertexShader = 91, DEV_SetVertexShader = 92, DEV_SetVertexShaderConstantF = 94, DEV_SetVertexShaderConstantI = 96,
       DEV_SetVertexShaderConstantB = 98, DEV_CreatePixelShader = 106, DEV_SetPixelShader = 107,
       DEV_SetPixelShaderConstantF = 109, DEV_SetPixelShaderConstantI = 111, DEV_SetPixelShaderConstantB = 113 };

// state op -> (class, D3D enum) in the effect's state table order (see extract_pc_effects.py STATE_TABLE)
static const uint16_t rs_of[] = {7, 8, 9, 14, 15, 16, 19, 20, 22, 23, 24, 25, 26, 27, 28, 29, 34, 35, 36, 37, 38, 48, 52, 53, 54, 55,
    56, 57, 58, 59, 60, 128, 129, 130, 131, 132, 133, 134, 135, 198, 199, 200, 201, 202, 203, 204, 205, 136, 137, 139, 140, 141,
    142, 143, 145, 146, 147, 148, 151, 152, 154, 155, 166, 156, 157, 158, 159, 160, 161, 162, 163, 165, 167, 168, 170, 171, 172,
    173, 174, 175, 176, 178, 179, 180, 181, 182, 183, 184, 185, 186, 187, 188, 189, 190, 191, 192, 193, 194, 195, 206, 207, 208, 209};
#define N_RS (int)(sizeof rs_of / sizeof *rs_of)                  // 103 render states
static const uint8_t tss_of[] = {1, 26, 2, 3, 4, 27, 5, 6, 28, 7, 8, 9, 10, 11, 22, 23, 24, 32};
#define OP_TSS N_RS                                               // 103..120
#define OP_MISC (OP_TSS + 18)                                     // 121 NPatchMode, 122 FVF
#define OP_TRANSFORM (OP_MISC + 2)                                // 123 PROJECTION, VIEW, WORLD, TEXTURE0
#define OP_MATERIAL (OP_TRANSFORM + 4)                            // 127..131
#define OP_LIGHT (OP_MATERIAL + 5)                                // 132..144, 145 LightEnable
#define OP_SHADER (OP_LIGHT + 14)                                 // 146 VertexShader, 147 PixelShader
#define OP_CONST (OP_SHADER + 2)                                  // 148..163
#define OP_TEX (OP_CONST + 16)                                    // 164 Texture
#define OP_SAMP (OP_TEX + 1)                                      // 165..177 sampler states, 178 Sampler

// Value of a state as 32-bit words (up to 16): constant, parameter reference or `mov` expression.
// An FXLC state expression, parsed once: its inputs resolved to parameters, its literals and its code.
struct FxlcPre { struct { Param *p; uint32_t reg; } cin[16]; int ncin; double clit[64]; uint32_t nclit; uint32_t *code, ncode; };
static struct FxlcPre *fxlc_parse(Effect *fx, State *st) {
    // FXLC: version, comments (CTAB maps c# -> parameter, CLIT literals), then FXLC with `mov` ops
    uint32_t *t = st->fxlc, n = st->fxlc_n, i = 1;
    struct FxlcPre *pp = calloc(1, sizeof *pp);
    double *clit = pp->clit; uint32_t nclit = 0;
    struct { char name[64]; uint32_t reg, count; } cin[16]; int ncin = 0;
    uint32_t *code = NULL, ncode = 0;
    while (i < n) {
        uint32_t tok = t[i];
        if ((tok & 0xFFFF) == 0xFFFE) {
            uint32_t len = (tok >> 16) & 0x7FFF;
            uint32_t *d = &t[i + 1];
            if (d[0] == 0x42415443) {                          // "CTAB"
                uint8_t *cb = (uint8_t *)&d[1];
                uint32_t nc, co;
                memcpy(&nc, cb + 12, 4); memcpy(&co, cb + 16, 4);
                for (uint32_t k = 0; k < nc && ncin < 16; ++k) {
                    uint32_t nm, rs;
                    uint16_t ri, rc;
                    memcpy(&nm, cb + co + 20 * k, 4);
                    memcpy(&rs, cb + co + 20 * k + 4, 2); rs &= 0xFFFF;
                    memcpy(&ri, cb + co + 20 * k + 6, 2); memcpy(&rc, cb + co + 20 * k + 8, 2);
                    snprintf(cin[ncin].name, 64, "%s", (char *)cb + nm);
                    cin[ncin].reg = ri; cin[ncin].count = rc;
                    ++ncin;
                }
            } else if (d[0] == 0x54494C43) {                   // "CLIT"
                nclit = d[1] < 64 ? d[1] : 64;
                memcpy(clit, &d[2], 8 * nclit);
            } else if (d[0] == 0x434C5846) {                   // "FXLC"
                code = &d[1]; ncode = len - 1;
            }
            i += 1 + len;
            continue;
        }
        if (tok == 0x0000FFFF) break;
        ++i;
    }
    for (int k = 0; k < ncin; ++k) { pp->cin[k].p = find_param(fx->params, fx->nparams, cin[k].name); pp->cin[k].reg = cin[k].reg; }
    pp->ncin = ncin; pp->nclit = nclit; pp->code = code; pp->ncode = ncode;
    return pp;
}
static Param *ref_param(Effect *fx, State *st) {
    if (!st->ref_done) {
        Param *p = find_param(fx->params, fx->nparams, st->ref);
        char *br = strchr(st->ref, '[');
        if (!p && br) { char nm[64]; snprintf(nm, sizeof nm, "%.*s", (int)(br - st->ref), st->ref); Param *a = find_param(fx->params, fx->nparams, nm); int i = atoi(br + 1); if (a && i < a->nmembers) p = &a->members[i]; }
        st->ref_param = p;
        st->ref_done = 1;
    }
    return st->ref_param;
}
// The newest change stamp among the parameters a state reads (0: a constant state). A `Sampler = <sampler
// parameter>` state also depends on the parameters the sampler's own states read (its texture).
static uint64_t state_stamp(Effect *fx, State *st) {
    if (st->kind == 3) {
        Param *p = ref_param(fx, st);
        uint64_t t = pstamp(p);
        if (p) for (int k = 0; k < p->nstates; ++k) { uint64_t u = state_stamp(fx, &p->states[k]); if (u > t) t = u; }
        return t;
    }
    if (st->kind == 2) {
        if (!st->pre) st->pre = fxlc_parse(fx, st);
        uint64_t t = 0;
        for (int k = 0; k < st->pre->ncin; ++k) { uint64_t u = pstamp(st->pre->cin[k].p); if (u > t) t = u; }
        return t;
    }
    return 0;
}
static int state_words(Effect *fx, State *st, uint32_t *out, int cap) {
    if (st->kind == 3) {
        Param *p = ref_param(fx, st);
        if (!p) return 0;
        if (p->cls == PC_OBJECT) { out[0] = p->data ? *(uint32_t *)p->data : 0; out[1] = (uint32_t)(uintptr_t)p; return -1; }   // object reference
        int n = (int)(p->bytes / 4);
        if (n > cap) n = cap;
        memcpy(out, p->data, 4 * (size_t)n);
        return n;
    }
    if (st->kind == 2) {
        if (!st->pre) st->pre = fxlc_parse(fx, st);
        struct FxlcPre *pp = st->pre;
        uint32_t *code = pp->code, ncode = pp->ncode, nclit = pp->nclit;
        double *clit = pp->clit;
        int ncin = pp->ncin;
        if (!code) return 0;
        float cregs[64][4] = {{0}};
        for (int k = 0; k < ncin; ++k) {
            Param *p = pp->cin[k].p;
            if (!p) continue;
            float v[64] = {0};
            uint32_t comps = p->bytes / 4 < 64 ? p->bytes / 4 : 64;
            get_floats(p, v, comps);
            uint32_t per = p->elements ? p->members[0].rows * p->members[0].cols : (p->cls == PC_MATRIX_COLUMNS ? p->rows : p->cols);
            if (!per) per = 1;
            for (uint32_t q = 0; q < comps; ++q) {
                uint32_t r = pp->cin[k].reg + q / per, cc = q % per;
                if (r < 64 && cc < 4) cregs[r][cc] = v[q];
            }
        }
        float outr[16][4] = {{0}};
        uint32_t cnt = code[0], j = 1;
        int maxo = 0;
        for (uint32_t k = 0; k < cnt && j + 2 < ncode; ++k) {
            uint32_t op = code[j], nin = code[j + 1];
            j += 2;
            uint32_t ncomp = op & 0xFFFF, opc = (op >> 20) & 0x7FF;
            float src[4] = {0};
            for (uint32_t a = 0; a < nin; ++a) {
                uint32_t rel = code[j++];
                if (rel) j += 2;
                uint32_t tab = code[j], off = code[j + 1];
                j += 2;
                for (uint32_t q = 0; q < ncomp && q < 4; ++q) {
                    if (tab == 1) src[q] = off + q < nclit ? (float)clit[off + q] : 0;
                    else if (tab == 2) src[q] = (off + q) / 4 < 64 ? cregs[(off + q) / 4][(off + q) % 4] : 0;
                }
            }
            uint32_t rel = code[j++];
            if (rel) j += 2;
            uint32_t tab = code[j], off = code[j + 1];
            j += 2;
            if (opc != 0x001 /* mov */ && boot_trace) fprintf(stderr, "      effect expression op %03X not supported (treated as mov)\n", opc);
            for (uint32_t q = 0; q < ncomp && q < 4; ++q)
                if (tab == 4 && (off + q) / 4 < 16) { outr[(off + q) / 4][(off + q) % 4] = src[q]; if ((int)((off + q) / 4) + 1 > maxo) maxo = (int)((off + q) / 4) + 1; }
        }
        // Scalar states take the first output component; numeric state params decide the word type.
        int words = (int)(st->value->bytes / 4);
        if (words < 1) words = 1;
        if (words > cap) words = cap;
        for (int w = 0; w < words; ++w) {
            float f = outr[w / 4][w % 4];
            if (st->value->type == PT_FLOAT) memcpy(&out[w], &f, 4); else out[w] = (uint32_t)(int32_t)f;
        }
        return words;
    }
    if (st->value->cls == PC_OBJECT) { out[0] = st->value->data ? *(uint32_t *)st->value->data : 0; return 1; }
    int n = (int)(st->value->bytes / 4);
    if (n > cap) n = cap;
    if (st->value->data) memcpy(out, st->value->data, 4 * (size_t)n);
    return n;
}

static void apply_sampler(Cpu *c, Effect *fx, Param *sp, uint32_t reg, uint64_t since) {
    for (int k = 0; k < sp->nstates; ++k) {
        State *st = &sp->states[k];
        if (since && state_stamp(fx, st) <= since) continue;
        uint32_t w[16];
        int n = state_words(fx, st, w, 16);
        if (st->op == OP_TEX) {
            uint32_t a[2] = {reg, n == -1 ? w[0] : (n ? w[0] : 0)};
            dev_call(c, fx, DEV_SetTexture, 2, a);
        } else if (st->op >= OP_SAMP && st->op < OP_SAMP + 13 && n) {
            uint32_t a[3] = {reg, st->op - OP_SAMP + 1, w[0]};
            dev_call(c, fx, DEV_SetSamplerState, 3, a);
        }
    }
}

// A shader's CTAB entry resolved once: the parameter, register set/index/count and the shader's class for it.
struct CtabEntry { Param *p; uint16_t rs, ri, rc, ccls; };
static void ctab_resolve(Effect *fx, State *st) {
    const uint32_t *code = st->code;
    uint32_t n = st->code_n;
    st->ctab_done = 1;
    for (uint32_t i = 1; i < n;) {
        uint32_t tok = code[i];
        if ((tok & 0xFFFF) != 0xFFFE) { if (tok == 0x0000FFFF) break; ++i; continue; }
        uint32_t len = (tok >> 16) & 0x7FFF;
        if (code[i + 1] == 0x42415443) {                          // CTAB
            const uint8_t *cb = (const uint8_t *)&code[i + 2];
            uint32_t nc, co;
            memcpy(&nc, cb + 12, 4); memcpy(&co, cb + 16, 4);
            st->ctab = calloc(nc ? nc : 1, sizeof *st->ctab);
            for (uint32_t k = 0; k < nc; ++k) {
                uint32_t nm, ti; uint16_t rs, ri, rc, ccls;
                memcpy(&nm, cb + co + 20 * k, 4);
                memcpy(&rs, cb + co + 20 * k + 4, 2); memcpy(&ri, cb + co + 20 * k + 6, 2); memcpy(&rc, cb + co + 20 * k + 8, 2);
                memcpy(&ti, cb + co + 20 * k + 12, 4);
                memcpy(&ccls, cb + ti, 2);
                Param *p = find_param(fx->params, fx->nparams, (const char *)cb + nm);
                if (!p) continue;
                st->ctab[st->nctab++] = (struct CtabEntry){p, rs, ri, rc, ccls};
            }
            return;
        }
        i += 1 + len;
    }
}

// Uploads the constants a shader's CTAB asks for from the effect's parameters.
// since: 0 uploads everything (BeginPass); otherwise only constants whose parameters changed after that stamp.
static void upload_ctab(Cpu *c, Effect *fx, State *st, int ps, uint64_t since) {
    if (!st->ctab_done) ctab_resolve(fx, st);
    for (int k = 0; k < st->nctab; ++k) {
        struct CtabEntry *ce = &st->ctab[k];
        Param *p = ce->p;
        uint16_t rs = ce->rs, ri = ce->ri, rc = ce->rc, ccls = ce->ccls;
        if (rs == 3) {                                            // sampler register(s)
            for (uint16_t r = 0; r < rc; ++r) {
                Param *sp = p->elements ? (r < p->nmembers ? &p->members[r] : NULL) : p;
                if (sp) apply_sampler(c, fx, sp, (ps ? 0 : 0x101) + ri + r, since);
            }
            continue;
        }
        if (since && pstamp(p) <= since) continue;
        // numeric. Matrices are laid out by the *shader's* class for the constant (CTAB type info), as D3DX
        // does: MATRIX_ROWS -> register i = row i, MATRIX_COLUMNS -> register j = column j of the logical
        // matrix, whatever the effect parameter's own class is.
        float regs[256][4];
        uint32_t nmax = rc < 256 ? rc : 256;
        memset(regs, 0, 16 * (size_t)nmax);
        uint32_t nreg = 0;
        Param *list = p->elements ? p->members : p;
        int nl = p->elements ? p->nmembers : 1;
        for (int e = 0; e < nl && nreg < nmax; ++e) {
            Param *q = &list[e];
            if (q->cls == PC_MATRIX_ROWS || q->cls == PC_MATRIX_COLUMNS) {
                float m[16];
                get_matrix(q, m, 0);
                int as_cols = ccls == PC_MATRIX_COLUMNS || (ccls != PC_MATRIX_ROWS && q->cls == PC_MATRIX_COLUMNS);
                uint32_t nr = as_cols ? q->cols : q->rows, per = as_cols ? q->rows : q->cols;
                for (uint32_t r = 0; r < nr && nreg < nmax; ++r, ++nreg)
                    for (uint32_t cc = 0; cc < per && cc < 4; ++cc) regs[nreg][cc] = as_cols ? m[cc * 4 + r] : m[r * 4 + cc];
                continue;
            }
            uint32_t cnt = q->rows * q->cols;
            float v[16] = {0};
            get_floats(q, v, cnt < 16 ? cnt : 16);
            for (uint32_t cc = 0; cc < cnt && cc < 4; ++cc) regs[nreg][cc] = v[cc];
            ++nreg;
        }
        if (!nreg) continue;
        uint32_t buf = scratch(16 * nreg);
        if (rs == 2) {
            memcpy(G + buf, regs, 16 * nreg);
            uint32_t a[3] = {ri, buf, nreg};
            dev_call(c, fx, ps ? DEV_SetPixelShaderConstantF : DEV_SetVertexShaderConstantF, 3, a);
        } else {
            for (uint32_t r = 0; r < nreg; ++r) for (int cc = 0; cc < 4; ++cc) M32(buf + 16 * r + 4 * cc) = (uint32_t)(int32_t)regs[r][cc];
            if (rs == 0) { for (uint32_t r = 0; r < nreg; ++r) M32(buf + 4 * r) = regs[r][0] != 0; }
            uint32_t a[3] = {ri, buf, nreg};
            dev_call(c, fx, rs == 1 ? (ps ? DEV_SetPixelShaderConstantI : DEV_SetVertexShaderConstantI)
                                    : (ps ? DEV_SetPixelShaderConstantB : DEV_SetVertexShaderConstantB), 3, a);
        }
    }
}

// commit: CommitChanges, which applies only what reads parameters changed since the pass was last applied.
static void apply_pass(Cpu *c, Effect *fx, Pass *P, int commit) {
    State *vs = NULL, *ps = NULL;
    uint64_t since = commit ? P->applied : 0;
    P->applied = fx_epoch;
    for (int k = 0; k < P->nstates; ++k) {
        State *st = &P->states[k];
        if (since) {
            if ((st->op == OP_SHADER || st->op == OP_SHADER + 1) && st->kind == 1 && st->code && st->shader) {
                if (st->op == OP_SHADER) vs = st; else ps = st;   // the shader is bound; its constants may need updates
                continue;
            }
            if (state_stamp(fx, st) <= since) continue;
        }
        uint32_t w[16] = {0};
        int n = state_words(fx, st, w, 16);
        uint32_t op = st->op;
        if (op < (uint32_t)N_RS) { if (n) { uint32_t a[2] = {rs_of[op], w[0]}; dev_call(c, fx, DEV_SetRenderState, 2, a); } }
        else if (op < OP_MISC) { if (n) { uint32_t a[3] = {st->index, tss_of[op - OP_TSS], w[0]}; dev_call(c, fx, DEV_SetTextureStageState, 3, a); } }
        else if (op == OP_MISC + 1) { if (n) { uint32_t a[1] = {w[0]}; dev_call(c, fx, DEV_SetFVF, 1, a); } }
        else if (op >= OP_TRANSFORM && op < OP_MATERIAL) {
            static const uint32_t ts[] = {3, 2, 256, 16};
            if (n >= 16) {
                uint32_t m = scratch(64);
                memcpy(G + m, w, 64);
                uint32_t a[2] = {ts[op - OP_TRANSFORM] + st->index, m};
                dev_call(c, fx, DEV_SetTransform, 2, a);
            }
        } else if (op == OP_SHADER || op == OP_SHADER + 1) {
            int is_ps = op == OP_SHADER + 1;
            if (st->kind == 1 && st->code) {
                if (!st->shader) {
                    uint32_t code = galloc(4 * st->code_n), out = galloc(4);
                    memcpy(G + code, st->code, 4 * st->code_n);
                    uint32_t a[2] = {code, out};
                    dev_call(c, fx, is_ps ? DEV_CreatePixelShader : DEV_CreateVertexShader, 2, a);
                    st->shader = M32(out);
                }
                uint32_t a[1] = {st->shader};
                dev_call(c, fx, is_ps ? DEV_SetPixelShader : DEV_SetVertexShader, 1, a);
                if (is_ps) ps = st; else vs = st;
            } else {
                uint32_t a[1] = {0};                              // NULL shader: fixed function
                dev_call(c, fx, is_ps ? DEV_SetPixelShader : DEV_SetVertexShader, 1, a);
            }
        } else if (op >= OP_CONST && op < OP_TEX) {
            uint32_t which = op - OP_CONST;                       // 0 VSF, 1 VSB, 2 VSI, 3..7 VS legacy, 8 PSF, 9 PSB, 10 PSI, ...
            int is_ps = which >= 8, kind = which % 8;
            if (n > 0) {
                uint32_t buf = scratch(4 * 16);
                memcpy(G + buf, w, 4 * (size_t)n);
                uint32_t cnt = kind == 1 ? (uint32_t)n : ((uint32_t)n + 3) / 4;
                int slot = kind == 1 ? (is_ps ? DEV_SetPixelShaderConstantB : DEV_SetVertexShaderConstantB)
                         : kind == 2 ? (is_ps ? DEV_SetPixelShaderConstantI : DEV_SetVertexShaderConstantI)
                         : (is_ps ? DEV_SetPixelShaderConstantF : DEV_SetVertexShaderConstantF);
                uint32_t a[3] = {st->index, buf, cnt};
                dev_call(c, fx, slot, 3, a);
            }
        } else if (op == OP_TEX) {
            uint32_t a[2] = {st->index, n ? w[0] : 0};
            dev_call(c, fx, DEV_SetTexture, 2, a);
        } else if (op >= OP_SAMP && op < OP_SAMP + 13) {
            if (n > 0) { uint32_t a[3] = {st->index, op - OP_SAMP + 1, w[0]}; dev_call(c, fx, DEV_SetSamplerState, 3, a); }
        } else if (op == OP_SAMP + 13) {                          // Sampler[i] = <sampler parameter>
            if (n == -1) apply_sampler(c, fx, (Param *)(uintptr_t)w[1], st->index, since);
        }
        // MATERIAL / LIGHT / NPatchMode: fixed-function lighting, not used by the shader techniques (recorded only)
    }
    if (vs) upload_ctab(c, fx, vs, 0, since);
    if (ps) upload_ctab(c, fx, ps, 1, since);
}

// ---------------- COM plumbing ----------------
static Effect **effects;
static int neffects;
static Effect *fx_of(uint32_t g) {
    if (!g || M32(g + 8) != 0x0EFFEC70) return NULL;
    uint32_t i = M32(g + 4);
    return i < (uint32_t)neffects ? effects[i] : NULL;
}
#define FX fx_of(arg(c, 0))
static int unimplemented(Cpu *c) { (void)c; return 0; }
typedef struct { const char *name; HostFn fn; } Impl;
static uint32_t make_vtable(const char *iface, const char *const *names, int n, const Impl *impl) {
    uint32_t vt = galloc(4 * (uint32_t)n);
    for (int i = 0; i < n; ++i) {
        char *full = malloc(strlen(iface) + strlen(names[i]) + 3);
        sprintf(full, "%s::%s", iface, names[i]);
        HostFn fn = unimplemented;
        for (const Impl *p = impl; p->name; ++p) if (!strcmp(p->name, names[i])) fn = p->fn;
        M32(vt + 4 * i) = host_fn(fn, full);
    }
    return vt;
}

static int e_QueryInterface(Cpu *c) { if (arg(c, 2)) M32(arg(c, 2)) = 0; RET(0x80004002u, 3); }
static int e_AddRef(Cpu *c) { Effect *fx = FX; RET(fx ? ++fx->refs : 1, 1); }
static int e_Release(Cpu *c) { Effect *fx = FX; RET(fx && fx->refs > 0 ? --fx->refs : 0, 1); }
static int e_GetDesc(Cpu *c) {
    Effect *fx = FX;
    uint32_t p = arg(c, 1);
    static uint32_t creator;
    if (!creator) creator = gstr("port/runtime d3dx_effect");
    M32(p) = creator; M32(p + 4) = (uint32_t)fx->nparams; M32(p + 8) = (uint32_t)fx->ntechs; M32(p + 12) = 0;
    RET(D3D_OK, 2);
}
static int e_GetParameterDesc(Cpu *c) {        // (this, h, desc*)
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 3);
    uint32_t d = arg(c, 2);
    M32(d) = p->gname; M32(d + 4) = p->gsemantic; M32(d + 8) = p->cls; M32(d + 12) = p->type; M32(d + 16) = p->rows;
    M32(d + 20) = p->cols; M32(d + 24) = p->elements; M32(d + 28) = (uint32_t)p->nann; M32(d + 32) = p->member_count;
    M32(d + 36) = p->flags; M32(d + 40) = p->bytes;
    RET(D3D_OK, 3);
}
static int e_GetTechniqueDesc(Cpu *c) {
    Technique *t = tech_of(FX, arg(c, 1));
    if (!t) RET(D3DERR_INVALIDCALL, 3);
    uint32_t d = arg(c, 2);
    M32(d) = t->gname; M32(d + 4) = (uint32_t)t->npasses; M32(d + 8) = (uint32_t)t->nann;
    RET(D3D_OK, 3);
}
static int e_GetPassDesc(Cpu *c) {
    Pass *P = pass_of(arg(c, 1));
    if (!P) RET(D3DERR_INVALIDCALL, 3);
    uint32_t d = arg(c, 2);
    M32(d) = P->gname; M32(d + 4) = (uint32_t)P->nann; M32(d + 8) = P->vs_code; M32(d + 12) = P->ps_code;
    RET(D3D_OK, 3);
}
static int e_GetParameter(Cpu *c) {            // (this, hParent, index)
    Effect *fx = FX;
    uint32_t i = arg(c, 2);
    if (!arg(c, 1)) RET(i < (uint32_t)fx->nparams ? fx->params[i].handle : 0, 3);
    Param *p = param_of(fx, arg(c, 1));
    RET(p && p->cls == PC_STRUCT && !p->elements && i < (uint32_t)p->nmembers ? p->members[i].handle : 0, 3);
}
static int e_GetParameterByName(Cpu *c) {      // (this, hParent, name)
    Effect *fx = FX;
    const char *name = gptr(arg(c, 2));
    if (!name) RET(0, 3);
    Param *p = NULL;
    if (!arg(c, 1)) p = param_of(fx, arg(c, 2));
    else { Param *par = param_of(fx, arg(c, 1)); if (par) p = find_param(par->members, par->nmembers, name); }
    if (boot_trace && !p) fprintf(stderr, "      effect: no parameter %s\n", name);
    RET(p ? p->handle : 0, 3);
}
static int e_GetParameterBySemantic(Cpu *c) {
    Effect *fx = FX;
    const char *s = gptr(arg(c, 2));
    Param *list = fx->params; int n = fx->nparams;
    if (arg(c, 1)) { Param *par = param_of(fx, arg(c, 1)); if (!par) RET(0, 3); list = par->members; n = par->nmembers; }
    for (int i = 0; s && i < n; ++i) if (!strcasecmp(list[i].semantic, s)) RET(list[i].handle, 3);
    RET(0, 3);
}
static int e_GetParameterElement(Cpu *c) {
    Param *p = param_of(FX, arg(c, 1));
    uint32_t i = arg(c, 2);
    RET(p && p->elements && i < p->elements ? p->members[i].handle : 0, 3);
}
static int e_GetTechnique(Cpu *c) { Effect *fx = FX; uint32_t i = arg(c, 1); RET(i < (uint32_t)fx->ntechs ? fx->techs[i].handle : 0, 2); }
static int e_GetTechniqueByName(Cpu *c) { Technique *t = tech_of(FX, arg(c, 1)); RET(t ? t->handle : 0, 2); }
static int e_GetPass(Cpu *c) {
    Technique *t = tech_of(FX, arg(c, 1));
    uint32_t i = arg(c, 2);
    RET(t && i < (uint32_t)t->npasses ? t->passes[i].handle : 0, 3);
}
static int e_GetPassByName(Cpu *c) {
    Technique *t = tech_of(FX, arg(c, 1));
    const char *n = gptr(arg(c, 2));
    for (int i = 0; t && n && i < t->npasses; ++i) if (!strcmp(t->passes[i].name, n)) RET(t->passes[i].handle, 3);
    RET(0, 3);
}
static void annotations_of(Effect *fx, uint32_t h, Param **list, int *n) {
    *list = NULL; *n = 0;
    if (h >= HANDLE_BASE && h < HANDLE_BASE + 4 * nh) {
        HEntry *e = &htab[(h - HANDLE_BASE) / 4];
        if (e->kind == 1) { *list = ((Param *)e->p)->annotations; *n = ((Param *)e->p)->nann; }
        if (e->kind == 2) { *list = ((Technique *)e->p)->annotations; *n = ((Technique *)e->p)->nann; }
        if (e->kind == 3) { *list = ((Pass *)e->p)->annotations; *n = ((Pass *)e->p)->nann; }
        return;
    }
    Technique *t = tech_of(fx, h);
    if (t) { *list = t->annotations; *n = t->nann; return; }
    Param *p = param_of(fx, h);
    if (p) { *list = p->annotations; *n = p->nann; }
}
static int e_GetAnnotation(Cpu *c) {
    Param *l; int n;
    annotations_of(FX, arg(c, 1), &l, &n);
    RET(arg(c, 2) < (uint32_t)n ? l[arg(c, 2)].handle : 0, 3);
}
static int e_GetAnnotationByName(Cpu *c) {
    Param *l; int n;
    annotations_of(FX, arg(c, 1), &l, &n);
    const char *name = gptr(arg(c, 2));
    for (int i = 0; name && i < n; ++i) if (!strcmp(l[i].name, name)) RET(l[i].handle, 3);
    RET(0, 3);
}
static int e_SetValue(Cpu *c) {                // (this, h, data*, bytes)
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 4);
    if (p->cls == PC_OBJECT) { if (p->data) { memcpy(p->data, G + arg(c, 2), 4); touch(p); } RET(D3D_OK, 4); }
    uint32_t n = arg(c, 3) < p->bytes ? arg(c, 3) : p->bytes;
    if (p->elements) { uint32_t o = 0; for (int i = 0; i < p->nmembers && o < n; ++i) { uint32_t k = p->members[i].bytes; memcpy(p->members[i].data, G + arg(c, 2) + o, k < n - o ? k : n - o); o += k; } }
    else memcpy(p->data, G + arg(c, 2), n);
    touch(p);
    RET(D3D_OK, 4);
}
static int e_GetValue(Cpu *c) {
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 4);
    if (p->cls == PC_OBJECT) {
        uint32_t v = p->data ? *(uint32_t *)p->data : 0;
        if (p->type == PT_STRING) { Object *o = find_object(FX, p->object_id); static uint32_t g; g = o ? gstr((char *)o->data) : 0; v = g; }
        memcpy(G + arg(c, 2), &v, 4);
        RET(D3D_OK, 4);
    }
    uint32_t n = arg(c, 3) < p->bytes ? arg(c, 3) : p->bytes;
    if (p->elements) { uint32_t o = 0; for (int i = 0; i < p->nmembers && o < n; ++i) { uint32_t k = p->members[i].bytes; memcpy(G + arg(c, 2) + o, p->members[i].data, k < n - o ? k : n - o); o += k; } }
    else memcpy(G + arg(c, 2), p->data, n);
    RET(D3D_OK, 4);
}
static int e_SetBool(Cpu *c) { Param *p = param_of(FX, arg(c, 1)); if (!p) RET(D3DERR_INVALIDCALL, 3); float f = arg(c, 2) != 0; set_floats(p, &f, 1); RET(D3D_OK, 3); }
static int e_SetInt(Cpu *c) { Param *p = param_of(FX, arg(c, 1)); if (!p) RET(D3DERR_INVALIDCALL, 3); float f = (float)(int32_t)arg(c, 2); if (p->type != PT_FLOAT) { memcpy(p->data, G + c->esp + 12, 4); touch(p); RET(D3D_OK, 3); } set_floats(p, &f, 1); RET(D3D_OK, 3); }
static int e_SetFloat(Cpu *c) {
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 3);
    float f = argf(c, 2);
    // Both PC tunnel-strength uploads use SetFloat on this effect parameter.
    float lighting_inv_shadow_strength(float original);
    if (!strcmp(p->name, "InvShadowStrength")) f = lighting_inv_shadow_strength(f);
    set_floats(p, &f, 1);
    RET(D3D_OK, 3);
}
static int e_GetBool(Cpu *c) { Param *p = param_of(FX, arg(c, 1)); float f = 0; if (p) get_floats(p, &f, 1); M32(arg(c, 2)) = f != 0; RET(p ? D3D_OK : D3DERR_INVALIDCALL, 3); }
static int e_GetInt(Cpu *c) { Param *p = param_of(FX, arg(c, 1)); float f = 0; if (p) get_floats(p, &f, 1); M32(arg(c, 2)) = (uint32_t)(int32_t)f; RET(p ? D3D_OK : D3DERR_INVALIDCALL, 3); }
static int e_GetFloat(Cpu *c) { Param *p = param_of(FX, arg(c, 1)); float f = 0; if (p) get_floats(p, &f, 1); MF32(arg(c, 2)) = f; RET(p ? D3D_OK : D3DERR_INVALIDCALL, 3); }
static int e_SetFloatArray(Cpu *c) {           // (this, h, float*, count)
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 4);
    set_floats(p, (const float *)(G + arg(c, 2)), arg(c, 3));
    RET(D3D_OK, 4);
}
static int e_GetFloatArray(Cpu *c) {
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 4);
    get_floats(p, (float *)(G + arg(c, 2)), arg(c, 3));
    RET(D3D_OK, 4);
}
static int e_SetIntArray(Cpu *c) {
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 4);
    float v[256];
    uint32_t n = arg(c, 3) < 256 ? arg(c, 3) : 256;
    for (uint32_t i = 0; i < n; ++i) v[i] = (float)(int32_t)M32(arg(c, 2) + 4 * i);
    set_floats(p, v, n);
    RET(D3D_OK, 4);
}
static int e_SetVector(Cpu *c) {               // (this, h, D3DXVECTOR4*)
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 3);
    widescreen_drop_offset(c, arg(c, 2));       // lens raindrops keep their 4:3 shape (only at 006D480D)
    set_floats(p, (const float *)(G + arg(c, 2)), 4);
    RET(D3D_OK, 3);
}
static int e_GetVector(Cpu *c) {
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 3);
    float v[4] = {0};
    get_floats(p, v, 4);
    memcpy(G + arg(c, 2), v, 16);
    RET(D3D_OK, 3);
}
static int e_SetVectorArray(Cpu *c) {          // (this, h, D3DXVECTOR4*, count)
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 4);
    const float *v = (const float *)(G + arg(c, 2));
    if (p->elements) {
        for (uint32_t i = 0; i < arg(c, 3) && i < p->elements; ++i) set_floats(&p->members[i], v + 4 * i, 4);
    } else set_floats(p, v, 4 * arg(c, 3));
    RET(D3D_OK, 4);
}
static int e_GetVectorArray(Cpu *c) {
    Param *p = param_of(FX, arg(c, 1));
    if (!p) RET(D3DERR_INVALIDCALL, 4);
    float *v = (float *)(G + arg(c, 2));
    for (uint32_t i = 0; i < arg(c, 3); ++i) { memset(v + 4 * i, 0, 16); if (p->elements && i < p->elements) get_floats(&p->members[i], v + 4 * i, 4); }
    RET(D3D_OK, 4);
}
static int set_matrix_common(Cpu *c, int transpose, int array, int pointers) {
    Param *p = param_of(FX, arg(c, 1));
    if (boot_trace && p) {
        static int logged;
        if (logged++ < 40) fprintf(stderr, "      effect matrix %s: %s (class %u, %ux%u) transpose=%d array=%d ptrs=%d from %08X\n",
                                   p->name, p->cls == PC_MATRIX_ROWS ? "rows" : "columns", p->cls, p->rows, p->cols, transpose, array, pointers, M32(c->esp));
    }
    int nargs = array ? 4 : 3;
    if (!p) RET(D3DERR_INVALIDCALL, nargs);
    uint32_t count = array ? arg(c, 3) : 1;
    for (uint32_t i = 0; i < count; ++i) {
        uint32_t m = pointers ? M32(arg(c, 2) + 4 * i) : arg(c, 2) + 64 * i;
        Param *q = p->elements ? (i < p->elements ? &p->members[i] : NULL) : (i == 0 ? p : NULL);
        if (q) set_matrix(q, (const float *)(G + m), transpose);
    }
    RET(D3D_OK, nargs);
}
static int get_matrix_common(Cpu *c, int transpose, int array) {
    Param *p = param_of(FX, arg(c, 1));
    int nargs = array ? 4 : 3;
    if (!p) RET(D3DERR_INVALIDCALL, nargs);
    uint32_t count = array ? arg(c, 3) : 1;
    for (uint32_t i = 0; i < count; ++i) {
        Param *q = p->elements ? (i < p->elements ? &p->members[i] : NULL) : (i == 0 ? p : NULL);
        if (q) get_matrix(q, (float *)(G + arg(c, 2) + 64 * i), transpose);
    }
    RET(D3D_OK, nargs);
}
static int e_SetMatrix(Cpu *c) { return set_matrix_common(c, 0, 0, 0); }
static int e_GetMatrix(Cpu *c) { return get_matrix_common(c, 0, 0); }
static int e_SetMatrixArray(Cpu *c) { return set_matrix_common(c, 0, 1, 0); }
static int e_GetMatrixArray(Cpu *c) { return get_matrix_common(c, 0, 1); }
static int e_SetMatrixPointerArray(Cpu *c) { return set_matrix_common(c, 0, 1, 1); }
static int e_SetMatrixTranspose(Cpu *c) { return set_matrix_common(c, 1, 0, 0); }
static int e_GetMatrixTranspose(Cpu *c) { return get_matrix_common(c, 1, 0); }
static int e_SetMatrixTransposeArray(Cpu *c) { return set_matrix_common(c, 1, 1, 0); }
static int e_GetMatrixTransposeArray(Cpu *c) { return get_matrix_common(c, 1, 1); }
static int e_SetMatrixTransposePointerArray(Cpu *c) { return set_matrix_common(c, 1, 1, 1); }
static int e_SetString(Cpu *c) { RET(D3D_OK, 3); }
static int e_GetString(Cpu *c) {
    Effect *fx = FX;
    Param *p = param_of(fx, arg(c, 1));
    if (!p || p->type != PT_STRING) RET(D3DERR_INVALIDCALL, 3);
    Object *o = find_object(fx, p->object_id);
    M32(arg(c, 2)) = o ? gstr((char *)o->data) : 0;
    RET(D3D_OK, 3);
}
static int e_SetTexture(Cpu *c) {              // (this, h, texture)
    Param *p = param_of(FX, arg(c, 1));
    if (!p || p->cls != PC_OBJECT) RET(D3DERR_INVALIDCALL, 3);
    memcpy(p->data, &(uint32_t){arg(c, 2)}, 4);
    touch(p);
    RET(D3D_OK, 3);
}
static int e_GetTexture(Cpu *c) {
    Param *p = param_of(FX, arg(c, 1));
    if (!p || p->cls != PC_OBJECT) RET(D3DERR_INVALIDCALL, 3);
    memcpy(G + arg(c, 2), p->data, 4);
    RET(D3D_OK, 3);
}
static int e_SetArrayRange(Cpu *c) { RET(D3D_OK, 4); }
static int e_GetPool(Cpu *c) { Effect *fx = FX; M32(arg(c, 1)) = fx->pool; RET(fx->pool ? D3D_OK : D3DERR_INVALIDCALL, 2); }
static int e_SetTechnique(Cpu *c) {
    Effect *fx = FX;
    Technique *t = tech_of(fx, arg(c, 1));
    if (!t) RET(D3DERR_INVALIDCALL, 2);
    fx->cur = t;
    RET(D3D_OK, 2);
}
static int e_GetCurrentTechnique(Cpu *c) { Effect *fx = FX; RET(fx->cur ? fx->cur->handle : 0, 1); }
static int e_ValidateTechnique(Cpu *c) { RET(tech_of(FX, arg(c, 1)) ? D3D_OK : D3DERR_INVALIDCALL, 2); }
static int e_FindNextValidTechnique(Cpu *c) {  // (this, hTechnique, pNext*): every technique validates
    Effect *fx = FX;
    Technique *t = tech_of(fx, arg(c, 1));
    int next = t ? (int)(t - fx->techs) + 1 : 0;
    M32(arg(c, 2)) = next < fx->ntechs ? fx->techs[next].handle : 0;
    RET(next < fx->ntechs ? D3D_OK : 1 /* S_FALSE */, 3);
}
static int e_IsParameterUsed(Cpu *c) { RET(1, 3); }
// Begin(flags) without D3DXFX_DONOTSAVESTATE (1): D3DX saves the device state the technique's passes set and End
// restores it; the game always passes 0 (Begin is vtable +0xFC: 006C5350.c:85, 9816, 10217). Here the render,
// texture-stage and sampler states (sampler states not with D3DXFX_DONOTSAVESAMPLERSTATE, 4); shaders, textures and
// constants are left as the passes set them (the game binds its own before each draw). Without this a pass's states
// leaked into later draws: the sky, glossy-window and depth effects' MipMapLodBias -1 stayed set on 85 % of the
// race's texture binds, harmless only while the bias was ignored.
typedef struct SavedState { uint32_t cls, idx, st, val; } SavedState;   // cls 0 render, 1 texture stage, 2 sampler
static void save_state(Cpu *c, Effect *fx, uint32_t cls, uint32_t idx, uint32_t st) {
    for (int i = 0; i < fx->nsaved; ++i) if (fx->saved[i].cls == cls && fx->saved[i].idx == idx && fx->saved[i].st == st) return;
    if (fx->nsaved == fx->capsaved) {
        fx->capsaved = fx->capsaved ? fx->capsaved * 2 : 32;
        fx->saved = realloc(fx->saved, sizeof *fx->saved * (size_t)fx->capsaved);
    }
    uint32_t out = scratch(4);
    M32(out) = 0;
    if (cls == 0) { uint32_t a[2] = {st, out}; dev_call(c, fx, DEV_GetRenderState, 2, a); }
    else { uint32_t a[3] = {idx, st, out}; dev_call(c, fx, cls == 1 ? DEV_GetTextureStageState : DEV_GetSamplerState, 3, a); }
    fx->saved[fx->nsaved++] = (SavedState){cls, idx, st, M32(out)};
}
static void save_technique(Cpu *c, Effect *fx, Technique *t, uint32_t flags) {
    fx->nsaved = 0;
    if (!t || (flags & 1)) return;
    for (int i = 0; i < t->npasses; ++i)
        for (int k = 0; k < t->passes[i].nstates; ++k) {
            State *st = &t->passes[i].states[k];
            uint32_t op = st->op;
            if (op < (uint32_t)N_RS) save_state(c, fx, 0, 0, rs_of[op]);
            else if (op < OP_MISC) save_state(c, fx, 1, st->index, tss_of[op - OP_TSS]);
            else if (flags & 4) continue;
            else if (op >= OP_SAMP && op < OP_SAMP + 13) save_state(c, fx, 2, st->index, op - OP_SAMP + 1);
            else if (op == OP_SAMP + 13 && st->kind == 3) {      // Sampler[i] = <sampler parameter>: its sampler_state
                Param *sp = ref_param(fx, st);
                for (int j = 0; sp && j < sp->nstates; ++j)
                    if (sp->states[j].op >= OP_SAMP && sp->states[j].op < OP_SAMP + 13) save_state(c, fx, 2, st->index, sp->states[j].op - OP_SAMP + 1);
            } else if ((op == OP_SHADER || op == OP_SHADER + 1) && st->kind == 1 && st->code) {   // the shader's samplers (upload_ctab)
                if (!st->ctab_done) ctab_resolve(fx, st);
                for (int e = 0; e < st->nctab; ++e) {
                    struct CtabEntry *ce = &st->ctab[e];
                    if (ce->rs != 3 || !ce->p) continue;
                    for (uint16_t r = 0; r < ce->rc; ++r) {
                        Param *sp = ce->p->elements ? (r < ce->p->nmembers ? &ce->p->members[r] : NULL) : ce->p;
                        for (int j = 0; sp && j < sp->nstates; ++j)
                            if (sp->states[j].op >= OP_SAMP && sp->states[j].op < OP_SAMP + 13)
                                save_state(c, fx, 2, (op == OP_SHADER + 1 ? 0 : 0x101) + ce->ri + r, sp->states[j].op - OP_SAMP + 1);
                    }
                }
            }
        }
}
static int e_Begin(Cpu *c) {                   // (this, pPasses*, flags)
    Effect *fx = FX;
    if (!fx->cur && fx->ntechs) fx->cur = &fx->techs[0];
    if (arg(c, 1)) M32(arg(c, 1)) = fx->cur ? (uint32_t)fx->cur->npasses : 0;
    uint32_t esp = c->esp;
    save_technique(c, fx, fx->cur, arg(c, 2));
    c->esp = esp;
    RET(D3D_OK, 3);
}
static int e_BeginPass(Cpu *c) {
    Effect *fx = FX;
    uint32_t i = arg(c, 1);
    if (!fx->cur || i >= (uint32_t)fx->cur->npasses) RET(D3DERR_INVALIDCALL, 2);
    fx->pass = &fx->cur->passes[i];
    fx->in_pass = 1;
    snprintf(diag_fx_pass, sizeof diag_fx_pass, "%s/%s/%s", fx->name, fx->cur->name, fx->pass->name);
    uint32_t esp = c->esp;
    apply_pass(c, fx, fx->pass, 0);
    c->esp = esp;
    RET(D3D_OK, 2);
}
static int e_CommitChanges(Cpu *c) {
    Effect *fx = FX;
    if (fx->in_pass && fx->pass) { uint32_t esp = c->esp; apply_pass(c, fx, fx->pass, 1); c->esp = esp; }
    RET(D3D_OK, 1);
}
static int e_EndPass(Cpu *c) { Effect *fx = FX; fx->in_pass = 0; snprintf(diag_fx_pass, sizeof diag_fx_pass, "(no effect pass)"); RET(D3D_OK, 1); }
static int e_End(Cpu *c) {
    Effect *fx = FX;
    uint32_t esp = c->esp;
    for (int i = 0; i < fx->nsaved; ++i) {                   // the states as they were at Begin
        SavedState *v = &fx->saved[i];
        if (v->cls == 0) { uint32_t a[2] = {v->st, v->val}; dev_call(c, fx, DEV_SetRenderState, 2, a); }
        else { uint32_t a[3] = {v->idx, v->st, v->val}; dev_call(c, fx, v->cls == 1 ? DEV_SetTextureStageState : DEV_SetSamplerState, 3, a); }
    }
    fx->nsaved = 0;
    c->esp = esp;
    RET(D3D_OK, 1);
}
static int e_GetDevice(Cpu *c) { M32(arg(c, 1)) = FX->device; RET(D3D_OK, 2); }
static int e_OnLostDevice(Cpu *c) { RET(D3D_OK, 1); }
static int e_OnResetDevice(Cpu *c) { RET(D3D_OK, 1); }
static int e_SetStateManager(Cpu *c) { RET(D3D_OK, 2); }
static int e_GetStateManager(Cpu *c) { M32(arg(c, 1)) = 0; RET(D3D_OK, 2); }

static const char *const n_effect[] = {"QueryInterface", "AddRef", "Release", "GetDesc", "GetParameterDesc",
    "GetTechniqueDesc", "GetPassDesc", "GetFunctionDesc", "GetParameter", "GetParameterByName", "GetParameterBySemantic",
    "GetParameterElement", "GetTechnique", "GetTechniqueByName", "GetPass", "GetPassByName", "GetFunction",
    "GetFunctionByName", "GetAnnotation", "GetAnnotationByName", "SetValue", "GetValue", "SetBool", "GetBool",
    "SetBoolArray", "GetBoolArray", "SetInt", "GetInt", "SetIntArray", "GetIntArray", "SetFloat", "GetFloat",
    "SetFloatArray", "GetFloatArray", "SetVector", "GetVector", "SetVectorArray", "GetVectorArray", "SetMatrix",
    "GetMatrix", "SetMatrixArray", "GetMatrixArray", "SetMatrixPointerArray", "GetMatrixPointerArray",
    "SetMatrixTranspose", "GetMatrixTranspose", "SetMatrixTransposeArray", "GetMatrixTransposeArray",
    "SetMatrixTransposePointerArray", "GetMatrixTransposePointerArray", "SetString", "GetString", "SetTexture",
    "GetTexture", "GetPixelShader", "GetVertexShader", "SetArrayRange", "GetPool", "SetTechnique",
    "GetCurrentTechnique", "ValidateTechnique", "FindNextValidTechnique", "IsParameterUsed", "Begin", "BeginPass",
    "CommitChanges", "EndPass", "End", "GetDevice", "OnLostDevice", "OnResetDevice", "SetStateManager",
    "GetStateManager", "BeginParameterBlock", "EndParameterBlock", "ApplyParameterBlock", "CloneEffect"};
#define E(n) {#n, e_##n}
static const Impl i_effect[] = {E(QueryInterface), E(AddRef), E(Release), E(GetDesc), E(GetParameterDesc),
    E(GetTechniqueDesc), E(GetPassDesc), E(GetParameter), E(GetParameterByName), E(GetParameterBySemantic),
    E(GetParameterElement), E(GetTechnique), E(GetTechniqueByName), E(GetPass), E(GetPassByName), E(GetAnnotation),
    E(GetAnnotationByName), E(SetValue), E(GetValue), E(SetBool), E(GetBool), E(SetInt), E(GetInt), E(SetIntArray),
    E(SetFloat), E(GetFloat), E(SetFloatArray), E(GetFloatArray), E(SetVector), E(GetVector), E(SetVectorArray),
    E(GetVectorArray), E(SetMatrix), E(GetMatrix), E(SetMatrixArray), E(GetMatrixArray), E(SetMatrixPointerArray),
    E(SetMatrixTranspose), E(GetMatrixTranspose), E(SetMatrixTransposeArray), E(GetMatrixTransposeArray),
    E(SetMatrixTransposePointerArray), E(SetString), E(GetString), E(SetTexture), E(GetTexture), E(SetArrayRange),
    E(GetPool), E(SetTechnique), E(GetCurrentTechnique), E(ValidateTechnique), E(FindNextValidTechnique),
    E(IsParameterUsed), E(Begin), E(BeginPass), E(CommitChanges), E(EndPass), E(End), E(GetDevice), E(OnLostDevice),
    E(OnResetDevice), E(SetStateManager), E(GetStateManager), {0, 0}};
static const char *const n_pool[] = {"QueryInterface", "AddRef", "Release"};
static const Impl i_pool[] = {{"QueryInterface", e_QueryInterface}, {"AddRef", e_AddRef}, {"Release", e_Release}, {0, 0}};
static uint32_t vt_effect, vt_pool;

// ---------------- resources in the guest image ----------------
// RT_RCDATA (10) entry named `name` in speed.exe's resource directory, as loaded at 0x400000.
static int find_resource(const char *name, uint32_t *va, uint32_t *size) {
    uint32_t pe = 0x400000 + M32(0x400000 + 0x3C);
    uint32_t rsrc = 0x400000 + M32(pe + 24 + 96 + 2 * 8);
    if (rsrc == 0x400000) return 0;
    uint32_t root = rsrc;
    uint16_t nn = M16(root + 12), ni = M16(root + 14);
    for (uint32_t i = 0; i < (uint32_t)nn + ni; ++i) {
        uint32_t id = M32(root + 16 + 8 * i), off = M32(root + 20 + 8 * i);
        if (id != 10 || !(off & 0x80000000)) continue;
        uint32_t d2 = rsrc + (off & 0x7FFFFFFF);
        uint16_t n2 = M16(d2 + 12) + M16(d2 + 14);
        for (uint32_t k = 0; k < n2; ++k) {
            uint32_t nid = M32(d2 + 16 + 8 * k), o2 = M32(d2 + 20 + 8 * k);
            if (!(nid & 0x80000000)) continue;
            uint32_t s = rsrc + (nid & 0x7FFFFFFF);
            uint16_t len = M16(s);
            char buf[128];
            uint32_t q = 0;
            for (; q < len && q < 127; ++q) buf[q] = (char)M16(s + 2 + 2 * q);
            buf[q] = 0;
            if (strcasecmp(buf, name)) continue;
            uint32_t d3 = rsrc + (o2 & 0x7FFFFFFF);
            if (o2 & 0x80000000) d3 = rsrc + (M32(d3 + 20) & 0x7FFFFFFF);    // language level
            *va = 0x400000 + M32(d3);
            *size = M32(d3 + 4);
            return 1;
        }
    }
    return 0;
}

int d3dx_effect(Cpu *c, const char *fn) {
    if (!vt_effect) {
        vt_effect = make_vtable("ID3DXEffect", n_effect, (int)(sizeof n_effect / sizeof *n_effect), i_effect);
        vt_pool = make_vtable("ID3DXEffectPool", n_pool, 3, i_pool);
    }
    if (!strcmp(fn, "D3DXCreateEffectPool")) {
        uint32_t g = galloc(16);
        M32(g) = vt_pool; M32(g + 4) = 0xFFFFFFFF; M32(g + 8) = 0;
        M32(arg(c, 0)) = g;
        RET(D3D_OK, 1);
    }
    if (!strcmp(fn, "D3DXCreateEffectFromResourceA")) {   // (device, module, name, defines, include, flags, pool, ppEffect, ppErrors)
        const char *name = gptr(arg(c, 2));
        uint32_t va, size;
        if (arg(c, 8)) M32(arg(c, 8)) = 0;
        if (!name || !find_resource(name, &va, &size)) {
            fprintf(stderr, "boot: D3DXCreateEffectFromResourceA(%s): resource not found\n", name ? name : "?");
            RET(0x88760B59u, 9);                                  // D3DXERR_INVALIDDATA
        }
        Effect *fx = calloc(1, sizeof *fx);
        fx->device = arg(c, 0);
        fx->pool = arg(c, 6);
        fx->refs = 1;
        snprintf(fx->name, sizeof fx->name, "%s", name);
        if (!parse_effect(fx, G + va, size)) { fprintf(stderr, "boot: effect %s: bad fx_2_0 data\n", name); RET(0x88760B59u, 9); }
        if (fx->pool) share_params(fx);
        effects = realloc(effects, (size_t)(neffects + 1) * sizeof *effects);
        effects[neffects] = fx;
        fx->guest = galloc(16);
        M32(fx->guest) = vt_effect; M32(fx->guest + 4) = (uint32_t)neffects++; M32(fx->guest + 8) = 0x0EFFEC70;
        if (boot_trace) fprintf(stderr, "      effect %s: %d parameters, %d techniques\n", name, fx->nparams, fx->ntechs);
        M32(arg(c, 7)) = fx->guest;
        RET(D3D_OK, 9);
    }
    return 0;
}
