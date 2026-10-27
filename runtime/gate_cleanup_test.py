#!/usr/bin/env python3
"""Source only cleanup/guard blocks into shell fixtures; NEVER run gate, run_guarded or a game.
Pre-fix gate fail leaves sleeping mixsmoke/dstest fixtures (FAIL); current fail/TERM and normal completion leave
no owned process (PASS). The mix/launcher TERM chain uses production traps and a stub slot_release sentinel.
2026-10-25: old fail leaves 7/7 PIDs alive; fixed fail/TERM/success leave 0/7, release marker records 143.
Here pgrep reports sysmond unavailable; recorded PIDs + kill -0 prove the result, pgrep proof needs a host with sysmond available.
Run: $TOOLS_PY -I runtime/gate_cleanup_test.py
"""
from pathlib import Path
import os
import shlex
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent
PGREP_ERROR = None


def block(name):
    source = (ROOT / name).read_text()
    return source.split("# BEGIN offline cleanup checks", 1)[1].split("\n", 1)[1].split("# END offline cleanup checks", 1)[0]


def children(pid):
    global PGREP_ERROR
    result = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        if PGREP_ERROR is None:
            PGREP_ERROR = result.stderr.strip().replace("\n", "; ")
            print(f"pgrep unavailable: {PGREP_ERROR}; checking recorded fixture PIDs with kill -0")
        return None
    return [int(p) for p in result.stdout.split()]


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def until(predicate, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.03)
    raise AssertionError("fixture timed out")


def script(path, code):
    path.write_text("#!/bin/sh\n" + code)
    path.chmod(0o700)
    return shlex.quote(str(path))


def run_case(tmp, mode):
    tmp.mkdir()
    q = shlex.quote
    prefix = f"cd {q(str(tmp))}\n"
    guard_source = (ROOT / "run_guarded.sh").read_text()
    guard = guard_source.split("# A stoppable guard:", 1)[1]
    guard = guard[guard.index("(\n"):guard.index("guard=$!") + len("guard=$!")]
    # Only add PID recording to the copied guard; its process/trap/wait behavior stays the production behavior.
    guard = guard.replace('timer=$!; wait "$timer"', 'timer=$!; echo "$timer" > timer.pid; wait "$timer"')
    launcher = script(tmp / "run_guarded-stub.sh", prefix + '''
slot_release() { echo "released ${1:-}" > released; rm -f slot; }
''' + block("run_guarded.sh") + '''
echo $$ > launcher.pid
touch slot
secs=300
sleep 300 & pid=$!
echo $pid > game.pid
''' + guard + '''
echo $guard > guard.pid
touch ready
wait "$pid"; rc=$?; pid=
''')
    mix = script(tmp / "mixsmoke-stub.sh", prefix + block("mixsmoke.sh") + f'''
echo $$ > mix.pid
{launcher} & rg=$!
wait "$rg"
rg=
''')
    dstest = script(tmp / "dstest-stub.sh", prefix + '''
echo $$ > dstest.pid
sleep 300 & child=$!
echo $child > dstest-child.pid
trap 'kill "$child" 2>/dev/null; wait "$child" 2>/dev/null; exit 143' TERM INT HUP
wait "$child"
''')
    legacy = mode == "legacy-fail"
    cleanup = 'fail() { echo "GATE FAIL: $*"; exit 1; }\n' if legacy else block("gate.sh")
    end = 'fail "forced early refs failure"\n'
    if mode == "TERM":
        end = 'kill -TERM $$\n'
    elif mode == "success":
        # Simulate jobs completing normally before gate success clears its tracked PIDs.
        end = '''
kill -TERM "$mix_pid" "$dstest_pid"
wait "$mix_pid"; mix_pid=
wait "$dstest_pid"; dstest_pid=
echo "GATE PASS stub"
'''
    script(tmp / "gate-stub.sh", prefix + 'log=stub\n' + cleanup + f'''
{mix} & mix_pid=$!
{dstest} & dstest_pid=$!
while [ ! -f proceed ]; do sleep 0.03; done
''' + end)
    output = (tmp / "output").open("w")
    process = subprocess.Popen(["/bin/sh", str(tmp / "gate-stub.sh")], stdout=output, stderr=subprocess.STDOUT)
    owned = []
    try:
        names = ("mix", "dstest", "launcher", "game", "guard", "timer", "dstest-child")
        until(lambda: (tmp / "ready").exists() and all((tmp / f"{name}.pid").exists() and
                                                       (tmp / f"{name}.pid").stat().st_size for name in names))
        ids = {name: int((tmp / f"{name}.pid").read_text()) for name in names}
        # Include both the guard's timer and dstest's sleeping child in the proof.
        owned = list(ids.values())
        assert all(alive(pid) for pid in owned)
        before = children(process.pid)
        print(f"{mode}: pgrep -P {process.pid} before={before}; owned PIDs={owned}")
        assert before is None or set(before) >= {ids["mix"], ids["dstest"]}
        (tmp / "proceed").touch()
        rc = process.wait(timeout=15)
        assert rc == (0 if mode == "success" else 143 if mode == "TERM" else 1), rc
        if legacy:
            left = [pid for pid in owned if alive(pid)]
            after = children(ids["mix"])
            print(f"legacy-fail: pgrep -P {ids['mix']} after={after}; survivors={left} -> FAIL (expected)")
            assert ids["mix"] in left and ids["launcher"] in left and ids["game"] in left and ids["dstest"] in left
            assert not (tmp / "released").exists() and (tmp / "slot").exists()
        else:
            until(lambda: not any(alive(pid) for pid in owned))
            print(f"{mode}: pgrep -P {ids['mix']} after={children(ids['mix'])}; survivors=[]; slot released -> PASS")
            assert (tmp / "released").read_text().strip() == "released 143"
            assert not (tmp / "slot").exists()
    finally:
        # Clean up the deliberately leaked legacy fixtures through their production TERM chain.
        for name in ("mix", "dstest"):
            file = tmp / f"{name}.pid"
            if file.exists():
                try: os.kill(int(file.read_text()), signal.SIGTERM)
                except ProcessLookupError: pass
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        if owned:
            try:
                until(lambda: not any(alive(pid) for pid in owned), seconds=12)
            finally:
                for pid in owned:
                    if alive(pid):
                        try: os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError: pass
        output.close()


def main():
    with tempfile.TemporaryDirectory(prefix="gate_cleanup_") as tmp:
        for mode in ("legacy-fail", "fixed-fail", "TERM", "success"):
            run_case(Path(tmp) / mode, mode)
    print("GATE CLEANUP PASS: pre-fix leak reproduced; early fail, TERM, success stop all owned processes/release slot")


if __name__ == "__main__":
    main()
