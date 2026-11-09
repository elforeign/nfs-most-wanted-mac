// Transient streaming actions. The secret has no public getter.
#pragma once
#ifdef __cplusplus
extern "C" {
#endif
void stream_key_edit(int unused);
void stream_key_forget(int unused);
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
