#!/usr/bin/env python3
"""Build the native app through CMake, regenerating original-game code only when needed.

The game is a directory holding game.toml (tools/build.py --game-dir). Its
outputs (the translation, the texture pack, the apps, the logs) go under
<game-dir>/build when the game lives outside the kit, else under the kit's
build/. The kit's own default is games/stub, a game that does not exist."""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/recomp"))
sys.path.insert(0, str(ROOT / "tools"))
import game_config  # noqa: E402
import buildlock  # noqa: E402
import copy_layouts  # noqa: E402
import package_desktop  # noqa: E402
import stage_game_files  # noqa: E402
import web_launcher  # noqa: E402

# What each --target builds. `plugins` is every mod plugin the game ships.
TARGETS = {
    "app": ["recomp_app"],
    "smoke": ["pop_smoke"],
    "headless": ["pop_headless"],
    "fixture": ["pop_fixture"],
    "gen": ["recomp_gen"],
    "plugins": ["plugins"],
    "ios": ["recomp_app"],
    "android": ["recomp_app"],
    "web": ["recomp_app"],
}
MACOS_ONLY = {"ios"}
NEEDS_GEN = {"app", "smoke", "headless", "fixture", "gen", "ios", "android", "web"}
# Targets with presets of their own, whatever the host system.
OWN_PRESET = {"ios", "android", "web"}


def default_preset(system=None):
    """The CMake preset for this operating system."""
    return {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}[system or platform.system()]


def preset_name(preset, config, stub=False, target=None):
    """Debug, stub, mobile and web builds use separate presets and binary directories."""
    if target in OWN_PRESET:
        return target + "-stub" if stub else target
    if stub:
        return preset + "-stub"
    return preset if config == "Release" else preset + "-debug"


def build_root_for(game_dir, root=ROOT):
    """Outputs live beside the game when it is outside the kit, else in the kit's build/."""
    game_dir = Path(game_dir)
    try:
        game_dir.relative_to(root)
        return Path(root) / "build"
    except ValueError:
        return game_dir / "build"


def build_dir_for(build_root, preset):
    """The CMake binary directory: what the presets name inside the kit, beside the game outside it."""
    return Path(build_root) / "cmake" / preset


def archive_path(build_root, system=None, preset=None):
    """Where CMake writes the translated archive on this platform."""
    name = "recomp_gen.lib" if (system or platform.system()) == "Windows" else "librecomp_gen.a"
    return Path(build_root) / "cmake" / (preset or default_preset(system)) / "lib" / name


def cmake_tool(name):
    """Prefer the venv's pinned cmake/ctest beside this interpreter, then PATH."""
    beside = Path(sys.executable).parent / name
    if beside.exists():
        return str(beside)
    return shutil.which(name) or name


def game_defines(game_dir, build_root, version=None):
    """The two cache paths every configure needs, and the version baked into the hosts when one is given (else the
    CMake default: git describe, or "unknown" in a source archive)."""
    defines = ["-DRECOMP_GAME_DIR=%s" % Path(game_dir).as_posix(), "-DPOP_BUILD_ROOT=%s" % Path(build_root).as_posix()]
    if version:
        defines.append("-DPOP_RECOMP_VERSION=%s" % version)
    return defines


def configure(preset, extra=(), build_dir=None):
    """Configure a preset; `build_dir` overrides the preset's binary directory."""
    command = [cmake_tool("cmake"), "--preset", preset, "-DPython3_EXECUTABLE=" + sys.executable]
    if build_dir is not None:
        command += ["-B", str(build_dir)]
    subprocess.run(command + list(extra), cwd=ROOT, check=True)


def build(preset, targets, jobs, extra=(), build_dir=None, config="Release"):
    """`extra` goes after the targets: a leading "--" hands the rest to the native tool.

    A build directory named directly (not through the build preset) needs the
    configuration spelled out too: the Xcode generator is multi-config and
    would otherwise build Debug, whose -O0 translation overflows the guest's
    stack on the device."""
    command = [cmake_tool("cmake"), "--build"]
    command += [str(build_dir), "--config", config] if build_dir is not None else ["--preset", preset]
    command += ["--parallel", str(jobs), "--target"] + list(targets) + list(extra)
    subprocess.run(command, cwd=ROOT, check=True)


def devicectl_list():
    """Paired devices as devicectl reports them."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        path = tmp.name
    subprocess.run(["xcrun", "devicectl", "list", "devices", "--json-output", path], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(path) as fh:
        return json.load(fh)["result"]["devices"]


def pick_device(devices):
    """The one paired iPad, or exit asking for --device."""
    ipads = [d for d in devices
             if d.get("hardwareProperties", {}).get("productType", "").startswith("iPad")
             and d.get("connectionProperties", {}).get("pairingState") == "paired"]
    if len(ipads) == 1:
        return ipads[0]["identifier"]
    names = ", ".join("%s (%s)" % (d.get("deviceProperties", {}).get("name", "?"), d["identifier"]) for d in ipads)
    sys.exit("Pass --device <identifier>; paired iPads: %s" % (names or "none"))


def ios_app_bundle(app_name, build_root):
    """The signed bundle under <build root>/ios; Xcode adds a configuration directory (Release-iphoneos)."""
    found = sorted((Path(build_root) / "ios").glob("**/%s.app" % app_name))
    if not found:
        sys.exit("No %s.app under %s/ios; did the iOS build succeed?" % (app_name, build_root))
    return found[-1]


def install_and_launch(app, bundle_id, device, console):
    """Install the bundle with devicectl and launch it, optionally streaming its console."""
    subprocess.run(["xcrun", "devicectl", "device", "install", "app", "--device", device, str(app)], check=True)
    launch = ["xcrun", "devicectl", "device", "process", "launch", "--terminate-existing", "--device", device]
    if console:
        launch.append("--console")
    launch.append(bundle_id)
    subprocess.run(launch, check=True)


def android_project(build_root, cfg, *, gen_dir):
    """Render the Gradle project without building it; gen_dir is the CMake binary directory.

    SDL's Java sources stay in that build's FetchContent checkout. Gradle
    only packages the native library built by CMake, never invokes CMake.
    """
    template = ROOT / "platform/android"
    out = Path(build_root) / "android"
    shutil.copytree(template, out, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".gradle", "build", ".gitignore", "local.properties"))
    values = {key: cfg["game"][key] for key in ("app_name", "bundle_id", "id")}
    values["sdl_java_dir"] = (Path(gen_dir).resolve() / "_deps/sdl3-src/android-project/app/src/main/java").as_posix()
    for original in template.rglob("*.in"):
        source = out / original.relative_to(template)
        text = source.read_text()
        for key, value in values.items():
            if source.name.endswith(".xml.in"):
                value = escape(value, {'"': "&quot;", "'": "&apos;"})
            else:
                value = json.dumps(value, ensure_ascii=False)[1:-1].replace("$", r"\$")
            text = text.replace("@%s@" % key, value)
        source.with_suffix("").write_text(text)
        source.unlink()
    return out


def android_stage_layout_assets(out, game_dir):
    """Put the game's control layouts in the APK's assets, as controls/.

    RecompActivity copies them out to the app's external files folder on
    start, which is where host_resource("controls") looks on Android. The
    directory is rebuilt from scratch so a layout the game dropped also
    leaves the APK, as the ffmpeg notice does when video goes off.
    """
    controls = Path(out) / "app/src/main/assets/controls"
    if controls.exists():
        shutil.rmtree(controls)
    return copy_layouts.copy_layouts(game_dir, controls)


def android_stage_core_assets(out, game_dir):
    """Package core manifests/data; native plugins are linked into libmain.so.

    Never carry a developer's desktop binaries, sources or debug bundles into
    the APK. Only the app-owned core tree is rebuilt; profiles are unrelated.
    """
    from copy_core_mods import copy_core_mods
    copy_core_mods(game_dir, Path(out) / "app/src/main/assets/mods/core")


def android_apk(build_root, cfg, *, gen_dir, game_dir):
    """Stage the native libraries beside the SDL activity and assemble a debug APK."""
    library = Path(gen_dir) / "host/libmain.so"
    sdl_activity = Path(gen_dir) / "_deps/sdl3-src/android-project/app/src/main/java/org/libsdl/app/SDLActivity.java"
    for path in (library, sdl_activity):
        if not path.is_file():
            raise FileNotFoundError("Android CMake build input is missing: %s" % path)
    out = android_project(build_root, cfg, gen_dir=gen_dir)
    jni = out / "app/src/main/jniLibs/arm64-v8a"
    jni.mkdir(parents=True, exist_ok=True)
    shutil.copy2(library, jni / "libmain.so")
    # Read the configured option, not leftover installed libraries: switching
    # video off must remove the previous build's copies from the APK too.
    cache = (Path(gen_dir) / "CMakeCache.txt").read_text().splitlines()
    video = any(line.startswith("RECOMP_VIDEO:BOOL=") and
                line.partition("=")[2].upper() in {"1", "ON", "YES", "TRUE", "Y"}
                for line in cache)
    for component in ("avformat", "avcodec", "avutil"):
        name = "lib%s.so" % component
        if video:
            shutil.copy2(Path(gen_dir) / "ffmpeg/lib" / name, jni / name)
        else:
            (jni / name).unlink(missing_ok=True)
    notice = out / "app/src/main/assets/ffmpeg-NOTICE.md"
    if video:
        notice.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "third_party/ffmpeg/NOTICE.md", notice)
    else:
        notice.unlink(missing_ok=True)
    android_stage_layout_assets(out, game_dir)
    android_stage_core_assets(out, game_dir)
    wrapper = "gradlew.bat" if platform.system() == "Windows" else "./gradlew"
    subprocess.run([wrapper, "assembleDebug"], cwd=out, check=True)
    apk = out / "app/build/outputs/apk/debug/app-debug.apk"
    if not apk.is_file():
        raise FileNotFoundError("No APK after assembleDebug: %s" % apk)
    print("Packaged %s (%d bytes)" % (apk, apk.stat().st_size), flush=True)
    return apk


def android_push_game(command, cfg, build_root):
    """Stage the configured install, then push game/ without deleting device saves.

    Recreate only our generated staging directory so exclusions also apply to
    files staged by an earlier build. The shared stager uses excluded() and
    writes the executable hash to .stamp, just as it does for an iOS bundle.
    """
    source = cfg["developer_exe_path"].parent
    executable = cfg["game"]["executable"]
    if not (source / executable).is_file():
        raise FileNotFoundError("Prepare your own game installation with tools/setup.py before --push-game")
    staged = Path(build_root) / "android/game"
    if staged.exists():
        shutil.rmtree(staged)
    count = stage_game_files.stage(source, staged, executable, cfg["bundle"]["exclude"],
                                   stage_game_files.kept(cfg))
    destination = "/sdcard/Android/data/%s/files" % cfg["game"]["bundle_id"]
    print("Staged %d game files in %s; pushing to %s/game" % (count, staged, destination), flush=True)
    subprocess.run(command + ["shell", "mkdir", "-p", destination], check=True)
    # Push game/ into its parent on every run: never create game/game/.
    subprocess.run(command + ["push", str(staged), destination + "/"], check=True)


def android_install_and_launch(apk, bundle_id, device=None, console=False, *, game_cfg=None, build_root=None):
    """Install, optionally push game data, then launch on one ready adb device.

    An explicit data push requires a device; a build alone may skip device
    actions. The APK is installed first so Android owns the external files path.
    """
    adb = shutil.which("adb")
    if not adb and os.environ.get("ANDROID_HOME"):
        name = "adb.exe" if platform.system() == "Windows" else "adb"
        candidate = Path(os.environ["ANDROID_HOME"]) / "platform-tools" / name
        if candidate.is_file():
            adb = str(candidate)
    if not adb:
        if game_cfg is not None:
            raise ValueError("adb unavailable; --push-game requires Android platform-tools and a ready device")
        print("adb unavailable; skipped Android install, launch and logcat.")
        return
    result = subprocess.run([adb, "devices"], check=True, capture_output=True, text=True)
    devices = [fields[0] for line in result.stdout.splitlines()
               if len(fields := line.split()) == 2 and fields[1] == "device"]
    if not devices:
        if game_cfg is not None:
            raise ValueError("No Android device attached; --push-game requires a ready device in adb devices")
        print("No Android device attached; skipped install, launch and logcat.")
        return
    if device is not None and device not in devices:
        raise ValueError("Android device %s is not ready in adb devices" % device)
    if device is None and len(devices) != 1:
        raise ValueError("Pass --device <adb serial>; ready Android devices: %s" % ", ".join(devices))
    command = [adb, "-s", device or devices[0]]
    subprocess.run(command + ["install", "-r", str(apk)], check=True)
    if game_cfg is not None:
        android_push_game(command, game_cfg, build_root)
    subprocess.run(command + ["shell", "am", "start", "-n", bundle_id + "/dev.recompkit.RecompActivity"], check=True)
    if console:
        subprocess.run(command + ["logcat"], check=True)


def publish_generated(build_root, translate):
    """Stage a translation, then publish gen/ and symbols.json by rename.

    `translate(stage_dir)` writes the sources and raises on failure; the
    published tree is untouched in that case. Publishing is renames only, so
    a reader under the same lock never sees half a generation."""
    recomp = Path(build_root) / "recomp"
    recomp.mkdir(parents=True, exist_ok=True)
    gen, old = recomp / "gen", recomp / "gen.old"
    stage = recomp / ("gen.new.%d" % os.getpid())
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    try:
        translate(stage)
        # x86.h sits beside the generated sources so #include "x86.h" resolves.
        shutil.copy(ROOT / "runtime/x86.h", stage / "x86.h")
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
    if gen.exists():
        gen.rename(old)
    stage.rename(gen)
    symbols = gen / "symbols.json"
    if symbols.is_file():
        temporary = recomp / ("symbols.json.new.%d" % os.getpid())
        shutil.copy(symbols, temporary)
        temporary.replace(recomp / "symbols.json")
    shutil.rmtree(old, ignore_errors=True)


def run_translator(stage, game_dir, build_root, allow_table_gaps=None, aux_modules=(),
                   allow_unmodelled=None, discovered=None, forget=None):
    """Translate the image into `stage`, then each auxiliary module (game.toml
    [modules.aux.<key>]) into `stage/aux-<key>`, which cmake/Translate.cmake
    compiles into its own library. A module is translated under the same
    --allow-table-gaps/--allow-unmodelled acceptances as the image: they are
    the build's, and a module's listing has the same gaps a game's has."""
    command = [sys.executable, str(ROOT / "tools/recomp/translate.py"), "--out", str(stage),
               "--game", str(game_dir),
               "--report", str(Path(build_root) / "recomp/translate-report.json")]
    if allow_table_gaps:
        command += ["--allow-table-gaps", allow_table_gaps]
    if allow_unmodelled:
        command += ["--allow-unmodelled", allow_unmodelled]
    if discovered:
        command += ["--discovered", str(discovered)]
    if forget:
        command += ["--forget", forget]
    subprocess.run(command, cwd=ROOT, check=True)
    for key in aux_modules:
        out = Path(stage) / ("aux-" + key)
        out.mkdir()
        module = [sys.executable, str(ROOT / "tools/recomp/translate.py"), "--out", str(out),
                  "--game", str(game_dir), "--module", key,
                  "--report", str(Path(build_root) / ("recomp/translate-%s-report.json" % key))]
        if allow_table_gaps:
            module += ["--allow-table-gaps", allow_table_gaps]
        if allow_unmodelled:
            module += ["--allow-unmodelled", allow_unmodelled]
        subprocess.run(module, cwd=ROOT, check=True)


def texture_pack(game_dir, build_root):
    """Compile the redistributable material-detail layer when its inputs are newer.
    Original-game replacement textures remain optional, locally prepared pack entries.
    A game without artwork has no texture pack."""
    detail = Path(build_root) / "texture-pack/terrain-detail.popt"
    artwork = Path(game_dir) / "assets/terrain/materials-v1.png"
    compiler = ROOT / "tools/recomp/terrain_detail.py"
    if not artwork.is_file():
        return
    if (not detail.is_file() or not (detail.parent / "manifest.json").is_file()
            or detail.stat().st_mtime < max(artwork.stat().st_mtime, compiler.stat().st_mtime)):
        subprocess.run([sys.executable, str(compiler), "--source", str(artwork),
                        "--output", str(detail.parent)], cwd=ROOT, check=True)


def web_site(game_dir, build_root, preset, cfg):
    """The launcher page with this game's web build beside it, ready to serve."""
    site = Path(build_root) / (preset + "-site")
    web_launcher.build([game_dir], site)
    web_launcher.copy_web_build(cfg["game"]["id"], Path(build_root) / preset / "recomp", site)
    return site


def parse_args(argv, system=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regenerate", action="store_true", help="Regenerate and compile translated C")
    parser.add_argument("--allow-table-gaps", metavar="REASON", default=None,
                        help="Accept jump-table sites the translator cannot decode (passed to translate.py)")
    parser.add_argument("--forget", metavar="ADDR[,ADDR...]", default=None,
                        help="translate as if the listing had never named these functions "
                             "(tools/recomp/translate.py --forget): an experiment, not a build")
    parser.add_argument("--discovered", metavar="FILE", default=None, type=Path,
                        help="a file a run wrote with RECOMP_DISCOVERY: the addresses it "
                             "reached that the translation did not carry become entry points")
    parser.add_argument("--allow-unmodelled", metavar="REASON", default=None,
                        help="Translate instructions the translator cannot model into a trap at "
                             "their own address (passed to translate.py)")
    parser.add_argument("--version", default=None,
                        help="Version baked into the hosts (POP_RECOMP_VERSION; default: git describe, else unknown). "
                        "A game's Setup passes its release version: a source archive has no git history")
    parser.add_argument("--target", choices=sorted(TARGETS), default="app")
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 8))
    parser.add_argument("--preset", default=default_preset(system), help="CMake configure preset")
    parser.add_argument("--config", choices=("Release", "Debug"), default="Release")
    parser.add_argument("--game-dir", type=Path, default=ROOT / "games/stub",
                        help="Absolute directory holding the game.toml this build is for (default: the kit's stub game)")
    parser.add_argument("--stub", action="store_true",
                        help="Link the hosts against a stub translation (no game code; CI's build)")
    parser.add_argument("--device", default=None, help="devicectl identifier (iOS) or adb serial (Android)")
    parser.add_argument("--team", default=os.environ.get("RECOMP_IOS_TEAM", ""),
                        help="Apple team id for automatic signing (ios target; default $RECOMP_IOS_TEAM)")
    parser.add_argument("--no-install", action="store_true", help="Build the mobile app without installing it")
    parser.add_argument("--push-game", action="store_true",
                        help="Android: push the configured game install minus [bundle].exclude before launch")
    parser.add_argument("--console", action="store_true", help="After launching on the device, stream its console")
    args = parser.parse_args(argv)
    if args.push_game and (args.target != "android" or args.no_install):
        parser.error("--push-game requires --target android without --no-install")
    if args.target == "ios" and not args.stub and not args.team:
        parser.error("--target ios needs --team or RECOMP_IOS_TEAM")
    if args.stub and (args.config == "Debug" or args.regenerate):
        parser.error("--stub cannot be combined with --config Debug or --regenerate")
    if not args.game_dir.is_absolute():
        parser.error("--game-dir must be absolute: %s" % args.game_dir)
    if not (args.game_dir / "game.toml").is_file():
        parser.error("No game config at %s/game.toml" % args.game_dir)
    if args.target == "plugins" and not (args.game_dir / "mods/CMakeLists.txt").is_file():
        parser.error("%s has no mods/CMakeLists.txt; nothing to build for --target plugins" % args.game_dir)
    if args.target in MACOS_ONLY and (system or platform.system()) != "Darwin":
        parser.error("The iOS packager runs on macOS")
    if args.target == "web" and not os.environ.get("EMSDK"):
        parser.error("--target web needs the Emscripten SDK's environment (source emsdk_env.sh)")
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    args.build_root = build_root_for(args.game_dir)
    return args, parser


def main():
    """Check inputs, translate under the build lock when needed, then configure and build."""
    args, parser = parse_args(sys.argv[1:])
    cfg = game_config.load(args.game_dir)
    # Regenerating needs the game and its listings.
    if args.regenerate and not cfg["developer_exe_path"].is_file():
        parser.error("Prepare your own game installation with tools/setup.py first")
    preset = preset_name(args.preset, args.config, stub=args.stub, target=args.target)
    build_dir = build_dir_for(args.build_root, preset)
    defines = game_defines(args.game_dir, args.build_root, args.version)
    try:
        # The lock lives at <build root>/recomp/.lock: BuildLock joins build/recomp/.lock onto its argument.
        with buildlock.BuildLock(args.build_root.parent, "tools/build.py"):
            if args.target in NEEDS_GEN and args.regenerate:
                if not (cfg["listings_path"] / "functions.tsv").is_file():
                    parser.error("Translation listings are missing; run tools/setup.py without --link-only")
                publish_generated(args.build_root,
                                  lambda stage: run_translator(stage, args.game_dir, args.build_root,
                                                               args.allow_table_gaps,
                                                               [m["key"] for m in cfg["aux_modules"]],
                                                               args.allow_unmodelled,
                                                               args.discovered, args.forget))
            # Generated sources include the adjacent runtime header. Refresh
            # it under the same lock even when their translation is unchanged.
            header = args.build_root / "recomp/gen/x86.h"
            if header.parent.is_dir():
                current = (ROOT / "runtime/x86.h").read_bytes()
                if not header.is_file() or header.read_bytes() != current:
                    temporary = header.with_suffix(".h.new")
                    temporary.write_bytes(current)
                    temporary.replace(header)
            if args.target == "ios":
                if not args.stub and not (args.build_root / "recomp/gen/table.c").is_file():
                    parser.error("No translation in %s/recomp/gen; run tools/build.py --regenerate on macOS first"
                                 % args.build_root)
                configure(preset, defines + ["-DRECOMP_IOS_TEAM=" + args.team], build_dir=build_dir)
                extra = ["--", "CODE_SIGNING_ALLOWED=NO"] if args.stub else ["--", "-allowProvisioningUpdates"]
                build(preset, TARGETS["ios"], args.jobs, extra, build_dir=build_dir, config="Release")
                if not args.stub and not args.no_install:
                    app = ios_app_bundle(cfg["game"]["app_name"], args.build_root)
                    device = args.device or pick_device(devicectl_list())
                    install_and_launch(app, cfg["game"]["bundle_id"], device, args.console)
            else:
                # A link-only build ships no texture pack, and its CI has no numpy.
                if args.target == "app" and not args.stub:
                    texture_pack(args.game_dir, args.build_root)
                configure(preset, defines, build_dir=build_dir)
                build(preset, TARGETS[args.target], args.jobs, build_dir=build_dir, config=args.config)
                if args.target == "web":
                    site = web_site(args.game_dir, args.build_root, preset, cfg)
                    print("Web site in %s (serve it with tools/web_launcher.py --serve)" % site)
                if args.target == "android":
                    apk = android_apk(args.build_root, cfg, gen_dir=build_dir, game_dir=args.game_dir)
                    if not args.no_install:
                        android_install_and_launch(apk, cfg["game"]["bundle_id"], args.device, args.console,
                                                   game_cfg=cfg if args.push_game else None,
                                                   build_root=args.build_root)
                system = "Windows" if preset.startswith("windows-cross") else platform.system()
                if args.target == "app" and not args.stub and system in {"Linux", "Windows"}:
                    # The desktop Ninja presets write OUTPUT_NAME into POP_OUT.
                    desktop_root = args.build_root / "windows" if preset.startswith("windows-cross") else args.build_root
                    suffix = ".exe" if system == "Windows" else ""
                    binary = desktop_root / "recomp" / (cfg["game"]["app_name"] + suffix)
                    if not binary.is_file():
                        parser.exit(1, "No desktop app binary at %s after the build\n" % binary)
                    packaged = package_desktop.stage(binary, cfg, desktop_root / "package",
                                                     system=system, build_dir=build_dir, game_dir=args.game_dir)
                    print("Packaged %s" % packaged)
    except subprocess.CalledProcessError as error:
        parser.exit(error.returncode or 1, "Build failed; see the compiler output above.\n")
    except TimeoutError as error:
        parser.exit(1, "%s\n" % error)
    except (OSError, ValueError) as error:
        parser.exit(1, "%s\n" % error)


if __name__ == "__main__":
    main()
