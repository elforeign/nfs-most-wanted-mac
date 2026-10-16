#!/usr/bin/env python3
"""The setup kit: turn your own installed copy of Need for Speed: Most Wanted (2005, PC, English Black Edition 1.3)
into the native Mac app. Run through ./setup.sh, which prepares the Python environment first.

    ./setup.sh "/path/to/Need for Speed Most Wanted" [--xenon-effects FILE] [--no-pools] [--out DIR] [--check-only]

Steps: check the game folder and speed.exe -> link the game (nothing in it is changed) -> fetch Ghidra and a Java
runtime (pinned hashes) -> analyse speed.exe once -> translate and build -> build the effects, the light pools and
the Xbox 360 texture map and the Mods folder's texture list from your files -> package dist/NFS Most Wanted
Native.app. Everything stays on this Mac."""
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
# 2.1: the HD texture pack step (tools/hd_pack) runs only when asked: --hd-textures runs it, --no-hd-textures skips
# it; with neither flag this default decides (owner D-169 (9): on; 3.0, owner D-185: OFF, so the game is playable about
# 10 minutes after Build; the Setup app's toggle and a later run add it).
HD_DEFAULT_ON = False
# 2.0.4: the shader warm-up (tools/warm_seed; about 25-35 minutes of offscreen drives). 3.0 (owner D-185): OFF by
# default like the HD textures; --shader-seed (the Setup app's "Prepare the shaders" toggle) makes it.
WARM_DEFAULT_ON = False
HD_CACHE = CACHE / "hd"
# 3.0 (owner D-172/D-173): the HD HUD step (tools/hd_pack/hud_pack.py; minutes, no model) has its own toggle, on by
# default; the material set and the wall relief are made inside the HD textures step (they need its pictures).
HUD_CACHE = CACHE / "hud"


def say(msg):
    print(msg, flush=True)
    with LOG.open("a") as f:
        f.write(msg + "\n")


def fail(msg):
    say("\nSETUP STOPPED: " + msg)
    sys.exit(1)


# The environment's own tools (cmake, ninja from kit/requirements-dev.txt) come first on PATH for every step.
ENV = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"))


def toolchain_module():
    """The kit's compiler check (kit/tools/toolchain.py, 3.0.4, owner D-203a)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("toolchain", KIT / "tools/toolchain.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_toolchain():
    """3.0.4 (D-203a): every player's Setup compiles the game with their own compiler. Log whether it is the one the
    release was tested with (Save bug report attaches this log), stop below the kit's minimum, and make CMake use
    the same /usr/bin/clang the check read (not a Homebrew clang earlier on PATH)."""
    tc = toolchain_module()
    ok, line = tc.check(tc.read_spec(), *tc.probe())
    say("  " + line)
    if not ok:
        fail(line.split(": ", 1)[1])
    ENV.update(tc.COMPILER_ENV)


def release_version():
    """The version this source is ("## 3.0.4 — ..." at the top of CHANGELOG.md), baked into the game so its log
    says "(kit 3.0.4)": a source archive has no git history for CMake to describe."""
    top = next((l for l in (ROOT / "CHANGELOG.md").read_text().splitlines() if l.startswith("## ")), "")
    return top[3:].split()[0] if top[3:].split() else "unknown"


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


# Where Setup looks for the player's own Xbox 360 movies inside the game folder (any case).
MOVIE_FOLDERS = ("X360-Movies", "USA-Movies")


def game_movies_line(game):
    """2.0.3: what the game's own MOVIES folder holds (the app plays both kinds natively): the PC movies (*_pal or
    *_ntsc from a retail copy) and the converted Xbox 360 ones (the Converted Movies pack: 1280x720 *_ntsc.vp6 with
    the EA HD bumper). Counts by name only; nothing is read or changed."""
    folder = find_dir_ci(game, "MOVIES")
    names = [q.name.lower() for q in folder.iterdir() if q.is_file()] if folder else []
    pal = sum(n.endswith("_english_pal.vp6") for n in names)
    ntsc = sum(n.endswith("_english_ntsc.vp6") for n in names)
    if not pal and not ntsc:
        return "game movies: none in your game folder's MOVIES folder (the game starts at the title screen)"
    kinds = []
    if pal:
        kinds.append(f"{pal} PC (European)")
    if ntsc:
        kinds.append(f"{ntsc} {'converted Xbox 360' if 'eahd_bumper_english_ntsc.vp6' in names else 'PC (US)'}")
    return "game movies: " + ", ".join(kinds) + " in MOVIES; they play in the game"


# The PC game's own FRONTEND/FrontB.lzc (retail 1.3) and the Xbox 360 Stuff Pack's.
FRONTB_PC = (2921586, "3e2dcbb970ad9ec607ed516143f42a6e39a68af8d1e216173c09cdd2ec362fe7")
FRONTB_STUFF = 13895352


def front_end_line(game):
    """2.0.4: whether the game folder's own FrontB.lzc is the PC original; a copy changed by a mod is used as it is
    when the Stuff Pack's front end is off (said, never changed: the game folder is never written to)."""
    f = find_ci(game, "FRONTEND", "FrontB.lzc")
    if not f:
        return "PC front end: FRONTEND/FrontB.lzc not found in your game folder"
    size = f.stat().st_size
    if size == FRONTB_PC[0] and hashlib.sha256(f.read_bytes()).hexdigest() == FRONTB_PC[1]:
        return "PC front end: your game's own FrontB.lzc is the original"
    what = "the Stuff Pack's (installed into the game folder)" if size == FRONTB_STUFF else "changed by a mod"
    return (f"PC front end: your game's own FrontB.lzc is not the original ({size} bytes, {what}); with the Xbox 360 "
            "front end off the game uses it as it is (restore the original from your backup to get the PC menus)")


# The PC game's own GLOBAL/GlobalB.lzc (retail 1.3): the front end's file that also holds car and gameplay tables.
GLOBALB_PC = (1520744, "696549f5551024617fb9b2743165863ee091e82b1a32e57e0949bc7777a0fde8")


def global_b_line(game, x360fe):
    """2.0.4 (run 28 review S3): the Stuff Pack front end overlays GlobalB.lzc, which also holds car and gameplay tables:
    a player's own modded GlobalB is then not used while the front end is on. Say so (never changed: D-140)."""
    pack = Path(x360fe) / "GLOBAL/GlobalB.lzc"
    f = find_ci(game, "GLOBAL", "GlobalB.lzc")
    if not pack.is_file() or not f:
        return None
    size = f.stat().st_size
    if size == GLOBALB_PC[0] and hashlib.sha256(f.read_bytes()).hexdigest() == GLOBALB_PC[1]:
        return None
    if size == pack.stat().st_size and f.read_bytes() == pack.read_bytes():
        return None
    return (f"Note: your game's own GLOBAL/GlobalB.lzc is changed by a mod ({size} bytes). While the Xbox 360 front end "
            "is on, the Stuff Pack's GlobalB.lzc is used instead, so changes it holds (cars, gameplay tables) do not "
            "apply; F10 -> Xbox 360 -> Xbox 360 front end off uses yours (restart)")


# The PC game's own GLOBAL/attributes.bin (retail 1.3): the attribute database (car and gameplay values).
ATTRIBUTES_PC = (689728, "3d0362f2c052b2f93576852f504cb7806b14eefbf83b8531d45dc1d430ec6337")


def attributes_line(game):
    """2.0.4 (run 28, a player report): a game folder with the Xbox 360 Stuff Pack (or another mod) installed over it
    has a changed GLOBAL/attributes.bin; careers saved with the original files are then refused as "damaged" (and the
    reverse). Said, never changed (D-140). Seen on the owner's folder: the same save loads with the original files."""
    f = find_ci(game, "GLOBAL", "attributes.bin")
    if not f:
        return None
    size = f.stat().st_size
    if size == ATTRIBUTES_PC[0] and hashlib.sha256(f.read_bytes()).hexdigest() == ATTRIBUTES_PC[1]:
        return None
    return (f"Note: your game's GLOBAL/attributes.bin is not the original ({size} bytes): the Xbox 360 Stuff Pack or "
            "another mod was installed over the game folder. The game then refuses careers saved with the original "
            "files (\"appears to be damaged\"), and the reverse; the save files themselves are not changed. To use those "
            "careers, put the original GLOBAL folder back and build again (keep the Stuff Pack download as its own "
            "folder: the setup takes its front end from there)")


MOD_NAMES = ROOT / "build/modnames/known.tsv"


def mod_names_list(*extra, out=MOD_NAMES, timeout=1800):
    """2.0.4: write the Mods folder's game texture list (tools/setup_kit/mod_names.py) from the linked game files and
    any extra folders that exist. Returns the list's path, or None (said; the setup goes on)."""
    out.unlink(missing_ok=True)
    roots = [ROOT / "original/retail"] + [p for p in extra if Path(p).is_dir()]
    try:
        r = subprocess.run([PY, Path(__file__).with_name("mod_names.py"), out, *roots], cwd=ROOT, capture_output=True, text=True,
                           env=ENV, timeout=timeout)
        line = (r.stdout.strip() or r.stderr.strip()).splitlines()[-1:] or ["no output"]
        ok = r.returncode == 0 and out.is_file()
    except (OSError, subprocess.TimeoutExpired) as e:
        line, ok = [str(e)], False
    if ok:
        say("  " + line[0])
        return out
    say("  mods name list: not made (" + line[0].strip() + "); texture mods in the Mods folder still load, unchecked")
    return None


def stream(cmd, prefix):
    """Run a generator step, its output to the log; lines starting with prefix are shown. Returns the exit code."""
    with LOG.open("a") as log:
        log.write("  $ " + " ".join(str(a) for a in cmd) + "\n")
        p = subprocess.Popen([str(a) for a in cmd], cwd=ROOT, env=ENV, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            log.write(line)
            if line.startswith(prefix):
                say("  " + line.strip())
        return p.wait()


def find_dir_ci(base, name):
    """base/name, a folder, matching the name without regard to case."""
    base = Path(base)
    if not base.is_dir():
        return None
    return next((q for q in base.iterdir() if q.is_dir() and q.name.lower() == name.lower()), None)


def analysis_done():
    inputs = ROOT / "analysis/decompiled/inputs.json"
    try:
        return json.loads(inputs.read_text()).get("executable_sha256") == PIN and \
            (ROOT / "analysis/decompiled/speed.exe/functions.tsv").is_file()
    except (OSError, ValueError):
        return False


def fresh_build_tree(root):
    """A new source version never reuses the earlier version's compiled tree: the unpacked source carries the commit
    time as its file dates, so Ninja could keep an older object that is newer than that and link a mixed app (the
    2.0.0 black screen: "plugin abi api_size 416 is not a usable prefix of this host's 408"). Returns the stamp file and
    this source's identity; the caller writes it once the build succeeded."""
    stamp = root / "build/source-stamp"
    version = next((l for l in (root / "CHANGELOG.md").read_text().splitlines() if l.startswith("## ")), "")
    ident = version + " " + hashlib.sha256((root / "kit/mods/pop_mod_api.h").read_bytes()).hexdigest()
    if not stamp.is_file() or stamp.read_text() != ident:
        if (root / "build/cmake").exists():
            say("  new source version: rebuilding from scratch (the earlier build is not reused)")
        # run 28 review F1: the warm-up's offscreen runner belongs to the earlier version too. 3.0.4 (run 38, D-202):
        # the prepared shaders (build/warm) are kept: warmseed.py reuses them only when this version's shader
        # generator, effects and formats are the ones they were made with (its fingerprint), else drives again
        if (root / "build/warm/seed/order.txt").is_file():
            say("  shader warm-up: the earlier version's prepared shaders are kept for checking")
        for p in (root / "build/cmake", root / "build/SpeedRecomp.app", root / "build/recomp/mods",
                  root / "build/recomp/pop_smoke"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
    return stamp, ident


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game", type=Path, help="your installed game folder (holds speed.exe, GLOBAL, TRACKS, ...)")
    ap.add_argument("--xenon-effects", type=Path, help="XenonEffects.tpk from your copy of the XenonEffects mod "
                    "(found automatically in the game folder's scripts folder)")
    ap.add_argument("--x360-movies", type=Path, help="a folder with your own copy of the US Xbox 360 disc's movies "
                    "(*_english_ntsc.wmv; default: an X360-Movies folder in your game folder, if it's there)")
    ap.add_argument("--x360-stuff", type=Path, help="your unzipped Xbox 360 Stuff Pack download (the Easy Installation "
                    "folder) for the Xbox 360 front end (default: an X360-Stuff or Easy Installation folder in your game "
                    "folder, if it's there)")
    ap.add_argument("--no-pools", action="store_true", help="do not build the light pools")
    warm = ap.add_mutually_exclusive_group()
    warm.add_argument("--shader-seed", dest="shader_seed", action="store_true", default=WARM_DEFAULT_ON,
                      help="prepare the game's shaders ahead (about 35 minutes; without it the first drive stutters "
                      "for a few minutes, once)")
    warm.add_argument("--no-shader-seed", dest="shader_seed", action="store_false", help="skip the shader warm-up")
    hd = ap.add_mutually_exclusive_group()
    hd.add_argument("--hd-textures", dest="hd_textures", action="store_true", default=HD_DEFAULT_ON,
                    help="make the HD texture pack from your game (about 1¼ hours on an M5 Pro, much longer on a base M1; "
                    "about 5 GB of downloads and cache; resumes when you run the setup again)")
    hd.add_argument("--no-hd-textures", dest="hd_textures", action="store_false", help="skip the HD texture pack")
    ap.add_argument("--no-hd-hud", dest="hd_hud", action="store_false", default=True,
                    help="skip the HD HUD (a few minutes; without it the original HUD shows)")
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
    check_toolchain()
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
    stamp, ident = fresh_build_tree(ROOT)
    version = release_version()
    run([PY, "tools/build.py", "--regenerate", "--allow-table-gaps", ALLOW_GAPS, "--version", version])
    run([PY, "tools/build.py", "--target", "plugins", "--version", version])
    if not (ROOT / "build/SpeedRecomp.app/Contents/MacOS/SpeedRecomp").is_file():
        fail("the build finished without build/SpeedRecomp.app")
    stamp.write_text(ident)

    say("5/7 Building the effects and the extras from your game")
    gen = ROOT / "build/generated"
    shutil.rmtree(gen, ignore_errors=True)
    gen.mkdir(parents=True)
    # The rebuilt post-processing chain (our own programs in effects/; the HDR form for HDR output) and its pass list.
    run([PY, "tools/effects/gen_ssao.py", "--fx", gen / "clean-chain.fx"])
    run([PY, "tools/effects/gen_ssao.py", "--fx", gen / "clean-chain-hdr.fx", "--hdr"])
    run([PY, "tools/effects/fxgen.py", "effects/post-host.hlsl", "effects", gen / "post-host.fx"])
    shutil.copyfile(ROOT / "effects/clean-chain.txt", gen / "clean-chain.txt")
    if args.no_pools:
        say("  light pools: skipped (--no-pools)")
    else:
        run([PY, "tools/effects/pools_bin.py", ROOT / "original/retail", "effects/lamp-classes.toml", gen / "clean-pools.bin"])
        run([PY, "tools/effects/pools_fx.py", "effects", ROOT / "original/retail/speed.exe", gen])

    say("6/7 Optional packs")
    r = subprocess.run([PY, "tools/setup_kit/x360_textures_map.py", ROOT / "original/retail", gen / "x360-textures.map"],
                       cwd=ROOT, capture_output=True, text=True, env=ENV)
    say("  " + (r.stdout.strip() or r.stderr.strip()))
    if r.returncode not in (0, 2):
        fail("the Xbox 360 texture pack's name list could not be read")
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

    buttons = find_ci(game, "GLOBAL", "XtendedInputButtons.tpk")
    say("  button icons: XtendedInputButtons.tpk found in GLOBAL" if buttons else
        "  button icons: not added (optional: XtendedInputButtons.tpk in your game folder's GLOBAL folder)")
    say("  " + game_movies_line(game))

    movies_src, where = None, ""
    if args.x360_movies:
        movies_src = args.x360_movies.expanduser()
        if not movies_src.is_dir():
            fail(f"{movies_src} is not a folder")
    else:
        for name in MOVIE_FOLDERS:
            movies_src = find_dir_ci(game, name)
            if movies_src:
                where = f" (found in your game folder's {movies_src.name} folder)"
                break
    movies = ROOT / "build/x360-movies"
    shutil.rmtree(movies, ignore_errors=True)
    if movies_src:
        r = subprocess.run([PY, "tools/setup_kit/x360_movies.py", movies_src, movies], cwd=ROOT, capture_output=True,
                           text=True, env=ENV)
        say("  " + (r.stdout.strip() or r.stderr.strip()) + (where if r.returncode == 0 else ""))
        if r.returncode not in (0, 2):
            fail("the Xbox 360 movies could not be checked")
    else:
        say("  Xbox 360 movies: not added (put your own copy of the US Xbox 360 disc's movies in a folder named "
            "X360-Movies in your game folder, or pass --x360-movies FOLDER); the game's own PC movies play")

    # 2.0.4: the Xbox 360 Stuff Pack's front end (menus, title and loading art, garages) from the player's download,
    # as an overlay beside the mod; F10 -> Xbox 360 -> "Xbox 360 front end" turns it off at the next start.
    stuff_src = args.x360_stuff.expanduser() if args.x360_stuff else game
    if args.x360_stuff and not stuff_src.is_dir():
        fail(f"{stuff_src} is not a folder")
    r = subprocess.run([PY, "tools/setup_kit/x360_stuff.py", stuff_src, gen / "x360fe"], cwd=ROOT, capture_output=True,
                       text=True, env=ENV)
    for line in (r.stdout.strip() or r.stderr.strip()).splitlines():
        say("  " + line.strip())
    if r.returncode not in (0, 2):
        fail("the Xbox 360 Stuff Pack could not be checked")
    if r.returncode == 2 and not args.x360_stuff:
        say("  (optional: put the unzipped Stuff Pack download's Easy Installation folder in your game folder, "
            "or pass --x360-stuff FOLDER)")
    say("  " + front_end_line(game))
    al = attributes_line(game)
    if al:
        say("  " + al)
    gl = global_b_line(game, gen / "x360fe")
    if gl:
        say("  " + gl)

    # 2.0.4: the game texture list for the player's Mods folder (hashes and sizes only, from these game files and the
    # front-end overlay). Never fails the setup: without it every mod still loads, unchecked.
    mod_names = mod_names_list(gen / "x360fe")

    # 2.1: the HD texture pack, made on this Mac from the player's own files (tools/hd_pack; its own Python
    # environment, model and photos in ~/Library/NFSMW-Native-Setup/hd, outside the build tree, so a new run resumes).
    # Never fails the setup: without it the game's own textures show. 3.0: the material set and the wall relief follow
    # it, made from its pictures (each optional; without them those features stay off and the game says so).
    hd_pack, materials, relief = HD_CACHE / "pack", HD_CACHE / "materials", HD_CACHE / "relief"
    if not args.hd_textures:
        say("  HD textures: skipped" + (" (--no-hd-textures)" if HD_DEFAULT_ON else " (turn on Make HD textures, or "
            "pass --hd-textures, to make them)"))
    else:
        say("  HD textures: making the HD texture pack (about 1¼ hours on an M5 Pro, much longer on a base M1)")
        hd_cmd = [PY, "tools/hd_pack/hdpack.py", "--game", game, "--cache", HD_CACHE]
        if args.x360_stuff:
            hd_cmd += ["--stuff", stuff_src]
        rc = stream(hd_cmd, "HD textures:")
        if rc and (hd_pack / "index.tsv").is_file():   # only a finished earlier run writes index.tsv
            say("  HD textures: not finished this time (see build/setup.log); the pack made earlier is used")
        elif rc or not (hd_pack / "index.tsv").is_file():
            say("  HD textures: skipped (see build/setup.log); the game's own textures show")
        if (hd_pack / "index.tsv").is_file():
            say("  HD textures: making the material set and the wall relief (about 3-10 minutes)")
            if stream([PY, "tools/hd_pack/materials.py", "--hd-cache", HD_CACHE, "--out", materials], "HD textures:") \
                    or not (materials / "index.tsv").is_file():
                say("  HD textures: material set skipped (see build/setup.log); road detail and the wall sun sheen stay off")
            if stream([PY, "tools/hd_pack/relief.py", "--hd-cache", HD_CACHE, "--out", relief], "HD textures:") \
                    or not (relief / "index.tsv").is_file():
                say("  HD textures: wall relief skipped (see build/setup.log); the walls keep the game's look")
    have_hd = args.hd_textures and (hd_pack / "index.tsv").is_file()

    # 3.0 (D-173): the HD HUD, made on this Mac from the game's own HUD, minimap and font pictures (4x, edges redrawn)
    # with our own vector gauges and icons over them (tools/hd_pack/hud_pack.py). Minutes; never fails the setup.
    if not args.hd_hud:
        say("  HD HUD: skipped (--no-hd-hud); the original HUD shows")
    else:
        say("  HD HUD: making the HD HUD (a few minutes)")
        hud_cmd = [PY, "tools/hd_pack/hud_pack.py", "--game", game, "--out", HUD_CACHE]
        if args.x360_stuff:
            hud_cmd += ["--stuff", stuff_src]
        if stream(hud_cmd, "HUD textures:") or not (HUD_CACHE / "ui/index.tsv").is_file():
            say("  HD HUD: skipped (see build/setup.log); the original HUD shows")

    # 2.0.4: the shader warm-up, made on this Mac (offscreen Quick Races). Never fails the setup: without it the first
    # drive only stutters once. 3.0: after the packs, and with them, so it records the shaders they turn on.
    seed = ROOT / "build/warm/seed"
    if not args.shader_seed:
        say("  shader warm-up: skipped" + (" (--no-shader-seed)" if WARM_DEFAULT_ON else " (turn on Prepare the shaders, "
            "or pass --shader-seed, to make it)") + "; the first drive will stutter once")
    else:
        say("  shader warm-up: preparing the game's shaders (reused in seconds when an earlier build made them the same "
            "way; otherwise about 35 minutes, offscreen, no window)")
        warm_cmd = [PY, "tools/warm_seed/warmseed.py", "--cache", ROOT / "build/warm", "--out", seed]
        if have_hd:
            warm_cmd += ["--hd-pack", hd_pack, "--materials", materials, "--relief", relief]
        if stream(warm_cmd, "shader warm-up:") or not (seed / "order.txt").is_file():
            say("  shader warm-up: skipped (see build/setup.log); the first drive will stutter once")

    say("7/7 Packaging")
    pack = [PY, "tools/setup_kit/package.py", "--game", game, "--image", ROOT / "original/retail/speed.exe",
            "--generated", gen, "--out", args.out.expanduser().resolve()]
    if movies.is_dir():
        pack += ["--movies", movies]
    if mod_names:
        pack += ["--mod-names", mod_names]
    if args.shader_seed and (seed / "order.txt").is_file():
        pack += ["--warm-seed", seed]
    if have_hd:
        pack += ["--hd-pack", hd_pack, "--materials", materials, "--relief", relief]
    if args.hd_hud and (HUD_CACHE / "ui/index.tsv").is_file():
        pack += ["--hud-pack", HUD_CACHE]
    run(pack)
    app = args.out.expanduser().resolve() / "NFS Most Wanted Native.app"
    say(f"\nDone: {app}\nOpen it, or move it to Applications first. Your saves and settings will be in "
        "~/Library/Application Support/NFS Most Wanted Native/.")


if __name__ == "__main__":
    main()
