#!/usr/bin/env python3
"""gpu_sandbox_test.py: runtime/gpu_sandbox.sb lets tests hard-link files they may already write, and still refuses a
hard link whose original lies outside the writable paths (a writable alias of an outside file).
Good profile: 2 checks pass. Broken copies: link allow block removed -> the inside link fails (1 failure); file
writes allowed everywhere (with links allowed) -> the outside link succeeds (1 failure). Exit 3 without sandbox-exec
or when an enclosing sandbox refuses nested profiles (exit 71, sandbox_apply EPERM).
AAC proof: the in-memory encoder exits 0 with the current profile; restoring the
old AudioComponentRegistrar denial exits 3 (codec factory unavailable). Both
children start fresh to avoid a cached registrar connection. No audio devices.
The test fails unless the exact exit-0/exit-3 pair is observed on a capable host.
"""
import os, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "runtime" / "gpu_sandbox.sb"


class SandboxUnavailable(RuntimeError):
    pass


def sandbox(profile, workdir, command):
    r = subprocess.run(["sandbox-exec", "-f", str(profile), "-D", f"WORKDIR={workdir}", *command],
                       capture_output=True, text=True, timeout=30)
    if r.returncode == 71 and 'sandbox_apply: Operation not permitted' in r.stderr:
        raise SandboxUnavailable(r.stderr.strip())
    return r


def run(profile, workdir, outside):
    inside = Path(tempfile.mkdtemp(prefix="sbtest."))
    try:
        (inside / "a").write_text("x")
        script = (f'ln "{inside}/a" "{inside}/b" && echo IN_OK || echo IN_NO; '
                  f'ln "{outside}/secret" "{inside}/s" 2>/dev/null && echo OUT_LINKED || echo OUT_NO')
        r = sandbox(profile, workdir, ["/bin/sh", "-c", script])
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


def check_aac(src, work):
    exe = work / 'aac'
    build = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(ROOT / 'runtime/gpu_sandbox_test.c'), '-framework', 'AudioToolbox',
                            '-o', str(exe)], capture_output=True, text=True, timeout=60)
    if build.returncode:
        print('FAIL AAC build: ' + build.stderr[-2000:])
        return 1
    old = work / 'old-aac.sb'
    old.write_text(src + '\n(deny mach-lookup (global-name "com.apple.audio.AudioComponentRegistrar"))\n')
    current = sandbox(PROFILE, work, [str(exe)])
    previous = sandbox(old, work, [str(exe)])
    ok = (current.returncode == 0 and 'PASS AAC encode:' in current.stdout
          and previous.returncode == 3 and 'SKIP: AAC encoder unavailable:' in previous.stdout)
    print(('PASS ' if ok else 'FAIL ') +
          f'AAC profile pair: current exit={current.returncode}, old exit={previous.returncode} (expected 0/3)')
    for name, result in (('current', current), ('old', previous)):
        print(f'AAC {name}: ' + (result.stdout + result.stderr).strip()[-1200:])
    return int(not ok)


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
        fails += check_aac(src, work)
        print("PASS gpu_sandbox links + AAC" if not fails else f"FAIL gpu_sandbox ({fails})")
        return 1 if fails else 0
    finally:
        shutil.rmtree(outside, ignore_errors=True); shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
