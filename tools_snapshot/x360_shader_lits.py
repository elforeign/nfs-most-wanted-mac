#!/usr/bin/env python3
"""x360_shader_lits.py - the X360 shader literal constants the runtime's hand-written Metal shaders use, read from the
player's own X360 NFS/ZZDATA0.BIN (public release: the repository holds the shader code we wrote, never the game's
numbers). Run by tools/gen_tables.py; writes x360_shader_lits.inc (runtime/gen/), included by materials.m and
render_post.m.

The game's 205 shader programs sit uncompressed in NFS/ZZDATA0.BIN (18 FXL effects, 0x09BB7B1C-0x09C19894) in the 2005
Xbox 360 container layout (magic 0x102A0E00, VS when bit 0 is set): +04 virtualSize, +08 physicalSize, +0C definitions
offset; the definitions hold the literal constants inline ({u16 reg, u16 count} + count floats, register = byte
offset / 16 + 208, ending at c256); the constant table (D3DX CTAB, at +10) gives each effect parameter's default value.
Each shader is identified by content: SHA-256 of its whole container (header, constant table, descriptor, microcode)
with the literal VALUES and the parameter DEFAULT values zeroed, so a changed number changes only that value and a
changed program (another build, a mod, garbage) is refused.

Input: the full ZZDATA0.BIN (base 0), or a slice of it named zzdata0_<hex base>.bin (the owner's extract). Without one:
--stub writes the same macros as zeros with X360_SHADER_LITS 0 (the runtime keeps those features PC).

Macros (C string literals, spliced into the MSL source by concatenation so the shader text is byte for byte what it
was): X360L_<shader>_C<reg> "float4(a, b, c, d)", X360LC_<shader>_C<reg> "float4(a,b,c,d)" (compact), named scalars
X360S_*; effect parameter defaults for C code as float literals X360F_* (and {...} initialisers for tables).

usage: x360_shader_lits.py (--zzdata0 FILE | --stub) --out FILE.inc
       x360_shader_lits.py --zzdata0 FILE --pin     (developer: print the identity hashes of the shaders used)"""
import argparse
import hashlib
import math
import re
import struct
import sys
from pathlib import Path

WINDOW = (0x09BB7000, 0x09C1C000)    # the 18 effects (retail USA disc), searched for containers by content
MAGIC = 0x102A0E00

# Shader (its retail ZZDATA0 offset) -> SHA-256 (first 16 hex digits) of its container with the literal values and the
# parameter default values zeroed (retail USA X360 disc; printed by --pin).
PINS = """
09bb7b1c 53f1082e0d9f82f1
09bb919c 9231f07a58cd63c7
09bc003c c1577b4d28e42a99
09bc692c 55327c6d958dfdaf
09bc78b4 33b7cc34bf8ebac3
09bcbda4 2868a08d172fb282
09bcc710 f61b13d7813febf1
09bcd0b8 2699046d6d357733
09bcda60 4dffcca565a72d82
09bce418 95a3ff1d5d5dc7a8
09bcedd0 eb76bf6792d1f156
09bcf7c4 a861605a86b67437
09bd020c 2e56d5760a983294
09bd0c54 95c854d836909fc1
09bd16ac e3891ef48220400a
09bd294c 821e251fb80db208
09bd31e8 931875b63e008b83
09bd5428 e268582f3f1863da
09bd6274 e64be1348c72e086
09bd6bd4 f4388a8a3a8687fb
09bd7534 92643b3c317d760a
09bd7e94 b1f112c2a235d5d9
09be9844 75dfaa9f84195632
09bea960 a0dfb64a59bd7b99
09bf5164 e4a3ca7d2947dd0d
09bfa2d0 4f98fb4e914d551f
09bfc724 44a626c3c81d539c
09bfd3e0 6cf582f13aa2bf33
09bffc0c 01ca2e4c00b2c8b8
09c05e24 cf267d9f8b988d13
09c07860 9e9a67b6b6a74412
09c0c3b4 1955e6d2579b4382
09c0d800 082e021e1e9407e2
09c0de10 a949e0cb6d8c4cb5
09c14d8c 9e259c9c82dd27c0
09c17c18 635176f5f0ba7c31
"""

# Register sets: (shader, first register, count, style) -> X360L_ / X360LC_ macros, one per register.
SETS = [
    # 04_grass Grass Shell0-4 VS, Shell0/1 PS (materials.m grass_vs_body0-4, grass_ps_body0/1)
    ("09bcbda4", 255, 1, "p"), ("09bcc710", 255, 1, "p"), ("09bcd0b8", 255, 1, "p"), ("09bcda60", 254, 2, "p"),
    ("09bce418", 254, 2, "p"), ("09bd294c", 254, 2, "p"), ("09bd31e8", 254, 2, "p"),
    # 04_grass GrassTransition Shell0-4 VS / PS (grass_transition_vs_body0-4, grass_transition_ps_body0-4)
    ("09bcedd0", 255, 1, "p"), ("09bcf7c4", 255, 1, "p"), ("09bd020c", 255, 1, "p"), ("09bd0c54", 254, 2, "p"),
    ("09bd16ac", 254, 2, "p"), ("09bd5428", 253, 3, "p"), ("09bd6274", 254, 2, "p"), ("09bd6bd4", 254, 2, "p"),
    ("09bd7534", 254, 2, "p"), ("09bd7e94", 254, 2, "p"),
    # 13_watersea VS / PS (water_vs_body, water_ps_body)
    ("09bfc724", 253, 3, "p"), ("09bfd3e0", 253, 3, "p"),
    # 16_worldnormalmap aux-light VS / PS (aux_vs_body, aux_ps_body)
    ("09c0c3b4", 255, 1, "c"), ("09c0de10", 254, 2, "c"),
    # 03_glassreflect VS / PS (glass_vs_body, glass_ps_body)
    ("09bc692c", 254, 2, "p"), ("09bc78b4", 252, 4, "p"),
    # 11 billboard trees VS (trees_vs_body)
    ("09bf5164", 255, 1, "p"),
]

# Scalars used inline by hand-written shader code: (macro, shader, register, component, spelling, scale)
# spelling: p = shortest float32 text with ".0" on integers, i = shortest without it, d = shortest without the leading
# zero (".01"), g = 9 significant digits. scale: the port's use of the game's value (-1: negated, 2: doubled).
SCALARS = [
    # 00_car VS 09bb7b1c: the metallic flake (materials.m car paint R30)
    ("X360S_CAR_FLAKE_SCALE", "09bb7b1c", 254, 1, "p", 1),     # MetallicScale x this
    ("X360S_CAR_FLAKE_SCALE_T", "09bb7b1c", 254, 1, "i", 1),
    ("X360S_CAR_FLAKE_VOL", "09bb7b1c", 255, 2, "p", 1),       # VOLUMEMAP coordinates = object position x this
    ("X360S_CAR_FLAKE_VOL_T", "09bb7b1c", 255, 2, "i", 1),
    ("X360S_CAR_FADE_A", "09bb7b1c", 255, 1, "p", 1),          # fade = saturate(A - B |LocalEyePos - pos|)
    ("X360S_CAR_FADE_A_T", "09bb7b1c", 255, 1, "i", 1),
    ("X360S_CAR_FADE_B", "09bb7b1c", 254, 0, "p", -1),
    ("X360S_CAR_FADE_B_T", "09bb7b1c", 254, 0, "i", -1),
    # 00_car PS 09bb919c: the hotspot lobe
    ("X360S_CAR_HOT_SCALE", "09bb919c", 255, 3, "p", 1),
    ("X360S_CAR_HOT_POWER", "09bb919c", 253, 0, "p", 1),
    # 01_car_normalmap PS 09bc003c (materials.m car paint R26)
    ("X360S_CARNM_SPEC_POWER", "09bc003c", 252, 1, "p", 1),
    ("X360S_CARNM_WRAP", "09bc003c", 253, 2, "p", 1),
    ("X360S_CARNM_WRAP_T", "09bc003c", 253, 2, "i", 1),
    ("X360S_CARNM_SPEC", "09bc003c", 254, 0, "p", 1),
    ("X360S_CARNM_NV_MIN", "09bc003c", 254, 1, "p", 1),
    ("X360S_CARNM_NV_MIN_T", "09bc003c", 254, 1, "d", 1),
    ("X360S_CARNM_FLAKE_T", "09bc003c", 253, 0, "i", 1),
    # 17_road wetroad VS 09c14d8c / PS 09c17c18 (materials.m rain ripples)
    ("X360S_RIPPLE_TILE", "09c14d8c", 254, 3, "p", 1),
    ("X360S_RIPPLE_SCALE2", "09c17c18", 253, 2, "p", 1),
    ("X360S_RIPPLE_REFL", "09c17c18", 254, 1, "p", 1),
    ("X360S_RIPPLE_REFL_T", "09c17c18", 254, 1, "i", 1),
    ("X360S_RIPPLE_GLINT", "09c17c18", 254, 2, "p", 1),
    ("X360S_RIPPLE_GLINT_T", "09c17c18", 254, 2, "i", 1),
    # 16_worldnormalmap PS 09c0d800 (materials.m R25 parallax): offset = (2 x scale h + bias), self-shadow x this
    ("X360S_PARALLAX_SCALE2", "09c0d800", 255, 2, "p", 2),
    ("X360S_PARALLAX_BIAS", "09c0d800", 254, 0, "p", -1),
    ("X360S_PARALLAX_SELFSHADOW", "09c0d800", 254, 1, "p", 1),
    # 16_worldnormalmap aux-light VS 09c0c3b4: the depth nudge (materials.m aux_vs_body)
    ("X360S_AUX_DEPTH_NUDGE", "09c0c3b4", 255, 0, "g", 1),
    ("X360S_AUX_DEPTH_NUDGE_T", "09c0c3b4", 255, 0, "d", 1),
    # 15_worldbone VS 09c05e24: the N.V floor (materials.m bone_vs_body)
    ("X360S_BONE_NV_MIN", "09c05e24", 255, 2, "p", 1),
    # 06_particles PS 09bea960: shadow term 0.5 + 0.5 shadow (materials.m particles_msl X360 path)
    ("X360S_PARTICLE_HALF", "09bea960", 255, 0, "p", 1),
    # 12_visualtreatment PS 09bfa2d0: speed-blur tap weights and the blur's depth fade (render_post.m fs_vt)
    ("X360S_VT_W0", "09bfa2d0", 255, 0, "i", 1),
    ("X360S_VT_W1", "09bfa2d0", 254, 2, "i", 1),
    ("X360S_VT_W2", "09bfa2d0", 255, 3, "i", 1),
    ("X360S_VT_W3", "09bfa2d0", 255, 2, "i", 1),
    ("X360S_VT_FADE_A", "09bfa2d0", 255, 1, "i", 1),
    ("X360S_VT_FADE_B", "09bfa2d0", 254, 0, "g", 1),
]


# Effect parameter defaults (CTAB DefaultValue) used by C code: (macro, shader, parameter, [(row, component), ...], scale)
# One pair: a float literal (X360F_x 0.65f); several: an initialiser list ({0.0f, 0.3f, ...}).
DEFAULTS = [
    ("X360F_PARTICLE_MAXD", "09be9844", "MaxParticleSize", [(0, 0)], 1),       # 06_particles VS (materials.m size clamp)
    ("X360F_SPECMAPSCALE", "09c07860", "g_fSpecMapScale", [(0, 0)], 1),       # 15_worldbone PS (materials.m bone k[83])
    ("X360F_WAVE0_0", "09bfc724", "wave0", [(0, 0)], 1),                      # 13_watersea VS wave0/wave1 rows .x
    ("X360F_WAVE0_2", "09bfc724", "wave0", [(2, 0)], 1),
    ("X360F_WAVE0_3", "09bfc724", "wave0", [(3, 0)], 1),
    ("X360F_WAVE1_0", "09bfc724", "wave1", [(0, 0)], 1),
    ("X360F_WAVE1_2", "09bfc724", "wave1", [(2, 0)], 1),
    ("X360F_WAVE1_3", "09bfc724", "wave1", [(3, 0)], 1),
    ("X360F_WAVE1_3N", "09bfc724", "wave1", [(3, 0)], -1),                    # the port's mirrored .y
    ("X360F_KMAXDIST_GRASS", "09bcbda4", "kMaxDist", [(0, 0)], 1),
    ("X360F_KMAXDIST_WATER", "09bfc724", "kMaxDist", [(0, 0)], 1),
    ("X360F_KMAXDIST_GLASS", "09bc692c", "kMaxDist", [(0, 0)], 1),
    ("X360F_KMAXDIST_TREES", "09bf5164", "kMaxDist", [(0, 0)], 1),
    ("X360F_KMAXDIST_WORLD", "09bffc0c", "kMaxDist", [(0, 0)], 1),           # 14_world VS (render.m fog clamp)
    ("X360F_GRASS_CURL", "09bcbda4", "CURLTIGHTNESS", [(0, 0)], 1),           # 04_grass (attrib_x360.c)
    ("X360F_GRASS_AMBIENT_SHELL", "09bd294c", "AmbientShellTable", [(r, 0) for r in range(6)], 1),
    ("X360F_GRASS_AMBIENT_NOISE", "09bd294c", "AmbientNoiseTable", [(r, 0) for r in range(6)], 1),
    ("X360F_GRASS_DIFFUSE_SHELL", "09bd294c", "DiffuseShellTable", [(r, 0) for r in range(6)], 1),
]


def u32(d, o):
    return struct.unpack_from(">I", d, o)[0]


def f32(bits):
    return struct.unpack(">f", struct.pack(">I", bits))[0]


def shortest(v):
    if v == int(v) and abs(v) < 1e7:
        return "%d" % v
    for n in range(1, 10):
        s = "%.*g" % (n, v)
        if struct.unpack("<f", struct.pack("<f", float(s)))[0] == v:
            return s
    return "%.9g" % v


def spell(v, how):
    if how == "g":
        return "%.9g" % v
    s = shortest(v)
    if how == "p" and "." not in s and "e" not in s:
        s += ".0"
    if how == "d" and s.startswith("0."):
        s = s[1:]
    return s


def g9(v, compact):
    s = "%.9g" % v
    if not compact and "." not in s and "e" not in s:
        s += ".0"
    return s


def parse(d, o):
    """One container at o: (identity hash, {register: [4 float32 bit patterns]}) or None if it doesn't hold together."""
    if o + 0x18 > len(d):
        return None
    flags, virt, phys, def_off, ctab_off, desc_off = struct.unpack_from(">6I", d, o)
    if not (0x18 <= virt <= 0x10000 and 0 < phys <= 0x20000 and o + virt + phys <= len(d)):
        return None
    if not all(x == 0 or 0x18 <= x < virt for x in (def_off, ctab_off, desc_off)):
        return None
    blob = bytearray(d[o:o + virt + phys])
    lits = {}
    if def_off:
        p = def_off + 0x18
        while True:
            if p + 4 > virt:
                return None
            w = u32(blob, p)
            if w == 0:
                break
            reg, count = w >> 16, w & 0xFFFF
            if count == 0 or count % 4 or p + 4 + 4 * count > virt or (reg & 0xFFF) % 16:
                return None
            first = (reg & 0xFFF) // 16 + 208
            for k in range(count // 4):
                lits[first + k] = list(struct.unpack_from(">4I", blob, p + 4 + 16 * k))
            blob[p + 4:p + 4 + 4 * count] = bytes(4 * count)    # identity: everything but the values
            p += 4 + 4 * count
    defaults = {}
    if ctab_off:
        t = ctab_off + 4
        if t + 28 > virt:
            return None
        n, info = struct.unpack_from(">I", blob, t + 12)[0], struct.unpack_from(">I", blob, t + 16)[0]
        if n > 256 or t + info + 20 * n > virt:
            return None
        spans = []
        for k in range(n):
            name, rs, ri, rc, _, ti, dv = struct.unpack_from(">IHHHHII", blob, t + info + 20 * k)
            if t + name >= virt or (dv and t + dv + 16 * rc > virt):
                return None
            end = bytes(blob).find(b"\0", t + name, virt)
            if end < 0:
                return None
            if dv and rs == 2:
                defaults[bytes(blob[t + name:end]).decode("latin-1")] = [
                    list(struct.unpack_from(">4I", blob, t + dv + 16 * r)) for r in range(rc)]
                spans.append((t + dv, 16 * rc))
        for a, n4 in spans:                                       # identity: everything but the default values
            blob[a:a + n4] = bytes(n4)
    return hashlib.sha256(bytes(blob)).hexdigest()[:16], (lits, defaults)


def scan(path):
    """All containers in the effect window of ZZDATA0 (full file, or a zzdata0_<base>.bin slice)."""
    m = re.match(r"zzdata0_([0-9a-fA-F]+)\.bin$", path.name, re.I)
    base = int(m.group(1), 16) if m else 0
    size = path.stat().st_size
    if base > WINDOW[0] or base + size < WINDOW[1]:
        raise SystemExit("x360_shader_lits: %s does not cover ZZDATA0.BIN 0x%X-0x%X (the X360 shaders); it is not the "
                         "retail X360 NFS/ZZDATA0.BIN (%d bytes at base 0x%X)" % (path, WINDOW[0], WINDOW[1], size, base))
    with open(path, "rb") as f:
        f.seek(WINDOW[0] - base)
        d = f.read(WINDOW[1] - WINDOW[0])
    found = {}
    for i in range(0, len(d) - 0x18, 4):
        if (u32(d, i) & 0xFFFFFF00) != MAGIC:
            continue
        r = parse(d, i)
        if r:
            found.setdefault(r[0], []).append(("%08x" % (WINDOW[0] + i), r[1]))
    return found


def needed():
    return sorted({s[0] for s in SETS} | {s[1] for s in SCALARS} | {s[1] for s in DEFAULTS})


def pins():
    return dict(line.split() for line in PINS.split("\n") if line.strip())


def read_all(path):
    found = scan(path)
    want, have = pins(), {}
    missing = []
    for name in needed():
        hits = found.get(want.get(name, "-"), [])
        if len(hits) != 1:
            missing.append(name)
            continue
        have[name] = hits[0][1]
    if missing:
        n = sum(len(v) for v in found.values())
        raise SystemExit("x360_shader_lits: refused %s: %d of the %d X360 shaders the port uses are not in it (%s%s; %d "
                         "shader containers found). It is not the retail USA Xbox 360 NFS Most Wanted NFS/ZZDATA0.BIN "
                         "(another build or a modified/garbage file). Use the disc's own file, or build without X360 "
                         "shaders (gen_tables.py --no-x360)." % (path, len(missing), len(needed()), " ".join(missing[:6]),
                                                                  " ..." if len(missing) > 6 else "", n))
    return have


def finite(v, what):
    if not math.isfinite(v):
        raise SystemExit("x360_shader_lits: refused: %s is not a finite number (%r); the file is not the retail "
                         "ZZDATA0.BIN (modified)" % (what, v))
    return v


def text(have, source):
    out = ["// Generated by tools_snapshot/x360_shader_lits.py from the player's %s. Do not edit." % source,
           "// The X360 shaders' literal constants (by shader = ZZDATA0 offset, register) and the scalars the port's",
           "// hand-written Metal code takes from them.",
           "#define X360_SHADER_LITS %d" % (1 if have else 0)]
    for name, first, count, style in SETS:
        for r in range(first, first + count):
            vals = [finite(f32(b), "%s c%d" % (name, r)) for b in have[name][0][r]] if have else [0.0] * 4
            sep = "," if style == "c" else ", "
            out.append('#define X360L%s_%s_C%d "float4(%s)"' % ("C" if style == "c" else "", name.upper(), r,
                                                                 sep.join(g9(v, style == "c") for v in vals)))
    for macro, name, reg, comp, how, scale in SCALARS:
        if not have:                       # valid MSL wherever it is spliced ("0.0f"); the features are off anyway
            out.append('#define %s "0.0"' % macro)
            continue
        v = finite(struct.unpack("<f", struct.pack("<f", f32(have[name][0][reg][comp]) * scale))[0], "%s c%d" % (name, reg))
        out.append('#define %s "%s"' % (macro, spell(v, how)))
    for macro, name, param, cells, scale in DEFAULTS:
        vals = [finite(struct.unpack("<f", struct.pack("<f", f32(have[name][1][param][r][c]) * scale))[0],
                       "%s %s" % (name, param)) if have else 0.0 for r, c in cells]
        text = ["%sf" % spell(v, "p") for v in vals]
        out.append("#define %s %s" % (macro, text[0] if len(text) == 1 else "{" + ", ".join(text) + "}"))
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zzdata0", type=Path)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--pin", action="store_true")
    a = ap.parse_args()
    if a.pin:
        found = scan(a.zzdata0)
        by_off = {off: h for h, v in found.items() for off, _ in v}
        for name in needed():
            print(name, by_off.get(name, "MISSING"))
        return
    if a.stub:
        t = text(None, "files with no X360 NFS/ZZDATA0.BIN: zeros, the X360 shader features stay PC")
    else:
        t = text(read_all(a.zzdata0), "X360 NFS/ZZDATA0.BIN")
    a.out.write_text(t)
    print("x360_shader_lits: wrote %s (%s)" % (a.out, "stub" if a.stub else "%d shaders" % len(needed())))


if __name__ == "__main__":
    main()
