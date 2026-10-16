#!/usr/bin/env python3
"""The compiler check a game's Setup runs before it builds (NFSMW run 37, owner D-203a).

Reads TOOLCHAIN.txt (the tested compiler and the minimum), asks the player's /usr/bin/clang and the macOS SDK for
their versions, and says one of three things: the tested compiler; another compiler at or above the minimum (the
game is built anyway, and both Setup's log and the game's own "[host] built with" line say which); or one below the
minimum (Setup stops with the way to update). A compiler that cannot be read is reported, not refused.

    python tools/toolchain.py            prints the line Setup would log; exit 1 below the minimum
"""
import re
import subprocess
import sys
from pathlib import Path

SPEC = Path(__file__).resolve().parents[1] / "TOOLCHAIN.txt"
CLANG = "/usr/bin/clang"
# CMake takes the compilers from these: Setup sets them so the build uses the compiler this check read, not
# whichever clang comes first on PATH (a Homebrew llvm, say).
COMPILER_ENV = {"CC": "/usr/bin/clang", "CXX": "/usr/bin/clang++", "OBJC": "/usr/bin/clang",
                "OBJCXX": "/usr/bin/clang++"}
APPLE_CLANG = re.compile(r"Apple clang version (\d+)\.(\d+)\.(\d+) \((clang-[\d.]+)\)")
UPDATE_HINT = ("update the Command Line Tools: Software Update in System Settings, or run  xcode-select --install  "
               "(an older macOS may need a newer macOS first)")


def read_spec(path=SPEC):
    """TOOLCHAIN.txt: `key = value` lines, # comments."""
    spec = {}
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            key, _, value = line.partition("=")
            spec[key.strip()] = value.strip()
    for key in ("tested_clang", "tested_sdk", "minimum_clang", "minimum_sdk"):
        if not spec.get(key):
            raise ValueError("%s: missing %s" % (path, key))
    return spec


def probe(clang=CLANG):
    """(first line of `clang --version`, SDK version) as the player's Mac reports them; "" when unreadable."""
    def out(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    version = out([clang, "--version"]).splitlines()
    return (version[0] if version else ""), out(["xcrun", "--sdk", "macosx", "--show-sdk-version"])


def major(version):
    m = re.match(r"(\d+)", version or "")
    return int(m.group(1)) if m else None


def check(spec, clang_line, sdk):
    """(ok, line): ok is False only for a compiler or SDK below the minimum."""
    m = APPLE_CLANG.search(clang_line)
    tested = "%s, macOS SDK %s" % (spec["tested_clang"], spec["tested_sdk"])
    if not m:
        return True, ("toolchain: could not read the compiler's version (%r, SDK %r); building anyway (tested with "
                      "%s)" % (clang_line, sdk or "unknown", tested))
    have = "Apple clang %s.%s.%s (%s), macOS SDK %s" % (m.group(1), m.group(2), m.group(3), m.group(4),
                                                        sdk or "unknown")
    sdk_major = major(sdk)
    if int(m.group(1)) < int(spec["minimum_clang"]) or (sdk_major is not None and
                                                         sdk_major < int(spec["minimum_sdk"])):
        return False, ("toolchain: %s is older than this game needs (Apple clang %s and macOS SDK %s or later); %s"
                       % (have, spec["minimum_clang"], spec["minimum_sdk"], UPDATE_HINT))
    if have == tested:
        return True, "toolchain: %s: the tested compiler" % have
    return True, ("toolchain: %s: not the tested compiler (tested with %s); the game is built anyway and its log "
                  "says so" % (have, tested))


def main():
    ok, line = check(read_spec(), *probe())
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
