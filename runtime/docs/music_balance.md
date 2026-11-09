# Music space

Options > Audio > **Music space: engine** and **Music space: effects** independently offer
−2, −3 and −4 dB, live. Both default −2 dB. Accept saves, Back cancels the
preview, and leaving without Accept restores the opening values. These rows
appear only in the game's Audio menu. Saved −2/−3/−4 choices retain their values;
saved Off is ignored and uses the −2 dB default. Explicit environment overrides
win and lock edits. Their switches are `NFS_MUSIC_ENGINE_TRIM=2|3|4` and
`NFS_MUSIC_EFFECTS_TRIM=2|3|4`; an obsolete `off` override uses −2 dB.
These are constant source trims: no music boost, new ducking, EQ or panning change.
The existing game sliders, music selection, speech ducking and low-pass controls
continue to run. The effects trim includes sirens and other directional warnings;
their level is reduced along with other driving effects. The −2/−2 dB default
has passed listening; the offline proof checks routing and PCM behavior.

The premise is visible before the DirectSound stream exists. In the retail PC
trace, `SFXCTL_MasterVol::UpdateParams` (`004B59F0`, decomp
`speed_004b12b0.c:5208`, assembly `004B5A0B..004B5B19`) shapes Master, FE/IG
music, Speech, SFX and Car settings. Both car inputs read AudioSettings+0x18.
There was no separate port engine/effects trim; reducing the SFX input also
changes ambience. `SFXObj_PFEATrax::ProcessUpdate` (`004F6EA0`, decomp
`speed_004eb210.c:12360`) reads separate licensed/score/ambience volume and
filter outputs. Speech activity still enters the map at `004DDC00`
(`speed_004d7bd0.c:8051`). This feature does not change those paths or duck curves.

`NFSMixMap::ProcessMixMap` (`004F4270`, `speed_004eb210.c:8834`) calls
`UpdateMasterChannels` (`004E3160`, `speed_004df040.c:3907`), then
`MixMasterChannels` (`004E9B30`, same file:11291). The latter converts authored
levels plus controllers to packed Q15 volume, pitch and filter fields.
`music_balance.c` wraps this one function and scales only its final Q15 volume
halfwords, after the original gain clamp. A volume record can also contain
negative-flagged 3D angle copies: those bypass destinations are excluded using
the actual 3D-reference count. Each gain destination is scaled once even
if several records write it. Pitch, filters, angle and state flags retain the
original bytes. Its bounded scratch mask covers 4096 output blocks; an unknown
larger layout is left unchanged. There are no allocations in the hook.

Category IDs follow `EAXSound::GetSndBase_Object` (`004AEB10`,
`speed_004abb70.c:4745`): state bits 16–23, instance bits 11–15, object bits 4–10.
The fixture pairs compiled MXB IDs with named DYN records in all four map variants
on each platform (1040 records, 43 distinct source IDs):

| Source | State / object IDs | Trim |
|---|---|---|
| Player single Ginsu / engine, including their transmission layers | PLAYERCAR 2 / 1, 2 | Engine |
| Player shifts, turbo, nitrous, tyres, road/wind/weather, bottom-outs, reverb, whoosh, truck effects | PLAYERCAR 2 / 3–9, 11–13, 16, 18, 19 | Effects |
| AI engines, nitrous, skids | AIRACECAR 3 / 0–2, 4, 5, 7 | Effects |
| Cop engine, skids, siren | COPCAR 4 / 0, 7, 17 | Effects |
| Traffic engine, whoosh, horn, skids | TRAFFIC 5 / 0–3 | Effects |
| World objects, collision, driveby, helicopter | States 6, 7, 8, 11 / 0 | Effects |
| Truck engine and effects | TRUCK 12 / 0, 2, 14, 19, 20 | Effects |
| FE/cell/cop/radio speech, mixed NIS/moment streams and HUD | MAIN 0 / all | Untouched |
| Licensed music, adaptive score, streamed ambience and music filter | MUSIC 1 / all | Untouched |
| Unknown IDs, controllers and unclassified fields | Other | Untouched |

The subsequent Snd loop (`00823104`, `speed_0081a10a.c:9709`) accumulates
per-voice contributions into the common float planes. Its 16-frame gain-ramped
accumulator is `0082279F` (same file:9134). The master converter `00825957`
(same file:12792; `runtime/audio_limiter.c:81`) produces the combined int16 PCM.
The host cannot separate these categories after that sum. Its Catmull–Rom source
conversion (`host/audio/dsound_host.cpp`, interpolation at line 502) is unchanged.

Offline proof: `music_balance_test.py` is discovered by `offline_tests.sh` and
drives the production hook with pinned retail translated map writer, mixer and
int16 converter. It checks all nine independent trim pairs, three authored/ducked
levels, instance IDs, real 3D references, duplicate destinations, protected categories,
solo PCM dB and normalized channel-power shares. Production passes 993,473 checks,
including 9,720 exact comparisons of real 3D angle copies. Protected categories
preserve 110,592 PCM bytes exactly. Solo 0 dB becomes −1.999917 / −2.999932 /
−4.000039 dB; maximum per-channel error is 0.001047015 dB and normalized
channel-power share error is 0.000016625. Wrong defaults, missing rows, localized
labels, broken Back, missing trim, wrong routing, duplicate trim, filter trim,
wrong gain and trimmed angle copies must all fail.

Default PCM deliberately differs from the authored gain: player engine `40020010`
and player skids `40020070`, authored −4 dB, use Q15 20651 before trim and 16404
at the −2 dB default, yielding −1.999744 dB in the six-channel solo PCM fixture.
All nine retained saved choice pairs load through the production registry.
The actual application preference parser loads profiles containing the three
retired gameplay/sign keys and saved Music Off without error; a strict loader
control rejects those same four profiles and four environment override cases. Native callbacks check independent
choice cycling, preview, Accept, Back, unaccepted destruction, cancellation
rebasing and environment locks. `SetupAudio` (00529310) appends two retail
FEToggleWidgets with private Act/Draw callbacks; the existing FE lifecycle hooks
handle cancellation and acceptance. Both Options.fng and Pause_Options.fng
contain OPTION_NAME_ / OPTION_DATA_ objects 1–10; the two added rows use 8–9
with the misleading Audio Mode row hidden (9–10 if explicitly restored).
No game audio slider or save layout changes. Native appearance and navigation
still need a game check.
Tests use no audio device or game process. This does not establish perceptual
clarity or a full-game limiter response.
