# env.sh - sourced by the runtime/*.sh launchers (`. "$(dirname "$0")/env.sh"` from runtime/<script>.sh, before any cd):
# where the tools live, found relative to this checkout so nothing needs a user-specific path.
#   TOOLS_PY   the Python with the project's packages (default <checkout parent>/tools/.venv/bin/python)
#   NFS_PARENT the folder holding the checkout and tools/ (a worktree beside port/ shares it)
#   NFS_GAME   the game folder the launchers use (default <NFS_PARENT>/Need For Speed Most Wanted Black Edition)
#   NFS_RUNS   machine-wide slots and ledger (default <NFS_PARENT>/.runs)
NFS_PARENT=$(cd "$(dirname "$0")/../.." && pwd)
TOOLS_PY=${TOOLS_PY:-$NFS_PARENT/tools/.venv/bin/python}
: "${NFS_GAME:=$NFS_PARENT/Need For Speed Most Wanted Black Edition}"
: "${NFS_RUNS:=$NFS_PARENT/.runs}"
# nfs_need_py: stop with a clear message when the Python is missing (call it in scripts that run Python).
nfs_need_py() {
    [ -x "$TOOLS_PY" ] || { echo "TOOLS_PY: no Python at '$TOOLS_PY' - set TOOLS_PY=/path/to/python (needs capstone, numpy, pillow) or create <parent>/tools/.venv" >&2; exit 2; }
}
