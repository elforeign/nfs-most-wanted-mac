// Streaming actions. The secret has no public getter.
#pragma once
#define STREAM_KEY_DIR ".stream-key"
#define STREAM_KEY_FILE "stream-key"
#define STREAM_KEY_TEMP "stream-key.tmp"
#ifdef __cplusplus
extern "C" {
#endif
void stream_key_edit(int unused);
// Forget and quit scrub the in-memory key and editor before finishing transport.
void stream_key_forget(int unused);
void stream_key_quit(void);
void stream_key_go(int unused);
void stream_key_stop(int unused);
void stream_key_status(char *buf, int size);
void stream_key_close(void);
int stream_key_editing(void);
#ifdef __OBJC__
@class NSEvent;
int stream_key_route_event(NSEvent *event);
#endif
#ifdef __cplusplus
}
#endif
