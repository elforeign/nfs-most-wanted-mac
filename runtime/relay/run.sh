#!/bin/sh
# Container deployment alternative; Docker is the only restart supervisor.
set -eu
[ "$#" = 0 ] || { echo 'usage: run.sh (settings via environment)' >&2; exit 2; }
[ "$(id -u)" != 0 ] || { echo 'run as an unprivileged account' >&2; exit 2; }
image=${RELAY_IMAGE:-}
# A registry digest or a local content ID, never a mutable tag. No implicit pulls.
if ! printf '%s\n' "$image" | LC_ALL=C grep -Eq '^([A-Za-z0-9][A-Za-z0-9._:/-]*@)?sha256:[0-9a-f]{64}$'; then
    echo 'RELAY_IMAGE must be a content digest' >&2; exit 2
fi
bind=${RELAY_BIND:-127.0.0.1}
case "$bind" in 127.0.0.1|0.0.0.0) ;; *) echo 'RELAY_BIND must be 127.0.0.1 or explicitly activated 0.0.0.0' >&2; exit 2 ;; esac
state=${RELAY_STATE_DIR:-}
case "$state" in /*) ;; *) echo 'RELAY_STATE_DIR must be an absolute dedicated directory' >&2; exit 2 ;; esac
case "$state" in *,*|*:*|*'
'*) echo 'invalid state directory' >&2; exit 2 ;; esac
[ -d "$state" ] && [ ! -L "$state" ] || { echo 'state directory must already exist without a symlink' >&2; exit 2; }
exec docker run --detach --pull=never --name nfsmw-relay \
    --network host --user 65532:65532 --read-only --cap-drop=ALL --security-opt=no-new-privileges \
    --tmpfs /run/relay:rw,nosuid,nodev,noexec,mode=0700,uid=65532,gid=65532,size=1m \
    --mount type=bind,src="$state",dst=/var/lib/relay \
    --memory=128m --memory-swap=128m --pids-limit=16 --ulimit nofile=256:256 --ulimit core=0:0 \
    --log-driver=local --log-opt max-size=4m --log-opt max-file=3 \
    --restart=on-failure:5 --stop-timeout=5 \
    "$image" --bind "$bind" --port 47800 --near 32 --max 100 --state-dir /var/lib/relay --admin-dir /run/relay
