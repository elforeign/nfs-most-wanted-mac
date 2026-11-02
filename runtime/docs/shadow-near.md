# Dense near shadow fit

The fitter and experimental shadow path are Mac-only; Windows retains its original shadow renderer without either addition.

The experimental renderer retains the original far pass and adds one independent
near caster pass for world/road hardware receivers. `NFS_SHADOW_NEAR` defaults Off.
The numerical fitter and its S2 measurements are described below; rendering,
allocation, pose diagnostics and remaining screen proof follow them.

`shadow_near.h` is a C-compatible, caller-owned contract. `shadow_near_fit` is
implemented beside the existing fit in `x360_shadow_fit.cpp`; no internal player
state, allocation, settings, or renderer dependency is introduced. Separate
players can interleave calls without influencing each other.

The camera envelope supplies near/far distance and maximum horizontal/vertical
projection slopes. A sphere of radius
`D * sqrt(1 + tan_x^2 + tan_y^2)` contains every yaw/pitch/roll of that frustum.
The caller supplies a fixed receiver box in a fixed, right-handed orthonormal
light basis, with each half extent at least that radius. `light_depth` points
away from the light. The caller must widen the declared envelope before a camera
mode/FOV leaves it. Neither the basis nor the footprint follows per-frame yaw.
A light/environment change starts a new fit epoch with a new explicit basis.
The supplied depth direction must match the selected far map's actual shadow
light, including the retail elevation clamp when using the X360 fit.

The independent near resolution is checked against a supplied device dimension
limit and D32 payload budget. Both 4096 and 8192 are supported; this does not
change `NFS_SHADOW_SIZE`. The budget is not an allocation guarantee: the render
integration must include stencil, alignment, colour masks and other attachments.
8192 D32 alone is 268,435,456 bytes (256 MiB); 4096 is 64 MiB.

For each light-plane half extent `H`, guard `g`, blend `b`, resolution `N`, the
outer half extent is `H / (1 - 2*(g+b+1)/N)`. The extra texel reserves half a
snap and the final half-texel receiver convention. Pitch is twice the outer half
extent divided by N. The centre is snapped relative to the supplied world origin
with parity phase `(N mod 2)/2`; this preserves world-origin texel-centre phase
for even and odd resolutions. Subtexel moves within one cell leave the lattice
unchanged. Crossing a cell changes the lookup by exactly one texel. This is not
a promise of invariance for moves crossing a snap boundary.

The caller must supply conservative world bounds for **all contributing
casters**, including off-screen/upstream alpha-tested geometry. The fit unions
all eight caster-corner depth coordinates with the receiver box's depth extent,
adds positive padding, and quantizes the interval outward. A sufficiently large
quantum keeps local depth bounds stable; crossing a quantized boundary can change
the depth mapping. No shared near/far depth interval is assumed. Geometry outside
the near XY footprint can be clipped during caster rasterization; depth culling
must use the returned interval rather than the receiver box alone. The fitter
cannot detect a caller omitting a caster from the input bounds.

All matrices are row-major for row vectors, computed in double precision.
`world_to_clip` is the caster projection: XY in [-1,1], Z in [0,1].
`world_to_texture` includes the final receiver bias exactly once: X scale +.5,
Y scale -.5, translation `.5 + .5/N` on both axes. `usable` is the outer valid
UV rectangle; `blend` is the inner full-near rectangle. Between those rectangles,
the receiver integration must smoothly blend visibility with the existing far
map. Outside `usable`, it must use far visibility. Strength/fade composition
belongs after visibility blending. Filtering is specified in near-map texels.

The input `world_to_far_texture` must be the existing **final** receiver matrix,
including the far map's own bias and size. The returned transform is
`inverse(world_to_far_texture) * world_to_texture`. Multiply a complete far
homogeneous vec4 by this transform, then divide near XYZ by the resulting near W.
If far XYZ was already divided, pass `(farXYZ, 1)`; homogeneous scaling cancels.
Never preserve far Z/W, treat the transform as an affine UV scale/offset, or add
another half texel. Near/far depth ranges can differ, including reversed far Z.
The integration must explicitly convert double matrices to renderer precision;
the reported double errors do not establish float/GPU precision at large world
coordinates.

Invalid/nonfinite inputs, malformed bases/bounds, insufficient receiver coverage,
singular/ill-conditioned far matrices, and exceeded device/budget limits return zero and clear
the result. The fit is pure; input and result must be non-overlapping objects.

## Numerical evidence

`shadow_near_fit_test.py` pins the complete reviewed predecessor production source
and its shadow constants as literal text with independent SHA-256 hashes. The old
single-map adapter executes its actual full-range 70 m/1600 fitter; it does not
pretend a missing near API is a compilation failure. Both production versions
compile and execute through the same pair contract. The new adapter retains the
retail LiSPSM result and calls the independent near fit. The old adapter exposes
its one selected projection to both consumers, as the rejected prototype did.
Retail far results also compare exactly across 216 pinned/current camera cases.

The fixture is a numerical horizontal camera at (0,0,1), looking +Y, 3840x2160,
projection scales .845690625/1.50345, light (-.467,-.464,.753). It is **not a
captured pose**. The 1,089 positions span screen NDC X [-.8,.8], lower-screen
Y [.55,1], with road distance 1.50345–2.73355 m. Stride is the larger reciprocal
row norm of the 2x2 screen-to-shadow-texel Jacobian: spacing perpendicular to each
texel contour. It is not a singular-value bound for every possible contour.
Analytic quotient-rule derivatives agree with central finite differences.

| Projection | Footprint width | Pitch | Stride min / median / p95 / max (px) |
| --- | --- | --- | --- |
| Pinned single-map stable, 70 m / 1600 | 238.794104 m | 149.246315 mm | 50.775110 / 146.215521 / 250.438911 / 301.042965 |
| Near sphere, illustrative 3 m / 8192 | 10.137253 m | 1.237458 mm | .420996 / 1.212328 / 2.076484 / 2.496062 |
| Same near envelope, 4096 | 10.162124 m | 2.481 mm | .844057 / 2.430605 / 4.163157 / 5.004371 |

The near example has guard 1, blend 8 and depth [-64,48] m from supplied caster
bounds, padding 1 m and quantum 16 m. The documented design example's 1.88/2.42
p95/max uses a different sampling fixture. This explicitly pinned lower-road ROI
gives 2.08/2.50; it **does not meet** the capture target p95 <=2. Neither fixture
selects an accepted allocation. Actual bumper matrices must determine the
smallest adequate footprint and allocation.

Production checks: 81 pair checks, 216 unchanged-far comparisons, 7,210 API checks,
zero failures. The original executes 80 common checks and fails five density /
independent-footprint checks. Six production mutations fail behaviorally:
unsnapped centre 26; affine-W assumption 3,468; omitted upstream depth 30;
doubled texture-centre bias 12; shared far Z 3,468; reversed multiply order 3,468.
Worst double-precision homogeneous XYZ error is 2.73e-11, depth error 1.92e-12,
and final lattice phase error 9.31e-10 texels. The existing yaw fixture executes
both pinned and changed production: each 21,056 checks / zero failures; each
unsnapped control fails 256. Yaw stability was already passing in the predecessor.

S2 fitter-stage translation/runtime builds passed with zero compiler errors.
That stage's full offline suite passed: 214 tests in 423 seconds, with five explicit
input/host-only skips; host shader/GPU checks and publication/tree-clean pass.

## Physical edge and capture requirements

Use solar angular diameter 0.53 degrees as the physical reference. For normal
incidence, full geometric penumbra is `2*d*tan(.265 degrees)`, 9.2503 mm per metre
of blocker/receiver separation. This full support width is not a normalized
10–90% width. An ideal uniform solar disk behind a straight opaque edge gives a
10–90% width about .687 of full support; textures/translucency require separate
measurement.

Project oblique cases explicitly. For parallel blocker/receiver planes with
normal gap h, receiver normal n, unit direction l toward the light, and
`c = abs(dot(n,l))`, ray separation is `t=h/c`. Angular offsets perpendicular to l
map to the receiver through
`delta_r = t*(delta_l - l*dot(n,delta_l)/dot(n,l))`.
Full widths are `2*h*tan(alpha)/c` transverse to the incidence plane and
`2*h*tan(alpha)/c^2` along it, with alpha=.265 degrees. For a receiver-plane edge
normal e, use `2*t*tan(alpha)*sqrt(1+(dot(e,l)/dot(n,l))^2)` before projecting
through the camera Jacobian. Near grazing incidence needs the actual scene
geometry; the fitter does not introduce a broad kernel to approximate it.

The next capture must independently report:

- Normalized 10–90% contact-edge width along a registered normal cross-section,
  with lit/dark levels and blocker/receiver geometry recorded.
- Texel staircase/contour displacement against the same pose at 2N resolution;
  do not count a broadened sampling kernel as physical softness.
- Frozen 4K Off/repeated-Off/On and 2N reference, stride p95 <=2 px, thin-shadow
  contrast and area within 5%, and transition width/continuity.
- Registered 64-frame yaw sweeps and holds, noise-adjusted crawl mean/p95 improved
  at least 25% beyond repeat variation, with unsnapped, blurred and omitted-caster
  controls failing their respective checks.
- Upstream/alpha-tested coverage, split-view isolation, allocation failure,
  supported camera modes, float/GPU precision, memory and solo GPU timing.

Numerical fitting does not establish appearance, capture acceptance, memory
availability or performance. No game capture or timing accompanies this fit.

## Shared far caster culling

The frame prepares scenery lists before the far shadow pass moves its light
camera. At `006E50C8`, after the final caster view-projection multiply and before
any caster draw, `x360_shadow_casters` rebuilds the six inward D3D clip planes
from that exact unbiassed matrix and refreshes the shadow scenery entry. It then
calls `GrandSceneryCullInfo::CullView` for only that entry. Its draw table and
nonpacked matrices occupy one reusable 380,064-byte guest allocation, with the
same 5,000-entry limit as the shared table. The frame-arena cursor, limit and
failure counters are restored after culling. Receiver entries and the shared
draw table stay intact; the storage remains valid through the caster flush and
is reused by the next shadow pass. Animation and frame preparation are not
repeated. Guest CPU state is restored. Allocation failure retains the existing
selection.

Previously this refresh ran only in split mode and retained planes from the
earlier perspective camera. Solo scenery could therefore enter or leave the map
using an earlier camera's visibility, while casters and receivers already shared
the current projection. Matrix copies happen within the same pass, and the
stable fit recomputes each call. Refresh runs for the X360 light-camera fit or
experimental steady fit. With both Off, it leaves planes and lists untouched.

`shadow_caster_refresh_test.py` executes the production refresh and translated
visibility, scenery drawing and matrix allocation with actual X360/steady fits.
Synthetic translation, yaw and combined motion run in solo and split modes:
current 3,956 checks / zero failures, SHA-pinned main 4,559 / 65 failures, reviewed
all-view refresh 6,378 / 88 failures. Ten stale selection cases recover 10/10
versus main's 0/10. Across 12 frames, main and current consume 23,424 frame-arena
bytes versus 47,360 for the reviewed refresh. Each frame checks equal arena use,
unchanged receiver lists and a successful later allocation near exhaustion.
Solo both Off reculls are zero versus 18 on the reviewed branch; full guest
memory and CPU remain unchanged. Guard/arena-leak controls fail 22/24 checks;
omitting plane refresh or list rebuilding fails 11/23. Fresh game appearance
and shadow-only recull timing remain unmeasured.

Steady shadows remain the rejected coarse single-map experiment described above.
With the near switch Off there is no second near caster map. X360 post contact
shading is a separate current-depth pass, applied once with at most 8% additional
scene attenuation; it can darken an already shadowed contact. These facts do not
establish that a particular reported overlap is unintended double composition.

## Experimental caster and receiver rendering

`NFS_SHADOW_NEAR=off|on` defaults **Off**; F10 World > Dense near shadows changes
it live. Retail completes first, then one additional caster traversal replays
world/scenery, vehicles and merged meshes with an independent scenery table.
All loaded section bounds extend the light extrusion, including upstream alpha
casters. Material alpha/texkill, culling, numeric constant/slope bias and the
complete far projection remain on their existing paths. Guest CPU, matrix-cache,
render state and host bindings are restored. Unknown routes, insufficient bounds,
unencoded nonempty draws, exhausted guest allocations and failed resources retain
retail. A binding must match source view, frame and generation.

`NFS_SHADOW_NEAR_SIZE` independently selects 256–16384, default 8192, subject to
device dimension and actual working-set/allocation checks. It does not change
`NFS_SHADOW_SIZE`. Measured depth/stencil + colour allocation is 152,567,808 bytes
at 4096² and 610,271,232 bytes at 8192², including alignment. These are device
measurements, not payload-only estimates or an FPS claim. The default 3 m envelope
uses slopes 1.5/1 and conservative yaw-sphere radius 6.18466 m; no allocation or
native stride is accepted by eye.

The exact hardware programs are world t1/t2/t3 (slot 1, texcoord3), worldnormalmap
t1/t2/t3 (slot 3, texcoord7), and worldreflect t1/t2/t3 (slot 4, texcoord7).
`host/shaders/shadow_near_msl.h` pins all nine bytecode identities. World t4/t5,
worldnormalmap t4/t5 and worldreflect t4/t5 implement software depth subtraction;
they retain retail, as do all no-shadow, car/hood, glass, grass, tree and particle
receiver programs. A failed patch or compile leaves the original draw selected.

The translator slices the complete original coordinate/tap/reduction dataflow
into a far helper; interleaved material calculations remain untouched. The near
branch replaces that complete filter and executes one bilinear hardware comparison
inside full-near bounds. Only transition pixels also evaluate the original far
filter. Outside near bounds, the original far helper runs with its original
projection, offsets, sampler and arithmetic. Homogeneous far XYZW is multiplied
by S2's full remap and divided once. S2 already includes centre alignment; the
receiver adds none. Near offsets use actual near-map texel dimensions. Visibility
blends before the existing material/weather strength and fade. Near raster bias
retains the caster path; no new receiver depth offset is added.

Fragment buffer 28 and texture 30 avoid guest/material and HDR diagnostic slots.
The near variant bit participates in translated shader and pipeline keys; the
shader-library source identity includes the receiver ABI. Diagnostic variants
share a shader with explicit uniform modes, so no stale compiled mode is reused.

## Four-way held diagnostics

Both new controls require the normal diagnostic context and the feature On:

- `BOOT_DIAG_SHADOW_NEAR_AB=retail,projection,filter,full` selects the ordered list
  of up to four variants; a single name is also supported.
- `BOOT_DIAG_SHADOW_NEAR_HOLD_AT=<sim>` selects the first race-relative capture
  time, default 12. It freezes no ordinary gameplay when diagnostics are absent.

Retail is LiSPSM plus legacy filter; projection is near plus the legacy-width
kernel in near texels; filter is LiSPSM plus one bilinear comparison; full is near
plus one bilinear comparison, with retail far outside. Each pass is rerendered.
The pacing hook holds simulation/real time between variants, while a post hook
pins adaptation/exposure. Camera, projection, light and weather have no elapsed
time between passes. The render-frame counter advances; it is not a physics tick.
Names preserve burst/sample/variant identity: `near_BB_SSS_VARIANT.bmp` and
`near_BB_SSS_VARIANT_ids.json`. JSON contains exact held state, receiver draw IDs
and caster IDs. `shadow_near.tsv` records all matrices and allocated bytes;
`shadow_near_draws.tsv` retains caster material/alpha/bias/binding evidence.

With `BOOT_DIAG_SEQ=sim+10.5,sim+12,sim+16:64`, each requested time starts a
bounded 64-sample burst, with all selected variants held at each sample. The yaw
fixture holds at 10.5 and repeats its 2°/s sweep at 12 and 16. Lossless top-down BMP capture is
bounded by the existing eight-shot queue; a 64-sample four-way 4K burst writes
about 8.49 GB. The prior PNG writer completed the hold but only 56 complete sweep
samples in 180 seconds and never reached the repeat. The BMP writer passes pixel
and orientation tests, but has no fresh game capture within the three-run cap. Keep masks at their original dimensions;
new 4K output needs new masks. Never merge different cameras into one score.

`BOOT_DIAG_SHADOW_NEAR_VIEWS=far,near,coverage` remains the older three-view
screen diagnostic; four-way AB takes precedence. `BOOT_DIAG_SHADOW_POSE` captures
or replays the existing full pose schema. An absolute worktree JSON path selects
that file; `capture` writes `shadow_pose.json` inside the run's scratch diagnostic
directory. POSIX writes avoid the sandbox's rejected Foundation atomic writes.
The pose mechanism records render identity; it does not rewind physics. A full
pose replay fixes its camera and must not be used to cancel an intended yaw sweep.

## Native recipe and current evidence

All launches use 180 seconds and these separate environment-array elements:

```text
BOOT_RACE=15.1.1
BOOT_RACE_CAR=bmwm3gtre46
BOOT_RACE_AI=0
NFS_LOCKSTEP=1
NFS_SIMHZ=60
NFS_DISPLAY_PIXELS=3840x2160
NFS_SPEED_BLUR=off
BOOT_REG=FirstTime=0,g_RacingResolution=5,g_ShadowDetail=2,g_FSAALevel=1,g_RainEnable=0,g_RoadReflectionEnable=0
NFS_TEST_RAIN=0
NFS_SHADOW_FIT=x360
NFS_SHADOW_STABLE=off
NFS_SHADOW_NEAR=on
BOOT_DIAG_SHADOW_NEAR_AB=retail,projection,filter,full
```

Launch 1 adds `BOOT_KEYS=seq:sim:8,C,wait:30,C`,
`BOOT_DIAG_SHADOW_PAN=hold`, `BOOT_DIAG_SIMSHOTS=12,12.5`,
`BOOT_DIAG_SHADOW_NEAR_VIEWS=far,near,coverage`, and
`BOOT_DIAG_SHADOW_POSE=capture`. Launch 2 omits pan and bumper keys, adds
`BOOT_KEYS=seq:sim:20,C,wait:30,C,wait:30,C`, `BOOT_DIAG_SIMSHOTS=25`,
`BOOT_DIAG_SHADOW_NEAR_HOLD_AT=25`, and scratch pose capture. Launch 3 repeats the
bumper keys with pan yaw and the three bounded bursts above. The first full pose
manifest failed to write, so a complete cross-launch replay is not proved.

**S3 screen proof FAIL:** the first launch shows a changed foreground car shadow
and 81 near caster draws / 9 alpha draws, but native thin fence/foliage coverage
and receiver IDs were not established. All five far/near/remap matrices and the
simulation word match across its four passes. **Hood-shadow proof FAIL:** Launch
2 shows the BMW and a nonempty 76,000-pixel hood mask, but no crossing shadow.
The four held state records match exactly; hood differences are at most 1/255.
These subjects cannot be replaced by synthetic coverage evidence.

The exploratory 4K road normal measures retail/projection/filter/full 10–90% widths
14.882570 / 1.796366 / 7.563865 / 1.701138 pixels. Its 50% contour positions are
202.327600 / 202.950873 / 202.253598 / 202.910861 along that normal. No native
millimetre calibration, blocker gap or 2N reference exists, so these are neither
physical-softness acceptance nor contour-error measurements. The road-normal ROI
was selected after exploratory image comparison; these widths are descriptive,
not pre-pinned acceptance evidence. Labelled images:
`run_shots/shadow_near_s4_road.jpg`, `run_shots/shadow_near_s4_hood.jpg`.

The GPU fixture compiles SHA-pinned original production sources and all nine new
bodies, samples depth steps at 1,025 homogeneous subtexel phases per variant, and
instruments the actual comparison expressions on the GPU. With synthetic far/near
pitch 4/1 mm, the same results hold in all three supported families:

| Hardware level | Evaluated fetches retail/projection/filter/full | 10–90% widths in synthetic mm, same order |
| --- | --- | --- |
| 1 | 1 / 1 / 1 / 1 | 3.1994 / .8000 / 3.1994 / .8000 |
| 2 | 4 / 4 / 1 / 1 | 6.4004 / 1.6000 / 3.1994 / .8000 |
| 3 | 16 / 16 / 1 / 1 | 12.8006 / 3.2000 / 3.1994 / .8000 |

Those pitches are test calibration, not native geometry. At normal incidence,
a 1 m blocker gap gives 6.355415 mm normalized 10–90% solar-disc width. The metric
reports width separately from 50% contour location and rejects broadened,
shifted, rescaled-mask, empty-hood and advanced-time controls. Hardware tests cover
bounds blending, invalid depth/W/nonfinite values, white borders and comparison
bias sign. The GPU proof passes 56,785 checks, rejects all nine pinned originals against
the dense single-comparison reference, and rejects 51 compiled mutation
executions. Off and outside-near output parity is exact. The 99 unsupported
programs retain exact source. Original and mutation counts are in the test report.
Native thin-detail, physical softness, 2N contour, full S3 replay and solo timing
remain unproved. The experiment stays Off.
