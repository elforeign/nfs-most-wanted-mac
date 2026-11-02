#!/bin/sh
# Linux container proof only: isolated network namespace, loopback relay and fresh peers.
set -eu
[ "$#" = 1 ] && [ "$1" = --loopback ] || { echo 'usage: check.sh --loopback' >&2; exit 2; }
[ "$(id -u)" != 0 ] || { echo 'run as an unprivileged account' >&2; exit 2; }
[ "$(uname -s)" = Linux ] || { echo 'PENDING: run runtime/relay/check.sh --loopback on Linux'; exit 3; }
for image in "${RELAY_IMAGE:-}" "${RELAY_SMOKE_IMAGE:-}"; do
    if ! printf '%s\n' "$image" | LC_ALL=C grep -Eq '^([A-Za-z0-9][A-Za-z0-9._:/-]*@)?sha256:[0-9a-f]{64}$'; then
        echo 'set RELAY_IMAGE and RELAY_SMOKE_IMAGE to content digests' >&2; exit 2
    fi
done
name=nfsmw-relay-check-$$
created=0
cleanup() { if [ "$created" = 1 ]; then docker rm -f "$name" >/dev/null 2>&1 || true; fi; }
trap cleanup EXIT
trap 'exit 1' INT TERM
# Never expose a port or use host networking in this check.
docker run --detach --pull=never --name "$name" --network none \
    --user 65532:65532 --read-only --cap-drop=ALL --security-opt=no-new-privileges \
    --tmpfs /run/relay:rw,nosuid,nodev,noexec,mode=0700,uid=65532,gid=65532,size=1m \
    --tmpfs /var/lib/relay:rw,nosuid,nodev,noexec,mode=0700,uid=65532,gid=65532,size=1m \
    --memory=128m --memory-swap=128m --pids-limit=16 --ulimit nofile=256:256 --ulimit core=0:0 \
    --log-driver=local --log-opt max-size=4m --log-opt max-file=3 --restart=no --stop-timeout=5 \
    "$RELAY_IMAGE" --bind 127.0.0.1 --port 47800 --near 32 --max 100 --state-dir /var/lib/relay --admin-dir /run/relay >/dev/null
created=1
[ "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$name")" = none ]
peer() {
    docker run --rm --pull=never --network "container:$name" --user 65532:65532 --read-only \
        --cap-drop=ALL --security-opt=no-new-privileges --memory=128m --memory-swap=128m --pids-limit=16 \
        --ulimit nofile=256:256 --ulimit core=0:0 --log-driver=none --restart=no \
        --entrypoint /peer "$RELAY_SMOKE_IMAGE" 47800
}
peer
docker stop --time 5 "$name" >/dev/null
[ "$(docker inspect --format '{{.State.ExitCode}}' "$name")" = 0 ]
docker logs --tail 10 "$name" 2>&1 | grep -q 'stopped signal 15'
docker start "$name" >/dev/null
peer
docker stop --time 5 "$name" >/dev/null
[ "$(docker inspect --format '{{.State.ExitCode}}' "$name")" = 0 ]
docker logs --tail 10 "$name"
echo 'RELAY CONTAINER PASS: isolated loopback; SIGTERM exit 0; restart; fresh admission; UDP 47800'
