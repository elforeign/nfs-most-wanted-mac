// roam_core.c - online free roam: wire format, client session and interpolation (roam_core.h has the format).
#include "roam_core.h"

#include <math.h>
#include <string.h>

int roam_pose_valid(const float pos[3], const float vel[3]) {
    for (int i = 0; i < 3; ++i)
        if (!isfinite(pos[i]) || fabsf(pos[i]) > 1000000.0f ||
            !isfinite(vel[i]) || fabsf(vel[i]) > 10000.0f) return 0;
    return 1;
}

static void put16(uint8_t *b, uint16_t v) { b[0] = (uint8_t)v; b[1] = (uint8_t)(v >> 8); }
static void put32(uint8_t *b, uint32_t v) { put16(b, (uint16_t)v); put16(b + 2, (uint16_t)(v >> 16)); }
static uint16_t get16(const uint8_t *b) { return (uint16_t)(b[0] | b[1] << 8); }
static uint32_t get32(const uint8_t *b) { return get16(b) | (uint32_t)get16(b + 2) << 16; }
static void putf(uint8_t *b, float f) { uint32_t u; memcpy(&u, &f, 4); put32(b, u); }
static float getf(const uint8_t *b) { uint32_t u = get32(b); float f; memcpy(&f, &u, 4); return f; }

int roam_hdr_put(uint8_t *b, int type, uint16_t id, uint16_t seq) {
    b[0] = 'R'; b[1] = 'M'; b[2] = ROAM_VERSION; b[3] = (uint8_t)type;
    put16(b + 4, id); put16(b + 6, seq);
    return ROAM_HDR;
}

int roam_hdr_get(const uint8_t *b, int n, int *type, uint16_t *id, uint16_t *seq) {
    if (n < ROAM_HDR || b[0] != 'R' || b[1] != 'M') return -1;
    *type = b[3]; *id = get16(b + 4); *seq = get16(b + 6);
    return b[2] == ROAM_VERSION ? 0 : -2;
}

void roam_state_put(uint8_t *b, const RoamState *s) {
    put32(b, s->t);
    for (int i = 0; i < 3; ++i) { putf(b + 4 + 4 * i, s->pos[i]); putf(b + 16 + 4 * i, s->vel[i]); }
    for (int i = 0; i < 4; ++i) put16(b + 28 + 2 * i, (uint16_t)s->q[i]);
    put16(b + 36, s->flags); put16(b + 38, s->look);
}

void roam_state_get(const uint8_t *b, RoamState *s) {
    s->t = get32(b);
    for (int i = 0; i < 3; ++i) { s->pos[i] = getf(b + 4 + 4 * i); s->vel[i] = getf(b + 16 + 4 * i); }
    for (int i = 0; i < 4; ++i) s->q[i] = (int16_t)get16(b + 28 + 2 * i);
    s->flags = get16(b + 36); s->look = get16(b + 38);
}

int roam_look_put(uint8_t *b, const RoamLook *l) {
    int n = l->nparts > ROAM_LOOK_PARTS ? ROAM_LOOK_PARTS : l->nparts;
    put16(b, l->rev); b[2] = (uint8_t)n; put32(b + 3, l->car);
    for (int i = 0; i < n; ++i) put32(b + 7 + 4 * i, l->parts[i]);
    return 7 + 4 * n;
}

int roam_look_get(const uint8_t *b, int n, RoamLook *l) {
    if (n < 7 || b[2] > ROAM_LOOK_PARTS || n < 7 + 4 * b[2]) return -1;
    l->rev = get16(b); l->nparts = b[2]; l->car = get32(b + 3);
    for (int i = 0; i < l->nparts; ++i) l->parts[i] = get32(b + 7 + 4 * i);
    return 0;
}

// ---- view
void roam_view_init(RoamView *v) {
    memset(v, 0, sizeof *v);
    v->delay_ms = 100;
    v->extrap_ms = 250;
}

RoamRemote *roam_view_find(RoamView *v, uint16_t id) {
    for (int i = 0; i < ROAM_MAX_REMOTE; ++i)
        if (v->r[i].used && v->r[i].id == id) return &v->r[i];
    return 0;
}

static RoamRemote *remote_slot(RoamView *v, uint16_t id, uint32_t local_ms) {
    RoamRemote *r = roam_view_find(v, id);
    if (!r) {
        for (int i = 0; i < ROAM_MAX_REMOTE && !r; ++i)
            if (!v->r[i].used) r = &v->r[i];
        if (!r) return 0;
        memset(r, 0, sizeof *r);
        r->used = 1; r->id = id;
        r->last_rx = local_ms;
    }
    return r;
}

void roam_view_add(RoamView *v, uint16_t id, uint16_t seq, const RoamState *s, uint32_t local_ms) {
    if (!roam_pose_valid(s->pos, s->vel)) return;
    RoamRemote *r = remote_slot(v, id, local_ms);
    if (!r) return;
    for (int i = 0; i < r->n; ++i)
        if (r->seq[i] == seq) { r->dropped_dup++; return; }
    if (!r->n || (int32_t)(s->t - r->s[r->n - 1].t) > 0) r->last_rx = local_ms;
    // Clock: the least-delayed sample of the last ROAM_OFFS says how far the sender's clock is behind ours.
    r->off[r->ioff] = (int32_t)(local_ms - s->t);
    r->ioff = (r->ioff + 1) % ROAM_OFFS;
    if (r->noff < ROAM_OFFS) r->noff++;
    // A car appearing or leaving is a discontinuity: never interpolate across it (a newer snapshot with the other
    // car bit starts the buffer afresh; an older one is too late to matter).
    if (r->n && ((s->flags ^ r->s[r->n - 1].flags) & ROAM_FLAG_CAR)) {
        if ((int32_t)(s->t - r->s[r->n - 1].t) <= 0) { r->dropped_late++; return; }
        r->n = 0;
    }
    // Insert sorted by sender time; older than everything held in a full buffer is too late to matter.
    int at = r->n;
    while (at > 0 && (int32_t)(r->s[at - 1].t - s->t) > 0) at--;
    if (r->n == ROAM_JB) {
        if (at == 0) { r->dropped_late++; return; }
        memmove(&r->s[0], &r->s[1], (size_t)(at - 1) * sizeof r->s[0]);
        memmove(&r->seq[0], &r->seq[1], (size_t)(at - 1) * sizeof r->seq[0]);
        at--;
    } else {
        memmove(&r->s[at + 1], &r->s[at], (size_t)(r->n - at) * sizeof r->s[0]);
        memmove(&r->seq[at + 1], &r->seq[at], (size_t)(r->n - at) * sizeof r->seq[0]);
        r->n++;
    }
    r->s[at] = *s; r->seq[at] = seq;
}

static void qget(const RoamState *s, float q[4]) {
    float l = 0;
    for (int i = 0; i < 4; ++i) { q[i] = s->q[i] / 32767.0f; l += q[i] * q[i]; }
    l = l > 0 ? 1 / sqrtf(l) : 0;
    if (!l) { q[0] = q[1] = q[2] = 0; q[3] = 1; return; }
    for (int i = 0; i < 4; ++i) q[i] *= l;
}

int roam_view_pose(const RoamView *v, uint16_t id, uint32_t local_ms, float pos[3], float q[4], float vel[3]) {
    const RoamRemote *r = roam_view_find((RoamView *)v, id);
    if (!r || !r->n || r->map_only || (uint32_t)(local_ms - r->last_rx) > 1000) return 0;
    int32_t off = r->off[0];
    for (int i = 1; i < r->noff; ++i) if (r->off[i] < off) off = r->off[i];
    double t = (double)(int32_t)(local_ms - (uint32_t)off) - v->delay_ms;   // render time on the sender's clock
    const RoamState *a = &r->s[0], *b = 0;
    for (int i = 1; i < r->n; ++i) {
        if ((double)(int32_t)r->s[i].t >= t) { a = &r->s[i - 1]; b = &r->s[i]; break; }
        a = &r->s[i];
    }
    if ((double)(int32_t)a->t > t) b = 0;            // before the oldest: hold it
    if (!b) {                                         // past the newest (or before the oldest): extrapolate/hold
        double dt = (t - (double)(int32_t)a->t) / 1000.0;
        if (dt < 0) dt = 0;
        if (dt > v->extrap_ms / 1000.0) dt = v->extrap_ms / 1000.0;
        for (int i = 0; i < 3; ++i) { pos[i] = (float)(a->pos[i] + a->vel[i] * dt); vel[i] = a->vel[i]; }
        qget(a, q);
        return 1;
    }
    double T = (double)(int32_t)(b->t - a->t) / 1000.0, u = T > 0 ? (t - (double)(int32_t)a->t) / 1000.0 / T : 1;
    if (u < 0) u = 0;
    if (u > 1) u = 1;
    double h00 = (1 + 2 * u) * (1 - u) * (1 - u), h10 = u * (1 - u) * (1 - u), h01 = u * u * (3 - 2 * u), h11 = u * u * (u - 1);
    for (int i = 0; i < 3; ++i) {
        pos[i] = (float)(h00 * a->pos[i] + h10 * T * a->vel[i] + h01 * b->pos[i] + h11 * T * b->vel[i]);
        vel[i] = (float)(a->vel[i] + (b->vel[i] - a->vel[i]) * u);
    }
    float qa[4], qb[4], d = 0, l = 0;
    qget(a, qa); qget(b, qb);
    for (int i = 0; i < 4; ++i) d += qa[i] * qb[i];
    float sgn = d < 0 ? -1.0f : 1.0f;
    for (int i = 0; i < 4; ++i) { q[i] = (float)(qa[i] + (sgn * qb[i] - qa[i]) * u); l += q[i] * q[i]; }
    l = 1 / sqrtf(l);
    for (int i = 0; i < 4; ++i) q[i] *= l;
    return 1;
}

void roam_view_expire(RoamView *v, uint32_t local_ms, uint32_t timeout_ms) {
    for (int i = 0; i < ROAM_MAX_REMOTE; ++i)
        if (v->r[i].used && (uint32_t)(local_ms - v->r[i].last_rx) > timeout_ms) v->r[i].used = 0;
}

// ---- client
void roam_client_init(RoamClient *c, uint32_t room, const char *name) {
    memset(c, 0, sizeof *c);
    c->room = room;
    if (name) strncpy(c->name, name, sizeof c->name - 1);
    roam_view_init(&c->view);
}

int roam_client_hello(RoamClient *c, uint8_t *out) {
    int n = roam_hdr_put(out, ROAM_HELLO, c->id, c->seq++);
    put32(out + n, c->room);
    memcpy(out + n + 4, c->name, 16);
    return n + 20;
}

int roam_client_state(RoamClient *c, const RoamState *s, uint8_t *out) {
    int n = roam_hdr_put(out, ROAM_STATE, c->id, c->seq++);
    roam_state_put(out + n, s);
    return n + ROAM_STATE_BYTES;
}

int roam_client_look(RoamClient *c, const RoamLook *l, uint8_t *out) {
    int n = roam_hdr_put(out, ROAM_LOOK, c->id, c->seq++);
    return n + roam_look_put(out + n, l);
}

int roam_client_bye(RoamClient *c, uint8_t *out) { return roam_hdr_put(out, ROAM_BYE, c->id, c->seq++); }

int roam_client_pins(RoamClient *c, const uint16_t *ids, int count, uint8_t *out) {
    if (count < 0 || count > ROAM_PIN_MAX || (count && !ids)) return 0;
    c->npins = count;
    if (count) memcpy(c->pins, ids, (size_t)count * sizeof *ids);
    int n = roam_hdr_put(out, ROAM_PINS, c->id, c->seq++); out[n++] = (uint8_t)count;
    for (int i = 0; i < count; ++i) { put16(out + n, ids[i]); n += 2; }
    return n;
}
static int member_index(const RoamClient *c, uint16_t id) {
    for (int i = 0; i < c->nmembers; ++i) if (c->members[i] == id) return i;
    return -1;
}
void roam_name_copy(char out[16], const char in[16]) {
    int i = 0;
    for (; i < 15 && in[i]; ++i) out[i] = (unsigned char)in[i] >= 32 && (unsigned char)in[i] < 127 ? in[i] : '?';
    memset(out + i, 0, (size_t)(16 - i));
}

void roam_client_input(RoamClient *c, const uint8_t *b, int n, uint32_t local_ms) {
    int type; uint16_t id, seq;
    if (n > ROAM_MAX_DGRAM || roam_hdr_get(b, n, &type, &id, &seq)) { c->rx_bad++; return; }
    b += ROAM_HDR; n -= ROAM_HDR;
    switch (type) {
    case ROAM_NAME: {
        if (n != 16) break;
        if (!c->has_members || member_index(c, id) < 0 || (uint32_t)(local_ms - c->member_ms) > 1000) return;
        RoamRemote *r = remote_slot(&c->view, id, local_ms);
        if (r) roam_name_copy(r->name, (const char *)b);
        return;
    }
    case ROAM_WELCOME:
        if (n != 4 || !get16(b) || !b[2] || b[2] > ROAM_INTEREST_MAX || b[3] != ROAM_HZ) break;
        if (!c->welcomed || c->id != get16(b) || (c->has_members && (uint32_t)(local_ms - c->member_ms) > 1000)) {
            roam_view_init(&c->view); c->has_members = c->nmembers = 0;
        }
        c->id = get16(b); c->near = b[2]; c->welcomed = 1;
        return;
    case ROAM_MEMBERS: {
        if (n < 1 || b[0] > ROAM_INTEREST_MAX || n != 1 + b[0] * 5) break;
        if (c->has_members && (int16_t)(uint16_t)(seq - c->member_seq) <= 0) return;
        for (int i = 0; i < b[0]; ++i) {
            const uint8_t *e = b + 1 + i * 5;
            if (!get16(e) || get16(e) == c->id || e[2] > 2) { c->rx_bad++; return; }
            for (int j = 0; j < i; ++j) if (get16(b + 1 + j * 5) == get16(e)) { c->rx_bad++; return; }
        }
        c->nmembers = b[0]; c->member_seq = seq; c->has_members = 1; c->member_ms = local_ms;
        for (int i = 0; i < c->nmembers; ++i) {
            const uint8_t *e = b + 1 + i * 5;
            c->members[i] = get16(e); c->tiers[i] = e[2]; c->member_state_seq[i] = get16(e + 3);
        }
        for (int i = 0; i < ROAM_MAX_REMOTE; ++i) if (c->view.r[i].used) {
            int at = member_index(c, c->view.r[i].id);
            if (at < 0) c->view.r[i].used = 0;
            else c->view.r[i].map_only = c->tiers[at] == 2;
        }
        return;
    }
    case ROAM_BUNDLE: {
        if (n < 1 || b[0] > ROAM_BUNDLE_MAX || n != 1 + b[0] * ROAM_ENTRY_BYTES) break;
        if (!c->has_members) return;
        c->rx_bundles++;
        for (int i = 0; i < b[0]; ++i) {
            const uint8_t *e = b + 1 + i * ROAM_ENTRY_BYTES;
            RoamState s;
            roam_state_get(e + 4, &s);
            uint16_t rid = get16(e);
            if (c->welcomed && rid == c->id) continue;
            if (c->has_members) {
                int at = member_index(c, rid);
                const RoamRemote *held = roam_view_find(&c->view, rid);
                if (at < 0 || (uint32_t)(local_ms - c->member_ms) > 1000 ||
                    ((!held || !held->n) && (int16_t)(uint16_t)(get16(e + 2) - c->member_state_seq[at]) < 0)) continue;
            }
            roam_view_add(&c->view, rid, get16(e + 2), &s, local_ms);
            RoamRemote *remote = roam_view_find(&c->view, rid);
            if (remote && c->has_members) remote->map_only = c->tiers[member_index(c, rid)] == 2;
            c->rx_entries++;
        }
        return;
    }
    case ROAM_LOOK: {
        RoamLook l;
        if (roam_look_get(b, n, &l)) break;
        if (!c->has_members || member_index(c, id) < 0 || (uint32_t)(local_ms - c->member_ms) > 1000) return;
        RoamRemote *r = remote_slot(&c->view, id, local_ms);
        if (r) { r->look = l; r->has_look = 1; }
        return;
    }
    case ROAM_ERR:
        if (n >= 1) c->last_err = b[0];
        return;
    default: break;
    }
    c->rx_bad++;
}

int roam_toggle_allowed(int welcomed, int has_car, int driving, int menu_open) {
    return welcomed && has_car && driving && !menu_open;
}
int roam_toggle_press(RoamToggle *t, int down, int allowed) {
    int edge = down && !t->held;
    t->held = down != 0;
    return edge && allowed;
}
