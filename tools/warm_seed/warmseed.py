#!/usr/bin/env python3
"""The shader warm-up (2.0.4): made on this Mac by the setup, so the first drive does not stutter.

    warmseed.py --cache DIR --out DIR [--drives N] [--race-seconds S]

The game's shaders are translated to Metal while it draws; each new one costs a short hold the first time it is seen.
The warm-up records them ahead: the built game runs offscreen through a few Quick Races with its own AI driving, in
the settings combinations a player is most likely to use (rain, HDR, the Xbox 360 modes and the golden filter, the
Advanced effects), and every shader it translates is kept. The packager puts the result in the app
(Contents/Resources/ShaderWarmSeed); at start the game compiles them in the background before they are needed.
The shaders are your Mac's own: made here from your game, never downloaded. Resumable: run it again and it carries on
from the drives already done. Progress lines: "shader warm-up: drive N of M" and "shader warm-up: seed ready (...)".
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# One drive per row: (name, Quick Race circuit index, mod settings, extra environment). The settings combinations
# are the ones the developers' own warm-up records (weather 0 unless the row is a rain drive; HDR never with 4x, as
# the game caps HDR at 2x). Circuits rotate through the three boroughs.
DRIVES = [
    ("rain-hdr", 16, {"weather": 1, "antialiasing": 1}, {"NFSMW_TEST_WEATHER_START": "0.65", "RECOMP_TEST_HDR_FLOAT": "1"}),
    ("adv-b", 5, {"lit_smoke": 1, "atmosphere": 1, "vehicle_lights": 1, "antialiasing": 2, "natural_water": 2}, {}),
    # 3.0: the Looks are retired; the Xbox 360 modes each feature keeps, with the golden colour filter.
    ("x360", 8, {"wet_roads": 2, "car_reflections": 2, "natural_glass": 2, "natural_water": 2, "golden_grade": 1}, {}),
    ("hdr", 14, {"antialiasing": 1}, {"RECOMP_TEST_HDR_FLOAT": "1"}),
    ("default", 0, {}, {}),
    ("adv-a", 2, {"lit_smoke": 1, "atmosphere": 1, "vehicle_lights": 1, "mirror_shadows": 1, "mirror_pools": 1,
                  "mirror_msaa": 1, "mirror_glass": 50, "antialiasing": 1, "natural_water": 1, "wall_sheen": 1}, {}),
    ("rain", 21, {"weather": 1}, {"NFSMW_TEST_WEATHER_START": "0.65"}),
    # 2.0.4: the Xbox 360 Stuff Pack front end has its own menus (and shaders): one short drive boots to its main
    # menu and stays there. Track None = menus only (the race drives above use the PC menus, whose path PATH is).
    ("x360-menus", None, {"x360_frontend": 1}, {}),
]
# Lines only the world prints (the first scenery item, the weather cycle, light pools with items: that line also prints
# "0 terrain-piece items" in the menus; the day cycle): a drive that logged
# one reached the race.
WORLD = re.compile(r"core\.nfsmw: (Xbox scenery lights: \d+ draw items so far|Weather: |Xbox light pools: [1-9]\d* terrain"
                   r"|Time of day: day cycle from|Time of day: \d)")
# From boot to the race on a fresh profile (3.0, run 33, D-183). Each key that changes the screen is pressed until the
# screen it leads to has the input (`keyuntil`, read from the game's front end by the runner), so a slower or faster
# boot cannot shift the keys onto the wrong screen (J3: this runner plays no intro movies, the title took input after
# 1 s, the timed path of 2.0.4 entered Career and stayed there; the title also ignores keys while its HD notice shows).
# Screens (run 33 census, frames viewed): the title WS_MW_LS_Splash -> "Create a new alias?" MC_Main (No is
# highlighted) -> MainMenu (Right Right = Quick Race) -> MainMenu_Sub (Quick Play, then Right + Enter: the mode list in
# the same screen, Enter on Circuit) -> Track_Select (Right x track) -> Track_Options -> Car_Select -> Dialog (the
# gearbox; Enter = automatic) -> Loading. A screen that never comes stops the drive (the runner quits).
PATH = [("RETURN", "MC_Main.fng", 3000, 90000, 1500),        # the title (pressed again while it ignores keys)
        ("RETURN", "MainMenu.fng", 5000, 30000, 2500),       # No: no alias
        ("RIGHT", None, 1000), ("RIGHT", None, 1500),       # Quick Race
        ("RETURN", "MainMenu_Sub.fng", 5000, 30000, 2500),
        ("RIGHT", None, 2000), ("RETURN", None, 5000),      # past Quick Play, into the mode list
        ("RETURN", "Track_Select.fng", 5000, 30000, 2500)]  # Circuit
RACE = [("RETURN", "Track_Options.fng", 5000, 30000, 2000), ("RETURN", "Car_Select.fng", 5000, 30000, 2500),
        ("RETURN", "Dialog.fng", 5000, 30000, 1500), ("RETURN", "Loading.fng", 5000, 30000, 20000)]
# 3.0.4 (run 39): the screens whose next keys are timed presses (no screen change to wait for). On a cold Metal cache
# (a player's first warm-up: nothing of this build's shader text compiled on that Mac yet) the main menu held one frame
# 1.7 s compiling 19 shaders and stalled for seconds after, and the two RIGHT presses were lost: the drive circled
# Career / alias and never raced (T3, reproduced with RECOMP_D3D9_SHADER_SALT). `await ... for` counts its hold in
# PRESENTED frames (script.h; unpinned 50 ms nominal, so 6000 = 120 frames), so it lasts until the burst is over on any
# Mac, and about 2 s on a warm one.
STEADY = ("MainMenu.fng", "MainMenu_Sub.fng", "Track_Select.fng")
STEADY_MS = 6000
KEY = re.compile(r"^[0-9a-f]{64}$")


def say(msg):
    print("shader warm-up: " + msg, flush=True)


def script(track, race_ms):
    lines = []
    if track is None:                      # menus only: boot, skip the intro and title, answer the profile question
        return "wait 20000\n" + "".join(f"key RETURN down\nwait 160\nkey RETURN up\nwait {w}\n" for w in (12000, 30000)) + "quit\n"

    def steps(path):
        for k, screen, *rest in path:
            if screen:   # pressed until that screen has the input, then its settle time
                every, within, settle = rest
                lines.append(f"keyuntil {k} fe:{screen}>0 every {every} within {within}")
                if screen in STEADY:
                    lines.append(f"await fe:{screen}>0 for {STEADY_MS} within 60000")
                lines.append(f"wait {settle}")
            else:
                lines.extend([f"key {k} down", "wait 160", f"key {k} up", f"wait {rest[0]}"])
    steps(PATH)
    steps([("RIGHT", None, 1100)] * track)
    steps(RACE)   # the race options, the car list, this car (the gearbox question), Auto: the race loads and starts
    # hold the accelerator (automatic gearbox): the car moves along the track, so more of it is drawn (run 28: with
    # no driver the car sat on the start line for the whole race)
    lines += ["key UP down", f"wait {race_ms}", "key UP up", "quit"]
    return "\n".join(lines) + "\n"


def core_dir(work):
    """The built core mod plus the files the setup made from your game (light pools, texture-pack map, packs)."""
    core = work / "core"
    shutil.rmtree(core, ignore_errors=True)
    shutil.copytree(ROOT / "build/recomp/mods/core/nfsmw", core / "nfsmw")
    gen = ROOT / "build/generated"
    if gen.is_dir():
        for p in gen.rglob("*"):
            if p.is_file():
                t = core / "nfsmw" / p.relative_to(gen)
                t.parent.mkdir(parents=True, exist_ok=True)
                if subprocess.run(["/bin/cp", "-c", str(p), str(t)], capture_output=True).returncode != 0:
                    shutil.copyfile(p, t)
    return core


def smoke():
    exe = ROOT / "build/recomp/pop_smoke"
    if not exe.exists():
        say("building the offscreen runner (a few minutes, once per build)")
        subprocess.run([sys.executable, str(ROOT / "tools/build.py"), "--target", "smoke"], cwd=ROOT, check=True,
                       stdout=sys.stdout, stderr=subprocess.STDOUT)
    if not exe.exists():
        raise SystemExit("the offscreen runner (build/recomp/pop_smoke) could not be built")
    return exe


def drive(exe, core, run, track, settings, extra, race_s):
    shutil.rmtree(run, ignore_errors=True)
    prof = run / "profile"
    prof.mkdir(parents=True)
    d = ROOT / "tools/setup_kit/defaults"
    shutil.copyfile(d / "registry.json", prof / "registry.json")
    ms = json.loads((d / "mod-settings.json").read_text())
    ms.update({"core.nfsmw/unlock_all": 1, "core.nfsmw/surround_audio": 0, "core.nfsmw/width": 1280,
               "core.nfsmw/height": 720, "core.nfsmw/weather": 0,
               "core.nfsmw/x360_frontend": 0})     # the race drives' menu path (PATH) is the PC front end's
    ms.update({"core.nfsmw/" + k: v for k, v in settings.items()})
    (prof / "mod-settings.json").write_text(json.dumps(ms, indent=1, sort_keys=True) + "\n")
    (run / "drive.script").write_text(script(track, race_s * 1000))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("RECOMP_", "POPM_", "NFSMW_"))}
    env.update(RECOMP_PROFILE_DIR=str(prof), RECOMP_REGISTRY=str(prof / "registry.json"),
               RECOMP_CORE_MODS_DIR=str(core), RECOMP_SMOKE_SECONDS=str(race_s + 150),
               RECOMP_HOST_PRESENT_PACING="duration", RECOMP_D3D9_SCALE="1", SDL_AUDIO_DRIVER="dummy",
               RECOMP_D3D9_SHADER_WARM_SEED="", RECOMP_D3D9_SHADER_WARM_DIR=str(run / "ShaderWarm"), **extra)
    with (run / "run.log").open("w") as log:
        p = subprocess.Popen([str(exe), "--script", str(run / "drive.script")], cwd=ROOT, env=env, stdout=log,
                             stderr=subprocess.STDOUT, start_new_session=True)
        try:
            p.wait(timeout=race_s + 240)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, 15)
            p.wait(timeout=30)
    order = run / "ShaderWarm/order.txt"
    n = sum(1 for k in order.read_text().split() if KEY.match(k)) if order.is_file() else 0
    world = bool(WORLD.search((run / "run.log").read_text(errors="replace")))
    return n, world


def merge(folders, out):
    """Union of the drives' recordings in first-use order; each source kept only when its file is there."""
    tmp = out.with_name(out.name + ".staging")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    order, pipes, seen = [], [], set()
    for d in folders:
        if not (d / "order.txt").is_file():
            continue
        for k in (d / "order.txt").read_text().split():
            if KEY.match(k) and k not in seen and (d / f"{k}.msl").is_file():
                shutil.copyfile(d / f"{k}.msl", tmp / f"{k}.msl")
                seen.add(k)
                order.append(k)
        if (d / "pipelines.txt").is_file():
            pipes += [l for l in (d / "pipelines.txt").read_text().splitlines() if l.strip()]
    pipes = list(dict.fromkeys(pipes))
    (tmp / "order.txt").write_text("".join(k + "\n" for k in order))
    (tmp / "pipelines.txt").write_text("".join(l + "\n" for l in pipes))
    stamp = [f"fingerprint {fingerprint()}",
             f"generated {time.strftime('%Y-%m-%d %H:%M:%S')} from {len(folders)} drives on this Mac (setup)",
             "source setup", f"libraries {len(order)} pipelines {len(pipes)}"] + [f"{h}  {f}" for f, h in parts()]
    (tmp / "stamp.txt").write_text("\n".join(stamp) + "\n")
    shutil.rmtree(out, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp.rename(out)
    return len(order), len(pipes)


def warm_format():
    """The runtime's key namespace and pipeline line format (d3d9_metal.mm "msl-v1", "-safe" from macOS 15;
    pipeline_warm_spec.h "p1"): a seed made under another namespace matches nothing."""
    try:
        major = int(platform.mac_ver()[0].split(".")[0] or 0)
    except ValueError:
        major = 0
    return ("msl-v1-safe" if major >= 15 else "msl-v1") + "/p1"


def parts():
    """What the seed's shaders are made from: a seed belongs to the build made from exactly these. Run 38 (D-202): the
    shader generator's sources, the pipeline line format, the effects programs Setup made (build/generated) and the
    runtime's key namespace; the renderer itself (d3d9_metal.mm) is left out on purpose, as it changes with every fix:
    a variant it selects differently is compiled once in the draw, as without a seed. 3.0.4 (run 39, challenge review):
    also kit/host/gpu/metal/surface.h, whose surf_ps_patch edits the generated pixel shaders (the generator includes it;
    tests/test_warm_seed.py checks that every header the generator includes for its MSL is listed here)."""
    files = ["kit/dx/d3d9_shadergen.cpp", "kit/dx/d3d9_msl.h", "kit/dx/d3d9_shader.cpp", "kit/dx/d3d9_shader.h",
             "kit/host/gpu/metal/surface.h", "kit/host/gpu/metal/pipeline_warm_spec.h"]
    gen = ROOT / "build/generated"
    if gen.is_dir():
        files += sorted(str(p.relative_to(ROOT)) for p in gen.iterdir()
                        if p.is_file() and (p.suffix == ".fx" or p.name == "clean-chain.txt"))
    out = [(f, hashlib.sha256((ROOT / f).read_bytes()).hexdigest()) for f in files if (ROOT / f).is_file()]
    fmt = warm_format()
    return out + [("format " + fmt, hashlib.sha256(fmt.encode()).hexdigest())]


def fingerprint():
    return hashlib.sha256("".join(f"{h}  {f}\n" for f, h in parts()).encode()).hexdigest()


def stamp_of(seed):
    """The kept seed's stamp as {"fingerprint", "generated", "libraries", "pipelines"}; {} without one."""
    f = seed / "stamp.txt"
    if not f.is_file():
        return {}
    info = {}
    for line in f.read_text(errors="replace").splitlines()[:4]:
        k, _, v = line.partition(" ")
        if k == "fingerprint":
            info["fingerprint"] = v.strip()
        elif k == "generated":
            info["generated"] = " ".join(v.split()[:2])
        elif k == "libraries":
            w = v.split()
            if len(w) == 3 and w[1] == "pipelines":
                info["libraries"], info["pipelines"] = w[0], w[2]
    return info


def pack_env(hd, materials, relief):
    """The environment the core mod would set from the app's Resources (remaster_tex.h), for the drives' runner, which
    has no app around it. A pack is used only when its index.tsv is there (written last, so it is finished)."""
    env = {}
    if hd and (hd / "index.tsv").is_file():
        env["RECOMP_REMASTER_PACK"] = str(hd.expanduser().resolve())
        if materials and (materials / "index.tsv").is_file():
            env.update(RECOMP_REMASTER_MATERIALS="1", RECOMP_REMASTER_MATERIALS_PACK=str(materials.expanduser().resolve()))
        if relief and (relief / "index.tsv").is_file():
            env.update(RECOMP_REMASTER_PBRWORLD="1", RECOMP_REMASTER_PBRWORLD_PACK=str(relief.expanduser().resolve()),
                       RECOMP_REMASTER_PBRWORLD_STRENGTH="1")
    return env


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=Path, default=ROOT / "build/warm")
    ap.add_argument("--out", type=Path, default=ROOT / "build/warm/seed")
    ap.add_argument("--drives", type=int, default=len(DRIVES))
    ap.add_argument("--race-seconds", type=int, default=150)
    # 3.0: the packs Setup made (each used only when finished): with them the drives record the shader variants the
    # packs turn on (material sets, wall relief, the wall sun sheen in adv-a), as the player's app will draw them.
    ap.add_argument("--hd-pack", type=Path, help="the HD texture pack (its index.tsv)")
    ap.add_argument("--materials", type=Path, help="the material set (Resources/Materials in the app)")
    ap.add_argument("--relief", type=Path, help="the wall relief (Resources/PbrPilot in the app)")
    a = ap.parse_args()
    cache, out = a.cache.expanduser().resolve(), a.out.expanduser().resolve()
    rows = DRIVES[:max(0, min(a.drives, len(DRIVES)))]
    if not rows:
        say("no drives asked for; nothing to do")
        return
    work = cache / "drives"
    work.mkdir(parents=True, exist_ok=True)
    fp = fingerprint()
    packs = pack_env(a.hd_pack, a.materials, a.relief)
    # run 28 review F2: a drive is reused only if the shaders' sources AND its own script and settings are the same (a
    # corrected menu path must drive again; the earlier marks keyed only the shader sources)
    mids = [hashlib.sha256(f"{fp}|{script(track, a.race_seconds * 1000)}|{sorted(settings.items())}|"
                           f"{sorted({**packs, **extra}.items())}".encode()).hexdigest()
            for _, track, settings, extra in rows]
    marks = [(work / name / "done").read_text().split() if (work / name / "done").is_file() else []
             for name, *_ in rows]
    kept = stamp_of(out)
    # Run 38 (D-202): Setup keeps build/warm across updates; a version that draws its shaders the same way reuses them
    # in seconds instead of driving again (the 30 minutes are the drives, not the compiling).
    if kept.get("fingerprint") == fp and all(m[:1] == [mid] for m, mid in zip(marks, mids)) \
            and (out / "order.txt").is_file():
        say(f"reused the prepared shaders (fingerprint {fp[:12]}, {kept.get('libraries', '?')} libraries, "
            f"{kept.get('pipelines', '?')} pipelines, made {kept.get('generated', '?')})")
        return
    if kept:
        if kept.get("fingerprint") != fp:
            say("the shaders changed in this version: preparing them again (about 35 minutes)")
        shutil.rmtree(out, ignore_errors=True)   # never left behind for the packager if the drives below fail
    exe = core = None
    done, raced, unconfirmed = [], 0, []
    for i, (name, track, settings, extra) in enumerate(rows):
        extra = {**packs, **extra}
        run = work / name
        mark = run / "done"
        mid = mids[i]
        if marks[i][:1] == [mid]:
            done.append(run / "ShaderWarm")
            raced += track is not None and marks[i][2:3] == ["race"]
            continue
        if exe is None:   # the offscreen runner and the core mod only when a drive is needed
            exe, core = smoke(), core_dir(cache)
        say(f"drive {i + 1} of {len(rows)} ({name})")
        n, world = drive(exe, core, run, track, settings, extra, a.race_seconds)
        if n == 0:
            say(f"drive {name} recorded nothing (see {run / 'run.log'}); left out")
            continue
        if track is not None:
            if world:
                raced += 1
            else:
                unconfirmed.append(name)
                say(f"drive {name}: the race was not seen in its log (menus only?); kept")
                if raced == 0:
                    # run 28: the first race drive never reached the world: the menu path does not work on this copy
                    # of the game, so the other drives would only repeat the menus. Stop (Setup goes on without it).
                    raise SystemExit("shader warm-up: the first drive did not reach a race (see "
                                     f"{run / 'run.log'}); skipped, the first drive will stutter once")
        mark.write_text(f"{mid} {n} {'race' if world else 'menus'}\n")
        done.append(run / "ShaderWarm")
    if not done:
        raise SystemExit("shader warm-up: no drive recorded anything; skipped (the first drive will stutter once)")
    libs, pipes = merge(done, out)
    races = sum(1 for r in rows if r[1] is not None)
    say(f"seed ready ({libs} libraries, {pipes} pipelines from {len(done)} of {len(rows)} drives; races reached "
        f"{raced} of {races})")


if __name__ == "__main__":
    main()
