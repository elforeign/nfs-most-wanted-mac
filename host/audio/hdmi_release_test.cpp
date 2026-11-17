// hdmi_release_test.cpp - a Dolby passthrough must give the HDMI device back in PCM when the game exits.
// The passthrough switches the device's stream to an encoded physical format ('mtct' for TrueHD) under hog mode; hog
// mode ends with the process, the format does not, and the system mixer's PCM then never reaches the TV in a form it
// plays. Each case runs in a child process that ends through exit() (every quit a player can reach: the menu's Quit,
// Cmd-Q, the window's close button and Restart game all end in ExitProcess -> exit -> atexit), and the parent reads the
// stream's format afterwards from a fake CoreAudio property layer kept in shared memory. No CoreAudio device is
// opened: every HAL and output-unit call in media.cpp is redirected to the fake below.
//   fixed:                                         11 checks / 0 failures (exit 0)
//   old close_all_at_exit (d->ready not closed):   11 checks / 4 failures (exit 1): the quit during a reopen leaves
//                                                  the stream in 'mtct' 768000 Hz, mixing off (the fake keeps the
//                                                  hog too; the real HAL drops it with the process, not the format)
#include <AudioToolbox/AudioToolbox.h>
#include <CoreAudio/CoreAudio.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <cstdio>
#include <cstring>

namespace fake_hal {
constexpr AudioObjectID kTv = 0x7FA0, kStream = 0x7FB0;
struct State {
    pid_t hog;
    UInt32 mixing;
    AudioStreamBasicDescription phys;
    int ioprocs;
    int starts;      // passthrough IOProcs started: proves a case really opened one
};
State *st;   // MAP_SHARED: what a child leaves behind is read by the parent
AudioStreamBasicDescription pcm() {
    AudioStreamBasicDescription f = {};
    f.mSampleRate = 48000; f.mFormatID = kAudioFormatLinearPCM; f.mChannelsPerFrame = 2; f.mBitsPerChannel = 24;
    f.mFormatFlags = kAudioFormatFlagIsSignedInteger; f.mFramesPerPacket = 1; f.mBytesPerFrame = f.mBytesPerPacket = 8;
    return f;
}
AudioStreamBasicDescription mat() {
    AudioStreamBasicDescription f = {};
    f.mSampleRate = 768000; f.mFormatID = 'mtct'; f.mChannelsPerFrame = 2; f.mBitsPerChannel = 16;
    f.mFramesPerPacket = 1; f.mBytesPerFrame = f.mBytesPerPacket = 4;
    return f;
}
template <class T> OSStatus put(const T &v, UInt32 *size, void *dst) {
    if (*size < sizeof v) return kAudioHardwareBadPropertySizeError;
    memcpy(dst, &v, sizeof v); *size = sizeof v; return noErr;
}
OSStatus size(AudioObjectID o, const AudioObjectPropertyAddress *a, UInt32, const void *, UInt32 *n) {
    if (o == kTv && a->mSelector == kAudioDevicePropertyStreams) { *n = sizeof(AudioStreamID); return noErr; }
    if (o == kStream && a->mSelector == kAudioStreamPropertyAvailablePhysicalFormats) {
        *n = 2 * sizeof(AudioStreamRangedDescription); return noErr;
    }
    return kAudioHardwareUnknownPropertyError;
}
OSStatus get(AudioObjectID o, const AudioObjectPropertyAddress *a, UInt32, const void *, UInt32 *n, void *dst) {
    const AudioObjectPropertySelector s = a->mSelector;
    if (o == kAudioObjectSystemObject && s == kAudioHardwarePropertyDefaultOutputDevice) return put(kTv, n, dst);
    if (o == kTv) {
        if (s == kAudioObjectPropertyName) { CFStringRef name = CFSTR("fake HDMI TV"); return put(name, n, dst); }
        if (s == kAudioDevicePropertyDeviceIsAlive) return put((UInt32)1, n, dst);
        if (s == kAudioDevicePropertyStreams) return put(kStream, n, dst);
        if (s == kAudioDevicePropertyHogMode) return put(st->hog, n, dst);
        if (s == kAudioDevicePropertySupportsMixing) return put(st->mixing, n, dst);
    }
    if (o == kStream) {
        if (s == kAudioStreamPropertyPhysicalFormat) return put(st->phys, n, dst);
        if (s == kAudioStreamPropertyAvailablePhysicalFormats) {
            AudioStreamRangedDescription r[2] = {{pcm(), {48000, 48000}}, {mat(), {768000, 768000}}};
            if (*n < sizeof r) return kAudioHardwareBadPropertySizeError;
            memcpy(dst, r, sizeof r); *n = sizeof r; return noErr;
        }
    }
    return kAudioHardwareUnknownPropertyError;
}
OSStatus set(AudioObjectID o, const AudioObjectPropertyAddress *a, UInt32, const void *, UInt32 n, const void *src) {
    const AudioObjectPropertySelector s = a->mSelector;
    if (o == kTv && s == kAudioDevicePropertyHogMode && n == sizeof(pid_t)) { memcpy(&st->hog, src, n); return noErr; }
    if (o == kTv && s == kAudioDevicePropertySupportsMixing && n == sizeof(UInt32)) { memcpy(&st->mixing, src, n); return noErr; }
    if (o == kStream && s == kAudioStreamPropertyPhysicalFormat && n == sizeof st->phys) { memcpy(&st->phys, src, n); return noErr; }
    return kAudioHardwareUnknownPropertyError;
}
OSStatus listener(AudioObjectID, const AudioObjectPropertyAddress *, AudioObjectPropertyListenerProc, void *) { return noErr; }
OSStatus create_proc(AudioObjectID, AudioDeviceIOProc, void *, AudioDeviceIOProcID *id) {
    *id = (AudioDeviceIOProcID)0x1; return noErr;   // never called back: the test only follows the device's state
}
OSStatus start(AudioObjectID, AudioDeviceIOProcID) { ++st->ioprocs; ++st->starts; return noErr; }
OSStatus stop(AudioObjectID, AudioDeviceIOProcID) { --st->ioprocs; return noErr; }
OSStatus destroy_proc(AudioObjectID, AudioDeviceIOProcID) { return noErr; }
AudioComponent no_component(AudioComponent, const AudioComponentDescription *) { return nullptr; }   // no PCM unit
}  // namespace fake_hal

#define AudioObjectGetPropertyData fake_hal::get
#define AudioObjectGetPropertyDataSize fake_hal::size
#define AudioObjectSetPropertyData fake_hal::set
#define AudioObjectAddPropertyListener fake_hal::listener
#define AudioObjectRemovePropertyListener fake_hal::listener
#define AudioDeviceCreateIOProcID fake_hal::create_proc
#define AudioDeviceStart fake_hal::start
#define AudioDeviceStop fake_hal::stop
#define AudioDeviceDestroyIOProcID fake_hal::destroy_proc
#define AudioComponentFindNext fake_hal::no_component
#include "../../media/media.cpp"
#ifndef HDMI_RELEASE_SOURCE
#define HDMI_RELEASE_SOURCE "dsound_host.cpp"
#endif
#include HDMI_RELEASE_SOURCE

static int checks = 0, failed = 0;
static void check(bool ok, const char *what) {
    ++checks;
    if (!ok) { ++failed; printf("FAIL: %s\n", what); }
}
static void show(const char *when) {
    printf("  %s: stream %s, hog %d, mixing %u, IOProcs running %d\n", when, media::format_text(fake_hal::st->phys).c_str(),
           (int)fake_hal::st->hog, (unsigned)fake_hal::st->mixing, fake_hal::st->ioprocs);
}
// Runs `body` in a child that then ends with exit(0) after registering the exit handler, as ds_device_create does.
template <class F> static bool in_child_then_exit(F body) {
    fflush(stdout);
    pid_t pid = fork();
    if (pid == 0) {
        atexit(close_all_at_exit);
        body();
        exit(0);
    }
    int status = 0;
    return pid > 0 && waitpid(pid, &status, 0) == pid && WIFEXITED(status) && WEXITSTATUS(status) == 0;
}
static void reset() { *fake_hal::st = {-1, 1, fake_hal::pcm(), 0, 0}; }
static void expect_released(const char *what) {
    char line[160];
    snprintf(line, sizeof line, "%s: the stream is PCM again", what);
    check(fake_hal::st->phys.mFormatID == kAudioFormatLinearPCM && fake_hal::st->phys.mSampleRate == 48000, line);
    snprintf(line, sizeof line, "%s: hog mode released", what);
    check(fake_hal::st->hog == -1, line);
    snprintf(line, sizeof line, "%s: mixing back on", what);
    check(fake_hal::st->mixing == 1, line);
    snprintf(line, sizeof line, "%s: IOProc stopped", what);
    check(fake_hal::st->ioprocs == 0, line);
}

int main() {
    unsetenv("NFS_AUDIO_FAKE_DEVICE");   // the real passthrough code, on the fake property layer above
    void *mem = mmap(nullptr, sizeof(fake_hal::State), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANON, -1, 0);
    if (mem == MAP_FAILED) return 2;
    fake_hal::st = (fake_hal::State *)mem;

    // 1. Quit with the TrueHD output playing (the usual quit).
    reset();
    bool ran = in_child_then_exit([] {
        auto *d = new DsDevice;
        d->cfg.sink = DS_SINK_DEVICE;
        d->out = open_output(DS_OUT_TRUEHD, 48000);
        printf("  quit while playing: passthrough %s\n", d->out ? "open" : "FAILED to open");
        show("before exit");
        if (!d->out || fake_hal::st->phys.mFormatID != 'mtct') _exit(4);   // the precondition: the stream is encoded
        shared().live.push_back(d);
    });
    check(ran && fake_hal::st->starts == 1, "quit while playing: child opened the passthrough and exited normally");
    show("after exit");
    expect_released("quit while playing");

    // 2. Quit while the output is being reopened (the launch reopen in the first seconds, a TV that woke, a device
    // change): the opener finishes its open after the mixer thread stopped and leaves it in d->ready.
    reset();
    ran = in_child_then_exit([] {
        auto *d = new DsDevice;
        d->cfg.sink = DS_SINK_DEVICE;
        d->cfg.auto_mode = 0;
        d->want_mode = DS_OUT_TRUEHD;
        shared().live.push_back(d);
        open_in_background(*d);      // exit() follows at once: the exit handler joins the opener
    });
    check(ran, "quit during a reopen: child exited normally");
    check(fake_hal::st->starts == 1, "quit during a reopen: the opener opened the passthrough");
    show("after exit");
    expect_released("quit during a reopen");

    printf("%d checks / %d failures\n", checks, failed);
    return failed ? 1 : 0;
}
