# Standalone relay operations

Deploy on any Linux VPS that supports the native systemd or Docker requirements
below, with UDP 47800 and a service limit of 100 simultaneous clients. Validate
capacity on the selected host before public activation. Provisioning, credentials
and public activation are separate operator actions. No install or activation
happens when building or running the offline suite.

Implementation plan: strict CLI validation and signal cleanup; a dependency-closed
standalone build; bounded native/container launchers; loopback regression, mutation
and deployment checks. Admission/abuse controls are a separate prerequisite for a
public service. A local harness result establishes no VPS capacity or Internet reachability.

## Build and provenance

No game data, generated game tables, Apple frameworks or game executable is needed.
A C11 compiler is required on macOS; Linux also needs math and OpenSSL crypto development
libraries (Debian: build-essential and libssl-dev). From the checkout root:

```sh
sh runtime/roam_relay_build.sh /tmp/roam_relay
/tmp/roam_relay --port 0 --seconds 1
sh runtime/roam_relay_build.sh --stage /tmp/relay-source
```

The staging destination must be empty. Compiler dependency discovery includes all
transitive local headers of exactly roam_relay_main.c, roam_core.c and roam_relay.c;
new admission headers are included automatically after integration. Staging includes
only those sources/headers, the build script, Dockerfile and test driver. The driver
builds a fresh-client peer with the real session codec; it is excluded from the
production image. Build from that stage to prove independence from private assets.

Retain the source revision, dirty diff, staged source archive, compiler version,
platform/architecture, build output, binary SHA-256, and release image content ID.
For native builds use `shasum -a 256 /tmp/roam_relay` on macOS or `sha256sum` on Linux.
Never identify a deployed artifact only by a branch name or mutable image tag.

No image digest is supplied or asserted here. On a trusted build machine, independently
review and obtain the real Debian bookworm-slim digest and retain its registry
provenance. Set DEBIAN_DIGEST to that `sha256:` digest. Docker's FROM references
always append it; omitting it fails instead of selecting a mutable default.
These commands require network/package retrieval on that later build machine:

```sh
cd /tmp/relay-source
# DEBIAN_DIGEST must already contain the independently verified digest.
docker build --target runtime --build-arg DEBIAN_DIGEST="$DEBIAN_DIGEST" --iidfile /tmp/relay.iid -t nfsmw-relay:reviewed .
docker build --target smoke --build-arg DEBIAN_DIGEST="$DEBIAN_DIGEST" --iidfile /tmp/smoke.iid -t nfsmw-relay:smoke .
export RELAY_IMAGE="$(cat /tmp/relay.iid)" RELAY_SMOKE_IMAGE="$(cat /tmp/smoke.iid)"
```

The base is pinned; package installation uses the configured Debian repositories.
This alone does not promise byte-reproducible future rebuilds. The image retains
base identity, compiler, source hashes, binary hash and build/runtime package lists
under /usr/share/nfsmw-relay/provenance. Archive them with the staged source and
final image. Use a retained package snapshot for reproducible rebuilds. Only the
production `runtime` target is deployed; the `smoke` target adds the peer binary.
The launchers accept registry `@sha256:` references or local `sha256:` content IDs
and prohibit implicit pulls. Transfer reviewed images with docker save/load, or
explicitly pull the reviewed digest before using them.

## Local proof

CLI defaults: bind 127.0.0.1, port 47800, near 32, max 100, indefinite duration.
Port 0 selects an ephemeral port and the startup line reports the actual endpoint,
effective near/max/duration and wire version. Numeric values accept decimal digits
only. Port: 0..65535; near: 1..32; max: 1..65000; seconds: 0..86400 (0 is indefinite).
Unknown, duplicate, missing, malformed and overflowing options exit 2 before opening
a socket. The generic allocator's larger cap is for controlled load work; shipped
service launchers fix max to 100. Raising it requires reviewed launchers and target-host proof.

SIGINT/SIGTERM stop after the current bounded service iteration, free relay state,
close the socket and exit 0. The service supervisors allow five seconds before
forced termination. Startup/resource/listener failures exit 1. Stats every ten
seconds are aggregate counts, without persistent player addresses or packet logs.
Core dumps are disabled by both launchers. The hosting provider still sees traffic.

The offline suite discovers roam_relay_cli_test.py and roam_relay_deploy_test.py.
The CLI deadline test runs serially. It checks validation before any socket call,
actual ephemeral ports, defaults, port conflict, finite duration, both signals,
port reuse and fresh admission after restart. Unchecked parsing and ignored shutdown
are deliberately introduced in temporary binaries and must fail those assertions.
The deployment test runs fixture launchers without Docker/network access and checks
header staging; its passing result is **manifest/fixture proof**, not Linux proof.

On Linux with Docker available to an unprivileged operator, run:

```sh
runtime/relay/check.sh --loopback
```

Set the two content IDs above first. This checks the actual production image, UDP
47800, orderly SIGTERM exit 0, restart on the same endpoint and a fresh client after
restart. The relay uses `--network none`; peers join that isolated namespace and
bind only loopback. No ports are published. The check has no public bind option and
does not pull images. A passing check proves manual container stop/start recovery;
test the chosen supervisor's crash recovery separately on the target VPS. Linux
build, container execution and target-host recovery remain unproved until those
commands actually run.

## Clean native installation (Linux with systemd 245 or newer)

Choose either native systemd or Docker below for a host/endpoint, never both.
Build/review a Linux binary first, then as an administrator create the dedicated
nfsmw-relay system user and group, without login or home. Install the immutable
release under /opt/nfsmw-relay/releases/RELEASE/roam_relay (root-owned, mode 0755)
and point /opt/nfsmw-relay/current at it. Preserve the previous release and hashes.
Copy relay.conf.example to /etc/nfsmw-relay/relay.conf, root-owned mode 0644;
leave RELAY_BIND=127.0.0.1. It is an EnvironmentFile, without shell commands.
Install nfsmw-relay.service under /etc/systemd/system/.

Before starting, create /etc/systemd/journald@nfsmw-relay.conf.d/bounds.conf:

```ini
[Journal]
Storage=persistent
SystemMaxUse=16M
RuntimeMaxUse=8M
SystemKeepFree=256M
MaxFileSec=1day
```

These are aggregate namespace limits (at most 24 MiB combined persistent/volatile
journal targets, with journal file granularity overhead); keep additional filesystem
headroom. The unit selects that namespace and rate limits output to 20 entries per
30 seconds. Retain no separate unbounded stdout files. Then run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now nfsmw-relay.service
sudo systemctl status nfsmw-relay.service
sudo journalctl --namespace=nfsmw-relay -u nfsmw-relay.service -n 20
```

The relay runs unprivileged with read-only filesystem access, no capabilities,
128 MiB memory, no swap, 16 tasks, 256 files and no core dumps. The unit blocks
non-loopback IP traffic. systemd alone restarts failed processes, with a bounded
start burst. `systemctl restart nfsmw-relay` is the orderly restart. For a crash
recovery drill use `systemctl kill --signal=SIGKILL nfsmw-relay`, then verify the
new PID and fresh local client admission; do this only on the selected test host.
After repeated startup failures, repair the cause and run `systemctl reset-failed
nfsmw-relay` before starting again. SIGTERM intentionally exits successfully.

## Container installation alternative

Use Linux Docker host networking with a reviewed local image. An unprivileged
operator must already have access to that daemon; Docker socket access is privileged
administration. This path assumes host networking reaches the actual host namespace;
rootless networking needs separate qualification. Export RELAY_IMAGE and RELAY_BIND
from the reviewed configuration, then run runtime/relay/run.sh without arguments.
The script rejects UID 0. The container process is UID/GID 65532 with all capabilities
dropped, no privilege escalation, a read-only root, no volumes, 128 MiB memory and
swap ceiling, 16 tasks, 256 files and no cores. UDP 47800 listens only on host loopback
by default. Docker alone supervises this alternative (`on-failure:5`); do not wrap it
in an additional restarting systemd unit. Logs use Docker's local driver, three 4 MiB
files per one named container (12 MiB configured aggregate plus metadata). No peers,
Python or test driver are in the production image.

Orderly restart: `docker restart --time 5 nfsmw-relay`. Inspect the new startup and
aggregate counters with `docker logs --tail 20 nfsmw-relay`. SIGTERM exits 0;
unexpected nonzero exits trigger Docker's bounded restart policy. After exhausted
retries, investigate then explicitly restart. Recreate to change image/configuration:
stop with a five-second timeout, remove only this container, then rerun run.sh.

## Rollback, disk bounds and recovery

Native rollback: stop the unit, repoint current to the retained previous reviewed
release, start, check the reported loopback endpoint and fresh client admission.
Container rollback: stop/remove this container, export the retained previous image
content ID, rerun run.sh, then verify admission. Restart loses transient sessions;
clients reconnect, and no persistent player-address database needs recovery.

Keep only current and previous release/images and their provenance on the service
host. Build elsewhere; do not accumulate build caches there. Put service artifacts
and Docker data on separately sized filesystems or quotas (for example 1 GiB native
releases, 8 GiB Docker data, adjusted after measuring reviewed images). Log limits
bound service logs; quotas bound aggregate image/cache/metadata disk growth. Monitor
free space and memory, restart count, client/rate/error counters and tick time.
At disk pressure, stop restart loops, inspect the bounded journal/container tail,
remove reviewed obsolete images/cache and recover space before restarting. Avoid
unbounded packet captures, core files, address logs or automatic prune of rollback images.

Port conflict: exit 1 with a bind error is intentional; there is no SO_REUSEPORT.
Inspect `ss -lunp 'sport = :47800'` on Linux or `lsof -nP -iUDP:47800` on macOS.
Stop the conflicting service deliberately, or choose another port for a manual local
CLI test. Never kill unrelated processes. Confirm both fresh admission and port reuse
after the planned service restart, rather than accepting a startup line alone.

## Separate public activation

Public activation is manual and occurs after admission/privacy integration, target
VPS crowd/spread/churn capacity tests at 100 clients, restart recovery and two-network
reachability proof. No shipped test enables it. For native systemd, deliberately
change RELAY_BIND to 0.0.0.0 and override the unit's localhost-only IP allow policy.
For the container path, deliberately export RELAY_BIND=0.0.0.0 before recreating it.
Only then configure provider and host firewalls for inbound **UDP 47800** (TCP is not
needed), confirm administration remains restricted, and verify from a second network.
No router/SSH/payment/provider changes are automated. Reverting to loopback and
closing the firewall rule withdraws public access. Reconfirm provider terms before
deployment; initial hosted capacity is a measured operating decision.
