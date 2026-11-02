// Synthetic native event API shared by the Windows host fixtures.
#include <errno.h>
#include <stdatomic.h>
typedef struct { unsigned kind; int manual, signaled; } NativeEvent;
static pthread_mutex_t event_mu=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t event_changed=PTHREAD_COND_INITIALIZER;
static _Thread_local DWORD native_error;
static _Atomic unsigned fake_event_waiting;
static unsigned fake_event_live;
static int fake_event_create_fail, fake_event_wait_fail;
DWORD GetLastError(void) { return native_error; }
HANDLE CreateEventA(void *sa,BOOL manual,BOOL initial,const char *name) {
    (void)sa; (void)name;
    if (fake_event_create_fail) { native_error=8; return NULL; }
    NativeEvent *e=malloc(sizeof *e);
    if (!e) { native_error=8; return NULL; }
    *e=(NativeEvent){2,manual,initial}; ++fake_event_live; return e;
}
BOOL SetEvent(HANDLE h) {
    pthread_mutex_lock(&event_mu);
    ((NativeEvent *)h)->signaled=1;
    pthread_cond_broadcast(&event_changed);
    pthread_mutex_unlock(&event_mu); return 1;
}
BOOL ResetEvent(HANDLE h) {
    pthread_mutex_lock(&event_mu); ((NativeEvent *)h)->signaled=0;
    pthread_mutex_unlock(&event_mu); return 1;
}
DWORD WaitForMultipleObjects(DWORD count,const HANDLE *handles,BOOL all,DWORD ms) {
    if (fake_event_wait_fail) { native_error=87; return WAIT_FAILED; }
    struct timespec deadline;
    clock_gettime(CLOCK_REALTIME,&deadline);
    deadline.tv_sec+=ms/1000; deadline.tv_nsec+=(ms%1000)*1000000;
    if (deadline.tv_nsec>=1000000000) { ++deadline.tv_sec; deadline.tv_nsec-=1000000000; }
    pthread_mutex_lock(&event_mu);
    DWORD result=WAIT_TIMEOUT;
    atomic_fetch_add(&fake_event_waiting,1);
    for (;;) {
        unsigned first=count, ready=0;
        for (unsigned i=0; i<count; ++i) {
            NativeEvent *e=handles[i];
            if (e->signaled) { if (first==count) first=i; ++ready; }
        }
        if ((all && ready==count) || (!all && ready)) {
            for (unsigned i=0; i<count; ++i) {
                NativeEvent *e=handles[i];
                if ((all || i==first) && !e->manual) e->signaled=0;
            }
            result=all ? WAIT_OBJECT_0 : WAIT_OBJECT_0+first; break;
        }
        if (!ms) break;
        int rc=ms==INFINITE ? pthread_cond_wait(&event_changed,&event_mu) :
                             pthread_cond_timedwait(&event_changed,&event_mu,&deadline);
        if (rc==ETIMEDOUT) break;
        if (rc) { native_error=87; result=WAIT_FAILED; break; }
    }
    atomic_fetch_sub(&fake_event_waiting,1);
    pthread_mutex_unlock(&event_mu); return result;
}
