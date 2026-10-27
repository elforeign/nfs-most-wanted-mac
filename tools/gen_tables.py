#!/usr/bin/env python3
"""gen_tables.py - writes the game-data tables the runtime compiles in (runtime/gen/*.inc, gitignored) from the player's
own files. The repository holds the generators, never the tables. Run by runtime/build.sh (with --if-stale: an
unchanged input set costs one content hash per file, ~1 s) and by Setup.

Inputs come from the roots manifest exactly as runtime/roots.h finds it (NFS_ROOTS, else <BOOT_USERDATA>/roots.txt, else
~/Library/Application Support/NFSMW-port/roots.txt): pc_root and, when present, x360_root. Without a manifest (a development
layout): NFS_GAME (default <checkout parent>/Need For Speed Most Wanted Black Edition) and NFS_X360_FILES (default
<game>/../x360/files when it exists). --pc / --x360 / --no-x360 override both.

pubtables_gen.py's PC tables (padprompts_l1, look_pc, car_shadow_off) always come from the PC folder. Every other table is
X360 data laid over the PC's (both copies are read; controltype_buttons: the X360 Control Types; callout_labels: the X360
default.xex in or beside the X360 root): with no X360 root each is written as an empty
table, the feature has nothing to apply and stays PC (its F10 row is hidden by roots_x360_has("world"/"audio")).
x360_shader_lits.inc (tools_snapshot/x360_shader_lits.py): the X360 shaders' literal constants the port's Metal shaders
use, from <x360_root>/NFS/ZZDATA0.BIN (NFS_X360_ZZDATA0 / --zzdata0 override it; development layout: the 2 MB slice
<x360_root>/../shaders/zzdata0_9B00000.bin); none: zeros with X360_SHADER_LITS 0 (the runtime keeps those features PC).
It also holds the X360 effect parameter defaults the C code uses (X360F_*). x360_shader_body.inc
(tools_snapshot/x360_shader_body.py): the bodies of the X360 shader programs materials.m runs, translated from the same
ZZDATA0.BIN with the vendored XenosRecomp translator (tools_snapshot/xenosrecomp, built with clang++); none: empty
bodies with X360_SHADER_BODIES 0. x360_engine.inc (pubtables_gen.py): the X360
post chain's engine constants from default.xex (none: X360_ENGINE_CONSTS 0, the PC chain). roots.h's "shaders" and
"engine" features hide the F10 rows built on them.

usage: gen_tables.py [--if-stale] [--pc DIR] [--x360 DIR | --no-x360] [--out DIR] [--jobs N]
Proof: with the supported game files, every table is byte-identical to the last committed copy (cmp, 6 of 6;
controltype_buttons: the same 40 entries as controltype.c's removed literal); an X360
ATTRIBUTES.BIN copy with one bit flipped (0x7968c) -> trees_params.inc differs in 1 line."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "runtime/gen"
STAMP = "inputs.txt"
VAULTS = ("ATTRIBUTES.BIN", "GAMEPLAY.BIN", "FE_ATTRIB.BIN")


def roots_file():
    e = os.environ.get("NFS_ROOTS")
    if e:
        p = Path(e)
    elif os.environ.get("BOOT_USERDATA"):
        p = Path(os.environ["BOOT_USERDATA"]) / "roots.txt"
    else:
        p = Path(os.environ.get("HOME", "/tmp")) / "Library/Application Support/NFSMW-port/roots.txt"
    return p if p.exists() else None


def read_roots(path):
    keys = {}
    for line in path.read_text().splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if v.strip():
            keys[k.strip()] = v.strip()
    return keys


def resolve(args):
    pc = x360 = None
    rf = roots_file()
    if rf:
        r = read_roots(rf)
        pc, x360 = r.get("pc_root"), r.get("x360_root")
    elif os.environ.get("NFS_PUBLIC") == "1" and not (args.pc or os.environ.get("NFS_GAME")):
        sys.exit("gen_tables: the public build reads the player's folders from roots.txt (NFS_ROOTS); none found")
    else:
        pc = os.environ.get("NFS_GAME") or str(ROOT.parent / "Need For Speed Most Wanted Black Edition")
        x360 = os.environ.get("NFS_X360_FILES") or str(Path(pc).parent / "x360/files")
        if not Path(x360).is_dir():
            x360 = None
    if os.environ.get("NFS_PUBLIC") == "1" and os.environ.get("NFS_X360_FILES"):
        x360 = os.environ["NFS_X360_FILES"]          # public build: an explicit override wins over the manifest (as at run time)
    pc = args.pc or pc
    x360 = None if args.no_x360 else (args.x360 or x360)
    if not pc or not (Path(pc) / "speed.exe").exists():
        sys.exit(f"gen_tables: no PC game folder with speed.exe ({pc}); set pc_root in roots.txt or NFS_GAME")
    return Path(pc), Path(x360) if x360 else None


def find(d, name):
    """A file under d by case-insensitive name (the PC folder spells some vaults in lower case)."""
    p = d / name
    if p.exists():
        return p
    for q in d.iterdir() if d.is_dir() else ():
        if q.name.lower() == name.lower():
            return q
    return p


def inputs(pc, x360, zz=None):
    files = [pc / "speed.exe", pc / "TRACKS/STREAML2RA.BUN", ROOT / "recomp/out/speed_32.c"]
    if zz:
        files.append(zz)
    files += [find(pc / "GLOBAL", v) for v in VAULTS] + [find(pc / "GLOBAL", "GLOBALB.BUN")]
    if x360:
        files += [x360 / "GLOBAL" / v for v in VAULTS] + [x360 / "GLOBAL/GLOBALB.BUN", xex_path(x360)]
        files += [x360 / "TRACKS/STREAML2RA.BUN", x360 / "TRACKS/STREAML2RA_PERM.BUN"]
    return files


def zzdata0_path(x360, arg=None):
    """The X360 NFS/ZZDATA0.BIN (shader literals), or None: --zzdata0 / NFS_X360_ZZDATA0, <x360_root>/NFS/ZZDATA0.BIN,
    else a zzdata0_<base>.bin slice in <x360_root>/../shaders (a development extract)."""
    e = arg or os.environ.get("NFS_X360_ZZDATA0")
    if e:
        return Path(e)
    if not x360:
        return None
    for p in (x360 / "NFS/ZZDATA0.BIN", x360.parent / "NFS/ZZDATA0.BIN"):
        if p.exists():
            return p
    slices = sorted((x360.parent / "shaders").glob("zzdata0_*.bin")) if (x360.parent / "shaders").is_dir() else []
    return slices[0] if slices else None


def xex_path(x360):
    """The X360 executable: in the X360 root, else beside it (development layout: x360/default.xex, x360/files)."""
    for p in (x360 / "default.xex", x360.parent / "default.xex"):
        if p.exists():
            return p
    return x360 / "default.xex"


def content_hash(f):
    """BLAKE2b of the file's bytes (about 1 s for the 1.1 GB of inputs)."""
    h = hashlib.blake2b(digest_size=16)
    with open(f, "rb") as fh:
        for block in iter(lambda: fh.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stamp_text(pc, x360, zz=None):
    lines = ["pc=%s" % pc, "x360=%s" % (x360 or "-"), "zzdata0=%s" % (zz or "-")]
    for f in inputs(pc, x360, zz):          # content, not size/mtime: a same-size, same-mtime edit still regenerates
        try:
            lines.append("%s\t%s" % (f, content_hash(f)))
        except OSError:
            lines.append("%s\tmissing" % f)
    for s in sorted([Path(__file__).resolve()] + [ROOT / p for p in GENERATORS]):
        lines.append("%s\t%s" % (s.relative_to(ROOT), hashlib.sha256(s.read_bytes()).hexdigest()))
    return "\n".join(lines) + "\n"


GENERATORS = ["tools_snapshot/attrib_x360.py", "tools_snapshot/x360audio_attrib.py", "runtime/trees_params.py",
              "recomp/lighting_data.py", "tools_snapshot/x360_auxlights.py", "tools_snapshot/x360_glass_uv1.py",
              "tools_snapshot/texture_compare.py", "tools_snapshot/controltype_gen.py", "tools_snapshot/vaults/compare_vaults.py",
              "tools_snapshot/vaults/vaultlib.py", "tools_snapshot/vaults/names.py",
              "tools_snapshot/pubtables_gen.py", "tools_snapshot/xex_read.c", "tools_snapshot/x360_shader_lits.py",
              "tools_snapshot/x360_shader_body.py"] + sorted(
                  str(p.relative_to(ROOT)) for p in (ROOT / "tools_snapshot/xenosrecomp").rglob("*") if p.suffix in (".cpp", ".h"))
PUBTABLES = ["padprompts_l1.inc", "look_pc.inc", "car_shadow_off.inc", "look_x360.inc", "glass_x360.inc", "grass_x360.inc",
             "callout_labels.inc", "x360_shader_lits.inc", "x360_shader_body.inc", "pc_shader_lits.inc", "x360_engine.inc"]

NONE = "// Generated by tools/gen_tables.py with no X360 root: no X360 data, the feature stays PC. Do not edit.\n"
STUBS = {
    "attrib_x360.inc": NONE +
        "static const struct { uint32_t cls, key, off, len; const uint8_t *data; } k_layout_patch[] = {};\n"
        "static const struct { uint32_t nexp, k0, k1, ncoll, nreloc, size; const uint8_t *blob; const uint16_t *reloc, *coll;"
        " const char *name; } k_g6[] = {};\n",
    "x360audio_attrib.inc": NONE +
        "typedef struct { uint32_t cls, key, off, len; uint8_t data[8]; } X360AttribPatch;\n"
        "static const X360AttribPatch k_audio_patch[] = {};\nstatic const X360AttribPatch k_traffic_patch[] = {};\n",
    "attrib_light_data.inc": NONE +
        "static const struct { uint32_t cls, key, off, len; uint8_t data[16]; } k_light_patch[] = {};\n",
    "trees_params.inc": NONE,
    "controltype_buttons.inc": NONE,
    "auxlights_data.inc": NONE + "".join("#define AUX_DATA_%s 0u\n" % k for k in (
        "CONTEXTS", "LIGHTS", "STORED_LIGHTS", "TRUNCATED_CONTEXTS", "LIT_INSTANCES", "MATCHED", "UNMATCHED",
        "COLLISIONS", "INVALID", "PC_INSTANCES", "X360_INSTANCES", "ROWS")) +
        "static const AuxLightContext aux_contexts[] = {};\nstatic const AuxLightKey aux_keys[] = {};\n",
    "glass_uv1.inc": NONE + "".join("#define GLASS_UV1_%s 0u\n" % k for k in (
        "VERTICES", "MATCHED", "BUFFERS", "CONFLICTS", "VALUES")) +
        "static const float glass_uv1_values[] = {};\n"
        "static const struct { uint64_t hash; uint32_t vertices, first; } glass_uv1_table[] = {};\n"
        "static const uint16_t glass_uv1_index[] = {};\n",
}


def outputs_text(out):
    """The output tables' content hashes (part of the stamp): "up to date" needs every output as this run wrote it."""
    lines = []
    for name in sorted(list(STUBS) + PUBTABLES):
        f = out / name
        lines.append("out %s\t%s" % (name, hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else "missing"))
    return "\n".join(lines) + "\n"


def generate(pc, x360, out, jobs, zz=None):
    py = sys.executable
    tmp = Path(tempfile.mkdtemp(prefix="gen_tables-", dir=out.parent))
    try:
        # x360_shader_lits.py: the X360 shader literals (refuses a ZZDATA0 that is not the retail one), or zeros
        cmd = [py, "-I", "tools_snapshot/x360_shader_lits.py", "--out", tmp / "x360_shader_lits.inc"]
        cmd += ["--zzdata0", zz] if zz else ["--stub"]
        r = subprocess.run([str(c) for c in cmd], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if r.returncode:
            sys.exit("gen_tables: x360_shader_lits.py failed:\n" + r.stdout[-3000:])
        # x360_shader_body.py: the bodies of the X360 shader programs the port runs, translated from the same file
        # (builds tools_snapshot/xenosrecomp with clang++; refuses the same files), or empty bodies
        cmd = [py, "-I", "tools_snapshot/x360_shader_body.py", "--out", tmp / "x360_shader_body.inc"]
        cmd += ["--zzdata0", zz, "--work", tmp / "shaderwork"] if zz else ["--stub"]
        r = subprocess.run([str(c) for c in cmd], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if r.returncode:
            sys.exit("gen_tables: x360_shader_body.py failed:\n" + r.stdout[-3000:])
        if x360 and not zz:
            print("gen_tables: no X360 NFS/ZZDATA0.BIN under %s: the X360 shader features stay PC" % x360)
        # pubtables_gen.py: PC tables always, X360 ones (or their no-X360 stubs) beside them
        cmd = [py, "-I", "tools_snapshot/pubtables_gen.py", "--pc", pc, "--out", tmp]
        if x360:
            cmd += ["--x360", x360, "--xex", xex_path(x360)]
        r = subprocess.run([str(c) for c in cmd], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if r.returncode:
            sys.exit("gen_tables: pubtables_gen.py failed:\n" + r.stdout[-3000:])
        if not x360:
            for name, text in STUBS.items():
                (tmp / name).write_text(text)
        else:
            xg, pg = x360 / "GLOBAL", pc / "GLOBAL"
            (tmp / "vaults").mkdir()
            steps = [   # (name, [command, ...] run in order); independent steps run in parallel
                ("attrib_x360.inc", [[py, "-I", "tools_snapshot/attrib_x360.py", xg, pg, tmp / "attrib_x360.inc"]]),
                ("x360audio_attrib.inc", [[py, "-I", "tools_snapshot/x360audio_attrib.py", xg, pg,
                                           tmp / "x360audio_attrib.inc"]]),
                ("trees_params.inc", [[py, "-I", "runtime/trees_params.py", xg, tmp / "trees_params.inc"]]),
                ("attrib_light_data.inc", [[py, "-I", "tools_snapshot/vaults/compare_vaults.py", pg, xg, tmp / "vaults"],
                                           [py, "-I", "recomp/lighting_data.py", "--tsv",
                                            tmp / "vaults/attrib_field_diffs.tsv", "--table", tmp / "attrib_light_data.inc"]]),
                ("auxlights_data.inc", [[py, "-I", "tools_snapshot/x360_auxlights.py", "--x360-tracks", x360 / "TRACKS",
                                         "--pc-tracks", pc / "TRACKS", "--output", tmp / "auxlights_data.inc"]]),
                ("controltype_buttons.inc", [[py, "-I", "tools_snapshot/controltype_gen.py", xg, pc / "speed.exe",
                                              tmp / "controltype_buttons.inc"]]),
                ("glass_uv1.inc", [[py, "-I", "tools_snapshot/x360_glass_uv1.py", x360, pc, tmp / "glass_uv1.inc",
                                    "--work", tmp / "glasswork"]]),
            ]
            # Names only order the diff TSV's rows (fields sort by name), so they order attrib_light_data.inc's rows; the
            # runtime applies every row by key, so any order is the same data. A development checkout's decompilation names
            # (NFS_NAME_DECOMPS) reproduce the order of the copy the repository used to hold; the public build skips them.
            env = dict(os.environ, NFS_NAME_CARDIRS=os.pathsep.join([str(pc / "CARS"), str(x360 / "CARS")]))
            decomps = [ROOT.parent / "decomp", ROOT.parent / "decomp_x360"]
            if os.environ.get("NFS_PUBLIC") != "1" and "NFS_NAME_DECOMPS" not in env and all(d.is_dir() for d in decomps):
                env["NFS_NAME_DECOMPS"] = os.pathsep.join(map(str, decomps))
            running, failed = [], []
            pending = list(steps)
            while pending or running:
                while pending and len(running) < jobs:
                    name, cmds = pending.pop(0)
                    log = open(tmp / (name + ".log"), "w")
                    running.append([name, cmds, 0, subprocess.Popen([str(c) for c in cmds[0]], cwd=ROOT, stdout=log,
                                                                    stderr=subprocess.STDOUT, env=env), log])
                for r in list(running):
                    name, cmds, i, proc, log = r
                    if proc.poll() is None:
                        continue
                    if proc.returncode:
                        failed.append(name)
                    elif i + 1 < len(cmds):
                        r[2], r[3] = i + 1, subprocess.Popen([str(c) for c in cmds[i + 1]], cwd=ROOT, stdout=log,
                                                             stderr=subprocess.STDOUT, env=env)
                        continue
                    log.close()
                    running.remove(r)
                if running:
                    time.sleep(0.2)
            if failed:
                for name in failed:
                    sys.stderr.write("gen_tables: %s failed:\n%s\n" % (name, (tmp / (name + ".log")).read_text()[-3000:]))
                sys.exit(1)
            shutil.rmtree(tmp / "glasswork", ignore_errors=True)   # the unpacked 880 MB X360 stream
        out.mkdir(parents=True, exist_ok=True)
        try:                                  # no stamp while outputs are replaced: an interrupted run is never "up to date"
            (out / STAMP).unlink()
        except FileNotFoundError:
            pass
        for name in list(STUBS) + PUBTABLES:
            os.replace(tmp / name, out / name)
        if (tmp / "vaults").is_dir():
            shutil.rmtree(out / "vaults", ignore_errors=True)
            os.replace(tmp / "vaults", out / "vaults")
        else:
            shutil.rmtree(out / "vaults", ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--if-stale", action="store_true", help="do nothing when the inputs match the last run's")
    ap.add_argument("--pc", help="PC game folder (default: roots manifest / legacy layout)")
    ap.add_argument("--x360", help="X360 extracted files folder")
    ap.add_argument("--no-x360", action="store_true", help="generate as if there were no X360 copy")
    ap.add_argument("--zzdata0", help="X360 NFS/ZZDATA0.BIN (or a zzdata0_<hex base>.bin slice) for the shader literals")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()
    pc, x360 = resolve(args)
    zz = None if args.no_x360 else zzdata0_path(x360, args.zzdata0)
    out = args.out.resolve()
    stamp = stamp_text(pc, x360, zz)
    have = out / STAMP
    if args.if_stale and have.exists() and have.read_text() == stamp + outputs_text(out):
        print("gen_tables: up to date (%s)" % ("X360 + PC" if x360 else "PC only"))
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    generate(pc, x360, out, args.jobs, zz)
    have.write_text(stamp + outputs_text(out))   # inputs and the outputs they produced: a later edit or partial copy regenerates
    print("gen_tables: wrote %d tables to %s from %s%s" % (len(STUBS) + len(PUBTABLES), out, pc, " + %s" % x360 if x360 else " (no X360 root: empty tables)"))


if __name__ == "__main__":
    main()
