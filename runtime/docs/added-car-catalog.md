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
reserve their slots even without an approved variant. Each variant is only
`{contract, compatibility}`, two hashes. This metadata carries no source names,
manufacturer labels, donor names, evidence or copied roster. Final release policy
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
