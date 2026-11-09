// Test only: live streaming to a loopback fake HLS ingest (NFS_TEST_LIVE_FAKE=<port>).
#pragma once
#ifdef __cplusplus
extern "C" {
#endif
// The BOOT_KEYS step `livego`: Go live with a fixed key to the fake ingest (no-op without the switch).
void live_fake_go(void);
#ifdef __cplusplus
}
#endif
