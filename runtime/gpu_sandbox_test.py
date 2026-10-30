#!/usr/bin/env python3
"""gpu_sandbox_test.py: runtime/gpu_sandbox.sb lets tests hard-link files they may already write, and still refuses a
hard link whose original lies outside the writable paths (a writable alias of an outside file).
Good profile: 2 checks pass. Broken copies: link allow block removed -> the inside link fails (1 failure); file
writes allowed everywhere (with links allowed) -> the outside link succeeds (1 failure). Exit 3 without sandbox-exec
or when an enclosing sandbox refuses nested profiles (exit 71, sandbox_apply EPERM)."""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "runtime" / "gpu_sandbox.sb"


class SandboxUnavailable(RuntimeError):
    pass


def run(profile, workdir, outside):
    inside = Path(tempfile.mkdtemp(prefix="sbtest."))
    try:
        (inside / "a").write_text("x")
        script = (f'ln "{inside}/a" "{inside}/b" && echo IN_OK || echo IN_NO; '
                  f'ln "{outside}/secret" "{inside}/s" 2>/dev/null && echo OUT_LINKED || echo OUT_NO')
        r = subprocess.run(["sandbox-exec", "-f", str(profile), "-D", f"WORKDIR={workdir}", "/bin/sh", "-c", script],
                           capture_output=True, text=True)
        if (os.environ.get('NFS_GPU_SANDBOX') == '1' and r.returncode == 71
                and 'sandbox_apply: Operation not permitted' in r.stderr):
            raise SandboxUnavailable(r.stderr.strip())
        return r.stdout.split()
    finally:
        shutil.rmtree(inside, ignore_errors=True)


def check(profile, workdir, outside):
    out = run(profile, workdir, outside)
    fails = 0
    for ok, msg in (("IN_OK" in out, "link inside temp allowed"), ("OUT_NO" in out, "outside original refused")):
        print(("PASS " if ok else "FAIL ") + msg)
        fails += not ok
    return fails


def main():
    if not shutil.which("sandbox-exec"):
        print("SKIP: no sandbox-exec"); return 3
    # The outside original must sit outside the INNER profile's writable paths.
    # The checkout cache remains writable by an enclosing suite sandbox, while
    # the inner WORKDIR is a separate temp directory. Neither inner temp rule
    # covers this source, so the negative link check keeps the same boundary.
    cache = ROOT / '.metal-cache'
    cache.mkdir(exist_ok=True)
    outside = Path(tempfile.mkdtemp(prefix=".sbtest-outside.", dir=cache))
    work = Path(tempfile.mkdtemp(prefix="sbwork."))
    try:
        (outside / "secret").write_text("y")
        src = PROFILE.read_text()
        try:
            fails = check(PROFILE, work, outside)
        except SandboxUnavailable:
            print('SKIP: enclosing sandbox refuses nested sandbox-exec (sandbox_apply EPERM); host runs link checks and broken profiles')
            return 3
        broken = 0
        start = src.index("(allow file-link\n"); end = src.index(")\n", src.index("/private/var/folders", start)) + 2
        for name, text in (("link allow block removed", src[:start] + src[end:]),
                           ("writes allowed everywhere", src + "\n(allow file-write*)\n(allow file-issue-extension)\n(allow file-link)\n")):
            p = work / "broken.sb"; p.write_text(text)
            n = check(p, work, outside)
            print(f"broken copy ({name}): {n} failure(s)")
            broken += n == 0
        if broken:
            print("FAIL self-check: a broken copy passed"); fails += 1
        print("PASS gpu_sandbox links" if not fails else f"FAIL gpu_sandbox links ({fails})")
        return 1 if fails else 0
    finally:
        shutil.rmtree(outside, ignore_errors=True); shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
