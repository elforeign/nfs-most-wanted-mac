# Windows DirectSound PCM slice

Premise: a synthetic DSOUND.dll!DirectSoundCreate at IAT 00401000 reaches
win_import_stop in the unregistered dispatcher: unsupported import, exit 2,
esp 00010000. Registration changes that same call to a successful COM object.

Reference locations: runtime/dsound.c:493 supplies the Mac COM registration;
host/audio/dsound_host.cpp:188 routes channels, :1232 creates the shared mixer,
and :1638 implements wrap locks. runtime/docs/audio.md:10 describes the larger
Mac output/features contract; media/media.cpp:5 includes the CoreAudio endpoint.
Those Mac feature integrations are outside this PCM slice.

The Windows bridge registers named and ordinal create/capture imports, with
IDirectSound8, IDirectSoundBuffer8 and IDirectSoundNotify vtables. Buffers live
in checked guest memory. Format, wrap-lock/unlock, play/stop, seeking, cursors,
volume, pan, frequency, duplication and notifications call the existing mixer.
Notification interfaces pin their buffers; duplicates pin their source. Call
adapters preserve the guest registers and perform exact stdcall stack cleanup.
The bounded guest bump arena retains guest allocations until VM teardown,
matching the current Windows runtime allocator; native mixer objects are freed.

Native PCM uses media::AudioOutput over a WinMM waveOut endpoint, with six
interleaved IEEE float channels ordered FL FR FC LFE BL BR, 48000 Hz and mask
0x3f. Submitted headers own copied samples through completion/reset. The shared
mixer uses the endpoint queue depth for pacing and audible cursor estimation.
NFS_AUDIO=pcm51/auto selects it; null/off uses the shared wall-clock null sink.
Unsupported encoded/stereo choices return DSERR_UNSUPPORTED. An injected
endpoint supports manual deterministic pumping or an output thread in fixtures.
Callbacks acquire the guest GIL to signal registered events. Stop, render and
final output-thread joins release the GIL so callbacks and guest waits progress.

recomp/windows_audio_test.py runs without devices: original import stop,
107 COM/import calls, 78 sample assertions, five event notifications, native
format/header/queue checks, output failure handling and COM ownership. Wrong
wrap pointers, channel order and queued-frame metadata each fail deliberately.
The Mac Mach-clock and Windows nanosecond-clock paths produce 408 identical
sample bytes. Freestanding and hosted COFF checks classify every dependency;
removing the endpoint exposes one unknown symbol and fails both audits.

SDK-free audio_compile.h/ is a local C++ compile model, never a runnable STL ABI.
SDK builds use real headers and resolve extra C++ symbols against definitions
in the explicitly linked CRT archives; arbitrary unresolved names still fail.
The desktop link adds winmm.lib and libcpmt.lib. Native SDK/link execution and
physical output remain unverified by Mac fixtures.

Follow-ons: Dolby Digital, DD+, TrueHD, stereo folding/endpoint negotiation,
hardware/default-device recovery, native latency validation, capture, 3D/FX COM,
movie delivery, and the Mac positional/music/speech/reverb/watchdog integrations.
No game run or physical audio device is used by the fixture.
