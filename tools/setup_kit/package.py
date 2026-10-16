#!/usr/bin/env python3
"""Package the built app with your game data: dist/NFS Most Wanted Native.app.

    package.py --game DIR --image speed.exe --generated DIR --out DIR

The kit's build/SpeedRecomp.app (executable, frameworks, core mod) + the launcher and defaults of
tools/setup_kit/defaults + your game folder (APFS clone where possible; the files the game.toml [bundle] exclude
list names are left out) with the supported speed.exe image + the files the setup built (build/generated: the
effects, light pools, Xbox 360 texture map, XenonEffects texture). Mach-O files are signed ad hoc, inside out. Writes
build/package.json (what went in, with hashes of the code; game data is listed by name only)."""
import argparse
import hashlib
import json
import plistlib
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "NFS Most Wanted Native"
MAGIC = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def sha(p):
    with open(p, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def macho(p):
    with open(p, "rb") as f:
        return f.read(4) in MAGIC


def min_macos(p):
    """The binary's deployment target (LC_BUILD_VERSION minos), so Info.plist says what the build really needs."""
    import struct
    d = Path(p).read_bytes()[:1 << 16]
    if d[:4] != b"\xcf\xfa\xed\xfe":
        return None
    ncmds, off = struct.unpack_from("<I", d, 16)[0], 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", d, off)
        if cmd == 0x32:
            v = struct.unpack_from("<I", d, off + 12)[0]
            return f"{v >> 16}.{(v >> 8) & 255}"
        off += size
    return None


def unlock(path):
    """2.0.4: clear the Finder "Locked" flag (uchg) on the app's own copies. Some game folders have locked files (one
    tester's: server.dll and three GLOBAL files, locked since 2005); a copy keeps the flag, and then neither a rebuild
    (removing the old app) nor the Trash can delete it. Only ever called on the app's files, never on the game folder."""
    if Path(path).exists():
        subprocess.run(["/usr/bin/chflags", "-R", "nouchg", str(path)], check=False, capture_output=True)


def remove_app(path):
    """Delete an app this tool made earlier, locked game copies included."""
    unlock(path)
    shutil.rmtree(path)


def clone_tree(src, dst, exclude):
    """Copy the game folder: APFS clones (cp -c) when possible, so it costs almost no disk space on the same volume.
    The copies are unlocked (see unlock); the game folder itself is never changed."""
    dst.mkdir(parents=True)
    for p in sorted(src.iterdir()):
        if p.name.lower() in exclude or p.name.lower() == "speed.exe" or p.name.startswith("."):
            continue
        # run 28 review F8: the Stuff Pack download a player keeps in the game folder is Setup's input, not game data
        if p.is_dir() and (p.name.lower() == "x360-stuff" or p.name.lower().startswith("easy installation")):
            continue
        r = subprocess.run(["/bin/cp", "-cR", str(p), str(dst / p.name)], capture_output=True)
        if r.returncode:
            subprocess.run(["/bin/cp", "-R", str(p), str(dst / p.name)], check=True)
    unlock(dst)


def hd_pack_line(index):
    """The HD texture pack's index header and row count, or None when the app has no pack."""
    if not index.is_file():
        return None
    lines = index.read_text().splitlines()
    return [lines[0] if lines else "", sum(1 for line in lines if line and not line.startswith("#"))]


def seed_matches(seed):
    """3.0.4 (run 38, D-202): a seed goes in only when its stamp names this source's shader fingerprint (warmseed.py),
    as the developers' packager already required (harness make_warm_seed.sh)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("warmseed", ROOT / "tools/warm_seed/warmseed.py")
    ws = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ws)
    return ws.stamp_of(seed).get("fingerprint") == ws.fingerprint()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", type=Path, required=True)
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--generated", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--movies", type=Path, help="the verified Xbox 360 movies (setup.py step 6) for Resources/USA-Movies")
    ap.add_argument("--warm-seed", type=Path, help="the shader warm-up made on this Mac (tools/warm_seed/warmseed.py) for "
                    "Resources/ShaderWarmSeed")
    ap.add_argument("--hd-pack", type=Path, help="the HD texture pack made on this Mac (tools/hd_pack/hdpack.py) for "
                    "Resources/RemasterPack")
    ap.add_argument("--hud-pack", type=Path, help="3.0: the HD HUD made on this Mac (tools/hd_pack/hud_pack.py): its ui/ "
                    "and ui2/ go to Resources/RemasterPack/ui and ui2")
    ap.add_argument("--materials", type=Path, help="3.0: the material set made on this Mac (tools/hd_pack/materials.py) "
                    "for Resources/Materials")
    ap.add_argument("--relief", type=Path, help="3.0: the wall relief made on this Mac (tools/hd_pack/relief.py) for "
                    "Resources/PbrPilot")
    ap.add_argument("--mod-names", type=Path, help="the game texture list for the Mods folder (tools/setup_kit/"
                    "mod_names.py) for Resources/ModNames/known.tsv")
    a = ap.parse_args()
    built = ROOT / "build/SpeedRecomp.app"
    cfg = tomllib.loads((ROOT / "game.toml").read_text())
    exclude = {x.lower() for x in cfg.get("bundle", {}).get("exclude", [])}
    exclude |= {"x360-movies", "usa-movies"}   # the player's movie folder goes to Resources/USA-Movies, verified, below
    if subprocess.run(["/usr/bin/pgrep", "-f", f"{NAME}.app/Contents/MacOS/SpeedRecomp"], capture_output=True).returncode == 0:
        sys.exit("the app is running; quit it first")
    a.out.mkdir(parents=True, exist_ok=True)
    app = a.out / f"{NAME}.app"
    tmp = a.out / f".{NAME}.app.new"
    if tmp.exists():
        remove_app(tmp)
    shutil.copytree(built, tmp, symlinks=True)
    shutil.rmtree(tmp / "Contents/_CodeSignature", ignore_errors=True)
    res = tmp / "Contents/Resources"
    d = ROOT / "tools/setup_kit/defaults"
    shutil.copyfile(d / "Launch", tmp / "Contents/MacOS/Launch")
    (tmp / "Contents/MacOS/Launch").chmod(0o755)
    (res / "Defaults").mkdir()
    for n in ("registry.json", "mod-settings.json"):
        shutil.copyfile(d / n, res / "Defaults" / n)
    shutil.copyfile(ROOT / "mods/core/nfsmw/NativeOptions.ini", res / "Defaults/NativeOptions.ini")
    (res / "Guides").mkdir()
    for n in ("COMPANION_GUIDE.md", "SETTINGS.md", "TROUBLESHOOTING.md"):
        shutil.copyfile(ROOT / "docs" / n, res / "Guides" / n)
    for n in ("NOTICE", "LICENSE"):
        shutil.copyfile(ROOT / n, res / n)
    core = res / "mods/core/nfsmw"
    for p in a.generated.rglob("*"):
        if p.is_file():
            t = core / p.relative_to(a.generated)
            t.parent.mkdir(parents=True, exist_ok=True)
            # An APFS clone when the disk allows it (the Stuff Pack's front end is 160 MB), else a plain copy.
            if subprocess.run(["/bin/cp", "-c", str(p), str(t)], capture_output=True).returncode != 0:
                shutil.copyfile(p, t)
    if a.warm_seed and (a.warm_seed / "order.txt").is_file():
        if seed_matches(a.warm_seed):
            clone_tree(a.warm_seed, res / "ShaderWarmSeed", set())
        else:   # 3.0.4 (run 38, D-202): a seed kept from an earlier version that this build draws differently
            print("shader warm-up: the kept seed does not match this version; left out", flush=True)
    if a.hd_pack and (a.hd_pack / "index.tsv").is_file():
        # 2.1: the core mod finds Resources/Game/../RemasterPack/index.tsv by itself (remaster_tex.h).
        clone_tree(a.hd_pack, res / "RemasterPack", set())
    # 3.0: the packs Setup made beside the HD textures. Each is used only when finished (its index.tsv is written last);
    # the core mod looks for each one by itself and logs "... but this app has no ... pack" when it is missing.
    if a.hud_pack and (a.hud_pack / "ui/index.tsv").is_file():
        clone_tree(a.hud_pack / "ui", res / "RemasterPack/ui", set())
        if (a.hud_pack / "ui2/index.tsv").is_file():
            clone_tree(a.hud_pack / "ui2", res / "RemasterPack/ui2", set())
    if a.materials and (a.materials / "index.tsv").is_file():
        clone_tree(a.materials, res / "Materials", set())
    if a.relief and (a.relief / "index.tsv").is_file():
        clone_tree(a.relief, res / "PbrPilot", set())
    if a.mod_names and a.mod_names.is_file():
        (res / "ModNames").mkdir()
        shutil.copyfile(a.mod_names, res / "ModNames/known.tsv")
    if a.movies and a.movies.is_dir():
        # The game's own list (native/usa_movie_manifest.h) is checked again before each file plays.
        clone_tree(a.movies, res / "USA-Movies", set())
    game = res / "Game"
    clone_tree(a.game, game, exclude)
    shutil.copyfile(a.image, game / "speed.exe")
    version = tomllib.loads((ROOT / "mods/core/nfsmw/mod.toml").read_text())["version"]   # 3.0: one version number
    info = tmp / "Contents/Info.plist"
    p = plistlib.loads(info.read_bytes())
    p.update(CFBundleDisplayName=NAME, CFBundleName="NFSMW Native", CFBundleExecutable="Launch",
             CFBundleIdentifier="io.github.nfsmw-native-mac", CFBundleShortVersionString=version, CFBundleVersion="10",
             LSMinimumSystemVersion=min_macos(tmp / "Contents/MacOS/SpeedRecomp") or "13.0", NSHighResolutionCapable=True, LSApplicationCategoryType="public.app-category.racing-games")
    p.pop("CFBundleIconFile", None)
    info.write_bytes(plistlib.dumps(p))
    nested = sorted((q for q in tmp.rglob("*") if q.is_file() and not q.is_symlink() and macho(q) and "Resources/Game" not in str(q)),
                    key=lambda q: (-len(q.relative_to(tmp).parts), str(q)))
    for q in nested:
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(q)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(tmp)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(tmp)], check=True)
    signed = [str(q.relative_to(tmp)) for q in nested]
    if app.exists():
        remove_app(app)
    tmp.rename(app)
    core = app / "Contents/Resources/mods/core/nfsmw"
    manifest = {
        "app": str(app),
        "executable_sha256": sha(app / "Contents/MacOS/SpeedRecomp"),
        "core_sha256": sha(core / "nfsmw.dylib") if (core / "nfsmw.dylib").exists() else None,
        "speed_exe_sha256": sha(app / "Contents/Resources/Game/speed.exe"),
        "generated": sorted(str(q.relative_to(a.generated)) for q in a.generated.rglob("*") if q.is_file()),
        "signed": signed,
        "shader_seed": (app / "Contents/Resources/ShaderWarmSeed/stamp.txt").read_text().splitlines()[:4]
        if (app / "Contents/Resources/ShaderWarmSeed/stamp.txt").is_file() else None,
        "mod_names": (app / "Contents/Resources/ModNames/known.tsv").read_text().splitlines()[0]
        if (app / "Contents/Resources/ModNames/known.tsv").is_file() else None,
        "hd_pack": hd_pack_line(app / "Contents/Resources/RemasterPack/index.tsv"),
        "hud_pack": [hd_pack_line(app / "Contents/Resources/RemasterPack" / n / "index.tsv") for n in ("ui", "ui2")],
        "materials": hd_pack_line(app / "Contents/Resources/Materials/index.tsv"),
        "relief": hd_pack_line(app / "Contents/Resources/PbrPilot/index.tsv"),
    }
    (ROOT / "build/package.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"packaged {app}")


if __name__ == "__main__":
    main()
