#!/usr/bin/env python3
"""The setup kit: turn your own installed copy of Need for Speed: Most Wanted (2005, PC, English Black Edition 1.3)
into the native Mac app. Run through ./setup.sh, which prepares the Python environment first.

    ./setup.sh "/path/to/Need for Speed Most Wanted" [--xenon-effects FILE] [--no-pools] [--out DIR] [--check-only]

Steps: check the game folder and speed.exe -> link the game (nothing in it is changed) -> fetch Ghidra and a Java
runtime (pinned hashes) -> analyse speed.exe once -> translate and build -> build the light pools and texture-pack
map from your files -> package dist/NFS Most Wanted Native.app. Everything stays on this Mac."""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = ROOT / "kit"
# Not under ~/Library/Caches: Ghidra 12.1.3 cannot find its extension points when installed there.
CACHE = Path.home() / "Library/NFSMW-Native-Setup"
LOG = ROOT / "build/setup.log"
PIN = "80774c2e5d619b4f120b48d4462896fd504c263399d203a238769cffde1d253c"
EXE_SIZE = 6029312
REQUIRED_DIRS = ("GLOBAL", "TRACKS", "CARS", "FRONTEND", "SOUND")
TOOLS = {
    "ghidra": {
        "url": "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.3_build/ghidra_12.1.3_PUBLIC_20260817.zip",
        "sha256": "93a5d11a9ad510622acaaf908c556a7b9b764d338e78a7567f3689bf5081fd54",
    },
    "jdk": {
        "url": "https://download.java.net/java/GA/jdk26.0.1/458fda22e4c54d5ba572ab8d2b22eb83/8/GPL/openjdk-26.0.1_macos-aarch64_bin.tar.gz",
        "sha256": "b2d57405194a312ed4ec6ec08e83b314d3fd2e425e895d704ec5ef8ea6059e17",
    },
}
ALLOW_GAPS = "MSVC 7.1 switch shapes; see docs/analysis.md"
PY = sys.executable


def say(msg):
    print(msg, flush=True)
    with LOG.open("a") as f:
        f.write(msg + "\n")


def fail(msg):
    say("\nSETUP STOPPED: " + msg)
    sys.exit(1)


# The environment's own tools (cmake, ninja from kit/requirements-dev.txt) come first on PATH for every step.
ENV = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"))


def run(args, **kw):
    say("  $ " + " ".join(str(a) for a in args))
    with LOG.open("a") as log:
        kw.setdefault("cwd", ROOT)
        kw.setdefault("env", ENV)
        r = subprocess.run([str(a) for a in args], stdout=log, stderr=subprocess.STDOUT, **kw)
    if r.returncode:
        fail(f"step failed (exit {r.returncode}); the last lines of {LOG} say why")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def normalized_exe(data):
    """The pinned image, or the same image with the Large Address Aware flag and a PE checksum set: clear both and
    the result must hash to the pin. Returns the pinned bytes, or None for any other file."""
    if len(data) != EXE_SIZE:
        return None
    if hashlib.sha256(data).hexdigest() == PIN:
        return data
    b = bytearray(data)
    pe = int.from_bytes(b[0x3c:0x40], "little")
    if pe + 0x5c > len(b) or b[pe:pe + 4] != b"PE\0\0":
        return None
    b[pe + 0x16] &= ~0x20 & 0xff           # IMAGE_FILE_LARGE_ADDRESS_AWARE
    b[pe + 0x58:pe + 0x5c] = b"\0\0\0\0"   # CheckSum (0 in the supported image)
    return bytes(b) if hashlib.sha256(b).hexdigest() == PIN else None


def check_game(game):
    if not game.is_dir():
        fail(f"{game} is not a folder")
    exe = next((p for p in game.iterdir() if p.name.lower() == "speed.exe"), None)
    if exe is None:
        fail(f"no speed.exe in {game}; point the setup at the installed game folder")
    names = {p.name.upper() for p in game.iterdir() if p.is_dir()}
    missing = [d for d in REQUIRED_DIRS if d not in names]
    if missing:
        fail(f"game folder incomplete: missing {', '.join(missing)}/")
    data = exe.read_bytes()
    image = normalized_exe(data)
    if image is None:
        fail("This speed.exe can't be used. The port is built for the PC Black Edition, version 1.3 (English). "
             "Nothing was changed.")
    laa = image != data
    say("  speed.exe: OK" + (" (4GB patch detected; the build uses the unpatched code)" if laa else ""))
    return exe, image


def link_game(game, image):
    """original/retail: a folder of links to the game's own entries, with the supported speed.exe image. The game
    folder itself is never written to."""
    dest = ROOT / "original/retail"
    if dest.is_symlink():
        dest.unlink()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for p in game.iterdir():
        if p.name.lower() == "speed.exe":
            continue
        (dest / p.name).symlink_to(p.resolve(), target_is_directory=p.is_dir())
    (dest / "speed.exe").write_bytes(image)
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "build/game-source.json").write_text(json.dumps({"game": str(game.resolve())}) + "\n")
    say(f"  linked {game} -> original/retail")


def fetch(name):
    spec = TOOLS[name]
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / spec["url"].rsplit("/", 1)[1]
    if not archive.exists() or sha(archive) != spec["sha256"]:
        say(f"  downloading {archive.name}")
        tmp = archive.with_suffix(".part")
        with urllib.request.urlopen(spec["url"]) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        if sha(tmp) != spec["sha256"]:
            tmp.unlink()
            fail(f"{archive.name} did not match its pinned SHA-256; not used")
        tmp.rename(archive)
    out = CACHE / name
    if not out.exists():
        say(f"  unpacking {archive.name}")
        tmp = CACHE / (name + ".unpack")
        shutil.rmtree(tmp, ignore_errors=True)
        if archive.suffix == ".zip":
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(tmp)], check=True)
        else:
            tmp.mkdir()
            with tarfile.open(archive) as t:
                t.extractall(tmp, filter="tar")
        tmp.rename(out)
    if name == "ghidra":
        return next(out.glob("ghidra_*_PUBLIC"))
    return next(out.glob("jdk-*.jdk")) / "Contents/Home"


def ghidra_natives(ghidra, java):
    """The Ghidra release has no Apple Silicon decompiler; build Ghidra's own natives once (support/gradle)."""
    if any(ghidra.glob("Ghidra/Features/Decompiler/*/os/mac_arm_64/decompile")) or \
            any(ghidra.glob("Ghidra/Features/Decompiler/os/mac_arm_64/decompile")):
        return
    say("  building Ghidra's decompiler for Apple Silicon (once)")
    # Started as arm64 explicitly: an Intel parent would make Gradle's xcrun an Intel process, which the Command Line
    # Tools on macOS 27 can no longer run ("unable to load libxcrun ... need 'x86_64'").
    run(["/usr/bin/arch", "-arm64", "/bin/sh", ghidra / "support/gradle/gradlew", "--no-daemon", "-q", "buildNatives"],
        cwd=ghidra / "support/gradle",
        env={**ENV, "JAVA_HOME": str(java)})
    if not any(ghidra.glob("Ghidra/Features/Decompiler/*/os/mac_arm_64/decompile")):
        fail("Ghidra's decompiler did not build; see build/setup.log")


def find_ci(base, *parts):
    """base/parts..., matching each name without regard to case (Windows game folders mix cases)."""
    p = Path(base)
    for part in parts:
        if not p.is_dir():
            return None
        p = next((q for q in p.iterdir() if q.name.lower() == part.lower()), None)
        if p is None:
            return None
    return p if p.is_file() else None


def analysis_done():
    inputs = ROOT / "analysis/decompiled/inputs.json"
    try:
        return json.loads(inputs.read_text()).get("executable_sha256") == PIN and \
            (ROOT / "analysis/decompiled/speed.exe/functions.tsv").is_file()
    except (OSError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game", type=Path, help="your installed game folder (holds speed.exe, GLOBAL, TRACKS, ...)")
    ap.add_argument("--xenon-effects", type=Path, help="XenonEffects.tpk from your copy of the XenonEffects mod "
                    "(found automatically in the game folder's scripts folder)")
    ap.add_argument("--no-pools", action="store_true", help="do not build the light pools")
    ap.add_argument("--out", type=Path, default=ROOT / "dist", help="where to write the app (default dist/)")
    ap.add_argument("--check-only", action="store_true", help="only check the game folder and speed.exe")
    ap.add_argument("--ghidra-home", type=Path, help="use this Ghidra 12.1.3 instead of downloading one")
    ap.add_argument("--java-home", type=Path, help="use this Java runtime (21 or later) instead of downloading one")
    args = ap.parse_args()
    if platform.machine() != "arm64":
        fail("this Python runs under Rosetta (Intel). Run ./setup.sh, which picks a native Apple Silicon Python.")
    (ROOT / "build").mkdir(exist_ok=True)
    LOG.write_text("")
    game = args.game.expanduser().resolve()

    say("1/7 Checking your game folder")
    exe, image = check_game(game)
    if args.check_only:
        say("Check passed. Nothing was changed.")
        return
    say("2/7 Linking the game")
    link_game(game, image)

    say("3/7 Analysing speed.exe (once; a few minutes)")
    if analysis_done():
        say("  already analysed; reused")
    else:
        java = args.java_home or fetch("jdk")
        ghidra = args.ghidra_home or fetch("ghidra")
        ghidra_natives(ghidra, java)
        run([PY, "tools/analyze.py", "--ghidra-home", ghidra, "--java-home", java])

    say("4/7 Translating and building")
    run([PY, "tools/build.py", "--regenerate", "--allow-table-gaps", ALLOW_GAPS])
    run([PY, "tools/build.py", "--target", "plugins"])
    if not (ROOT / "build/SpeedRecomp.app/Contents/MacOS/SpeedRecomp").is_file():
        fail("the build finished without build/SpeedRecomp.app")

    say("5/7 Building the extras from your game")
    gen = ROOT / "build/generated"
    shutil.rmtree(gen, ignore_errors=True)
    gen.mkdir(parents=True)
    if args.no_pools:
        say("  light pools: skipped (--no-pools)")
    else:
        run([PY, "tools/effects/pools_bin.py", ROOT / "original/retail", "effects/lamp-classes.toml", gen / "clean-pools.bin"])
        run([PY, "tools/effects/pools_fx.py", "effects", ROOT / "original/retail/speed.exe", gen])

    say("6/7 Optional packs")
    r = subprocess.run([PY, "tools/setup_kit/texture_pack_map.py", ROOT / "original/retail", gen / "texture-pack.map"],
                       cwd=ROOT, capture_output=True, text=True, env=ENV)
    say("  " + (r.stdout.strip() or r.stderr.strip()))
    if r.returncode not in (0, 2):
        fail("the texture pack's name list could not be read")
    tpk, where = None, ""
    if args.xenon_effects:
        tpk = args.xenon_effects.expanduser()
        if not tpk.is_file():
            fail(f"{tpk} not found")
    else:
        # XenonEffects.tpk copied into the game folder (scripts/, as the mod and the Xbox 360 Stuff Pack place it).
        for sub_dir in ("scripts", "GLOBAL"):
            tpk = find_ci(game, sub_dir, "XenonEffects.tpk")
            if tpk:
                where = f" (found in your game folder's {sub_dir} folder)"
                break
    if tpk:
        (gen / "xenon/GLOBAL").mkdir(parents=True)
        shutil.copyfile(tpk, gen / "xenon/GLOBAL/XenonEffects.tpk")
        say(f"  sparks and light trails: XenonEffects.tpk added{where}")
    else:
        say("  sparks and light trails: not added (put XenonEffects.tpk in your game folder's scripts folder, "
            "or pass --xenon-effects FILE)")

    say("7/7 Packaging")
    run([PY, "tools/setup_kit/package.py", "--game", game, "--image", ROOT / "original/retail/speed.exe",
         "--generated", gen, "--out", args.out.expanduser().resolve()])
    app = args.out.expanduser().resolve() / "NFS Most Wanted Native.app"
    say(f"\nDone: {app}\nOpen it, or move it to Applications first. Your saves and settings will be in "
        "~/Library/Application Support/NFS Most Wanted Native/.")


if __name__ == "__main__":
    main()
