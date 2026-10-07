#!/usr/bin/env python3
"""Package the built app with your game data: dist/NFS Most Wanted Native.app.

    package.py --game DIR --image speed.exe --generated DIR --out DIR

The kit's build/SpeedRecomp.app (executable, frameworks, core mod) + the launcher and defaults of
tools/setup_kit/defaults + your game folder (APFS clone where possible; the files the game.toml [bundle] exclude
list names are left out) with the supported speed.exe image + the files built from your game (build/generated:
light pools, texture-pack map, XenonEffects texture). Mach-O files are signed ad hoc, inside out. Writes
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


def clone_tree(src, dst, exclude):
    """Copy the game folder: APFS clones (cp -c) when possible, so it costs almost no disk space on the same volume."""
    dst.mkdir(parents=True)
    for p in sorted(src.iterdir()):
        if p.name.lower() in exclude or p.name.lower() == "speed.exe" or p.name.startswith("."):
            continue
        r = subprocess.run(["/bin/cp", "-cR", str(p), str(dst / p.name)], capture_output=True)
        if r.returncode:
            subprocess.run(["/bin/cp", "-R", str(p), str(dst / p.name)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", type=Path, required=True)
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--generated", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    built = ROOT / "build/SpeedRecomp.app"
    cfg = tomllib.loads((ROOT / "game.toml").read_text())
    exclude = {x.lower() for x in cfg.get("bundle", {}).get("exclude", [])}
    if subprocess.run(["/usr/bin/pgrep", "-f", f"{NAME}.app/Contents/MacOS/SpeedRecomp"], capture_output=True).returncode == 0:
        sys.exit("the app is running; quit it first")
    a.out.mkdir(parents=True, exist_ok=True)
    app = a.out / f"{NAME}.app"
    tmp = a.out / f".{NAME}.app.new"
    shutil.rmtree(tmp, ignore_errors=True)
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
            shutil.copyfile(p, t)
    game = res / "Game"
    clone_tree(a.game, game, exclude)
    shutil.copyfile(a.image, game / "speed.exe")
    info = tmp / "Contents/Info.plist"
    p = plistlib.loads(info.read_bytes())
    p.update(CFBundleDisplayName=NAME, CFBundleName="NFSMW Native", CFBundleExecutable="Launch",
             CFBundleIdentifier="io.github.nfsmw-native-mac", CFBundleShortVersionString="1.0.1", CFBundleVersion="2",
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
        shutil.rmtree(app)
    tmp.rename(app)
    core = app / "Contents/Resources/mods/core/nfsmw"
    manifest = {
        "app": str(app),
        "executable_sha256": sha(app / "Contents/MacOS/SpeedRecomp"),
        "core_sha256": sha(core / "nfsmw.dylib") if (core / "nfsmw.dylib").exists() else None,
        "speed_exe_sha256": sha(app / "Contents/Resources/Game/speed.exe"),
        "generated": sorted(str(q.relative_to(a.generated)) for q in a.generated.rglob("*") if q.is_file()),
        "signed": signed,
    }
    (ROOT / "build/package.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"packaged {app}")


if __name__ == "__main__":
    main()
