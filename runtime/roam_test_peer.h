// Fixture transports exercise the same challenge/retry path as UDP peers.
#pragma once
#include "roam_core.h"
#include <string.h>
#include <stdlib.h>
typedef struct { RoamClient *client; uint32_t now; } RoamTestDelivery;
static inline int roam_test_deliver(void *ctx, const struct sockaddr_in *to, const uint8_t *b, int n) {
    (void)to; RoamTestDelivery *d = ctx; roam_client_input(d->client, b, n, d->now); return n;
}
static inline int roam_test_join(RoamRelay *r, RoamClient *c, const struct sockaddr_in *from, uint32_t now) {
    RoamTestDelivery d = {c, now}; uint8_t b[ROAM_MAX_DGRAM];
    c->welcomed = 0;
    roam_relay_sender(r, roam_test_deliver, &d);
    for (int i = 0; i < 3 && !c->welcomed; ++i) {
        int n = roam_client_hello(c, b); roam_relay_input(r, b, n, from, now);
    }
    roam_relay_sender(r, NULL, NULL); return c->welcomed;
}
static inline RoamRelayPolicy roam_test_policy(void) {
    // Preserve the old 1,000-peer throughput controls; never bypass cookies or client limits.
    RoamRelayPolicy p = roam_relay_policy();
    p.admission_rate = p.source_rate = p.challenge_rate = 1000000;
    p.admission_burst = p.source_burst = p.challenge_burst = 1000000;
    p.signature_rate = p.signature_burst = 1000000;
    p.ingress_packets = p.ingress_packet_burst = 1000000;
    p.ingress_bytes = p.ingress_byte_burst = 128*1024*1024;
    p.egress_packets = p.egress_packet_burst = 1000000;
    p.egress_bytes = p.egress_byte_burst = 128*1024*1024;
    return p;
}
static inline RoamRelay *roam_test_relay(int fd, int near, int max) {
    RoamRelayPolicy p = roam_test_policy(); return roam_relay_new_policy(fd, near, max, &p);
}
