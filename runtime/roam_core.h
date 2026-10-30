// roam_core.h - online free roam: wire format v2, client session, interpolation and the relay.
//
// Players in their own career free roam see each other's cars. Each game sends its car's state 20 times a second
// to one relay over UDP; the relay sends bounded, staggered bundles for 32 interests in the same room. The relay
// is the same code in the standalone CLI (roam_relay_main.c) and in tests. All fields are little-endian.
//
// Every datagram starts with an 8-byte header:  'R' 'M' version type | sender id u16 | seq u16.
// A datagram with another version is dropped; the relay answers it once per address with ERR(ROAM_ERR_VERSION).
//   HELLO   c->r  room u32, name[16]                         WELCOME r->c  your id u16, near u8, hz u8
//   STATE   c->r  RoamState (40 bytes)                       BUNDLE  r->c  count u8, count x (id u16, seq u16, RoamState)
//   LOOK    c->r  rev u16, count u8, car u32, count x part u32   (the relay forwards it with the sender's id in the
//           header to each client that gains the sender as a neighbour or sees a new rev)
//   BYE     c->r  (empty)                                    ERR     r->c  code u8
//   MEMBERS r->c count u8, count x (id u16, tier u8, state seq u16); header seq orders full membership snapshots.
//   PINS    c->r count u8 (<=4), count x id u16; same-room, fresh friends consume the existing 32 slots.
//   NAME    r->c name[16], printable ASCII; header id is the same-room member being named.
// LOOK and NAME share one control datagram/client/tick, with periodic resends independent of state bundles.
// Tier 0/1/2 = 20/10/2 Hz; tier 2 is map-only. Stale states leave membership within 750+200 ms.
#pragma once
#include <stdint.h>

#define ROAM_VERSION 2
#define ROAM_PORT 47800
#define ROAM_MAX_DGRAM 1200
#define ROAM_HDR 8
#define ROAM_STATE_BYTES 40
#define ROAM_ENTRY_BYTES (4 + ROAM_STATE_BYTES)
#define ROAM_BUNDLE_MAX ((ROAM_MAX_DGRAM - ROAM_HDR - 1) / ROAM_ENTRY_BYTES)
#define ROAM_LOOK_PARTS 96
/* Packed customization: 139 installed-part indices (70 words), 22 scalar colour words. */
#define ROAM_LOOK_CUSTOM_WORDS 92
#define ROAM_HZ 20
#define ROAM_NEAR_DEFAULT 32
#define ROAM_INTEREST_MAX 32
#define ROAM_PIN_MAX 4
#define ROAM_STATE_FRESH_MS 750
#define ROAM_INTEREST_MS 200
#define ROAM_FLAG_CAR 1      // RoamState.flags bit 0: the sender has a car in the world (else the pose is meaningless)
#define ROAM_FLAG_GHOST 2    // bit 1: the sender has ghost mode on (no collisions with anyone's car)

enum { ROAM_HELLO = 1, ROAM_WELCOME, ROAM_STATE, ROAM_BUNDLE, ROAM_LOOK, ROAM_BYE, ROAM_ERR,
       ROAM_MEMBERS, ROAM_PINS, ROAM_NAME };
enum { ROAM_ERR_VERSION = 1, ROAM_ERR_FULL = 2 };

typedef struct {
    uint32_t t;              // sender's clock, ms
    float pos[3], vel[3];    // world metres, metres/second
    int16_t q[4];            // unit quaternion x 32767
    uint16_t flags, look;    // look = the sender's LOOK revision
} RoamState;

typedef struct {
    uint16_t rev;
    uint8_t nparts;
    uint32_t car, parts[ROAM_LOOK_PARTS];
} RoamLook;

// Remote poses are limited to +/-1,000 km and +/-10 km/s on each axis.
int roam_pose_valid(const float pos[3], const float vel[3]);

// ---- wire
int roam_hdr_put(uint8_t *b, int type, uint16_t id, uint16_t seq);
// 0 = ok; -1 = not ours / short; -2 = other version (type/id/seq still filled when long enough)
int roam_hdr_get(const uint8_t *b, int n, int *type, uint16_t *id, uint16_t *seq);
void roam_state_put(uint8_t *b, const RoamState *s);
void roam_state_get(const uint8_t *b, RoamState *s);
int roam_look_put(uint8_t *b, const RoamLook *l);              // body bytes
int roam_look_get(const uint8_t *b, int n, RoamLook *l);       // 0 ok

// ---- receiver: jitter buffer and interpolation per remote player
#define ROAM_JB 16
#define ROAM_OFFS 64
#define ROAM_MAX_REMOTE 64
typedef struct {
    int used;
    uint16_t id;
    int n;                       // snapshots held, sorted by t
    RoamState s[ROAM_JB];
    uint16_t seq[ROAM_JB];
    int32_t off[ROAM_OFFS];      // local receive ms - sender ms, ring
    int noff, ioff;
    uint32_t last_rx;
    RoamLook look;
    int has_look;
    char name[16];
    int map_only;                // a tier-2 interest retains snapshots but has no drawable pose
    uint32_t dropped_late, dropped_dup;
} RoamRemote;

typedef struct {
    RoamRemote r[ROAM_MAX_REMOTE];
    int delay_ms;                // render this far behind the remote's clock (default 100)
    int extrap_ms;               // extrapolate at most this far past the newest snapshot (default 250)
} RoamView;

void roam_view_init(RoamView *v);
RoamRemote *roam_view_find(RoamView *v, uint16_t id);
void roam_view_add(RoamView *v, uint16_t id, uint16_t seq, const RoamState *s, uint32_t local_ms);
// 1 = pose written (pos, unit quaternion q, vel); 0 = unknown id or no snapshot
int roam_view_pose(const RoamView *v, uint16_t id, uint32_t local_ms, float pos[3], float q[4], float vel[3]);
void roam_view_expire(RoamView *v, uint32_t local_ms, uint32_t timeout_ms);

// ---- client session (transport-free: callers send the bytes it builds and feed it what they receive)
typedef struct {
    uint32_t room;
    char name[16];
    uint16_t id, seq;
    int welcomed, near;
    int last_err;
    RoamView view;
    uint32_t rx_bundles, rx_entries, rx_bad;
    uint16_t members[ROAM_INTEREST_MAX], member_state_seq[ROAM_INTEREST_MAX], member_seq;
    uint8_t tiers[ROAM_INTEREST_MAX], nmembers, has_members;
    uint32_t member_ms;
    uint16_t pins[ROAM_PIN_MAX];
    int npins;
} RoamClient;

void roam_client_init(RoamClient *c, uint32_t room, const char *name);
int roam_client_hello(RoamClient *c, uint8_t *out);
int roam_client_state(RoamClient *c, const RoamState *s, uint8_t *out);
int roam_client_look(RoamClient *c, const RoamLook *l, uint8_t *out);
int roam_client_bye(RoamClient *c, uint8_t *out);
int roam_client_pins(RoamClient *c, const uint16_t *ids, int count, uint8_t *out);

// Ghost-mode toggle input (roam_game.c feeds it the D-pad Down / G state every frame). A press toggles once, on the
// edge; it counts only while online free roam is up (welcomed, a local car) and the player is driving (no menu, pause,
// F10 menu or on-screen keyboard). The held flag follows the button even when refused, so holding it through a menu
// never fires on the way out.
typedef struct { int held; } RoamToggle;
int roam_toggle_allowed(int welcomed, int has_car, int driving, int menu_open);
int roam_toggle_press(RoamToggle *t, int down, int allowed);
void roam_client_input(RoamClient *c, const uint8_t *b, int n, uint32_t local_ms);
void roam_name_copy(char out[16], const char in[16]);

// ---- relay (one nonblocking UDP socket; fd < 0 = deterministic sink, counted as successful sends)
struct sockaddr_in;
typedef struct RoamRelay RoamRelay;
typedef struct {
    uint64_t clients, rx, rx_rate_dropped, rx_bad, rx_version, tx, tx_bytes, timeouts, full;
    uint64_t rx_bytes, tx_attempts, tx_errors, tx_wouldblock, tx_error_bytes;
    uint64_t lookup_probes, candidates, selections, interest_changes, tier_changes;
    uint64_t bundle_overflow, state_entries[3], ticks, max_tick_us;
} RoamRelayStats;
RoamRelay *roam_relay_new(int fd, int near, int max_clients);
void roam_relay_free(RoamRelay *r);
void roam_relay_input(RoamRelay *r, const uint8_t *b, int n, const struct sockaddr_in *from, uint32_t now_ms);
void roam_relay_tick(RoamRelay *r, uint32_t now_ms);   // call ROAM_HZ times a second
// Drain the socket (non-blocking) into roam_relay_input, ticking when due. Returns datagrams read.
int roam_relay_service(RoamRelay *r, uint32_t now_ms);
RoamRelayStats roam_relay_stats(const RoamRelay *r);
