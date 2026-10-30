// Invalid network snapshots must never enter relay cells or client history.
#include "roam_core.h"
#include <arpa/inet.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
static int checks, failures;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; if (failures < 5) printf("FAIL %d: %s\n", __LINE__, #x); } } while (0)
int main(void) {
    RoamRelay *relay = roam_relay_new(-1, 2, 4); CHECK(relay != NULL); if (!relay) return 2;
    RoamClient c; roam_client_init(&c, 42, "fixture");
    struct sockaddr_in from = {.sin_family=AF_INET, .sin_port=htons(10001), .sin_addr.s_addr=htonl(INADDR_LOOPBACK)};
    uint8_t b[1200]; int n = roam_client_hello(&c, b); roam_relay_input(relay, b, n, &from, 1000); c.id = 1;
    RoamState good = {.t=1000, .pos={1,2,3}, .vel={4,5,6}, .q={0,0,0,32767}, .flags=ROAM_FLAG_CAR};
    n = roam_client_state(&c, &good, b); roam_relay_input(relay, b, n, &from, 1000);
    roam_view_add(&c.view, 7, 1, &good, 1000);
    roam_hdr_put(b, ROAM_MEMBERS, 0, 0);
    memcpy(b + ROAM_HDR, (uint8_t[]){1,7,0,0,1,0}, 6);
    roam_client_input(&c, b, ROAM_HDR + 6, 1000);
    const float bad[] = {NAN, INFINITY, -INFINITY, FLT_MAX, -FLT_MAX, 1000001, -1000001};
    unsigned rejected = 0;
    for (int field = 0; field < 6; ++field) for (unsigned i = 0; i < sizeof bad / sizeof *bad; ++i) {
        RoamState s = good; s.t += 1 + rejected;
        if (field < 3) s.pos[field] = bad[i]; else s.vel[field-3] = bad[i];
        uint32_t now = 1100 + 100 * rejected;
        unsigned before = roam_relay_stats(relay).rx_bad;
        n = roam_client_state(&c, &s, b); roam_relay_input(relay, b, n, &from, now);
        CHECK(roam_relay_stats(relay).rx_bad == before + 1);
        roam_hdr_put(b, ROAM_BUNDLE, 0, (uint16_t)rejected); b[ROAM_HDR] = 1;
        b[ROAM_HDR+1] = 7; b[ROAM_HDR+2] = 0; b[ROAM_HDR+3] = (uint8_t)(rejected + 2); b[ROAM_HDR+4] = 0;
        roam_state_put(b + ROAM_HDR + 5, &s);
        c.member_ms = now;
        roam_client_input(&c, b, ROAM_HDR + 1 + ROAM_ENTRY_BYTES, now);
        RoamRemote *remote = roam_view_find(&c.view, 7);
        CHECK(remote && remote->n == 1 && !memcmp(&remote->s[0], &good, sizeof good));
        roam_view_add(&c.view, 8, (uint16_t)rejected, &s, now);
        CHECK(!roam_view_find(&c.view, 8));
        ++rejected;
    }
    // Velocity-only finite boundary cases; inclusive position/velocity bounds remain usable.
    for (int axis = 0; axis < 3; ++axis) for (int sign = -1; sign <= 1; sign += 2) {
        RoamState s = good; s.vel[axis] = sign * 10001.0f;
        CHECK(!roam_pose_valid(s.pos, s.vel));
        unsigned before = roam_relay_stats(relay).rx_bad;
        n = roam_client_state(&c, &s, b); roam_relay_input(relay, b, n, &from, 6000 + rejected * 100);
        CHECK(roam_relay_stats(relay).rx_bad == before + 1); ++rejected;
        roam_view_add(&c.view, 8, (uint16_t)rejected, &s, 6000);
        CHECK(!roam_view_find(&c.view, 8));
        s.pos[axis] = sign * 1000000.0f; s.vel[axis] = sign * 10000.0f;
        CHECK(roam_pose_valid(s.pos, s.vel));
    }
    CHECK(roam_relay_stats(relay).rx_rate_dropped == 0);
    // Only tick a relay that has rejected the poison: broken controls report failures without UB.
    if (!failures) roam_relay_tick(relay, 12000);
    roam_relay_free(relay);
    printf("state: %d checks, %d failures; invalid snapshots=%u\n", checks, failures, rejected);
    return failures ? 1 : 0;
}
