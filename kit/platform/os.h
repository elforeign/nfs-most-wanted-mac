// os.h - the platform layer: what the runtime, the DirectX shims, the mod
// foundation and the shared host code need from the operating system. One
// POSIX implementation (macOS, Linux) and one Win32 implementation; nothing
// above this header includes a platform header, nothing here knows the guest.
//
// Every function is plain C with C linkage so C plugins and C++ hosts share
// it. Errors are reported the C way: -1 or NULL, errno where POSIX sets it.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <time.h>

#ifdef __cplusplus
extern "C" {
#endif

// ---------------------------------------------------------------------------
// Threads. A created thread is an opaque handle; identity is a 64-bit id that
// compares with ==, because the scheduler asks "is the caller this thread"
// far more often than it joins anything.
// ---------------------------------------------------------------------------
typedef struct OsThread OsThread;
typedef uint64_t OsThreadId;
// `stack_bytes` 0 means the platform default. NULL when the thread could not
// be created; nothing has started in that case.
OsThread *os_thread_create(void *(*fn)(void *), void *arg, size_t stack_bytes);
void os_thread_join(OsThread *t);   // waits, then frees the handle
void os_thread_detach(OsThread *t); // frees the handle; the thread runs on
void os_thread_exit(void);          // ends the calling thread; never returns
// Asks the scheduler to keep the calling thread on the fastest cores: the
// guest's threads draw every frame. A no-op where there is no such control.
void os_thread_prefer_performance(void);
// CPU time the calling thread has used (user + system), in ns; 0 when unknown.
// Evidence only (T01 frame attribution).
uint64_t os_thread_cpu_ns(void);
// Marks the calling thread as a short, periodic, latency-critical waker (macOS:
// time-constraint policy; its timed waits then wake without coalescing leeway).
// Only for threads that do almost no work per wake. No-op elsewhere.
void os_thread_set_time_critical_wake(void);
// The calling thread's scheduling class as a short name ("user-interactive",
// "default", ...), or "unknown". Evidence only.
const char *os_thread_class_name(void);
OsThreadId os_thread_self(void);
OsThreadId os_thread_id_of(const OsThread *t);

// ---------------------------------------------------------------------------
// Virtual memory: a zero-filled, readable, writable, page-aligned region.
// ---------------------------------------------------------------------------
void *os_vm_reserve(size_t bytes); // NULL on failure
void os_vm_release(void *p, size_t bytes);
// NFSMW issue 8: the host page size, and a page-aligned span made read-only (1) or read-write again (0); 0 on success.
size_t os_vm_page_size(void);
int os_vm_set_readonly(void *p, size_t bytes, int readonly);

// ---------------------------------------------------------------------------
// Plugins.
// ---------------------------------------------------------------------------
void *os_dlopen(const char *path);
void *os_dlopen_noload(const char *path); // a handle only if already loaded
void *os_dlsym(void *handle, const char *name);
int os_dlclose(void *handle);          // 0 on success
const char *os_dlerror(void);          // text for the last failure
const char *os_plugin_extension(void); // ".dylib", ".so" or ".dll"

// ---------------------------------------------------------------------------
// Paths. UTF-8 everywhere; the Win32 implementation converts.
// ---------------------------------------------------------------------------
typedef struct OsStat {
    uint64_t size;
    int64_t atime, mtime, ctime; // seconds since the epoch
    int is_dir, is_regular, is_symlink, is_readonly;
} OsStat;
int os_stat(const char *path, OsStat *out);      // follows symlinks; 0 or -1
int os_lstat(const char *path, OsStat *out);     // does not follow
int os_mkdir(const char *path);                  // 0, or -1 (an existing directory is -1, as mkdir)
int os_rename(const char *from, const char *to); // replaces an existing destination file
int os_unlink(const char *path);
int os_rmdir(const char *path);       // the directory must be empty
int os_getcwd(char *buf, size_t cap); // 0 or -1
int os_chdir(const char *path);
// Calls `fn` for every entry except "." and "..", in directory order. A
// nonzero return from `fn` stops the walk. -1 when the directory cannot be
// opened, 0 otherwise.
typedef int (*OsListDirFn)(const char *name, void *user);
int os_listdir(const char *dir, OsListDirFn fn, void *user);
// The trailing XXXXXX of `template_path` is replaced in place; returns an open
// read-write descriptor for the new file, or -1.
int os_mkstemp(char *template_path);
// mkdtemp: replaces the trailing XXXXXX and creates the directory; 0 or -1.
int os_mkdtemp(char *template_path);
// A writable temporary directory without a trailing separator ("/tmp", %TEMP%).
const char *os_temp_dir(void);
// "/dev/null" or "NUL".
const char *os_null_device(void);
// Bytes a new file may still take on the volume holding `path` (an existing
// file or directory). 0 or -1.
int os_free_space(const char *path, uint64_t *bytes_out);
// Sets a file's modification (and access) time, in seconds since the epoch.
int os_set_mtime(const char *path, int64_t mtime);
// A string value from the Windows registry. `key` starts with "HKLM\" or
// "HKCU\" and is read from the 64- and then the 32-bit view. -1 elsewhere.
int os_registry_read(const char *key, const char *value, char *buf, size_t cap);
// Per-user data directory for `app` (not created): ~/Library/Application Support/<app>,
// %APPDATA%\<app>, $XDG_DATA_HOME/<app> or ~/.local/share/<app>. 0 or -1.
int os_user_data_dir(const char *app, char *buf, size_t cap);
// Runs argv[0] with argv (NULL-terminated), inheriting stdio; 0 and a pid, or -1.
int os_spawn(const char *const argv[], int64_t *pid_out);
// Waits for the child. exit_code receives the exit status, or 128 + signal on
// POSIX when it died by a signal (so an abort reads as 134 everywhere); 0 or -1.
int os_wait(int64_t pid, int *exit_code);

// ---------------------------------------------------------------------------
// Descriptors. Binary mode always; created files are mode 0644.
// ---------------------------------------------------------------------------
enum {
    OS_O_RDONLY = 0,
    OS_O_WRONLY = 1,
    OS_O_RDWR = 2,
    OS_O_CREAT = 0x40,
    OS_O_EXCL = 0x80,
    OS_O_TRUNC = 0x200
};
enum { OS_SEEK_SET = 0, OS_SEEK_CUR = 1, OS_SEEK_END = 2 };
int os_fd_open(const char *path, int flags);
int64_t os_fd_read(int fd, void *buf, size_t n);        // bytes read, 0 at EOF, -1 on error
int64_t os_fd_write(int fd, const void *buf, size_t n); // bytes written, -1 on error
int64_t os_fd_seek(int fd, int64_t off, int whence);    // new offset or -1
int os_fd_close(int fd);
int os_fd_dup(int fd);
int os_fd_fsync(int fd);
int os_fd_truncate(int fd, int64_t length);
int os_fd_stat(int fd, OsStat *out);
// A FILE over an open descriptor, which then owns it.
void *os_fdopen(int fd, const char *mode);

// ---------------------------------------------------------------------------
// Process.
// ---------------------------------------------------------------------------
int os_exe_path(char *buf, size_t cap); // 0 or -1; NUL-terminated
// Report a fatal signal inside guest code. `what` is "SIGSEGV", "SIGBUS" or
// "an abort from the runtime". Returns 1 when handlers were installed and 0
// where the platform has no equivalent yet.
typedef void (*OsFaultFn)(const char *what);
int os_install_fault_handlers(OsFaultFn fn);
// For use inside the OsFaultFn: where the fault was in HOST code. The POSIX
// handler fills it from the signal's context before calling the OsFaultFn (on
// macOS: pc and lr of the faulting instruction, si_addr, and the main
// executable's load address, so `atos -o <exe> -l <image_load> <pc>` names the
// function); zeros where the platform does not provide it.
typedef struct OsFaultInfo {
    uint64_t pc, lr, addr, image_load;
} OsFaultInfo;
void os_fault_info(OsFaultInfo *out);
// NFSMW issue 8: asked first, inside the signal handler, for every SIGSEGV/SIGBUS. Returns 1 when it has dealt with
// the fault (the context may have been changed to step over the instruction) and the thread should go on; 0 sends
// the fault on to the OsFaultFn as before. Must be signal-safe. `addr` is si_addr; `uc` the signal's ucontext.
typedef int (*OsWriteFaultHook)(uint64_t addr, void *uc);
void os_set_write_fault_hook(OsWriteFaultHook hook);
// The main executable's load address (0 before os_install_fault_handlers, or where unknown), for atos -l.
uint64_t os_image_load(void);
// For use inside a fault handler: a raw write to the error stream and an
// immediate process exit, neither of which touches stdio or runs destructors.
void os_write_stderr_raw(const char *s, size_t n);
void os_exit_immediately(int code);

// ---------------------------------------------------------------------------
// Time.
// ---------------------------------------------------------------------------
uint64_t os_monotonic_ns(void); // never goes backwards; arbitrary origin
// Broken-down local and UTC time for a Unix timestamp; 0 or -1.
int os_localtime(int64_t seconds, struct tm *out);
int os_gmtime(int64_t seconds, struct tm *out);
uint64_t os_wall_time_us(void); // microseconds since the Unix epoch
void os_sleep_us(uint64_t us);

// ---------------------------------------------------------------------------
// Clocked PCM output for timed media. Optional: currently implemented by
// Apple's AudioQueue. Unavailable backends return NULL/-1, never a fake clock.
// One owner thread calls these functions. The device callback only returns
// preallocated buffers; it never decodes, allocates or touches guest memory.
// ---------------------------------------------------------------------------
typedef struct OsAudioOutput OsAudioOutput;
typedef enum OsAudioLayout {
    OS_AUDIO_STEREO = 2, // interleaved signed PCM16: L, R
    OS_AUDIO_51 = 6      // interleaved signed PCM16: L, R, C, LFE, Ls, Rs (DVD_12)
} OsAudioLayout;
typedef struct OsAudioClock {
    double sample_time;       // queue presentation timeline, in source-rate frames
    uint64_t enqueued_frames; // producer count, NOT a presentation clock
    uint64_t returned_frames; // frames in buffers the queue handed back; survives route
                              // changes, leads presentation by output latency
    uint64_t epoch;           // increments on explicit reset; old times are invalid
    uint64_t discontinuities; // cumulative native timeline discontinuities this epoch
    int valid;                // native timestamp has a finite, nonnegative sample time
    int running;              // native queue state; may remain true while paused
    int paused;               // owner's requested pause state
    int native_status;        // native clock error, 0 on success
} OsAudioClock;
// Opens paused. Fixed capacity: eight buffers of at most 2048 frames each.
// Native error is optional; -1 means invalid input or unsupported backend.
OsAudioOutput *os_audio_output_open(uint32_t rate, OsAudioLayout layout, int *native_error);
// Copies at most 2048 frames before returning; 0 means backpressure, -1 error.
int os_audio_output_write(OsAudioOutput *output, const int16_t *pcm, uint32_t frames);
int os_audio_output_start(OsAudioOutput *output); // also resumes after pause
int os_audio_output_pause(OsAudioOutput *output);
// Queue-local linear gain, finite 0..1; never changes the system volume or PCM.
int os_audio_output_set_gain(OsAudioOutput *output, float gain);
int os_audio_output_get_gain(OsAudioOutput *output, float *gain);
int os_audio_output_clock(OsAudioOutput *output, OsAudioClock *clock);
// Synchronous stop/flush, resets time and counters, leaves the queue paused.
int os_audio_output_reset(OsAudioOutput *output);
// Synchronously ends callbacks before freeing the queue and its storage.
void os_audio_output_close(OsAudioOutput *output);
// The device the queue is actually rendering to right now (it follows the
// system default and can change under a playing queue), with that device's
// current format and I/O period. Owner thread only; for diagnostics and for
// estimating how far buffer returns lead presentation. 0 on success.
typedef struct OsAudioRoute {
    char name[96];            // device name, or "" if unknown
    char uid[96];             // CoreAudio device UID, or ""
    int follows_default;      // queue has no explicit device: it tracks the default
    double device_rate;       // queue's device-side sample rate (0 if not reported)
    uint32_t device_channels; // queue's device-side channel count (0 if not reported)
    double nominal_rate;      // the device's nominal rate
    uint32_t io_frames;       // device I/O buffer size in device frames
    uint32_t latency_frames;  // device + safety-offset latency, device frames (not
                              // including transport/TV/receiver delay)
    uint32_t pipeline_frames; // frames this layer's own test tap adds to the queue's
                              // pull-ahead (0 in the game; see the render probe)
} OsAudioRoute;
int os_audio_output_route(OsAudioOutput *output, OsAudioRoute *route);
// TEST ONLY - never called by the game. Installed before os_audio_output_open,
// every later output stamps each submitted frame with its 1-based index in the
// output's stream (index bits 0-14 in channel 0, bits 15-29 in channel 1, other
// channels zero), records which indices a post-effects processing tap actually
// renders and when, and then renders silence. An output whose tap cannot be
// created fails to open, so stamps can never become audible.
typedef struct OsAudioRenderRecord {
    uint64_t host_ns;       // os_monotonic_ns() when the tap processed the slice
    uint64_t stamp_host_ns; // the tap timestamp's host time in the same clock, 0 if absent
    uint64_t first;         // first stamped index in this contiguous run (0: silence)
    uint32_t frames;        // frames in the run
    uint32_t output;        // 1-based sequence of the output (reopen/reset changes it)
    double sample_time;     // tap timestamp sample time for the slice
} OsAudioRenderRecord;
int os_audio_render_probe_install(uint32_t capacity);
// TEST ONLY - never called by the game. Marker mode keeps the production queue
// pipeline (no tap, which deepens it). Outputs opened afterwards run at queue
// volume 0 (set_gain is accepted but not applied) with level metering on
// (measured to be pre-volume), and submitted audio is replaced by a 10 ms
// full-scale burst at the start of each marker period k >= 1 of the output's
// stream (period_ms; 1000 = whole seconds): channel 0 always, channels 1-5
// carrying bits 0-4 of k. os_audio_output_clock records each rising edge of the
// metered level (resolution: the caller's poll rate).
typedef struct OsAudioMarkerEdge {
    uint64_t host_ns;
    double sample_time;    // queue timeline when the edge was seen
    uint32_t output;       // 1-based output sequence
    uint32_t channel_mask; // channels above half channel 0's level
    float level;           // channel 0 metered average power (linear)
    float levels[6];       // channels 0-5 metered average power
    uint32_t edge;         // 1: simple threshold rising edge (1 s markers)
} OsAudioMarkerEdge;
int os_audio_marker_install(uint32_t capacity, uint32_t period_ms);
size_t os_audio_marker_read(OsAudioMarkerEdge *edges, size_t max);
// Copies at most max records; returns the total recorded (may exceed max).
size_t os_audio_render_probe_read(OsAudioRenderRecord *records, size_t max);
// Replaces the device for timed-media outputs opened while installed. The host
// installs one while it owns the audio device exclusively (a Dolby Digital
// bitstream cannot be mixed with other PCM), so movie audio joins the host's
// own encoded mix. Each function mirrors the os_audio_output_* call of the same
// name; handles come from open. Outputs opened before a change keep their
// original backend. NULL restores the platform device. Test probes (render
// probe, marker mode) always use the platform device.
typedef struct OsAudioOutputProvider {
    void *(*open)(uint32_t rate, OsAudioLayout layout);
    int (*write)(void *output, const int16_t *pcm, uint32_t frames);
    int (*start)(void *output);
    int (*pause)(void *output);
    int (*set_gain)(void *output, float gain);
    int (*get_gain)(void *output, float *gain);
    int (*clock)(void *output, OsAudioClock *clock);
    int (*reset)(void *output);
    void (*close)(void *output);
    int (*route)(void *output, OsAudioRoute *route);
} OsAudioOutputProvider;
void os_audio_output_set_provider(const OsAudioOutputProvider *provider);
// Outputs open now (any backend). The idle callback, when set, runs on the
// closing thread each time the last open output closes; the host uses it to
// apply an output-transport change it deferred while a movie was playing.
int os_audio_outputs_open(void);
void os_audio_output_set_idle_callback(void (*callback)(void));
// macOS: `callback` runs (on a CoreAudio thread; keep it short) after the
// audio service (coreaudiod) restarts, which invalidates every device, stream
// and listener the process held. Registered once; later calls replace the
// callback. A no-op elsewhere. (Test135, the review's RC5 follow-up.)
void os_audio_on_service_restart(void (*callback)(void));

// ---------------------------------------------------------------------------
// Strings.
// ---------------------------------------------------------------------------
int os_strcasecmp(const char *a, const char *b);

// ---------------------------------------------------------------------------
// Environment. Values are copied; 0 or -1.
// ---------------------------------------------------------------------------
int os_setenv(const char *name, const char *value);
int os_unsetenv(const char *name);
// The kit's switches: `recomp_env("PIN_CLOCK")` reads RECOMP_PIN_CLOCK.  The
// prefix names the kit, never a game; a switch is unset when NULL.
const char *recomp_env(const char *name);
// Switches from a file, for a platform with no shell to set them in (the iPad
// app reads Documents/switches.txt at start). One NAME=VALUE per line, spaces
// around either trimmed; blank lines, # comments and lines without '=' are
// skipped. Returns how many were set; 0 when there is no such file.
int recomp_env_apply_file(const char *path);

#ifdef __cplusplus
}
#endif
