# Underglow core

The core models an RGB LED strip proxy and optional owned-car cosmetics. It has
no menu, switch, geometry, renderer or visible output. All cars start Off.
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

`underglow.bin` in the user-data directory is optional. Version 1 is explicit
little endian, at most 77,424 bytes: magic `UGSNAP\0\0`, u32 version, u32 snapshot
count, SHA-256 checksum (computed with its own 32-byte field zeroed), followed
by up to 16 snapshots, oldest first. Each snapshot contains SHA-256 of the
**exact 0xF848-byte serialized retail payload**, u32 entry count, then up to 200
24-byte records. Records contain six u32 values: owned-car handle, frontend
attribute key, pvehicle attribute key, customization handle, ownership kind
(low career/custom bits 2/4), and RGB preset. Only non-Off entries are stored.
The maximum is 48 + 16 × (36 + 200 × 24) = 77,424 bytes.

The type identity is the pair of immutable attribute keys, avoiding a guest
lookup or dependence on a runtime car-type enumeration. Customization identity
is its allocated handle, not mutable part contents: repainting a car must not
lose its cosmetic choice. Handle/index is checked against the actual 200-row
table. Duplicate handles or digests, unsupported versions, invalid presets,
invalid ownership/customization fields, excessive counts, bad checksums,
trailing bytes and truncation reject the entire file. A missing, evicted, stale
or corrupt snapshot resolves to Off without refusing a valid retail load.
Loading also matches every identity against the admitted live table.

Defaults and successful loads bind a fresh active profile and clear pending
state. Get/set require the explicit profile pointer and its exact 32 name bytes;
choices cannot leak to another profile or an unowned stock car. Successful
CreateCar and DeleteCar clear reused handles. Failed operations leave choices
alone. These hooks call retail exactly once and retain its entire CPU result;
they never write guest memory.
These hooks and the cosmetic store are Mac-only; Windows retains the original hooks and has no cosmetic store.

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

The three `underglow_*_test.py` drivers are automatically discovered by
`offline_tests.sh`. They compile production C in scratch directories; no game
runs or Unicorn are involved.

- Photometry: 73/73; centre 353.090215088 lx, reference error 0.000000025%;
  a tenfold green-flux error fails 8/73. Independent midpoint quadrature checks
  endpoints, sloping normals and clipped cosines. Mixtures, linear conversion,
  gamut Y preservation, invalid inputs, length/height scaling and Off are covered.
- Storage: 197/197 plus 4/4 fresh-process restart; digest omission fails 3,
  ignored rename failure fails 3, omitted record validation fails 8. Includes
  every truncation boundary, resealed malformed records, saturation, failed
  write/sync/rename and untouched retail fixture bytes.
- Lifecycle: 82/82 plus 3/3 fresh-process restart for each build; real translated retail save/load/create/delete, with contract
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

## Diagnostic fixture preflight (in progress)

`BOOT_KEYS=seq:` recognizes two diagnostic-only steps through a narrow
input adapter: `underglow:owned:is300` and `underglow:shop`.
The adapter requires diagnostics and TEST's started career;
every step stops input after at most 30 wall-clock seconds or 1,800 fixed ticks.
It currently emits no navigation keys. Owned creation uses retail
CreateNewCareerCar, selects its result with SetCurrentCar, and logs identity.
Shop uses SetGameMode and BeginCarCustomize and checks the resulting package,
career bit and owned record. The unfinished `underglow:save` and
`underglow:reload` tokens are rejected immediately without requesting MemcardEnter;
save/load completion driving remains unimplemented. This fixture is not ready for player use.

The exact initial preflight stops at MC_Main.fng's Save complete / Continue
screen before the owned step. A second fresh run with the same keys and
additional screenshot requests reproduces it. TEST's retail file exists at
63,596 bytes (36-byte container plus the unchanged 0xF848-byte payload), but
this alone proves neither the owned-car fixture nor its save/load round trip.
Native menu/routing, palette and rendered output remain unimplemented.
