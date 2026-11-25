# Added-car catalog contract

Version 1 is shared by Setup's `tools/setup_kit/car_catalog.py` and the runtime
reader. Its machine-readable shapes are in `car_catalog.schema.json` beside the
Python module. The schema describes shape; the verifier also enforces ordering,
identity, approval, compatibility, filesystem safety and exact file inventories.
No car assets, car roster or real approvals are distributed by this module.

Plan: reserve all identities, validate the complete conversion against a trusted
policy, copy into a durable immutable generation, verify it independently, and
atomically replace the selection pointer. Never modify source trees or saves.

Windows defers public car import and provenance admission to a future host implementation; imported-car saves are unsupported there and must remain on the Mac host with their required packs enabled.

The public “Cars from later games” restart option defaults Off, including when its setting is unavailable; the private catalog keeps its separate On default.

## Wire format and permanent slots

Every document is canonical ASCII JSON: sorted object keys, compact separators
(`,` and `:`), ASCII escapes, no nonfinite numbers, one final newline. Reject
duplicate keys and noncanonical bytes. Hashes are lowercase SHA-256 hex.
`digest(value)` hashes these exact bytes, including the newline. The 4 MiB JSON
and 2 GiB individual-file limits apply before allocation/copying.

The catalog has `schema: 1`, `policy_sha256`, `compatibility` and `cars`.
`cars` always has exactly 90 ordered rows. Slots 0–75 map to addition IDs
84–159. Slots 76–89 have ID zero, role `swap`, and `swap_index` 0–13 in the
existing runtime's frozen original-swap order. Addition `swap_index` is null.
Each row always includes `slot`, `id`, `role`, `swap_index` and `state`.
An `absent` row has only those fields. Missing cars never compact the list.
All swaps remain absent in version 1. Recognition of other later-game sources
does not enable their imports: ready rows support only Carbon PC/X360 and require
an exact independent approval. X360 recognition alone never enables conversion.

A `ready` row adds:

* `origin`: `game`, `platform`, `folder`, read locally. `identity` is the digest
  of `{game, folder}`; it is independent of platform, display labels and
  conversion revisions. Every platform still needs its own approved full
  contract. Equivalent approved PC/X360 imports can preserve the same slot and
  part semantics without sharing output approvals.
* `display`: local `name` and `manufacturer` strings. No public name table.
* `bindings`: local `folder`, `pvehicle`, `frontend`, `ecar` keys. The frontend
  RefSpec may differ from pvehicle; never substitute one for another.
* `donors`: local MW `model` and `price` donor keys. Donor bytes are pinned too.
* `parts`: ordered `{type, keys}` rows. Type IDs are unique bytes below 255;
  keys are ordered and unique, with at most 32768 flattened keys. Preserve
  absent slots, type IDs and flattened ordering when importing compatible data.
* `converter`: positive integer `revision` and a `policy` digest identifying
  material/conversion policy. It is distinct from the approval-document digest.
* `inputs`: four nonempty maps, `source`, `dependencies`, `donor`, `converter`.
  Each map pairs relative logical filenames with `{size, sha256}` records.
  Include every file actually read, including shared assets, donor packs,
  converter modules and converter dependencies. Converter identity is the full
  `converter` input map plus `converter.revision` and `converter.policy`.
* `outputs`: every emitted filename paired with `{size, sha256}`. Required:
  `GEOMETRY.BIN`, `TEXTURES.BIN`, `SOLIDMAP.BIN`. Pin all optional sidecars too.
  Source output directories must contain exactly those files. Output names are
  uppercase `[A-Z0-9_]{1,31}.BIN`. Generation paths are derived, never supplied:
  `cars/<slot>/<filename>`.

Bindings/donors use uppercase `[A-Z0-9_]{1,31}`. Input logical paths cannot
contain absolute components, empty/dot components, backslashes, controls or NUL.
There are no `approved`, `evidence` or caller-defined extension fields. Unknown
fields are errors. Evidence can inform a separate review but grants no trust.

## Approval trust boundary

`load_policy(path, expected_sha256)` verifies canonical policy bytes against an
**independently trusted digest supplied by package code**. Never take that digest
from the catalog, pointer, selected folder, policy itself or a neighboring file.
Python's default `packaged_policy()` uses `PACKAGED_POLICY_SHA256` and the package's
`car_catalog.approvals.json`. The constant is initially null: activation and
publication fail closed until an approved release supplies it. Release packaging
must install the final JSON and pin its exact digest both here and in the runtime
approval header. No command in this API creates approvals. A policy replacement
and its runtime header must be reviewed and shipped together. The explicit
`policy=` APIs allow trusted embedding/tests; never deserialize a `Policy` from
untrusted application input. Python object construction is not a security sandbox.

The policy has `schema: 1`, a positive integer `revision`, and exactly 90 ordered
`slots`. Each slot has `slot`, `identity` (a hash or null), and `variants`.
Null identity requires an empty variant list. Non-null identities are unique and
reserve their slots even without an approved variant. Each variant carries
`contract` and `compatibility` hashes. Retained variants also carry a `legacy`
group number and can verify previously published catalogs. This metadata carries
no source names, manufacturer labels, donor names, evidence or copied roster. Final release policy
assigns the existing stable slot identities; importers never allocate by discovery
order. Pending slots have no approved variants.

`contract_hash(row)` hashes the **entire ready row**. Thus source/dependency and
donor pins, converter code/dependency/policy identity, every output pin, display
values, bindings and part semantics must all match a packaged policy variant.
Changing a pin and recomputing all local hashes cannot grant trust. A policy
digest in a self-consistent catalog also cannot grant trust. `load_policy`'s
expected digest, or the runtime's independently compiled policy, is authoritative.

`slot_compatibility(row)` hashes the object containing only `slot`, `id`, `role`,
`swap_index`, `identity`, `bindings`, `donors`, `parts`. Output bytes, converter
revision and display labels do not change save meaning. Both compatible and
incompatible revisions require their own policy variants. Incompatible revisions
remain separate generations and have different slot signatures; never reinterpret
old saved part indices. Catalog `compatibility` hashes the ordered 90-element
array of ready slot signatures or null for absent slots. The aggregate changes
when availability changes. Save admission can compare the individual used-slot
signatures rather than requiring an identical aggregate or generation name.
This module never locates, parses, edits, deletes or migrates saves.

## Setup API and transaction

Start with `rows = reservations()`. Populate only approved local rows; reserve
all other rows unchanged. `make_catalog(rows, policy)` performs semantic approval
validation before I/O. `fingerprint(path)` makes a `{size, sha256}` record and
refuses symlinks in any component. Collect input records from the converter's
actual complete input report, and derive display/binding/part information locally.
This module does not infer omitted conversion dependencies or decode asset formats;
the packaged review must audit the converter's complete dependency report.

`publish(root, rows, output_dirs, input_dirs, *, policy=None, selection=None)`:

1. `output_dirs` maps each ready integer slot to its converter output directory.
   `input_dirs[slot]` maps each of the four input groups to its directory root.
   Pinned logical filenames are relative to the matching group root. Supply all
   and only ready slots; roots may repeat. The catalog root must be disjoint from
   every input and converter-output tree. No conversion occurs under this lock.
2. Freeze row data, validate policy, acquire the descriptor-based exclusive
   `.writer.lock`, and pin source directory descriptors. Hash every input and
   converter output; reject symlinks, hardlinks, special files and extra outputs.
   Files are streamed with bounded buffers and checked for concurrent changes.
3. Create `.stage-<random>` on the catalog filesystem. Copy each output to a new
   regular file, check its size/hash, make it read-only and fsync it. Write/fsync
   `catalog.json`, fsync subdirectories and seal directories read-only.
4. Rename the stage to `g-<catalog-sha256>` and fsync the parent. Existing identical
   generations are reused only after full verification; corrupt ones are refused,
   never overwritten. Selection does not affect the generation digest.
5. Independently verify the catalog, exact output inventory and all output bytes.
   Recheck all original input/output pins and their directory identities before
   committing. Write/fsync a new pointer and a byte-exact old-pointer recovery
   file, then atomically replace `current.json` and fsync the catalog directory.
   A reported final-directory-flush failure attempts atomic restoration of the
   previous pointer, or removes a new pointer if there was no previous one.

`current.json` contains only `schema: 1`, `generation`, `catalog_sha256`,
`compatibility` and sorted unique `selection` slot numbers. The generation is
exactly `g-` followed by the catalog digest (64 hex digits). Hashing catalog bytes
does not recurse: the generation name is not inside the catalog. The pointer's
selection can only reduce approved ready inventory; it cannot activate absent
or unapproved slots. Default selection is all ready slots. Writers serialize
publication, disable and selection changes using the same lock. Runtime readers
need no lock: an observed pointer refers only to complete retained generations.

`set_selection(root, slots, *, policy=None)` verifies the whole current generation
and atomically updates the pointer. `disable(root)` publishes a disabled pointer
with null `generation`, `catalog_sha256`, `compatibility` and empty selection,
even if the old catalog is corrupt. Disable before any later cleanup. It does
not require approval and cannot enable cars. No cleanup API is provided: keep
all generations, unfinished stages and recovery pointer files for restoration.
Do not prune based on current selection; saves may still need older generations.
`restore_generation(root, generation, *, policy=None, selection=None)` verifies
the complete retained generation against the packaged policy, then atomically
publishes its selection under the same writer lock. It reads no original source
trees and can restore after disable. A corrupt or unapproved generation cannot
be restored. Reimporting identical inputs under the same packaged policy also
reuses the generation. A changed packaged policy requires a fresh import under
that policy; compatible slot signatures remain stable. Never select unfinished
stages or use an old policy supplied by the local catalog as a trust root.

Failures before pointer replacement preserve the previous pointer bytes. Copy,
fsync, cancellation and verification errors propagate; sources are only opened
read-only. A crash may retain staging or a verified unselected generation.
After replacement a crash may expose the complete old or complete new pointer;
it cannot expose a partly written pointer. Persistent storage failure during
rollback is reported as an error and needs recovery; no software can promise
durable restoration on a volume refusing every write. The optional `fault(point)`
hook supports prepublication cancellation/testing at `inputs_verified`,
`output_durable`, `catalog_durable`, `generation_verified`, `pointer_ready`,
`before_publish`; it is never invoked after pointer replacement.

## Runtime reader requirements

`verify_current(root, *, policy=None)` returns `Verification` with `generation`,
`compatibility`, `catalog`, `selected` and `error`. It activates zero on every
failure, including missing pointer, missing policy, unapproved row, corrupt file,
unsafe path or incompatible schema. A disabled pointer also activates zero.
`selected` contains verified rows, preserving their exact sparse IDs. Inventory
rows remain in `catalog.cars`; absent reservations must not become selectable.
The returned containers are snapshots; do not treat a modified result as trust.

A native reader must apply the same strict field/type/duplicate-key rules, slot
mapping, independent policy digest/identity/variant validation, part limits and
path rules. Verify every ready row even if deselected, and activate **zero total**
on failure. Hash the complete canonical ready-row bytes plus a newline to match
`contract_hash`; never accept approval words or hash pins without policy matching.
Use directory-relative no-follow opens, require regular singly linked files,
verify the exact generation file inventory and every pin, and validate the pointer
digest, generation name and compatibility. Paths come solely from slot/filename
rules. Do not follow catalog-supplied source paths or access original game trees
at startup: the packaged contract attests the input pins, and installed generation
bytes are independently verified. Runtime registration, selection UI and save
admission belong to their separate consumers of this contract.
Native storage needs at least 67 bytes for `g-` plus 64 hex digits and a NUL,
65 bytes for a digest string, and 32 bytes for each binding/donor token. The old
short random generation identifier is not this version's wire format.

## Offline proof

`tests/setup/test_car_catalog.py` uses temporary synthetic policy and assets;
its approvals never enter the package. It pins the original missing behavior:
recognized/converted files without a verified published catalog activate zero.
It covers sparse activation, complete trust pins, compatible/incompatible
reimports, immutable reuse, corrupt generations, path attacks, serialized writers,
durable publication failures and source-tree/pointer preservation. Test output
records good and broken counts: 30 tests, 22 good checks and 117 rejected broken
controls. Bypassing the contract-approval guard in memory makes four tests fail
(zero errors). Real output approvals still require visual review.
## Optional source engine audio

`NFS_ADDCAR_AUDIO` defaults On (unset, `on` or `1`). Setup imports all 30
reviewed Carbon PC additions and 14 reviewed Xbox 360 variants with a locally
built `AUDIO.BIN`. The importer snapshots the selected car's attributes and
audio files under `SOUND/ENGINE`. The Xbox 360 route reads those logical ranges
from the selected disc's numbered archives. Reimport a retained Carbon pack to
get its source sidecar. Retained catalogs still open under their original
approval contracts. The earlier private Jaguar XK stock sidecar is also
readable. Missing or refused sidecars, Undercover additions, and explicit `off`
or `0` use donor audio. All 31 reviewed ProStreet PC additions import their
stock player engine/exhaust layers (version 5 below). The typed NG references
at vehicle index zero select filenames; Veyron selects `car_106-engine.tmx`
and `car_126-exhaust.tmx`. Only the audited attributes revision is admitted.
Sweet-bank, blower and gear selectors remain decoded inventory, without
requiring their banks for continuous playback.

The converter preserves `pvehicle.Audio[]` order. Each `caraudio` resolves to
engine and exhaust `engineaudio`, in that order. Each layer contains its
effective numeric calibration followed by the bank, acceleration GIN and
deceleration GIN. Carbon PC uses little-endian ABKC with big-endian
S10A codec-4 samples and standalone Gnsu20 GINs. Resident mono PCM preserves
authored loop start, including the intro in the 997GT3RS and 997TT engine bank.
Xbox 360 uses big-endian ABKC and resident mono S10A codec 3 at 48 kHz. Setup
validates XMA framing and the ordered tier calibration. It translates each
bank's interpreted AEMS operations and typed state to the guest ABI, then
checks the generated program, imports, selector rows and exported class.
The runtime decodes the console samples when a tier first needs them, using
the declared frame count and loop start. It drains and restarts the XMA
decoder at each internal stream boundary.
Setup and the runtime bind the complete package digest to the selected car
and source platform.
Replacing layers or recomputing the embedded checksum cannot admit different
content. Each package contains the selected tiers' direct banks and GINs.

The version-2 A7CA header is 68 bytes. It contains the magic, version, total
size, resource count, tier count, NUL-padded lowercase 16-byte car name and
payload SHA-256. Two little-endian words after it store the source maximum and
default engine upgrade levels. Each tier stores two 300-byte calibrations
followed individually by the three resource indices. The resource table
contains full SHA-256s, source bytes, validated pointer-free ABKC plans and
little-endian mono PCM. The PCM loop word is zero for one-shot playback and
one plus the loop-start frame for loops. Source bytes remain local to the
pack. Only contracts and fingerprints ship.

Version 3 identifies Carbon Xbox 360. It keeps the header, tier and resource
layouts and stores the original console ABKC bytes as the bank identity.
Each bank plan starts with the length and bytes of its translated executable
image followed by the same pointer-free execution manifest. Sample rows
store declared frames, rate and loop start with a zero PCM byte count. The
runtime checks those values against the source before decoding to float PCM.

Versions 4 and 5 identify ProStreet PC and keep the 68-byte header with one
tier. The payload starts with engine and exhaust TMX resource indices (two
words), each layer's RPM range (four floats), then the attributes SHA-256.
Each resource row stores kind (3 TMX, 2 bank), source byte count, zero and the
resident sound count, then its SHA-256 and original bytes. TMX rows append each
resident sound's frames, original rate, loop word and little-endian mono PCM.
Version 5 carries exactly two TMXs and obtains the RPM bounds from those files.
The runtime intersects their bounds, adds one RPM at the lower edge and removes
two RPM at the upper edge, then maps the live normalized RPM into that shared
range. It preserves three to six tracks, empty bins, reviewed curves 0/1/4/8/9,
original GIN rates and up to six Ginsu voices. Resident XAS rates are 32, 44 and
48 kHz. Released version-4 Veyron packages retain their four-resource contract.
SWTN, blower, shift and skid programs are not executed. The nine source vehicle
variants are not mapped to MW upgrades.

For race objects the port obtains the live MW engine upgrade level via
`Physics::Upgrades::GetLevel` (00672D30, engine type 4). Carbon PC's 0053B570
maps level 0 to tier 0. A positive level selects level minus one, except a car
with maximum level 1 selects tier 1. The result is bounded by the source
maximum and the number of authored tiers. Jaguar XK has a maximum of 2, so
levels 0 and 1 select tier 0 and level 2 selects tier 1.

| Jaguar XK tier | Engine bank and GINs | Exhaust bank and GINs |
| --- | --- | --- |
| 0 | `car_87_eng_mb_ee.abk`, `en_acl_87.gin`, `en_dcl_87.gin` | `car_87_exh_mb_ee.abk`, `ex_acl_87.gin`, `ex_dcl_87.gin` |
| 1 | `car_87_eng_mb_ee.abk`, `en_acl_87.gin`, `en_dcl_87.gin` | `car_82_exh_mb_ee.abk`, `ex_acl_82.gin`, `ex_dcl_82.gin` |

Resource opens use `SOUND/ADDCAR_AUDIO/<package SHA-256>/<resource SHA-256>`.
Both full digests are compared; runtime bank tokens are monotonically allocated,
never truncated hashes. Each open has an independent cursor. `boot.c` serves
these identities before unrelated X360 engine replacements. Source files and
native resource namespaces are not modified.

Native hooks are relocated at AEMS initialization `004DC360`, Ginsu initialization
`004EE1C0`, detach `004DC1D0`, and destructors `004F5CD0`/`004F5D60`. The existing `posaudio.c`
`004EDD90`/`004EE3E0` wrappers retain their positional context and native call,
then update the two distinct 100-word source exports. RPM comes from the live
engine's vtable `+44`; load, main gain, dry/wet gain, pitch and azimuth come from
the native live controller. Each export uses its source RPM bounds and complete
primary/secondary calibration, including w84's primary `+74` origin.

Source bank programs run through `interp_run`; ordinals 4/27/38 dispatch to the
owned adapters only inside this execution scope. The donor program is released
after successful source initialization. Source loop PCM uses continuous source
pan, priority, gain/pitch and biquad callbacks, then four mono native packet
streams carry stereo dry and wet sends into the native mixer/reverb. The source
float buffers pass through a scoped `00825310` conversion hook: native packet
reader `00827182` calls the converter installed at `00822BF3` via `009C2528`.
Only the eight owned packet buffers use the original float values in native
16-bit sample units; all unrelated buffers call the original converter. This
retains headroom before the existing master limiter. The measured source peak
is 1.08107078; a separate +1.25/-1.5 fixture fails with int16 staging and passes
with float conversion. Native transport startup gain is unity. The source
pool has 512 voices and separate priority arbitration. Four shared native
packet voices transport its output.

For player engines, the selected tier's four acceleration/deceleration GINs
use the native Ginsu path (004E1280 through synthesis, frequency update and
teardown). The two donor Ginsu streams stop after successful attachment. The
public runtime reads the sidecar from the catalog's verified snapshot. The
owning pvehicle vault generation holds the package and prepares decoded
float PCM and relocated ABKC images when a tier first needs them. On retirement
the package stops admitting engines. The last borrowing engine then releases
its bank images, PCM and package bytes, even if the same car has already
mounted in a new vault.

The baseline manifest registered 30 Carbon PC additions and admitted only
the legacy XK sidecar. The current manifest registered and admitted all 30
in four guarded null-audio runs. The upgraded XK run used
`NFS_TEST_UPGRADE_INTERVAL=1` to select the upgraded exhaust. The headless
replay selected the same tier from installed level 2. Each live car detached
and reattached after a race restart with the same bank and GIN identities.

| Live car | Tier | Package bytes | Package, decoded PCM and relocated bank bytes | Native / source voices at first race update | Nonzero mixed packet samples before restart |
| --- | ---: | ---: | ---: | ---: | ---: |
| Jaguar XK | 0 | 5,859,678 | 11,044,294 | 18 / 5 | 1,828,864 |
| Jaguar XK | 1 | 5,859,678 | 11,332,762 | 18 / 3 | 1,829,888 |
| Porsche 997 Turbo | 0 | 1,909,505 | 4,774,809 | 20 / 3 | 1,827,840 |
| Audi R8 | 0 | 4,759,106 | 10,830,490 | 18 / 3 | 1,829,888 |

`runtime/car_audio_test.py --private` compares 98/98 ordered layer records
across 49 tiers with the source vault and loads both banks for all 30 cars.
Both sides of a 997TT/R8 layer swap are rejected. Disabling the per-car digest
check fails the same control. The XK native packet-reader replay produces
163,701/247,936 nonzero samples, releases 194/194 guest allocations and destroys
44/44 native packets. The Off path compares 120,320 native PCM bytes plus
65,536 controller bytes and CPU state with the donor-only path. Listening was
outside this round of verification.

The Xbox 360 closure covers 22 ordered tiers and 44 layer records. Every
layer names the same bank and GIN files as its Carbon PC tier. Six cars carry
at least one different console calibration field. The 26 console banks
contain 208 live sample slots and three nonzero loop starts. Of the 53 GIN
paths, 52 are byte-identical to PC. The package retains the console
`EN_ACL_25.GIN` bytes.

The exported Setup route imported all 14 sidecars and the native catalog
streamed all 14. The packer rejects a Carbon PC ABK supplied under a console
resource name. The runtime rejects a single cross-car bank replacement with
a valid package checksum and refuses a PC sidecar in a console slot.
The console XK headless replay produced 163,704 nonzero samples and matched
120,320 donor PCM bytes with source audio Off. On unload it released all
194 guest allocations and 44 native packet objects.

All three guarded null-audio races admitted 14 console packages. Each car
used the same bank and four GIN identities after restart. Bank numbers below
identify the engine and exhaust files on the disc. Memory covers the package,
decoded PCM and relocated programs. Voice counts are native / source at the
first source update in each race.

| Xbox 360 race car | Tier | Engine / exhaust banks | Package bytes | Resident bank bytes | Voices before / after restart | Nonzero packet samples before restart |
| --- | ---: | --- | ---: | ---: | --- | ---: |
| Jaguar XK | 1 | 87 (`14528364f970`) / 82 (`3f59c85ded46`) | 1,847,426 | 7,042,438 | 18 / 1 → 20 / 2 | 1,824,768 |
| Porsche 997 Turbo | 0 | 46 (`9c008fde6920`) / 42 (`0ecb6d1f3ff0`) | 497,979 | 3,221,235 | 20 / 3 → 19 / 3 | 1,825,792 |
| Audi R8 | 0 | 62 (`de0e035021ba`) / 62 (`7fc98ee05bf4`) | 1,728,470 | 7,490,494 | 18 / 1 → 18 / 2 | 1,822,720 |

## Optional source grip

F10 > Cars > Added cars' own grip defaults to Off; changes apply after the current grip scope ends, from the next race or car change; an explicit environment switch wins.

`NFS_ADDCAR_GRIP=off|on` defaults to **off**. `on` selects admitted
GRIP.BIN tyre coefficients for the local player's added car, using its live
tyre upgrade level and installed Junkman scales. Stock cars, traffic and other
players retain native handling. Missing or refused metadata retains native handling.

The ordinary-road solver uses the X360/Carbon dynamic grip, static grip and
grip scale; native yaw-limit and rear-friction routines consume the selected
yaw-control table. C51 DRIFT_YAW_CONTROL adds source body yaw assistance after
native drift torque and before wheel forces. Steering, yaw speed and all
drift-only source fields retain their existing behavior. This is ordinary-road
source grip, not a complete source vehicle simulation.

Selection requires the current pvehicle identity and vault generation. Each
call owns its guest tyre-layout shadow and restores the original pointer and
thread-local scope on exit. Unload, reload and address reuse invalidate old
selection tokens. The default remains off pending a handling comparison.

## Source performance

Setup emits a 344-byte `PERFORMANCE.BIN` for each supported Carbon PC and Xbox
360 variant from that variant's local `GLOBAL/ATTRIBUTES.BIN`. The CPF3 header
stores its size and both collection identities. The payload stores typed
little-endian float values and explicit array lengths. Approval policy revision 8
pins the record alongside `AUDIO.BIN` when source audio is available. Earlier generations
can still load. CPF1 imports use donor nitrous. CPF2 imports carry source boost
and capacity plus the donor's remaining nitrous fields. Reimport to pick up the
complete source nitrous record.

To return to an older release after a revision 8 import, run that release's
Setup and reimport the cars your save uses.

The startup default, `NFS_ADDCAR_PERFORMANCE=on`, uses the record for driving
and Quick Race estimates. Set it to `off` to use the MW donor. MW keeps the
same save/type IDs, upgrade slots, fit dimensions, and local grip/contact
options. The engine, transmission, induction, chassis, brakes and tyres
project each donor endpoint's change from stock onto the Carbon stock values.
CPF3 nitrous uses the equipped refill timing, speed thresholds and flow at
every installed level. Carbon and MW share the equipped values and the kit
boost and capacity endpoints. A donor with one kit endpoint gets a separate
low endpoint so MW blends two equipped layouts. MW computes the stock and
maximum ratings from those collections using its retail normalization bounds.

| Collection and field | Carbon → MW offset | Decoded value |
| --- | --- | --- |
| pvehicle MASS | 40 → 38 | Driving mass in kg |
| pvehicle TENSOR_SCALE | 00 → 00 | Three inertia multipliers and vector padding |
| pvehicle HandlingRating | sparse → sparse | Stock and full-upgrade handling with 2 float values |
| engine TORQUE | 00 → 00 | 9 lb-ft samples on the idle-to-maximum RPM grid |
| engine SPEED_LIMITER | 2C → 2C | Array of up to 2 mph limits, empty in the supported stock records |
| engine ENGINE_BRAKING | 3C → 3C | 3 engine braking values |
| engine FLYWHEEL_MASS | 50 → 50 | Flywheel inertia setting |
| engine MAX_RPM | 54 → 54 | Maximum RPM |
| engine RED_LINE | 58 → 58 | Redline RPM |
| engine IDLE | 5C → 5C | Idle RPM |
| transmission GEAR_RATIO | 00 → 00 | Reverse, neutral, and forward ratios, with authored length |
| transmission DIFFERENTIAL | 2C → 2C | 3 differential values |
| transmission GEAR_EFFICIENCY (5250EB92) | 40 → 40 | 9 gear efficiency samples |
| transmission TORQUE_CONVERTER | 6C → 6C | Torque converter setting |
| transmission 7381EE38 | 70 → 70 | Front drive fraction used by MW drive-axle selection |
| transmission CF5DD4E9 | 74 → 74 | Clutch play and slip scale |
| transmission 8DC16371 | 78 → 78 | Optimal shift range scale |
| transmission SHIFT_SPEED | 7C → 7C | Shift speed setting |
| transmission FINAL_GEAR | 80 → 80 | Final-drive ratio |
| induction LOW_BOOST | 00 → 00 | Low-RPM boost multiplier |
| induction SPOOL_TIME_DOWN (1F158097) | 04 → 04 | Spool-down time |
| induction VACUUM | 08 → 08 | Vacuum setting |
| induction SPOOL (CF11AF87) | 0C → 0C | Spool setting |
| induction SPOOL_TIME_UP (AC1C0D2F) | 10 → 10 | Spool-up time |
| induction PSI | 14 → 14 | Pressure in psi |
| induction HIGH_BOOST | 18 → 18 | High-RPM boost multiplier |
| chassis AERO_COEFFICIENT | 70 → 68 | Aero coefficient |
| chassis DRAG_COEFFICIENT | 78 → 70 | Drag coefficient |
| brakes BRAKE_LOCK | 00 → 00 | Front/rear lock values |
| brakes BRAKES | 08 → 08 | Front/rear torque settings in lb-ft |
| brakes EBRAKE | 10 → 10 | Handbrake torque setting in lb-ft |
| tires STEERING | 78 → 48 | Steering scale |
| nos NOS_DISENGAGE | 00 → 00 | Delay before recharging, in seconds |
| nos TORQUE_BOOST | 04 → 04 | Fractional torque above the baseline |
| nos FLOW_RATE | 08 → 08 | Nitrous flow setting |
| nos RECHARGE_MIN | 0C → 0C | Refill time at the lower speed threshold, in seconds |
| nos NOS_CAPACITY | 10 → 10 | Capacity in seconds |
| nos RECHARGE_MAX | 14 → 14 | Refill time at the upper speed threshold, in seconds |
| nos RECHARGE_MAX_SPEED | 18 → 18 | Upper refill speed threshold, in mph |
| nos RECHARGE_MIN_SPEED | 1C → 1C | Lower refill speed threshold, in mph |

The conversion and native input regression is `tests/setup/test_car_performance.py`.

Setup approval policy revision 9 adds `PERFORMANCE.BIN` for all 31
ProStreet PC imports. The same startup switch enables their source values.
Setup resamples their engine torque curves on MW's RPM grid and projects the
source speed limits, drivetrain, axle brakes, steering scale and handling
ratings into MW's collections. MW supplies the upgrade slots, brake-lock
settings, steering rate and range, and nitrous kits. Reimport ProStreet cars
to add their driving records. Earlier approved generations still load.

Setup approval policy revision 10 adds the same performance record for all
14 Undercover PC imports. It includes stock mass and inertia, resampled
engine torque, speed limits, drivetrain and zero-speed brake and steering
calibration. MW uses those inputs for driving and to estimate stock speed
and acceleration on the retail scale. MW supplies the handling endpoints,
suspension, contact, steering and brake envelopes, and nitrous kits.
Superleggera keeps its own save ID and fit with the authored LP560-4 tuning
link. Reimport existing Undercover cars to add source performance. Earlier
approved generations still load and retain save compatibility.
