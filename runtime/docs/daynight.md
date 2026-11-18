# Day and night

`NFS_DAYNIGHT=on` enables a full sun orbit and night lighting in the original
world renderer. It is **Off by default**, read at startup. Off calls the original
advance and lighting blend through one branch in each wrapper. The native pause
Video > Time Of Day slider remains the daylight control while paused.

`NFS_TEST_DAYNIGHT_HOUR=0..24`, together with On, freezes an hour for comparison.
12 is noon, 18 is dusk, 0 (or 24) is midnight. This test override takes priority
over the clock mode and slider.

The clock lives for the process, seeded once from the game's phase. Free roam
advances by `dt/150` hours (60 real minutes per day), holds while paused and
wraps at 24. Every race holds its first frame's hour. Returning to free roam
restores the roam hour, including across loading and replacement lighting
objects. A changed phase while paused sets the hour through the native slider;
other native writes do not reseed the clock.

Carbon F10 Day-night mode and Fixed hour apply live while the startup Day-night lighting master is enabled.

`NFS_DAYNIGHT_MODE=game|cycle|fixed` selects the policy (default `game`). In
`game`, career events use their authored retail daylight hour, held throughout
the event; the roam hour returns afterward. `cycle` holds the clock's current
hour in career events as in any race. `fixed` uses `NFS_DAYNIGHT_HOUR=0..24`
(default 12). Mode and fixed hour are hidden saved preferences,
`gameplay.daynight_mode` and `gameplay.daynight_hour`; there is no F10 row.
They are read at the hook after the settings registry loads saved values, with
environment overrides taking precedence. The native menu row and wider slider
range are a separate change.

Test-only `NFS_TEST_DAYNIGHT_START=0..24` replaces the initial seed;
`NFS_TEST_DAYNIGHT_RATE=<positive seconds per game hour>` replaces 150.
Both require the feature On. Invalid values use the normal seed/rate.

The later image uses the same clock step and environment mode/hour/start/rate
settings, with the test-hour override retaining priority. Its decoded race flag
holds the hour, simulation state 4 pauses it, and loading/object replacement
preserves the roam clock. Career mode is not decoded (neutral input), so authored
career-time handover is unproven there; hidden saved clock preferences remain
specific to the original image. `carbon_daynight_clock:` logs hour and inputs
once per wall second when diagnostics are enabled. The sanitizer fixture advances
5.000000000 h in 50 s at rate 10 (pinned old path: .333333333 h) and holds fixed 21.
Live fixed proof: 76 records at 21.000000000; frame 1800's clear sky box
(600,24)–(900,104) has mean Rec. 709 luma 14.502860/255 (limit 40).
Live cycle/rate 10: 9.045277779 to 15.049583464 over 60.129985 unpaused
wall seconds, advancing 6.004305685 h; 60.043056854 input-dt seconds / 10
agrees within 4.1e-10 h. Both runs have no crash/hang/stall artifact; this
verifies clock inputs and hour policy, without a performance claim.

`NFS_DAYNIGHT_TREES` (later image, On by default, `0`/`off` turns it off, only with `NFS_DAYNIGHT=on`):
the world converter tags source tree foliage `00060000` (loader effect 0); around
those draws only, VS c250/c251 carry the source treeleaves day contract
`2 x (.13 + 1.2 x D x min(max(N.L,0) + .24, 1))` with D = .6/.48/.22, blended by
the clock night as native colours are; all other draws see c251.w = 0 and the
unchanged day colour. Uses the mesh normal (tree bounds are not decoded).
`recomp/private_daynight_trees_test.py` checks the program tail.

`NFS_DAYNIGHT_GRADE` (later image, On by default, `0`/`off` turns it off, only with `NFS_DAYNIGHT=on`):
Carbon's final visual treatment samples a 3D colour grade made for Palmont (a blue
cast: the Rockport noon road goes from R/B 1.07 before it to 0.84 after it, Most
Wanted shows 1.08). With the switch on, both treatment programs (plain and enhanced)
blend that grade toward neutral (`saturate` of its input) by pixel c31.z, uploaded
just before the treatment draw (hook 0071D4E0): the day weight 1 - night while the
imported Rockport dome is selected with an overlay, else 0. Bloom, vignette,
desaturation and fades stay native; weight 0 runs the retail grade exactly.
`recomp/private_daynight_grade_test.py` checks both programs. BOOT_DIAG logs
`carbon_daynight_grade: weight=... draws day=... zero=...` every 2 s.

The chosen daylight interval is 06:00–18:00. The original phase maps linearly
from 0.05 to 0.95 over that interval and holds an endpoint outside it. Direction
comes from a continuous 24-hour orbit: sunrise and sunset are opposite horizons,
the sun goes below ground at night, and an opposing moon supplies the light
direction there. The free-roam clock advances forward through sunset.

Night factor `n` is 1 before 05:00; `1-smoothstep((T-5)/2)` over 05:00–07:00;
0 over 07:00–17:00; `smoothstep((T-17)/2)` over 17:00–19:00; 1 after 19:00.
Here smoothstep(x) is clamped `x*x*(3-2*x)`. Dusk at 18:00 has n=0.5.

After each original weather/low-sun blend, all 23 live scalar fields (sun,
ambient including its multiplier, specular, reflection/sky brightness, sky/haze,
fog scales/scatter/falloff) are mixed as `day+n*(night-day)`. Padding and other
object state survive; repeated views do not accumulate the mix. After mixing,
sun and specular RGB are multiplied by `w = min(1, abs(sunZ)/0.20)`. The chosen
height 0.20 fades the directional contribution to zero at each horizon, so the
sun/moon direction handover is continuous in its weighted contribution. Ambient,
sky and all other fields keep their mixed values. Phase at +8, tilt at +0x10
and the unit light vector at +0x180..+0x188 keep their existing meaning for the
sky. When the hour decreases on two advancing frames, phase must reset exactly .95 to .05, with n=w=1 on both sides, once per day.
That wrap is counted and logged separately; every other frame keeps the raw .075/h phase bound, while circular T, n and weighted light stay bounded throughout.
Existing world-lighting options still
read this block and apply their own colour treatment.

`NFS_DAYNIGHT_CARS=on`, together with On (Off by default, read at startup), makes
cars follow the hour. Every in-game car is lit by the fixed rig
ShaperLightsCarsInGame (.data 008F2E70: sun 0.85/0.90/1.00 x 0.83, fills x 0.40
and x 0.45), whose colours `elSetupLights` 00502130 copies to the light context
without reading the time-of-day block. After the original, for that rig only,
light 0 is multiplied by `fs = Y(sun after) / Y(sun before)` and lights 1-2 by
`fa = Y(ambient x w after) / Y(ambient x w before)` (Rec. 709 luminance),
both kept from the last blend above. Noon gives exactly 1 (unchanged). The rig's
colours, ratios and directions stay. Diagnostics add `daynight: cars sun= sky=`;
`BOOT_DIAG_LOOK_AB=carsoff` draws a shot's held copy with the rig unscaled (the
same frame Off).

In Carbon's Rockport (`carbon_daynight_cars.h`, hook 00746A00), the same
`NFS_DAYNIGHT_CARS` with On (On by default inside Carbon, `0`/`off` turns it
off; Most Wanted keeps Off by default) replaces Carbon's fixed in-game rig
(00A6CC20/CCC0/CD60: three lights x .10/.15/.05, no clock read; Carbon zeroes
sun-relative kinds) by day. A private rig holds Most Wanted's three lights
above as fixed directions taken from the clock's light vector (key = sun
raised 10 degrees, fills 21 degrees below the horizon on the far side) and
runs through Carbon's own builder. Its harmonics (Carbon's car diffuse) keep
Most Wanted's rig; the light colours, which Carbon's car pixel shader uses only
for its pow-40 x8 specular lobes, become Carbon's own key colour on the key
and 0 on the fills, as Most Wanted's car shader makes one highlight from the
key and none from its rig colours. Day weight d = 1 - night: d = 1 builds from
it alone; between, colours and harmonics mix (1-d) native + d private and
directions by luminance; night, the front end and an absent clock keep the
native call alone. Diagnostics add `carbon_daynight: cars day= key= colour=`
once. Proof: `recomp/private_daynight_cars_test.py`.

`NFS_CAR_LOCALLIGHT` (On by default, `=0` restores Off; `car_locallight.c`, registered with the same hook and run after the hour
scale, only while the clock drives): the nearest in-range scenery light of `auxlights.c` adds an independent
`RGB * falloff * max(N.L, 0)` diffuse term. One lamp is uploaded per car, preserving a distinct direction
without altering the three-light rig. Both plain and normal-map car shaders evaluate the direction from the
lamp position to the shaded point in car-local space. The lights are placed by the instance records of the scenery sections loaded now
(`materials.m` lists them); the list is rebuilt on every section load and unload. Per light: the scenery light
shader's colour weights and falloff `3 I0 / (2 d2 + I)`, faded out at the radius by `1 - d2/R2`, then 0.75 of
that, at most 0.8 lamp luminance, times the lamp level `L` below (0 by day, so noon and
Off upload zero lamp constants). With `NFS_DAYNIGHT_LAMPS=1` the loaded lamp
packs' flares (type 13) are street lights too: the other game's street-lamp light values (RGB 182,215,240,
radius 22 m), weight `1 - d2/R2`; they come and go with their pack. The entire rig and its specular stay. No lamp in range uploads zero constants; Off keeps the original shader source. Diagnostics: `car_locallight: sections= lamps= street= misplaced=`
when the list changes, `car_locallight: frame= cars= lit= lamps= nearest= added= level= car= closest= at= loaded=`
on captured frames and once a second; `BOOT_DIAG_LOOK_AB=carlightoff,carlightzero` holds a shot Off and with the
gathered colour zeroed. The lamp follows cloned body/wheel light contexts through `elRotateLightContext`;
its world position is transformed into each part's local space. Check: `runtime/car_locallight_test.py`
(`--proof` for one rectangle; `--street-proof ON.png OFF.png ZERO.png` for the fixed four-box street test).
The frozen native paint source and disabled builders are byte-pinned; GPU checks compare 983,040 FP32/BGRA8
bytes against the native shaders with zero RGB or facing-away normals (zero changed bytes), while facing
normals brighten. The frozen fill re-aim control fails preservation (0.1000 becomes 0.0000).
Held-frame `bmwm3gtre46`, midnight, 4 m beside one isolated lamp (11.09 m to its head): Off A/B/C/D
29.157/36.895/30.551/17.131; On 47.779/36.895/42.839/17.131; zero RGB equals Off over the whole frame.
A is 29.5% brighter than B; the zero control fails the A/C gains. See the labelled
[Off/On still and crops](../../run_shots/car_locallight_additive_street_lamp_4m_bmwm3gtre46.jpg).

Moon diffuse/specular RGB 0.025/0.035/0.055, ambient 0.012/0.018/0.035 × 4,
haze 0.012/0.020/0.045, haze scale 0.15, scatter 0.05, sky/distance scales 1,
and sun falloff 8 are chosen values for visual review. An optional excluded
palette supplies the dark sky and environment fields; the public source uses
a chosen fallback and builds without that include. The optional night sky is
described below. Lamps/headlight projection (J7–J10) and grading (J11) are not
implemented here. The horizon fade's appearance still needs visual review.

With diagnostics enabled, `daynight_clock:` reports once per wall second:
hour, mode, race flag, raw career mode, held state, cumulative valid `dt` and
frame count, wrap count, largest frame dt, largest single-frame circular hour change, raw phase change outside wrap frames,
night-factor change and weighted-light vector change, plus phase, n, w and
weighted light. Sampling is entirely behind `diag_on`; the existing
`daynight:` diagnostics retain their format.

`daynight_clock_test.py` compiles the production clock, hooks and settings
registry under address/undefined sanitizers. It covers 150 s / 3600 s roam,
600 s race hold, all nine mode/state combinations, pause/slider/native writes,
process lifetime, two-process settings persistence, full-day normal and 30×
sweeps with the real sky factor/moon at midnight, and eight broken controls
including noon-wrap and dusk-phase-reset variants.

Proof: `runtime/daynight_test.py` exercises the production mix/orbit/hooks under
address/undefined sanitizers and rejects separately compiled missing-ambient
and retail-swing controls. Game proof and Off comparison are recorded below.

## Verification

The following J2/J6 measurements predate the process clock and horizon weight;
their recorded unweighted dusk colours describe that baseline.

Base: `7e70e79c`. Premise: the original world driver calls advance `00769360`
before the view-dependent blends `00769580` (decomp driver lines 6954, 6969,
7036, 7164). The blend fills the live block each time; no later writer prevents
holding a fixed hour through these two wrappers. The original light readers
temporarily adjust colours and restore the block. Neither reader was changed.
The first neutral baseline line was phase 0.445, sun 0.475/0.383/0.183,
ambient 0.199/0.247/0.320 × 4, sun Z 0.884.

All five runs used race 15.1.1, no AI opponents, autodrive, lockstep at 60,
the X360 post chain, simshots 12 and 25, and registry overrides
`g_RacingResolution=0,g_FSAALevel=2,g_ShadowDetail=2,g_RoadReflectionEnable=2,g_RainEnable=1`.
The tool supplied diagnostics, scratch profiles and guarded termination.
Requested budget used: 5 runs / 380 seconds out of 6 runs / 840 seconds.
No crash, hang or stall artifact was reported. These are visual checks,
with no frame-time or performance claim.

| Run | Fixed T | Phase | n | Sun RGB in the block | Ambient RGB × W | Solar Z / light Z |
|---|---|---|---|---|---|---|
| 3 noon | 12 | 0.500000 | 0.000000 | 0.600000 / 0.480000 / 0.220000 | 0.190000 / 0.240000 / 0.320000 × 4 | +0.877583 / +0.877583 |
| 4 dusk | 18 | 0.950000 | 0.500000 | 0.377500 / 0.257500 / 0.052500 | 0.081000 / 0.104000 / 0.197500 × 4 | 0 / 0 |
| 5 midnight | 0 | 0.050000 | 1.000000 | 0.025000 / 0.035000 / 0.055000 | 0.012000 / 0.018000 / 0.035000 × 4 | −0.877583 / +0.877583 (moon) |

The table gives the first in-game block in each run. Every diagnostic pair
(`daynight:` and `daynight: rest`) was checked, including all before/after
fields: noon 10 records / 230 scalar comparisons (max error 0), dusk 8 / 184
(max error 5.0e-7), midnight 10 / 230 (max error 8.9e-16). The logged weather
input varies normally; the result always equals `before+n*(night-before)`.

Run 2 Off versus run 1 unchanged baseline, both sim 12: `refs_compare.check`
with its chase-frame thresholds passed at **0.07%** changed blocks (limit 3%),
largest block difference 24.0, mean 0.05. Only the intended chase capture was
selected; no other reference type was substituted.

Matched sim-12 JPEGs, 1144 × 758 including the label strip; the red player
BMW M3 is visible in each:

- [Noon](../../run_shots/daynight-noon.jpg), 145,945 bytes.
- [Dusk](../../run_shots/daynight-dusk.jpg), 109,418 bytes.
- [Midnight](../../run_shots/daynight-midnight.jpg), 72,260 bytes.

Offline math: 2,323 mix scalar checks, maximum error 5.96e-8; 14,297 numeric
checks including orbit and actual wrappers, maximum error 1.20e-7. Missing
ambient fails by 0.00178000331; the retail swing control gives midnight
sun Z +0.137284 instead of −0.877583, error 1.014867. The exact production
source also compiles and passes with the private palette absent from the
include search path.

The impacted selection plus Brightness ran through the offline test tool:
**107 PASS, 0 FAIL, 3 SKIP**, including daynight, lighting options, Brightness,
switches, image isolation and publication checks. Skips: the nested-sandbox
scratch proof (nested sandbox unavailable), prebuilt-install and packaged
release-artifact checks (packaged Setup materials unavailable). Source registry
and build/suite registrations were added as directly required integration.

Both game runtimes and the public target build. The recompiler reports zero compile errors and
389 covered hooks/inline dependencies. The second-image link uses 42 units;
its isolation audit reports zero original-image reads/writes, forbidden
constructors or guest collisions. All 81 generated second-image C sources
retain their pre-build SHA-256 hashes. No second-image hook, source, or data
was edited. Visual approval of the chosen moon/ambient palette remains open;
lamps J7–J10 and clock policy/continuity J12 remain unproven.

## Night sky (J6)

With `NFS_DAYNIGHT=on`, the Most Wanted sky base pass blends by the night
factor `n` toward the night sky recipe decoded from the later game's sky
effect; the two cloud-overlay passes fade their alpha by `1-n`. Noon keeps the
original base output and overlay alpha. Off uses the original shader sources
and makes no extra texture binds. The diagnostic-only solar polygon hook
forwards every sprite unchanged.

The optional source is `SKY_NIGHT_CLOUDS_02` (`bc6f56d9`), a 256×256 record
in `GLOBAL/GLOBALB.BUN`, TPK `GLOBALTEXTURESPC`, offset `0x63ed00`. It is
encoded data, three planes in one texture: **alpha = cloud density, red =
cloud shading, green = the moon's face** (blue is not read). The recipe, with
every constant as decoded from the effect's pixel program:

- dome colour falling to zero at the zenith: `(1 - z/7000) x night sky colour`;
- moon: green plane, projected flat about the moon position, times
  `glow^1.7`, `glow = min((d x 0.000477)^-0.57, 0.915)`, tint (0.99, 1, 0.92);
- clouds: density `a` = alpha x a fade toward the horizon, tiled 2.77 times
  and scrolled along (-0.14, -0.28); the sky shows through `1 - a`; cloud body
  `a x red^2 x 0.34 x (1 - glow)^4`; moonlit rim
  `smoothstep(glow x 0.357) x 200 x (a (1 - a))^3.07`.

The recipe needs the dome-local position, which the original vertex program
does not pass on. The base pass's vertex program therefore writes the view
ray (position minus `LocalEyePos`) and the game's cloud scroll into its unused
texcoord4. Four things are this port's choices, because the source dome mesh,
its clock rate and its tone chain are not decoded: the ray is placed on an
ellipsoid (height 7000, radius where the decoded cloud fade ends) with a
planar cloud coordinate; the scroll clock is Most Wanted's cloud scroll; the
moon stands on the lighting model's moon bearing at the decoded distance
(the source fixes it at (-50000, 60000, 30000)); the level is set so the
decoded dome colour equals the J2 night sky colour. The source's fog term is
not applied.

`runtime/build.sh` invokes the generator in `daynight_sky_test.py` before
checking build freshness. It writes pixels and a source/decoder digest manifest
only to `recomp/out/daynight_sky_private.inc` and `.json`. The texture is uploaded
once as RGBA8Unorm and bound directly to fragment texture/sampler **14**, with
`n`, the night sky colour, the moon position and the level in fragment buffer
**6** (two vectors). The renderer passes its Metal device explicitly; its
recording encoder does not expose a device getter. The texture/sampler binding
cache is invalidated for slot 14 after use. Binding and night-draw counting
require the selected shader to contain the replacement inputs, so a failed
replacement compilation retains the original fallback.

Missing source files or a missing named record remove the generated include.
Without it, and in every public build, shader replacement and binding return
immediately: the original sky remains darkened by J2's lighting block. A stale
private include cannot enable the texture in a public build. No new switch is
introduced, and the second game runtime does not link this unit.

The solar producer at `00503830` queues only the two type-21 sprites. It now
returns without queuing either when the existing night factor exceeds .001;
Off and daylight delegate to the original producer. Other flare producers,
including lamps and headlights, and the shared render parameters are unchanged.
The `006BE750` hook counts submitted type-21 polygons; `daynight_sky_bind`
counts actual renderer draws within that context. These original counters
include **every view**, without any position test. Separate `moon_flare`
diagnostics count all flare types 0..31 whose polygon centres are within five
degrees of the moon ray in primary view 1. They count submissions/draws, not
visible fragments or texture alpha, and do not test oversized off-centre
sprites for overlap. Both counters log every 30 frames when a sky was drawn.
The bearing fixture accepts 2/2 aligned polygons (headlight type 3 and solar
21), rejects 2/2 opposing polygons, and rejects a broken cutoff (0/2 aligned).

The moon bearing uses the phase and the light's projection onto the orbit's
horizon bearing (azimuth at time-of-day offset .0c). Float phase can round to
an endpoint on either side of sunrise/sunset, so a Z threshold alone cannot
identify the light. The horizon projection distinguishes the sun from the
opposing moon at those endpoints. The 24 h sweep covers 432,171 samples with
fine steps at 05/06/07/17/18/19 h and adjacent representable hours, across
three azimuths and tilts. Maximum angular error is 2.83583403e-6 degrees at
1.657 h. Restoring the original flip decision gives 180 degrees and 18,000
wrong bearings just before 06:00 and after 18:00 (outer sampled hours
5.99999001 and 18.00000999). Disabling solar suppression also fails the
offline producer check; its 98 half-hour/diagnostic cases preserve the ABI
and the Off path.

Offline proof (`daynight_sky_test`): 2,441 orbit/diagnostic checks, maximum
factor error 5.52e-7; 36 Metal float comparisons per fixture at `n=0/.5/1`
against an independent reference of the decoded arithmetic, maximum error
1.47e-5 (limit 1e-4). Cloud density read from the green plane (the earlier
fault) fails by 0.074; a disabled base blend fails by 0.286; constant-one
cloud alpha fails by 0.5. Missing texture preserves the original shader and
makes zero binds. An encoder device-getter mutation is rejected.

Game proof: race 10.3.1, no opponents, autodrive, null audio, lockstep 60,
fixed hours, still at sim 30 s, sky box x690-775 y160-280 of 1144×720.
Luminance, mean RGB and standard deviation of the box; draws are sky /
night / sun-flare per frame:

| Hour | main | green-plane version | this version | draws (this version) |
|---|---|---|---|---|
| noon | 93.1, 81/96/102, 10.8 | same | same (0.00 % of blocks differ) | 12 / 0 / 0 |
| dusk 18:00 | 21.3, 17/22/30, 12.6 | 15.3, 11/16/25, 6.3 | 18.7, 16/19/24, 6.3 | 12 / 4 / 0 or 1 |
| midnight | 10.2, 10/10/10, 13.3 | 9.6, 5/10/19, 0.5 | 16.3, 16/16/18, 2.3 | 12 / 4 / 0 |
| Off | 88.4, 77/91/97, 10.7 | same | same (0.30 % of blocks, limit 3 %) | 12 / 0 / 0 |

Midnight now shows soft cloud cover with gaps instead of a flat fill; the
cloud structure is fainter than the original cloud band that main keeps
(std 2.3 against 13.3 in this box). Open questions for the eye: the level and
contrast of the night clouds, the moon's place, the dusk blend.

Open sky (race 21.1.1, still at sim 20 s, midnight, box x700-900 y60-230):
main 2.4, 2/2/3, std 2.9 (black sky with the original cloud band); this
version 9.5, 8/10/12, std 2.6 (soft cloud cover, blue gap band above the
horizon, brighter toward the moon's side).

At 04:30, race 15.1.1, no AI, autodrive, fixed hour, lockstep 60 and full
detail, the first 104 fixed-night samples at frames 180..3270 give:

| Counter (frames with a nonzero count / 104) | Suppression off control | Fixed |
|---|---:|---:|
| Original solar GPU draws, all views | 104 | 0 |
| Original solar polygon submissions, all views | 104 | 0 |
| Primary-view flare centres on the moon bearing, any type: GPU / polygons | 0 / 0 | 0 / 0 |

The producer's two exclude-view masks, `0x000000fe` and `0x00fc00f6`, both
exclude primary view 1 (`RenderSunAsFlare`, 00503830). The old all-view counter
therefore did **not** establish that a sprite covered the main-view moon.
This counter does not cover the independent screen-space SunInfo producer
`006D04E0`. Its four additive quads are now suppressed by a second night-only
hook (return plus one argument, `ret 4`). The 04:30 type-21 comparison alone
did not isolate this producer; the matched 04:00 draw sequence below does.
The earlier 45/104 count remains unreproduced. The fixed count run also sets
`g_OverBrightEnable=0`; the suppressed run with overbright on and a draw dump
has 0/93 periodic samples before entering a sky-free section. A menu-preset
run paused before its requested simshot and is excluded from the still proof.

**Washed face: decoded input and per-pass isolation.** In the sim-25 sky draw,
base pass `IDI_SKYBOX_FX/world/p0`, draw 4217966, the moon ray projects to
(1962.39, 663.34) in the raw 3840x2160 target. Drawshot 4217973 shows the disc
and upper face **before scenery and post-processing**. At (1930,640), the
shader equation predicts (0.94854, 0.95522, 0.93657); the capture is
(242,244,239)/255. The decoded cloud-rim term supplies 0.62822 of each
channel. At the nearby bright face pixel (1950,670), it supplies 0.68659;
total shader RGB is (1.05237,1.06009,1.03899), already clipped by the scene's
8-bit target. This is the moon, rather than a surviving solar sprite.

At the projected centre (1962,663), sky RGB is (249,251,246)/255. Its four
bilinear green texels are 249/251/250/254: **max 254, mean 251**; sampled green
is 0.98543 at UV (0.49620,0.49648). The complete green plane is 228..255,
mean 250.66884. The sampler reads encoded data as RGBA8Unorm, without an sRGB
conversion. The strong rim (0.63492 at the centre) and near-white face input
leave little face contrast. Evaluating only removal of that rim in the same
shader equation moves centre luminance **0.98250 -> 0.34758**; rim contrast
**0.20754 -> 0.17695**. This arithmetic isolation is not a rendered rim-off
run; it does not show a corrected face. The recipe, including the rim's gain
200, matches the decoded source; its emission level is the chosen night-sky
green / .04, which is 1 here, versus the source's nominal output level .5.
The previous shared emission level `.5 * night.sky[1] / .04` prevents this
clipping but leaves a dim moon. The current optional look separates moon
emission (`3.2 * night.sky[1] / .04`) from cloud/rim emission, which stays at
`.5 * night.sky[1] / .04` (equivalently `12.5 * dn[0].z` in the shader).
After cloud occlusion and rim addition, a hue-preserving sky-only shoulder
maps the maximum channel `p > .7` to `.7 + .25 * (1 - exp(-4 * (p - .7)))`,
then scales RGB by that value / `p`. It is continuous with slope 1 at .7 and
approaches .95, so bright configurations keep headroom in the unorm target.
This is a chosen port look, not an X360 night-moon recipe. Day-night remains
Off by default; Off forwards both solar producers, returns before texture
binding and preserves the original n=0 shader output byte-for-byte.

For the held post passes below, centre is (1962,663), rim is (1962,605),
Y = .2126 R + .7152 G + .0722 B in byte units, and rim contrast is
(Ycentre - Yrim)/(Ycentre + Yrim). Each variant holds the same sim-25 frame;
the equilibrium exposure override is 1.986 instead of 4.590 (`postfx.log`,
frames 1674..1685; `x360_post.m` builds exposure as
`2 * middle_gray / (adapted + epsilon)`).

| Held post pass | Centre Y / 255 | Rim contrast |
|---|---:|---:|
| Normal | 241.9278 | .069708 |
| Bloom removed | 255.0000 | .101264 |
| Tone curve removed | 255.0000 | .000000 |
| Adaptation replaced with equilibrium exposure | 99.9278 | .029906 |
| Entire composite bypassed (scene input) | 255.0000 | .057273 |
| Speed blur removed | 242.9278 | .047519 |
| Depth of field removed | 239.9278 | .086928 |
| Local curve averaging removed | 243.9278 | .044605 |
| Vignette removed | 243.9278 | .045730 |
| Fixed-pixel curve averaging | 237.9278 | .112041 |

Exposure produces the largest centre change among the rendered single-stage
post controls (**241.9278 -> 99.9278**, contrast **.069708 -> .029906**), but
flattens this rim measurement and does not restore face detail. Bloom removal
and the identity tone curve worsen saturation. With the PC chain, the registry
overbright comparison is **255 -> 254**, contrast **.306023 -> .140237**;
these are matched sim-time runs, not a held-frame pair, and the held X360
baseline itself drifts by 2 luminance bytes between runs. Overbright off does
not recover the face. These earlier 04:30 controls are confounded by the separate solar screen
stage in their scene input. No global post shader or exposure change is made.

**Final-frame moon proof (04:00).** Race 15.1.1, no AI, autodrive, fixed
hour, lockstep 60, full-detail registry and sim 25 s. The moon is wholly in
open sky above the hillside and between the trees. The 3440x2160 captures
have identical recorded sky projection, light vector and cloud scroll
(.499006); the projected centre is (1635.63, 462.44).

Draw 4162702 still contains the grey disc; by 4162745 the SunInfo screen
quads have covered it in yellow. The intervening `006D04E0` reads SunInfo and
SunTextures and draws four additive quads through `006D1260` (return site
`006D090F`), before the X360 composite. This identifies the dominant halo
producer; changing bloom threshold, sky haze or adaptation is unnecessary.
A first screen-suppressed capture with shared gain .85 was still dim
(inner-face mean Y 95.09 moving / 106.26 held), so the final gain separates
moon from clouds and applies the bounded shoulder described above.

Measurements use Y=.2126R+.7152G+.0722B in byte units, on the full-resolution
PNG, before JPEG resizing. The face projection gives radius
`rho = length(2 * (moon_uv - .5))`: face SD uses rho<.7 (6,611 pixels), disc
mean/clipping uses rho<1 (13,493 pixels), inner ring .85<=rho<.95 (2,433),
outer ring 1.05<=rho<1.15 (2,974). Edge contrast is inner mean minus outer
mean. The masks are identical before/after and contain no terrain or trees.

| Final-frame measurement | Shared gain .5 + screen SunInfo | Moon 3.2 / clouds .5 + screen SunInfo suppressed |
|---|---:|---:|
| Moving final: disc-edge contrast | 13.5175 | 19.0891 |
| Moving final: face SD(Y) | 27.6863 | 21.1028 |
| Moving final: disc mean Y | 143.3347 | 176.3977 |
| Moving final: inner-face mean Y | 148.2114 | 182.5226 |
| Moving final: clipped share (any RGB channel=255) | 0% | 0% |
| Held normal X360 final: disc-edge contrast | 15.5585 | 23.0910 |
| Held normal X360 final: face SD(Y) | 26.0031 | 9.9609 |
| Held normal X360 final: disc mean Y | 149.6157 | 190.6548 |
| Held normal X360 final: clipped share | 0% | 0% |
| Broken constant-white face SD(Y) | 0 | 0 |

The held normal view runs the complete X360 composite, with no stage
bypassed; it exposes the facial markings without the moving capture's
remaining temporal softness. The old high SD includes uneven solar glare
and is not evidence of better face detail. The JPEG includes **both final
views**, plus wider sky; no isolated pre-post image is substituted for a
final frame. [Before/after](../../run_shots/moonface-0430.jpg): 1920x1980,
376,531 bytes. Its retained filename refers to the previous 04:30 proof;
the current labels and measurements are 04:00.

Three guarded runs / 490 requested seconds were used. Capture-induced
warnings were retained: before hang_1/stall_1 at frame 1686, candidate at
1675, final at 1686; all resumed (last frames 11480 / 5652 / 7323). Current
hang stacks are PNG encoding / diagnostic GPU readback, not a new game
hang. The earlier main-gain and half-gain runs each had hang_1 and hang_2
beginning `hang: no frame for 5 s after frame 1673` / `...1675`, with
`frame 1673` / `frame 1675` next; their stall_1 lines were respectively
`stall: no frame for 500 ms after frame 1673 (528 ms since it)` and
`stall: no frame for 500 ms after frame 1675 (535 ms since it)`. Those
stacks are also in `diag_png` / `save_png` / PNG filtering. These are the
known heavy draw-capture stalls, present on main as well.


## Process-clock verification (J12a)

The clock fixture runs 1,116,283 numeric checks under address/undefined
sanitizers. A race holds exactly for 600 s; 150 s of free roam advances 1 h,
and 3600 s completes a day. Both 216,001-frame normal-rate and 7,201-frame
30× sweeps include the frame after midnight. Circular T, raw phase outside
midnight, n and weighted light stay within slope × step × 1.05 limits.
Each sweep has exactly one midnight reset with phase .949999988 to .050000001,
n=w=1 and real sky factor 1 at both endpoints. Real sky moon steps are
2.4334558 / 73.0130341, below 2.55543255 / 76.6629765 at 150 / 5 s/h.
All eight broken controls fail: no-freeze, no-weight, no-wrap, ignored-career,
event-identity, pinned-base-driver, noon-wrap and dusk-phase-reset. The last
two fail the phase rule itself: noon phase .949999988 to .500008285, n 1 to 0;
dusk raw phase step .899991632 exceeds 8.75e-6. The real settings registry
saves/reloads fixed 15.75 in two fresh processes.

The latest accelerated guarded run logs 29.343000592 h of advancement in
153 clock records, all race=0/career=2. It crosses both handovers and one
midnight with phase .949999988 to .050000001, n=w=1, and sky n=1 on both
bracketing records. Max T/phase/n/weighted-light steps are
.0108888894/.000604167581/.0034712553/.0125086108, all below the bounds
scaled by max dt=.0544444472. Whole logged sum_dt/wall=.999299049851.

The latest normal-rate sunset run has 127 consecutive held=0 records,
spanning 127.546759 s with rate error 3.4e-10 h against summed dt/150.
Its first interval of at least 60 s spans 60.133734 s, with error 2.53e-10 h
and sum_dt/wall=.968586511890. All step bounds pass, including 18:00.
Whole logged sum_dt/wall=.985070134154. Both runs have no crash/hang/stall
artifact. These ratios describe clock inputs; no performance claim is made.

Earlier guarded race proofs remain: Cycle has 41 records at T=11.304499626,
spread 0; Fixed midnight has 38 records at T=0 exactly, raw career mode 0.

Lapse stills use the nearest 300-frame captures; the footer reports approximate
interpolated time. The player BMW M3 GTR is visible in each:

- [Dawn, about 06:09](../../run_shots/dnclock-lapse-06.jpg).
- [Noon, about 11:55](../../run_shots/dnclock-lapse-12.jpg).
- [Dusk, about 17:45](../../run_shots/dnclock-lapse-18.jpg).
- [Midnight, about 00:03](../../run_shots/dnclock-lapse-00.jpg).

The earlier live no-freeze control drifts .227212966 h against a .2 h
rejection threshold. The latest noon-wrap control also fails as intended:
phase .949999988 to .500162482 and n 1 to 0 at the alleged wrap, while the
next sky record switches to n=0. Its largest circular T step is 11.9975 h.
Production source is restored byte-for-byte and rebuilt after the control.
The latest three runs use 690 requested seconds, with no crash/hang/stall
artifact. The four older lapse pictures remain reference captures.

Live career-event handover, online races, the other game, a 10-minute soak,
frame cost and visual approval remain unproven. The feature remains Off.

## Optional lamp bake

`NFS_DAYNIGHT_LAMPS=1` (Off by default, restart required) adds private generated Rockport section flare packs through LoaderLights; missing packs/public builds keep the original world, and these flares currently remain lit during the day.

`NFS_DAYNIGHT_BAKE=/path/to/bake-folder`, with `NFS_DAYNIGHT=on`, enables
linear RGBA8 lamp colours on original static world buffers. Restart after a
change. The folder must contain a validated `index.json` and its DNBK section
files. An unset switch or unavailable index keeps the original shader sources
and buffer bindings. This option is Off by default.

The index generator validates mesh order, counts and position SHA-256 against
the original stream, then splits colours at native vertex-buffer boundaries.
Runtime verifies the section file digest, headers and indexed colour ranges,
and matches each buffer's position digest and vertex count. Missing, truncated
or mismatched data binds a zero stream. Linear RGB is added to the ambient-lit
colour after its day vertex-colour multiplication, scaled by the live night
factor and `DN_LAMP_STRENGTH` (initially 1). There is no occlusion.

Sections upload on first use and remain cached for the process; GPU colour
storage is exactly four bytes per vertex in those sections, up to the city
bake's full size. Buffer lookups are cached by Metal buffer identity and guest
write generation, size, stride and address. Diagnostic capture-frame lines
report bound/missing/mismatched draws; section lines report vertices, bytes and
cumulative bytes held. Production binding and the index parser have offline
order, key and truncated-file controls. A parked 21.1.1 capture with bake-projected
40x20 road rectangles measures 2.0835x midnight pool and pool/dark gain, unchanged
dark-road luminance, and 0% noon difference. A midnight zero-night-factor control
fails both gain thresholds. The material profile preserves wet-road coordinates;
unlit road interiors outside the rectangles remain pixel-identical. Appearance
remains under review.

## Car lamps (J7b)
Retail lights every car's headlights in all play (`PVehicle::OnTaskFX` 006703B0 turns them on every tick). With
`NFS_DAYNIGHT=on`, `daynight_lamp_level()` (`daynight.c`) gives L = min(1, 2n): 1 from 18:00 to 06:00, 0 from 07:00
to 17:00, 1 whenever the clock is not driving. `daynight_carlamps.c` (from `mirror.c`'s `f_00505380`, call site
0074323A only) scales the L/R headlight flares and the unbraked L/R tail flares by L and skips them at 0; braked tail
lamps, reverse, cop lights and cutscenes stay retail. The headlight projection (`headlights.m`) scales lamp strength
by L and prepares nothing at 0. Lamp textures stay `_ON`: none of the 92 retail car packs carries the
`<car>_KIT00_HEADLIGHT_OFF` / `_GLASS_OFF` textures (one `BMWM3_STYLE03_HEADLIGHT_OFF` only).
BOOT_DIAG: `daynight_carlamps: frame=N L= head_in= head_out=` on each captured frame; check
`runtime/daynight_carlamps_test.py --proof`.

## Lamp streaks

`NFS_FLARE_STREAKS` (On by default, `=0`/`off` disables, restart required, only with `NFS_DAYNIGHT=on`; `flare_streaks.c`, from
`daynight_sky.c`'s Render 006BE750 hook) stretches the world section lamp flares that eRenderWorldLightFlares
00505E80 draws in the main views along their screen motion, after the later game's streak_flares vertex program:
head at the lamp, tail towards the previous frame's projection, tail colour down to x0.5. Length = screen velocity
x 1/30 s x night factor (car lamp level L), so it does not depend on the frame rate and is 0 by day; a camera cut,
a skipped frame or a frame over 0.1 s draws the retail billboard. Car, cop and pool flares keep their billboards.
Retail MW has no world section flares (0 records), so the switch changes nothing until the optional lamp bake is
present. BOOT_DIAG: `flare_streaks: frames A..B world_lamp_polys= other_flare_polys=` and the longest streak per
600 frames; `BOOT_DIAG_STREAK_SHOTS=<px>` shoots up to 8 frames with a streak of at least that length wholly on
screen, clear of the lap timer and top bar, and logs `flare_streaks: shot frame= sim=` for a matched Off launch. Check: `runtime/flare_streaks_test.py`.

Carbon world pools remain Off; authored linear RGB8/255 receives Y-preserving 15% D65 XYZ adaptation; `BOOT_DIAG_LOOK_AB=lamplightoff,lamplightshift` redraws held pools Off / shifted 40 m on world X.
