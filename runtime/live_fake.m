// Test only: unattended live runs post to a loopback fake HLS ingest (runtime/live_fake.sh), never to YouTube.
// With NFS_TEST_LIVE_FAKE=<port> in a diag run the BOOT_KEYS step `livego` starts live with livestream_start_fake: its fixed
// key alone goes to http://127.0.0.1:<port>/http_upload_hls (livestream.mm). Otherwise `livego` does nothing.
#import <Foundation/Foundation.h>
#include "live_fake.h"
#include "livestream.h"
#include "cliprec.h"
#include "host.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fake_port(void) {
    const char *e = getenv("NFS_TEST_LIVE_FAKE");
    if (!e || !*e) return 0;
    char *end = NULL; long port = strtol(e, &end, 10);
    return end && !*end && port > 0 && port <= 65535 ? (int)port : 0;
}
void live_fake_go(void) {
    int port = fake_port();
    if (!port) { fprintf(stderr, "live fake: livego ignored (NFS_TEST_LIVE_FAKE unset)\n"); return; }
    if (!diag_on) { fprintf(stderr, "live fake: livego ignored (diag runs only)\n"); return; }
    if (!livestream_start_fake(port)) { fprintf(stderr, "live fake: Go refused\n"); return; }
    if (!cliprec_live_start()) { livestream_fail(LIVESTREAM_FAILURE_CAPTURE); fprintf(stderr, "live fake: capture refused\n"); return; }
    fprintf(stderr, "live fake: Go to 127.0.0.1:%d\n", port);
}
