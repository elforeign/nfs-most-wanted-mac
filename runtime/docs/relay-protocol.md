# Relay admission and traffic policy

The v3 datagram header remains `RM | version u8 | type u8 | id u16 | seq u16`,
with little-endian integers. Maximum UDP payload is 1,200 bytes. STATE remains
40 bytes; a BUNDLE entry remains 44 bytes, at most 27 entries, at 20 Hz.
LOOK bodies must have exactly `7 + 4*count` bytes; trailing bytes are rejected.

## Return routability

A HELLO body is exactly `room u32 | name[16] | public-point[65]` (85 body
bytes, 93 including header), or those 85 bytes followed by `cookie[20] |
signature-length u8 | DER-signature[72]` (178 body bytes, 186 including header).
The public point is canonical uncompressed P-256; the DER signature is zero padded
after its declared length (1..72). All other lengths are rejected. Wire v2 clients
cannot join a v3 relay. The relay answers a cookie-less or invalid-cookie HELLO
with type 11, CHALLENGE: `issued_ms u32 | tag[16]`. Its complete UDP payload is
28 bytes, below either accepted HELLO size; it echoes sequence with id zero.
No slot, welcome, membership, state, name or appearance output occurs until a
valid cookie **and installation signature** pass. This includes fixtures,
loopback, embedded and standalone relays.

The signature uses ECDSA P-256 with SHA-256 over ASCII
`NFSMW relay installation proof` plus one NUL byte, version u8, all 85 initial
HELLO body bytes and the 20-byte cookie. The cookie binds the endpoint, so the
signature proves possession for that endpoint/room/public-key transcript. Public
identifiers are SHA-256 of the 65-byte public point; they alone authenticate nothing.
See [moderation/privacy](relay-moderation-privacy.md) for credential storage,
local administration, ban expiry, providers and retention limitations.

The tag is the first 16 bytes of HMAC-SHA256 over source IPv4 (four network-order
bytes), source UDP port (two network-order bytes), request version (one byte),
the exact 85 initial HELLO bytes, and eight little-endian absolute issuance timestamp
bytes. The wire carries only the low four timestamp bytes; validation reconstructs
the most recent non-future absolute timestamp and authenticates its full era.
CommonCrypto computes the MAC on macOS; OpenSSL computes it on Linux. The
32-byte process-local key comes from OS-backed `arc4random_buf` or OpenSSL
`RAND_bytes`; entropy failure fails relay construction. Provider/OS constant-time
comparison checks tags. Keys, cookies and source records are never persisted.
A monotonic 64-bit relay clock extends the continuously serviced 32-bit input
clock. Absolute age must be at most 10,000 ms; the authenticated era prevents a
captured cookie reviving after a 32-bit wrap, even with an unchanged key.
The relay rotates keys every 5,000 ms and accepts the current and immediately
previous key. Key retirement can therefore shorten a cookie's lifetime to
5,000 ms; clients recover through a fresh challenge. Missed rotation periods
discard obsolete keys, and entropy failure refuses cookie work. Changing address,
port, room, name, public point, version or restarting the relay also invalidates a cookie.
Rotation also tracks the OS's 64-bit monotonic clock independently, so a process
pause spanning a full input-clock wrap cannot preserve an otherwise recent-looking key.
The cookie proves return routability. The additional signature authenticates an installation, not a player account.

Clients retain the opaque cookie and sign its transcript on later HELLOs. A new challenge
resets welcome/id, membership and remote buffers, allowing an existing endpoint
to recover after restart. A valid repeated HELLO retains its client id and slot. A fixed 2,048-entry RAM-only
cache rejects consumed transcripts after disconnect before crypto/allocation; a fresh
challenge permits authenticated reconnect after a kick, while a ban prevents it.
WELCOME clears a previously reported error. The embedded probe retries after a
challenge and periodically if a response is lost. Old clients that cannot handle
CHALLENGE cannot join this relay.

Wrong-version packets are counted after the unauthenticated ingress limits.
Only requests of at least nine bytes can receive a nine-byte ERR_VERSION.
These errors share source/global reply limits with challenges, full and banned errors;
rotating ports or addresses does not bypass the global budget. ERR_FULL is sent
only after a valid-cookie HELLO and admission-budget check, and is nine bytes.
ERR_BANNED (code 5) is a nine-byte reply to a valid-cookie HELLO for a banned
installation, before signature work or allocation. Applying a ban also sends the
same bounded error to each existing session before removing it. The client clears
welcome/id, membership and remote poses and displays the explicit banned result;
a later WELCOME after unban clears it. Ban replies use the same source/global
reply and egress budgets, so delivery remains best effort under overload or loss.
All request responses fit the triggering request even with IPv4/UDP overhead.

## Default policy

Every rate is per second. Both packet and byte buckets must fund a datagram;
failure consumes neither bucket. One IPv4/UDP datagram costs one packet and
`UDP payload bytes + 28` wire bytes for unauthenticated ingress and global egress. Link-layer,
IPv4 options, fragmentation, tunnels and provider billing overhead are excluded.
Established-client limits retain their existing payload-byte accounting.

| Budget | Rate | Burst |
| --- | ---: | ---: |
| New client admissions, global | 20 | 100 |
| New client admissions, per source IPv4 | 10 | 100 |
| Challenges and bounded error replies, global | 100 | 100 |
| Installation signature verifications, global | 100 | 100 |
| Challenges and bounded error replies, per source IPv4 | 10 | 100 |
| Unauthenticated ingress packets | 8,000 | 512 |
| Unauthenticated ingress wire bytes | 2 MiB | 256 KiB |
| Global outbound packets | 8,192 | 512 |
| Global outbound wire bytes | 4 MiB | 256 KiB |
| Established-client packets | 40 | 20 |
| Established-client payload bytes | 8,192 | 4,096 |

An admitted endpoint with its matching client id pays only its own established
packet/payload budgets. All other traffic shares the unauthenticated ingress
budget. Empty packets, unknown endpoints and incorrect ids cannot spend an
admitted client's ingress credit, even when the admission budget is exhausted.

Source identity is the IPv4 address, excluding its port. At most 1,024 fixed RAM
admission records are created only after cookie validation. A record expires
60,000 ms after creation or its last successful new admission; invalid traffic,
repeated HELLOs and refused admissions never extend retention. Ticks also erase
idle records. A full source table rejects unknown validated sources rather than
evicting live records and resetting their credit. Client slots and source records
have separate lifetimes. Established HELLOs do not create source records or
consume new-admission credit. They pay established ingress when their id matches,
otherwise unauthenticated ingress, and common egress costs.
Pre-validation replies use 1,024 fixed IPv4 hash buckets, with collisions sharing
credit conservatively; they store no source identities and never allocate or
refresh admission records. Admission records plus reply buckets occupy 49,152
bytes on the 64-bit build. All replies also share the global challenge budget.
Full attempts consume admission credit to bound valid-cookie full-room floods.
Source/admission/capacity, ban and replay checks precede the expensive signature check.
Invalid-cookie traffic performs zero signature verifications. Verified installation
bans remove existing sessions and reject reconnects, including after restart.

Global/source buckets store integer milli-tokens. Initially `tokens=burst*1000`;
refill adds `elapsed_ms*rate`, saturating at `burst*1000`. A cost of N consumes
`N*1000`, preserving fractional credit. Products use uint64_t, elapsed time uses
modular uint32_t subtraction, and a backward clock sample grants no credit.
The continuously serviced relay keeps bucket samples within the signed half
of the 32-bit clock range; crossing clock wrap is supported. Established-client
buckets retain their existing float arithmetic. Failed socket sends consume
outbound budget, so errors cannot create extra transmission credit.

All output, including challenges, errors, WELCOME, MEMBERS, NAME, LOOK and
BUNDLE, goes through the same packet/byte ceiling. For any interval of T seconds,
transmitted wire bytes are at most `4 MiB*T + 256 KiB`, and packet attempts funded
by the bucket are at most `8192*T + 512`. Suppressed output is discarded rather
than queued without a bound. Each tick rotates recipients; each recipient rotates
membership/control/state priority after successful service. Pending membership
snapshots coalesce and retry. Control changes retain their periodic retries.
State samples are rebuilt from current state instead of accumulating a queue.
This preserves service among peers and message kinds under constrained output.

Service ticks before receiving if due, then receives at most 4,096 packets and
at most 2 ms of elapsed work, checked between receives. A final elapsed-time tick
check maintains progress during floods. The elapsed bound excludes no expensive
packet processing; a single receive/packet or tick can exceed it. Tick CPU remains
bounded by the configured client capacity and interest search, not this timer.

Aggregate counters also expose signature checks, authentication rejection/limits,
replay rejection, ban rejection and kicks. They expose invalid cookies, admission/source rejection, live source
records, dropped ingress, suppressed challenges, emitted challenges, suppressed
outbound packets/wire bytes, successful outbound wire bytes and receive yields.
They contain no source addresses or packet captures. `tx_bytes` retains successful
UDP payload bytes; `tx_wire_bytes` adds 28 bytes per successful datagram.

## Offline proof and fixture policies

The original premise was measured before editing: one 28-byte HELLO changed
`clients` from zero to one. The ASan/UBSan admission driver retains all 11,051
checks across the original seven cases, with zero normal failures. Thirteen mutations fail the named
cookie/allocation, admission, ingress, challenge, source, egress, recipient/message fairness, idle
expiry, service budget, parsing, error clearing and restart assertions
(39/3/3921/4004/1/1999/2/2/1/5/1/1/1 failures respectively).

Four further cases add 157 checks (11,208 total). On the pinned pre-fix relay,
one sender's empty datagrams dropped 14,400/14,400 admitted STATE packets over
6,000 ms and timed out 120/120 clients. Refreshing 1,024 spoofed sources every
30,000 ms rejected 3/3 legitimate sources. A captured cookie revived once at
age 4,294,967,297 ms. All three models now measure zero failures of those kinds.
Seven additional mutations restore ingress sharing, pre-validation record refresh,
modular cookie authentication, disabled rotation, disabled previous-key acceptance,
retry-driven retention and stale idle reply buckets; they produce 3/1/1/4/5/2/1
named failures respectively.
The clock/key tests also cover recent wrap, exact age boundaries, stale clock
samples, scheduled rotation, previous-key acceptance, retirement, recovery,
full-room retry expiry, reply credit after more than half a clock wrap of idle time,
and key retirement after a process pause spanning a full wrap.

At a fixed timestamp, 10,000 HELLOs allocate zero clients, drop 9,488 packets at
ingress, and emit 100 challenges: 5,600 IPv4/UDP bytes. Valid-cookie floods admit
100 immediately and 20 after one second. The one-second fake-send egress proof
measures 4,456,412 bytes against a 4,456,448-byte ceiling. The constrained fairness
fixture gives every one of 16 peers six state bundles over 16 seconds while
sending exactly 320 packets under a 20/s, burst-one policy. Loopback fixtures
count actual received wire bytes and packets and check the same rate-plus-burst
bounds. The embedded test retains sockets/cookies over a relay restart.

`roam_test_peer.h` completes the real challenge/retry/WELCOME path through a
counted transport; it never assigns admitted ids. The existing 1,000-client
throughput, churn, id-wrap and tier benchmarks explicitly construct a fixture
policy: global/source admission and challenge rates/bursts of 1,000,000;
ingress/egress rates and bursts of 1,000,000 packets and 128 MiB. These policies
retain cookies, strict parsing, source-table bounds and established-client
limits. They measure existing throughput controls separately from the default
public-service ceiling, which intentionally suppresses heavy 1,000-client loads.
The visual loopback fixture and embedded-server checks use default policy.

Consumers link roam_auth.c and, with the relay, roam_moderation.c. Linux links
OpenSSL libcrypto and libm; macOS links system Security and CoreFoundation.
The signed-HELLO fixture uses the same elevated policy for signature budgets.
The admission assertions above retain their traffic/cookie coverage with the
larger v3 HELLO; new installation/moderation and privacy controls are documented
in [relay-moderation-privacy.md](relay-moderation-privacy.md).
