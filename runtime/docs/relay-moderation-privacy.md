# Installation authentication, local moderation and privacy

Wire v3 requires proof of possession of a random P-256 installation key after
return-routability validation. A public identifier alone grants no access. This
adds installation bans to the accountless relay; a fresh installation identity
can evade a ban. This limitation is accepted. This authenticates HELLO, not an
account, a person's identity, game state or an encrypted connection.

## Installation credential

The game creates or loads a credential only inside the explicit online open path,
after validating its target and before opening UDP. `roam_client_init`, the Off
hooks, interpolation and settings registration do no credential, file or socket
work. Transport-only fixture clients generate disposable keys when asked to build
HELLO. The embedded reachability probe likewise uses a disposable key.

The game calls `userdata_dir()` rather than a fixed home path, so selected USERDATA
and scratch profiles each own their installation identity. The private material is
`USERDATA/online-identity/key`: a 97-byte X9.63 P-256 private representation (public
point followed by private scalar), in an owner-only directory, with a mode-0600
regular, single-link file. Symlinks and unsafe permissions are refused. A corrupt
existing credential is an explicit online failure, never an implicit reset.
On macOS, existing credential directories and key files with any extended ACL
are refused, even when their POSIX modes are private. Newly created directories
and staging files have inherited ACLs removed before private material is written;
ACL inspection or removal errors fail closed.
Import is checked by signing and verifying a test message as well as validating
its public representation. No installation key is placed in the Keychain.

Creation flushes a mode-0600 temporary file, publishes it using an exclusive hard
link, removes the temporary and flushes the directory. Concurrent first writers
reload the winner instead of replacing it. The dedicated directory is excluded
from Time Machine and marked UF_NODUMP before any key is generated. Online fails
if those macOS backup exclusions cannot be applied. Secret buffers are explicitly
cleared when sessions change. Private keys must not be copied into reports or
operating evidence. Deleting this file resets identity on the next online request.

macOS uses Security's in-memory P-256 keys and X9.62 DER ECDSA/SHA-256 signatures;
Linux uses OpenSSL libcrypto with the same point and signature representations.
The standalone macOS build links system Security and CoreFoundation, and the Linux
build links libcrypto and libm. Both remain independent of game assets.
Windows client and standalone relay builds are not provided. The original Windows
game client does not implement this transport. A Windows implementation would need
the complete v3 cookie and installation-proof protocol; v2 clients cannot join a
v3 relay on any platform.

## Admission proof and bounded work

See [relay-protocol.md](relay-protocol.md) for byte layouts. The cookie authenticates
endpoint, version, room, name and public point. The signature covers a domain
separator, version, those HELLO fields and the fresh cookie, binding possession to
that endpoint transcript. A copied point, missing proof, altered transcript,
wrong private key or expired cookie cannot authenticate.
Public points must use the canonical 65-byte uncompressed encoding beginning
with `0x04`, checked before identifier hashing and key import. Hybrid aliases are
rejected, so one public point cannot obtain another identifier by changing its encoding.

Parsing, unauthenticated ingress, cookies, bans, replay history, source/global new
admission and capacity checks precede signature verification. Expensive verification
has an additional global bucket of 100/s, burst 100. Established packets retain
their own ingress budget; HELLOs remain subject to the signature bucket. Production
never offers an authentication bypass. Load fixtures explicitly increase policy
budgets while retaining signatures, cookies, replay checks and all parser bounds.

A fixed 2,048-entry RAM cache remembers hashes of accepted HELLO/cookie transcripts
until their cookie lifetime ends. Replaying one after disconnect/kick is rejected
before verification/allocation and receives a bounded fresh challenge. The live
session may repeat its own proof, retaining its session id. Fresh HELLOs after a
kick may reconnect; bans prevent that. ECDSA signature malleability cannot bypass
replay detection because the cache excludes the signature bytes. A full replay
cache fails closed. Cookie rotation and process restart invalidate old cookies.
Replay records, endpoints, address throttles and cookie keys are RAM-only.

## Local administrative protocol

Standalone moderation is enabled with paired `--state-dir PATH --admin-dir PATH`.
Both directories must already exist, belong to the service uid and be mode 0700.
The shipped service launchers supply these arguments. Omitting both is an explicit
stateless/manual fixture mode; it does not claim persistent moderation. Embedded
private hosting enforces installation proofs but has no administrative listener.

The only administrative listener is `admin-dir/admin.sock`, an AF_UNIX stream
socket, mode 0600. Connecting processes must have the service uid as well as socket
access. There is no TCP or UDP administrative command. Four concurrent connections,
160 request bytes and a two-second idle lifetime bound resources. Each connection
accepts one ASCII line. Signs, overflow, embedded NUL/CR, trailing tokens, multiple
lines and oversized input produce `ERR syntax`; rejected input is never echoed.
Replies are bounded to 3,072 bytes. Slow/non-reading clients cannot block UDP.

| Request (newline terminated) | Result |
| --- | --- |
| `list 0` | Up to 32 `session-id public-identifier` lines, then `END cursor` |
| `list cursor` | Continue after that session id; `END 0` ends the scan |
| `kick session-id` | Remove that live session; fresh authenticated reconnect allowed |
| `kick identifier` | Remove all live sessions with that public identifier; fresh authenticated reconnect allowed |
| `ban session-id seconds` | Persist that verified installation identifier, then remove all its sessions |
| `ban identifier seconds` | Persist that public identifier, even while disconnected, then remove all its sessions |
| `unban identifier` | Atomically remove its ban; fresh authenticated reconnect allowed |

Session ids are decimal 1..65535, durations 1..2592000 seconds (30 days), and public
identifiers are 64 lowercase hexadecimal characters: SHA-256 of the canonical
65-byte public point. `list` never includes names, addresses, cookies or public
points. It is a local administrative view, not an operating-evidence export.
Decimal targets resolve a live verified session; public identifiers can be banned
without a live session. An identifier kick with no matching live session reports
`ERR session`. Errors distinguish syntax, absent sessions
and numeric storage errors. Successful ban replies follow persistence and removal.

The shipped relay binary also supplies the bounded local client. For native
administration, run as the service uid:
`sudo -u nfsmw-relay /opt/nfsmw-relay/current/roam_relay --control /run/nfsmw-relay/admin.sock --request 'list 0'`.
For the container use
`docker exec --user 65532:65532 nfsmw-relay /relay --control /run/relay/admin.sock --request 'list 0'`.
Other requests use the same form. The client opens only AF_UNIX, caps input/reply
size and socket timeouts, and returns nonzero for an error reply. Use the private operator/SSH route for reports and control;
never expose this socket through a network proxy or export its raw replies.

## Persistence, restart and retention

Only `bans` is durable service state. It contains at most 1,024 unique rows of
`public-identifier UTC-expiry-seconds`, or the explicit `# empty` marker. There
is no address, address hash, name, reason text, cookie, session id, private key or
public-point registry. Read bounds are fixed independently of file size; duplicate
ids, malformed/overflowing expiry, oversized files, unsafe modes, non-regular
files and symlinks refuse startup before UDP is opened. A lock on the state
directory excludes concurrent writers, including processes using another admin path.

Updates use exclusive mode-0600 `bans.tmp`, fsync, atomic rename and directory fsync.
Failures produce an explicit error and cannot report success. If rename committed
but directory fsync failed, in-memory enforcement still follows the committed file,
and the reply reports the durability error. A leftover staging file after a crash
blocks later writes explicitly; inspect/remove only that staging file while the
service is stopped. Existing valid bans remain effective. Never discard a corrupt
ban database just to get the service running: restore a reviewed valid copy.

Expiry uses UTC Unix time, not process uptime. Bans apply to reconnects, every
existing session using the installation, and service restarts. The host clock is
trusted and must be maintained; a backward wall-clock correction can extend an
expiry. Missing wall time fails startup and new bans; ban lookup fails closed.
Expired rows are removed atomically at startup and during service. Cleanup errors
are numeric and rate limited to one per ten seconds; expired bans remain expired,
and their obsolete disk rows are retried until storage recovers. Unban persists
across restart. No general player-history or identity database is retained.

## Service layout and operating evidence

This layout supersedes the earlier stateless/no-volume launch description in
[relay-operations.md](relay-operations.md). The CLI readiness line reports the bound
port and effective limits without an address. Its periodic logs contain aggregate
counters. The game no longer logs the selected relay address or player name when
online starts. Rejected options and administrative input are not echoed into logs.

Native systemd creates `/var/lib/nfsmw-relay` for durable bans and
`/run/nfsmw-relay` for the transient control socket, both mode 0700. ProtectSystem,
ReadOnlyPaths and explicit ReadWritePaths retain a read-only service except for
these directories. The runtime directory is removed on stop and must not be backed
up. Only a reviewed copy of the bounded bans file belongs in any service-state
backup; release binaries/provenance and bounded aggregate logs can be retained
separately. Preserve the current ban state when rolling back a binary.

For Docker, prepare a dedicated host directory owned by uid/gid 65532 and mode 0700,
then set `RELAY_STATE_DIR` before running `relay/run.sh`. It is the sole durable bind
mount at `/var/lib/relay`. `/run/relay` is a 1 MiB private RAM filesystem for the
socket. There is no game/profile mount. The isolated container smoke check uses
RAM-only disposable bans and control directories; its stop/start check does not
claim durable-volume proof. The native loopback fixture separately proves ban
persistence across real relay processes and UTC expiry after restart.

Both native/container launchers disable cores and swap; the process itself sets
RLIMIT_CORE=0 and Linux PR_SET_DUMPABLE=0 before retaining endpoints. The game also
disables process cores when online is requested. These controls do not prove a
provider's hypervisor, crash collection, snapshots or backup policy. Before public
activation, audit the actual host: prohibit process-memory dumps, packet captures,
address-bearing firewall/access logs, memory snapshots and backups of `/run`,
transient containers or swap. Retain only bounded aggregate monitoring and reviewed
identifier/expiry state. Provider-side visibility/retention must be disclosed and
checked separately; the provider necessarily sees network addresses and traffic.
UDP is unencrypted: network/provider observers can see names, public points, rooms
and car states. The same installation point is correlatable across sessions/hosts.

Players can use the existing F10 > Diagnostics > Report a problem route for client
failures. Review the sanitized report before sharing it through the existing
project reporting route. Do not add raw admin listings, key files, packet captures
or ban files to player reports. A service operator should receive moderation
reports through the existing private project/host contact, not a new UDP endpoint.
No new public reporting destination or address-retention service is introduced.

## Offline evidence

Before modification, the original relay admitted one ordinary unsigned HELLO
(clients=1). The cookie-only prerequisite passed its admission and identity
fixtures with clients containing no installation credential. The new fixtures
exercise the production provider and parser rather than a test authentication stub.

`roam_moderation_test.py` runs ASan/UBSan: 70 authentication/core checks plus 45
moderation/storage checks, zero normal failures. Signature bypass, ignored bans,
lost persistence and replay bypass produce 9/5/5/11 named failures respectively.
It also cross-verifies native and OpenSSL CLI signatures in both directions and
rejects an altered foreign signature. Scratch-profile credential integrity,
permissions, link safety, concurrent creation and backup exclusion are checked.
Live loopback proves kick/reconnect, replay rejection, all-session ban, persistent
restart rejection, unban and UTC expiry after restart; malformed admin/storage
controls refuse unsafe input. Native Security plus OpenSSL CLI interoperability is
host evidence, not a claim that the Linux production OpenSSL build has run here.

`roam_relay_privacy_test.py` scans four generated service/state/export/telemetry artifacts for
address/name/cookie/secret sentinels and encoded key material. Normal has zero
findings; deliberate address logging yields two findings, and SHA-256 persistence
of the source address yields one. Binary network-order and host-order logging
controls each yield two findings. Direct scanner controls changed from 1/0/0 to
1/1/1 findings for textual/network/host IPv4. The full address-derived form list
includes network bytes, host integer bytes and textual IPv4. The check also validates dump
and transient/durable deployment wiring. Test output contains counts, never secrets.

`roam_switch_test.py` counts zero key-generation, credential-load, USERDATA and
socket calls on Off. Added key-work and file-path-work mutations must fail that
assertion. Existing admission controls, interpolation, embedded restart, load,
appearance, budgets and service packaging remain part of the whole offline suite.
Aggregate counts and provider/restart status are exported to the gitignored
`.metal-cache/roam-moderation-proof.json`, which is also scanned for private data.
The id-wrap fixture places the allocator at 65530 and performs 16 signed joins,
checking wrap and duplicate live ids without 65,536 redundant signing operations.

`roam_security_test.py` proves the review fixes against the production provider and
loopback relay. The original implementation failed all four ACL cases (existing
directory/file grants and inherited directory/file grants), three of ten hybrid-alias
checks (including actual ban evasion), and all three public-identifier control cases.
The repaired implementation passes those same checks: ACL 4/0, alias 10/0 and
public-identifier 3/0 cases/failures. Public-identifier controls cover all-session
kick/ban, disconnected bans, reconnect, restart enforcement and unban. Test scratch
directories use an existing platform temporary root with space for the Unix-socket
path instead of assuming a platform-specific directory exists.

Pending host evidence: Linux/OpenSSL production build and container execution,
actual VPS dump/swap/backup/provider-retention inspection, public activation and
capacity/reachability checks. No public bind or game execution is part of this work.
