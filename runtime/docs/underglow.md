# Underglow core

The core models an RGB LED strip proxy with four mounted emitters and a dry
projected pool. Customize > Visual > Underglow saves independent per-car colour
and Light choices. Colour and Light start Off for every car. Choosing a colour keeps Light Off
until Light On is selected. Explicit saved Light On/Off choices are retained;
Road pool choices are independent.
Cosmetics never change the retail save layout or decide whether a save is valid.

## Measured source

Environmental Lights, *RGB-pixelcontrol-30-10 integrating-sphere test report*,
[manufacturer PDF](https://www.environmentallights.com/media/files/documents/RGB-pixelcontrol-30-10_test_report.pdf):
100 mm specimen, 5 V, 2π measurement geometry. The reported normalized flux is
already **lumens per metre**; do not divide these values by ten again.

| Channel | Flux (lm/m) | CIE 1931 x | CIE 1931 y | Peak (nm) |
| --- | ---: | ---: | ---: | ---: |
| Red | 51.857 | .694641 | .304843 | 631.529 |
| Green | 106.075 | .159396 | .722814 | 518.920 |
| Blue | 29.469 | .133762 | .060996 | 464.878 |

These are strip measurements, without installed-car shielding, mounting losses
or optical calibration. They establish neither calibrated glass neon nor the
output of an installed automotive RGB kit. In particular, do not combine neon
chromaticities from a different specimen with these fluxes.

## Photometry and colour

`underglow_photometry.h` uses metres, lumens, lux and dimensionless linear channel
duties in [0,1]. The preset encoding is an RGB bit mask: Off=0, R=1, G=2,
RG=3, B=4, RB=5, GB=6, RGB=7. Encoded sRGB must first pass through
`ug_srgb_to_linear`; 0.5 becomes 0.21404114048223255, not 0.5.
Light duties per named colour (`ug_preset_duties`): Yellow/Cyan/Magenta balance their two Rec.709
channels, White solves D65; equal duties missed four hues (`underglow_colour_test.py`).
For each primary with flux Y, X=Yx/y and Z=Y(1-x-y)/y. Mixtures add XYZ,
so independently enabled channels add measured luminous output.

For a uniform finite Lambertian segment, integrate

    dE = (flux_per_metre / π) max(0, n_emit·(-v)) max(0, n_receiver·v) ds / |v|⁴

where v points from the receiver to the source sample. Both normals must be
unit vectors. The production implementation clips the segment at the two
cosine zeroes and evaluates an analytic antiderivative of the resulting
quadratic divided by (perpendicular_distance² + t²)². It supports arbitrary
segment orientation and receiver slope, with no infinite-strip approximation.
Nonfinite inputs, negative flux, nonunit normals, zero length and receivers on
the segment's supporting line (squared distance ≤1e-20 m²) are rejected (output zero). Back-facing surfaces
and valid zero-flux sources return exactly zero. Degenerate line geometry is
rejected even for Off.

For a centred horizontal receiver at height h under a horizontal segment of
half-length a, the expression reduces to

    E = φ′/π [a/(h²+a²) + atan(a/h)/h].

Using the rounded green flux above, length 2 m and h=0.15 m gives
**353.090215088 lx**. The erroneous extra division by ten gives
**35.309021509 lx**. Uniformly scaling all geometry by k scales E by 1/k;
scaling only length approaches the infinite-line limit φ′/(2h).

All three measured primaries extend outside Rec.709. `ug_xyz_rec709` transforms
XYZ to linear Rec.709, clips negative channels, then rescales all remaining
channels to restore the input Y using (.2126729,.7151522,.0721750). This changes
chromaticity; it does not reproduce the measured spectrum. Output is unbounded
HDR linear RGB in the same photometric scale, never silently clipped to [0,1].
The returned signed relative Y error is after gamut mapping and before exposure
or tone mapping; the fixtures require |error| < 1e-12. Off is exactly (0,0,0).
A later renderer must document its exposure, display conversion and final
luminance error separately.

## Snapshot format and lifecycle

`underglow.bin` in the user-data directory is optional. Its version 2 data fork is explicit
little endian, at most 77,424 bytes: magic `UGSNAP\0\0`, u32 version, u32 snapshot
count, SHA-256 checksum (computed with its own 32-byte field zeroed), followed
by up to 16 snapshots, oldest first. Each snapshot contains SHA-256 of the
**exact 0xF848-byte serialized retail payload**, u32 entry count, then up to 200
24-byte records. Records contain six u32 values: owned-car handle, frontend
attribute key, pvehicle attribute key, customization handle, ownership kind
(low career/custom bits 2/4), and a choice word: bits 0..2 are the legacy colour
label ID, bit 8 enables Light. Legacy Blue=3, Cyan=4 and Yellow=6 decode to RGB
masks 4, 6 and 3. Writers apply the inverse mapping, so old menus retain the
picked name. Version 1 colour-only snapshots inherit Light On; version 2 keeps
its independent Light bit. Already-written version 3 records remain readable
as RGB masks, including zero.

The v2 reader in `1cd7efb7:runtime/underglow_store.c:65` accepts only versions 1
and 2; its `valid` function at line 30 also rejects zero choice words. There is
no ignored inline field. New explicit Light Off with colour Off therefore uses
the v2-compatible placeholder `0x100` (None/Light On) plus a bit in the macOS
extended attribute `org.nfsmw.underglow.zero`. Its 464 bytes contain the exact
data checksum, a 400-byte record bitmap and SHA-256 of those first 432 bytes.
Only placeholder records may carry a zero bit. The attribute is written to the
same temporary inode before fsync and rename; failure leaves the old file intact.
An old reader ignores the attribute and can load and republish all snapshots;
`underglow_review_test.py` executes that old reader and publisher across three
careers. An old rewrite or a copy that drops extended attributes loses only
the extra explicit-zero distinction (None stays dark, Light reads On). All
named colours and their independent Light bits remain representable in v2.
A stale attribute from an in-place old rewrite is ignored by checksum identity;
a matching but corrupt attribute rejects the file. Filesystems without attribute
support cannot publish explicit-zero choices; retail saves remain independent.
The retail payload and its size stay unchanged.
The maximum is 48 + 16 × (36 + 200 × 24) = 77,424 bytes.

Identity uses both immutable attribute keys, the owned handle, customization
handle and kind; repainting keeps the choice. Load matches every identity to
the live table. Duplicate handles/digests, malformed records, unsupported
versions, checksums, trailing bytes and truncation reject the entire file.
Missing or rejected snapshots default to colour Off / Light Off.

Defaults and loads bind a fresh profile and clear pending state. Get/set check
the profile pointer and exact 32 name bytes. Successful CreateCar/DeleteCar
clear reused handles; failures keep choices. Hooks call retail once, preserve
its CPU result and never write guest memory. The store is Mac-only; Windows
keeps original hooks.

Both private and public profile wrappers call the core independently of Extra
EA Trax readiness, assets and enablement. SaveToBuffer first invalidates any
older pending snapshot, then stages cosmetics after retail serialization and
any private metadata work. Staging performs no filesystem writes. Profile
changes discard pending state. `boot.c:save_sidecars_complete` publishes only
after the career task reports success, the file layer reports a new committed
career generation, and its failure sequence is unchanged. Failure or duplicate
completion discards the pending state.

Publishing reads and validates the bounded old file, replaces the matching
digest (or evicts the oldest), writes a mode-0600 sibling `mkstemp` file, checks
full write, fsync and close, then atomically renames. A write/sync/close/rename
failure removes the temporary and retains the preceding snapshot. Directory
sync after rename is best effort. The sidecar is optional and publication
failure is ignored by the retail save task; valid retail data remains valid.
There is no claim of multi-process merging or retention beyond 16 generations.

## Offline evidence

The `underglow_*_test.py` drivers are automatically discovered by
`offline_tests.sh`. They compile production C in scratch directories; no game
runs or Unicorn are involved.

- Photometry: 73/73; centre 353.090215088 lx, reference error 0.000000025%;
  a tenfold green-flux error fails 8/73. Independent midpoint quadrature checks
  endpoints, sloping normals and clipped cosines. Mixtures, linear conversion,
  gamut Y preservation, invalid inputs, length/height scaling and Off are covered.
- Storage: 229/229 plus 4/4 fresh-process restart; digest omission fails 3,
  ignored rename fails 7, omitted validation fails 6. Missing zero metadata
  fails 2 and ignored extension checksum fails 1. Includes truncation, malformed
  records, saturation and attribute/write/sync/rename faults; retail bytes stay intact.
- Lifecycle: 89/89 plus 4/4 fresh-process restart for each build; real translated retail save/load/create/delete, with contract
  fixtures for audio, catalog and part allocation, through both profile wrapper
  variants. Extra EA Trax is deliberately unavailable. The original callback
  omission loses the requested cosmetic round trip (requested Green=2, observed
  Off=0; 6/7 checks vs 7/7 with callbacks) while retail MD5 and all
  63,560 payload bytes remain correct. Individual callback omissions and omitted
  creation/deletion resets must each fail. The five hook wrappers preserve the
  complete CPU structure against their original functions. Independent identical-model cars,
  profile switching, sell/rebuy, failed operations and career completion, restart
  and digest mismatch are exercised. These are core proofs, not screen proof.

`carsave_write_test.py` additionally runs the production file layer with real
cosmetic completion: 59/59 checks, write-barrier bypass fails 17 and omitted
cosmetic completion fails 4. Failed cosmetic write/rename retains the previous
snapshot while retail publication succeeds; failed career write publishes no
cosmetics. Existing wrapper fixture consumers are updated for callback linkage.

## Native menu (runtime/underglow_menu.c)

Customise > Visual lists retail entries 0x301..0x307. For an owned career car (valid per-car
identity) an "Underglow" entry (id 0x308) follows HUD, added inside
CustomizeCategoryScreen::AddCustomOption (007BB560) when SetupVisual (007BC210) adds 0x307.
It opens a CustomizeSub page (007BFBD0 Setup, mode 0x308) of eight colour options 0x901..0x908
(Off, Red, Green, Yellow, Blue, Magenta, Cyan, White = presets 0..7; built with the same AddCustomOption as the
retail decal pages, compare SetupDecalPositions 007BCBB0). Hovering a colour writes it to the
per-car store (preview), Accept keeps it, and leaving the page any other way (Back, another
screen) restores the colour the page opened with. Notification handling follows retail in the
007BB870 wrapper (maxperf_career.c): the highlighted option is read as retail does (+0x38, id at
+0x50), Accept is 0C407210 and the page's exit is E1FDE1D1. Label text comes through the
SearchForString bridge (fe_console.c) only for our hashes and only when the game has none. The
icon is the HUD entry's texture; the page uses the vinyl pages' package, so it shows that
package's cart prompts. A career created this session is named after UserProfile::Default ran,
so the menu re-binds the store to the profile it works on (underglow_adopt).
A ninth native icon-scroller row, **Light: Off/On** (0x309), opens this same retail
CustomizeSub package in mode 0x309 with native Off and On options. Hover previews
the independent light choice; Accept keeps it and returns to Underglow. Back or
another exit restores the opening choice. The colour and light pages set their
retail parent mode at +154 and package at +14C. No F10 item is added.
`underglow_menu_test.py`: 67/67 menu checks, original Setup 51/67 (16 failures);
production menu/store save-load 18/18 plus fresh-process restart 2/2; omitting
the light bit fails 4/18. The translated retail lifecycle additionally passes
89/89 plus restart 4/4 in both profile builds with unchanged 63,560 retail bytes.

## Diagnostic fixture

`BOOT_KEYS=seq:` recognizes diagnostic-only steps through a narrow input adapter:
`underglow:owned:<car>` (stock name, e.g. `owned:carreragt`: created and selected as a career car) and `underglow:shop`. `underglow:shop` alone uses the career's own
selected car (GetCarRecordByHandle 0056ECC0 on the selected handle, owned career bits required);
after `owned:<car>` it uses that car. It needs diagnostics and TEST's started career, from
MainMenu_Sub.fng, and stops input after at most 30 wall-clock seconds or 1,800 fixed ticks.
Shop uses SetGameMode and BeginCarCustomize (00589D60) and checks the resulting package, career
bit and owned record. `underglow:save` and `underglow:reload` are rejected without requesting
MemcardEnter.

Bootstrap: after the save screen's Continue, `pkg:MainMenu_Sub.fng:RETURN` reaches the safe house
(dismissing the save and bonus dialogs), then `wait:400,RETURN` the cash-bonus dialog; owned and shop
wait (within 30 s) while `Dialog.fng` closes. On CustomizeMain, Visual is two RIGHTs from the first category.

## Mounted underglow (first visible version)

The native **Light** choice enables four mounted emissive strips and one bounded
dry projected pool when a non-Off colour is also selected. Its default is **Off**
for stock and added cars, including fresh profiles. Saved Light On/Off choices
are retained without migration or save format changes. Existing version 1
colour-only saves retain their inherited Light On state.
Colour Off always submits zero strips/pools and makes zero light allocations. `NFS_UNDERGLOW` is a diagnostic override:
unset follows the saved choice, `0` forces the light off, `1` forces it on;
colour Off, ownership and primary-view checks still apply. Appearance evidence
is recorded separately from the numerical helpers.

`underglow_light.m` uses the retail textured-headlight ePoly path decoded at
00738180 -> 006BE750 -> 006D1A40. TextureInfo+56=2 selects SRCALPHA/ONE
(006C68B0). The Measured pool covers the transformed strip footprint plus a one metre margin on
each side, with a 25 cm edge fade, generated from the four finite Lambertian
sources. Its dimensions and uploaded radiance update when the car changes. It uses a dry diffuse
albedo assumption rho=.18 and the conversion below. Emitters use
flux/(pi * .010 * 100) radiance. Both textures retain unbounded linear RGB in
RGBA32Float before the existing exposure/bloom path.

Owned-car provenance comes from FEPlayerCarDB::BuildRideForPlayer (005910E0),
propagates through CarViewer::SetRideInfo (007A9DB0) and
FrontEndRenderingCar::ReInit (007609B0), and is invalidated by RideInfo::Init
(00739A70). VehicleRenderConn::Load (0075D220) compares all 139 installed-part
indices against the owned customization record before binding the runtime ride.
The live render connection world ID must equal local player slot 0
ISimable::GetWorldID (virtual +70), reached through IPlayer::GetSimable (+4).
CarRenderInfo+88 is car-type data, not the live vehicle.
Rendering reads underglow_get(profile, handle),
rejects reflected/non-main poses, and preserves the guest CPU return state.
Garage submissions use the car render hook. Road submissions are copied into
one frame-owned slot and flushed through RenderVehicleFlares (00750B00),
after opaque geometry; stale frames, held Off passes and duplicate flushes
submit nothing.
The existing ambient-shadow ground elevation at CarRenderInfo+1178 supplies
the road plane; the measured safe-house floor uses z=0. No new collision
adapter is used. The first version projects onto a horizontal plane rather
than replaying road triangles. Its dry receiver albedo is an assumption, not
a sample of the road texture.

Deferred: wet road response, reflections/split views, bridge scenes, collision
adapter, body/wheel masks. Normal depth testing remains; no source-visibility
claim is made for this first projected version. A bounded shadowless plane can
intersect geometry on slopes and uneven roads. Off returns before the scratch
block, two texture objects, their storage/uploads, and all five submissions.

Mounts now derive from each live car, with no model, BODY hash, handle or LOD
allowlist. `CarRenderInfo::UpdateCarParts` (00755800, decoded
`speed_0074d050.c:7192,7307`) builds chassis bounds at +90/+A0 from installed
part solids; `eModel::GetBoundingBox` (00501570, `speed_005001a0.c:1947`) reads
the solid's min/max at +20/+30. The live ecar layout at +17B4 supplies four
TireOffsets XYZR records at +10 and TireSkidWidth at +DC. The constructor calls
UpdateWheelYRenderOffset (0074D780), so +110 contains lateral centres including
kit offsets (`speed_0074d050.c:575,632,17734`). Array lengths use the same +2
u16 as Attrib::Private::GetLength (00452940).

`underglow_light.m:car_mounts` places the side strips 5 cm inside the lateral
bounds, between axle radius envelopes plus 5 cm clearance. Cross strips sit
10 cm inside front/rear bounds, with their width inside all four inner tyre
edges plus 5 cm clearance. Strip height uses the mean wheel-centre Z minus
10 mm, clamped to the live vertical bounds with 5 mm mounting clearance. This
avoids an isolated low exhaust/bumper vertex moving all emitters below the
floor; the 997S fixture exercises this at chassis Z=.08 m. These are chassis
and wheel proxies, not measured mesh attachment points.
Rigid chassis transforms carry all four mounts; the pool uses their transformed
footprint. Finite, ordered bounds, four valid wheel records and nonempty mount
intervals are required. Reflection, missing ownership and missing road ground
remain rejected. No fixture handle participates in provenance or mounting.

Bounds must also agree with the wheel envelope: longitudinal overhang is at
most half the wheelbase beyond the tyre envelope, and lateral overhang at most
half the track beyond the wheel centres. Body spans must cover at least half
the wheelbase/track; vertical bounds must overlap the wheel-centre/radius plane
and stay within one track below / one wheelbase above it. Inconsistent bounds
reject all light and emit an `underglow mounts rejected:` line under BOOT_DIAG.
The per-render-object cache includes owned handle and identity, all 76 installed
part pointers, layout identity, bounds and wheel inputs. Unchanged frames copy
cached strips; car, kit, part or geometry changes rebuild once. Rejected geometry
is cached too. Measured preset duties are solved once per preset and shared by
the emitter and every pool pixel; turning does not solve them again.

The production admission test reads 73 stock BODY_A assets with wheel data and
admits 66 viable mount intervals, including every career chassis in that set. TRAFCAMPER has duplicated
front/rear axle positions (-1.15 m); TRAILERA, TRAILERB, TRAILERCMT, TRAILERCON,
TRAILERCRATE and TRAILERLOG have no 20 cm inter-axle strip interval after radius
clearances. These traffic/trailer assets are unsupported by this mount policy;
they are outside the retail career catalogue. Missing or malformed geometry or
wheel data in an added car is rejected, not replaced with BMW coordinates.

The retained **measurement fixture** is stock `bmwm3gtre46` KIT00, with axes
+X front, +Y left, +Z up. `ug_mount_fixture` never substitutes its coordinates
for another car. Its four 10 mm strips pass 3,948 mesh/transform checks against
BODY_A/B and BACKDROP_CRIB record 0 in CAREER_SAFEHOUSE.BIN: floor error <=.211
micrometres, mount clearance 22.110..143.941 mm. This does not measure exact
attachment or suspension adaptation for general bounds-derived mounts.

Finite-source helpers clip and merge ray/triangle intervals (at most 64
blockers). Receiver admission requires complete grounded collision data,
exact frame/generation ownership, metric containment and compatible normals.
The collision adapter and body/wheel source masks remain unbound; Near shadows
and Steady edges remain Off.

The float-target unit convention is explicit: given linear albedo rho,
`L = rho * E / pi` in cd/m², then divide by **100 cd/m² per renderer-linear
unit** before exposure or bloom. `ug_diffuse` accepts linear albedo, not sRGB
codes. The N1 XYZ-to-Rec.709 mapping preserves measured Y before this
conversion; mixtures add channel energy without colour-dependent brightness
normalization. This is a defined scale for the added contribution; it does
not calibrate the game's legacy sun, ambient light, exposure or display.

Helper evidence: 3,948 mount/transform and 15 observation-context checks;
CPU visibility 66/66 (max error .0037589%); Metal RGBA32Float 1,536/1,536
(max .002609068%, 0/125 forbidden pixels). Removed visibility fails 260 and
lights 56 forbidden pixels; tenfold units / colour normalization each fail 481.
Stale frame/generation each fail 4; normal/work caps each fail 2. The pinned
retail worldreflect `sm_main` renders zero added light and fails 13/24 nonzero
references. Missing symbols or compile failures do not count as rejection.

`underglow:scene:garage|dry|wet|bridge` retains the full-physical preflight.
Garage records car/camera/tyres and bounded geometry, then fails unestablished
material/source-visibility, wetness/exposure and held-state admission. Floor
identity and camera matrices do not certify collision coverage. Other scenes
fail explicitly. Wet BRDF, reflected strips, split-view and GPU budget remain
deferred; helper results do not establish appearance acceptance.

## First visible evidence and limits

The earlier measured BMW fixture reaches visible native Green by guarded launch 3.
Those stills predate the general bounds mounts. Final matched
Off/On stills are `run_shots/neon3c_garage_off.jpg` and
`run_shots/neon3c_garage_on.jpg` (1920x1080; each below 500 KB). The pool and
side/rear strip edges are visible. The added green difference lies at the
underside/floor; no green increase is measured above y=650 or in the lower
floor reflection. That reflection stays unlit. No obvious body/wheel leak is
established in this still; masks remain deferred, not validated generally.

`neon3c_road_off.jpg` and `neon3c_road_on.jpg` crop the underside and do not
establish road appearance. Their log records 4 strips + 1 pool, a valid road
face and dry ground. Carrera GT (general mounts, Magenta): `neonmenu_carreragt_{garage,road}_{on,off}.jpg`;
the road pool barely leaves the body footprint in chase view.

`underglow_light_test.py` executes production ownership, geometry, float texture
payloads and native ePoly submission. BMW M3 GTR, Carrera GT, Punto, Elise,
Mustang GT, Clio and 997S use independently decoded stock BODY_A bounds and
wheel values, distinct handles 82/11..16, and shared textures across car swaps.
Independent midpoint quadrature of submitted emitter quads checks pool RGB.
Diagnostic On, native On and forced On with saved Light Off pass 410,107 /
410,109 / 410,107 checks. Native Off, master Off and colour Off each pass 7/7
with no allocations, draws or road queries. Mutation results for ownership,
LOD, mounting height, native/queued admission, units and cache gates are in
the test header. These are submission checks, not appearance or frame cost.

`underglow_cars_test.py` independently enumerates 48 stock player models,
76 catalog additions and 14 replacements. Each must decode actual BODY_A
bounds and source wheels; missing data fails. All **124 player models plus
14 replacements** stay dark after colour selection, then submit **4 strips +
1 pool** after explicit Light On, at both Road pool strengths. The pinned
original On default fails 276 roster checks. Fresh colour, no-colour default,
v1 load/restart, explicit Off, v2 saved Off and zero-colour saved Off are
checked separately. Omitted v1 compatibility or zero retention must fail.
These submission checks do not establish visual fit in every scene.

`underglow_review_test.py` pins the five reviewed failure paths: 120 unchanged
frames rebuild 0 mounts (old 120); a part/car change rebuilds once. Two pool
refreshes solve preset duties once (old 81,922). Y=+/-20 m bounds reject with a
diagnostic (old admitted); X/Z and disjoint bounds also reject. All 35 v1/v2/v3
colour/Light cases retain intent (old nine wrong colours), and the old v2
publisher retains both other careers when saving a third (old zero retained).

The pool assumes rho=.18 and a horizontal plane with 2 mm depth lift; it can
intersect uneven geometry. Its texture refreshes on mount changes, preset
changes, >2 mm height change, or >1e-4 basis-component change. General slopes,
airborne cars, exact body attachment, source masks, legacy exposure calibration
and solo frame cost remain unverified.


## Road pool strength

Default **Measured**; native **Road pool** also offers **Stronger**.
[Reference and proof](underglow-pool.md).
