#!/usr/bin/env python3
"""x360_shader_body.py - the bodies of the X360 shader programs the port's Metal shaders run (grass, grass transition,
water, scenery lights, building glass, trees), translated at build time from the player's own X360 NFS/ZZDATA0.BIN.
The repository holds the translator and this script, never the game's programs. Run by tools/gen_tables.py; writes
x360_shader_body.inc (runtime/gen/), included by materials.m after x360_shader_lits.inc.

Pipeline (each step ours unless noted):
1. find each shader in ZZDATA0's effect window by content: x360_shader_lits.py's identity hash (the whole 2005
   container with literal values and parameter defaults zeroed) must match the pin for that shader, exactly once;
2. rewrite the 2005 container (magic 0x102A0E00) into the newer 0x102A11xx layout (definitions, constant table,
   descriptor, microcode);
3. translate the microcode with the XenosRecomp translator core (tools_snapshot/xenosrecomp, MIT, built here with the
   Command Line Tools' clang++; no Xcode, no DXC);
4. a readability pass (D3D9-style names: constant table names, plain inputs, merged predicated blocks, unused
   registers dropped);
5. the Metal pass: each statement re-spelled for MSL (types, select/abs/clamp spellings, the port's constant buffer
   layout) and laid out by RECIPES below: which translated statements a body uses, in what order and spelling style,
   plus the port's own lines (its bindings, specializations and test switches). The recipes hold no game content:
   statement numbers, register-buffer layouts and our own code.
The emitted text is byte for byte what materials.m held before (so Metal compiles the same source and its shader
cache keys stay the same); --check compares against a reference file of the old literals.

No ZZDATA0 (--stub): every body is "" with X360_SHADER_BODIES 0; the runtime keeps those features PC (they also need
X360_SHADER_LITS, generated from the same file).

usage: x360_shader_body.py (--zzdata0 FILE | --stub) --out FILE.inc [--work DIR] [--check REF.json]"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import x360_shader_lits as lits  # noqa: E402  (the shader window, container parser and identity pins)

TRANSLATOR = HERE / "xenosrecomp"
TRANSLATOR_SOURCES = ["main.cpp", "shader_recompiler.cpp"]

# Shaders without literal constants are not in x360_shader_lits.py's pins; their identity hash (same definition) is here.
EXTRA_PINS = {"09bf6634": "3613a3277582e0c9"}


def u32(d, o):
    return struct.unpack_from(">I", d, o)[0]


def words(*vals):
    return b"".join(struct.pack(">I", v & 0xFFFFFFFF) for v in vals)


# ---------------------------------------------------------------------------------------------------------------------
# 1. Locate by content


def read_window(path):
    m = re.match(r"zzdata0_([0-9a-fA-F]+)\.bin$", path.name, re.I)
    base = int(m.group(1), 16) if m else 0
    size = path.stat().st_size
    lo, hi = lits.WINDOW
    if base > lo or base + size < hi:
        raise SystemExit("x360_shader_body: %s does not cover ZZDATA0.BIN 0x%X-0x%X (the X360 shaders); it is not the "
                         "retail X360 NFS/ZZDATA0.BIN (%d bytes at base 0x%X)" % (path, lo, hi, size, base))
    with open(path, "rb") as f:
        f.seek(lo - base)
        return f.read(hi - lo)


def locate(d, names):
    """{shader: offset in d} for every wanted shader whose identity hash matches its pin exactly once."""
    want = dict(lits.pins())
    want.update(EXTRA_PINS)
    hits = {}
    for i in range(0, len(d) - 0x18, 4):
        if (u32(d, i) & 0xFFFFFF00) != lits.MAGIC:
            continue
        r = lits.parse(d, i)
        if r:
            hits.setdefault(r[0], []).append(i)
    found, missing = {}, []
    for n in names:
        h = hits.get(want.get(n, "-"), [])
        if len(h) != 1:
            missing.append(n)
        else:
            found[n] = h[0]
    return found, missing, sum(len(v) for v in hits.values())


# ---------------------------------------------------------------------------------------------------------------------
# 2. 2005 container -> newer layout (what the translator reads)


def convert(d, o):
    """Old layout (big-endian u32): +00 flags (0x102A0E00 | 1 for VS), +04 virtualSize, +08 physicalSize, +0C
    definitions, +10 constant table, +14 descriptor; microcode at virtualSize. Definitions: {u16 reg, u16 count} +
    count inline floats, register = byte offset / 16 + 208. Newer layout: 36-byte header, constant table, definition
    table (index, count, offset into the physical data), shader descriptor; physical data = microcode + literals."""
    flags, virt, phys, def_off, ctab_off, desc_off = struct.unpack_from(">6I", d, o)
    is_vs = bool(flags & 1)
    ctab = d[o + ctab_off:o + ctab_off + 4 + u32(d, o + ctab_off)]
    microcode = d[o + virt:o + virt + phys]
    literals = []
    if def_off:
        p = o + def_off + 0x18
        while True:
            w = u32(d, p)
            if w == 0:
                break
            reg, count = w >> 16, w & 0xFFFF
            literals.append(((reg & 0xFFF) // 16 + 208, list(struct.unpack_from(">%dI" % count, d, p + 4))))
            p += 4 + count * 4
        if u32(d, p + 4) != 0:
            raise SystemExit("x360_shader_body: refused: shader at 0x%X has integer constant definitions" % (lits.WINDOW[0] + o))
    desc = struct.unpack_from(">%dI" % ((virt - desc_off) // 4), d, o + desc_off)
    header_size = 36
    ctab_new = header_size
    defs_new = ctab_new + len(ctab)
    defs_new += (-defs_new) % 4
    lit_phys = len(microcode) + (-len(microcode)) % 16
    lit_blob = def_entries = b""
    for creg, vals in literals:
        def_entries += struct.pack(">HHI", creg + (0 if is_vs else 256), len(vals), lit_phys + len(lit_blob))
        lit_blob += words(*(vals + [0] * ((-len(vals)) % 4)))
    def_table = words(0, 0, 0, 0, len(def_entries)) + def_entries + words(0, 0)
    shader_new = defs_new + len(def_table)
    shader = words(0, len(microcode), 0, desc[0], 0, desc[4])
    shader += words(desc[6], desc[7], 0) + words(*desc[10:]) if is_vs else words(desc[6], desc[7]) + words(*desc[8:])
    virtual_size = shader_new + len(shader)
    virtual_size += (-virtual_size) % 16
    physical = microcode + b"\0" * ((-len(microcode)) % 16) + lit_blob
    out = bytearray(virtual_size)
    out[0:header_size] = words(0x102A1100 | (1 if is_vs else 0), virtual_size, len(physical), 0, ctab_new, defs_new,
                               shader_new, 0, 0)
    out[ctab_new:ctab_new + len(ctab)] = ctab
    out[defs_new:defs_new + len(def_table)] = def_table
    out[shader_new:shader_new + len(shader)] = shader
    return bytes(out) + physical, ctab_constants(d, o + ctab_off), is_vs


def ctab_constants(d, o):
    """The D3DX constant table: [(name, set, register, count)], set 0 bool, 1 int4, 2 float4, 3 sampler."""
    t = o + 4
    n, info = u32(d, t + 12), u32(d, t + 16)
    out = []
    for k in range(n):
        name, rs, ri, rc, _, _, _ = struct.unpack_from(">IHHHHII", d, t + info + k * 20)
        out.append((d[t + name:d.index(b"\0", t + name)].decode("latin-1"), rs, ri, rc))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# 3. Translate


def build_translator(work):
    exe = work / "xenos_translate"
    # xcrun picks the active developer tools' clang++ and its SDK (the Command Line Tools are enough; no Xcode)
    cxx = ["xcrun", "clang++"] if shutil.which("xcrun") else [shutil.which("clang++") or "clang++"]
    cmd = cxx + ["-std=c++17", "-O2", "-w", "-o", str(exe)] + [str(TRANSLATOR / s) for s in TRANSLATOR_SOURCES]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode:
        raise SystemExit("x360_shader_body: the shader translator did not build (Command Line Tools clang++):\n" + r.stdout[-3000:])
    return exe


def translate(exe, work, containers):
    """{shader: translator text} for {shader: container bytes}."""
    args = []
    for name, data in containers.items():
        (work / (name + ".bin")).write_bytes(data)
        args += [str(work / (name + ".bin")), str(work / (name + ".txt"))]
    r = subprocess.run([str(exe)] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
    if r.returncode:
        raise SystemExit("x360_shader_body: the shader translator failed:\n" + r.stdout[-3000:])
    return {name: (work / (name + ".txt")).read_text() for name in containers}


# ---------------------------------------------------------------------------------------------------------------------
# 4. Readability pass (D3D9-style statements; the same text the port's shaders were written from)


def matching_paren(s, i):
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    raise ValueError("unbalanced parentheses")


def split_args(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return out


def rewrite_calls(s, name, fn):
    """Every call name(...) in s replaced by fn(arguments)."""
    pat = re.compile(r"\b" + re.escape(name) + r"\(")
    pos = 0
    while True:
        m = pat.search(s, pos)
        if not m:
            return s
        close = matching_paren(s, m.end() - 1)
        rep = fn(split_args(s[m.end():close]))
        s = s[:m.start()] + rep + s[close + 1:]
        pos = m.start() + len(rep)


def block(lines, i):
    """The lines of the { } block opened at lines[i] ('{'), and the index of its '}'."""
    j, depth, body = i + 1, 1, []
    while j < len(lines):
        t = lines[j].strip()
        depth += (t == "{") - (t == "}")
        if depth == 0:
            break
        body.append(lines[j])
        j += 1
    return body, j


def merge_predicated(lines):
    """Consecutive `if (p0) {..}` / `if (!p0) {..}` blocks with the same condition become one when the earlier block
    does not assign p0."""
    out, i = [], 0
    while i < len(lines):
        m = re.match(r"^(\s*)if \((!?p0)\)$", lines[i])
        if m and i + 1 < len(lines) and lines[i + 1].strip() == "{":
            indent, cond = m.groups()
            body, j = block(lines, i + 1)
            while (not any(re.search(r"\bp0\s*=", b) for b in body) and j + 2 < len(lines)
                   and lines[j + 1].strip() == "if (%s)" % cond and lines[j + 2].strip() == "{"):
                more, j = block(lines, j + 2)
                body += more
            out += ["%sif (%s)" % (indent, cond), indent + "{"] + merge_predicated(body) + [indent + "}"]
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    return out


def readable(raw, consts):
    """The body of the translator's main() as D3D9-style statements (one string, its own line breaks)."""
    sig = raw.index("void main(")
    start = raw.index("{", raw.index(")\n{", sig))
    body = raw[start + 1:raw.rindex("}")]
    arrays = {c[0] for c in consts if c[1] == 2 and c[3] > 1}
    kept = []
    for ln in body.split("\n"):
        m = re.match(r"\s*float4 (c\d+) = asfloat\(uint4\((.*)\)\);", ln)
        if m or "g_HalfPixelOffset" in ln:
            continue                         # literals: the port's lines name them (X360L_* from x360_shader_lits.py)
        kept.append(ln)
    body = "\n".join(kept)
    body = re.sub(r"\[branch\] if \(g_SpecConstants\(\) & SPEC_CONSTANT_ALPHA_TEST\)\s*\{\s*clip\(oC0\.w - g_AlphaThreshold\);\s*\}",
                  "// alpha test is D3D render state (ALPHATESTENABLE/ALPHAREF), not shader code\n", body)
    body = re.sub(r"\s*else if \(g_SpecConstants\(\) & SPEC_CONSTANT_ALPHA_TO_COVERAGE\)\s*\{[^}]*\}", "", body)

    def tex(kind):
        def f(a):
            s = a[0].replace("_Texture%sDescriptorIndex" % kind, "")
            if len(a) >= 4 and a[3] != "float2(0, 0)":
                return "tex%soffset(%s, %s, %s)" % (kind, s, a[2], a[3])
            return "tex%s(%s, %s)" % (kind, s, a[2])
        return f
    body = rewrite_calls(body, "tfetch2D", tex("2D"))
    body = rewrite_calls(body, "tfetch3D", tex("3D"))
    body = rewrite_calls(body, "tfetchCube", lambda a: "texCUBE_xenos(%s, %s, %s)" % (
        a[0].replace("_TextureCubeDescriptorIndex", ""), a[2], a[3] if len(a) > 3 else "cubeMapData"))
    body = rewrite_calls(body, "tfetchTexcoord", lambda a: a[1])
    body = rewrite_calls(body, "tfetchR11G11B10", lambda a: "unpackNormalR11G11B10(%s)" % a[0])
    body = rewrite_calls(body, "rcp", lambda a: "(1.0 / (%s))" % a[0])
    for arr in sorted(arrays):
        body = rewrite_calls(body, arr, lambda a, arr=arr: "%s[%s]" % (arr, a[0]))
    body = re.sub(r"\(g_Booleans & (\w+)\) != 0", r"\1", body)
    body = re.sub(r"\(g_Booleans & (\w+)\) == 0", r"!\1", body)
    body = body.replace("float2(iFace ? 1.0 : -1.0, 1.0)", "float2(iFace >= 0 ? 1.0 : -1.0, 1.0)")
    lines = merge_predicated(body.split("\n"))
    text = "\n".join(lines)
    out = []
    for ln in lines:                         # unused registers / outputs / helpers
        m = re.match(r"\s*(?:float4|int|bool|float|CubeMapData) (\w+) = (?:0\.0|0|false|\(CubeMapData\)0);$", ln)
        if m and len(re.findall(r"\b%s\b" % m.group(1), text)) == 1:
            continue
        m = re.match(r"\s*(o\w+) = 0\.0;$", ln)
        if m and len(re.findall(r"\b%s\b" % m.group(1), text)) == 1:
            continue
        out.append(ln)
    return "\n".join(out)




# ---------------------------------------------------------------------------------------------------------------------
# 5. Metal pass: per-statement spellings, laid out by the recipes

XMAX = "3.402823466e+38f"
COMPARE = re.compile(r"^(\w+(?:\.[xyzw]+)?) = (.*?) (>=|<=|==|!=|>|<) (.*);$")
ALPHA_NOTE = "// alpha test is D3D render state (ALPHATESTENABLE/ALPHAREF), not shader code"


def sub_constants(s, r):
    """Effect parameters -> the port's constant buffer: NAME -> buf.k[n]; NAME[i] -> buf.k[n + i] (verbose) or the
    folded buf.k[n+i] (recipe "fold"); a0-relative c[N + a0] off a one-register parameter reads that parameter."""
    s = re.sub(r"\bc\[\d+ \+ a0\] /\* (\w+) \*/", r"\1", s)
    buf, fold = r["buf"], r.get("fold")
    for name in sorted(r["k"], key=len, reverse=True):
        n = r["k"][name]
        if fold:
            s = re.sub(r"\b%s\[(\d+)\]" % re.escape(name), lambda m: "%s.k[%d]" % (buf, n + int(m.group(1))), s)
        s = re.sub(r"\b%s\[([^\]]+)\]" % re.escape(name), lambda m: "%s.k[%d + %s]" % (buf, n, m.group(1)), s)
        s = re.sub(r"\b%s\b(?![\[(])" % re.escape(name), "%s.k[%d]" % (buf, n), s)
    return s


def sub_textures(s, r):
    """tex2D/tex3D/tex2Doffset(SAMPLER, uv[, offset]) -> the port's texture and sampler (recipe "tex" templates)."""
    for kind in ("2Doffset", "3Doffset", "2D", "3D"):
        s = rewrite_calls(s, "tex" + kind, lambda a: r["tex"][a[0]].format(uv=a[1], off=a[2] if len(a) > 2 else "float2(0)"))
    return s


def common(s, r):
    for a, b in r.get("subst", ()):          # the port's own bindings for single operands (regular expressions)
        s = re.sub(a, b, s)
    s = sub_constants(s, r)
    s = sub_textures(s, r)
    s = rewrite_calls(s, "unpackNormalR11G11B10", lambda a: a[0])    # PC vertices carry float normals
    return re.sub(r"\babs\(", "fabs(", s)


def verbose(ln, r):
    """One readable-pass line in the translator's own layout, spelled for Metal (grass, water, scenery lights)."""
    s = common(ln, r)
    s = re.sub(r"\bfrac\(", "fract(", s)
    if not r.get("alpha_note"):
        s = s.replace(ALPHA_NOTE, "")
    for a, b in r.get("inputs", {}).items():
        s = re.sub(r"\b%s\b" % a, b, s)
    s = s.replace("FLT_MIN, FLT_MAX", "(-%s), %s" % (XMAX, XMAX))
    s = rewrite_calls(s, "select", lambda a: "select( %s, %s,%s)" % (a[2], a[1], a[0]))
    m = re.match(r"^(\s*)clip\(any\((.*)\) \? -1 : 1\);$", s)
    if m:
        return "%sif (%s) discard_fragment();" % m.groups()
    m = re.match(r"^(\s*)(o\w+|float4 r\d+) = 0\.0;$", s)
    if m:
        return "%s%s = float4(0);" % m.groups()
    m = re.match(r"^(\s*)(\w+)\.([xyzw]+) = (.*);$", s)
    if m and r.get("trim") and len(m.group(3)) > 1:   # Metal wants operands as wide as the destination: no padding
        n = len(m.group(3))
        s = m.group(1) + "%s.%s = " % (m.group(2), m.group(3)) + re.sub(
            r"\.([xyzw]{%d})([xyzw]+)\b" % n, lambda q: "." + q.group(1) if len(set(q.group(2) + q.group(1)[-1])) == 1
            else q.group(0), m.group(4)) + ";"
        m = re.match(r"^(\s*)(\w+)\.([xyzw]+) = (.*);$", s)
    if m and len(m.group(3)) > 1 and r.get("wrap", True):
        return "%s%s.%s = float%d(%s);" % (m.group(1), m.group(2), m.group(3), len(m.group(3)), m.group(4))
    if s.strip() == "return;":
        return r.get("ret", s.replace("return;", ""))
    return s


def condensed(ln, r):
    """One readable-pass statement in the port's condensed spelling (glass, trees): no full or padding swizzles, no
    max(x, x) copies, comparisons as float()."""
    s = common(ln.strip(), r)
    s = re.sub(r"clamp\(\(1\.0 / \((\w+\.\w+)\)\), FLT_MIN, FLT_MAX\)", r"clamp(1.0 / \1, -XMAX, XMAX)", s)
    s = s.replace("FLT_MIN, FLT_MAX", "-XMAX, XMAX")
    s = re.sub(r"\.xyzw\b", "", s)
    s = rewrite_calls(s, "max", lambda a: a[0] if len(a) == 2 and a[0] == a[1] else "max(%s)" % ", ".join(a))
    m = COMPARE.match(s)
    return "%s = float(%s %s %s);" % m.groups() if m else s


def parts_verbose(text, r):
    """[part, ...]: the transformed lines, split where the port's own statements replace a range ("cut", kept in
    materials.m between the parts) and without the ranges it drops."""
    lines = [verbose(ln, r) for ln in text.split("\n")]
    for i in r.get("full", ()):              # the port keeps all four components (PC geometry has z)
        lines[i] = re.sub(r"\.xyw\b", "", lines[i])
    term = [ln + "\n" for ln in lines[:-1]] + [lines[-1]]
    cuts = {i: (a, b) for a, b in r.get("cut", ()) for i in range(a, b + 1)}
    drops = {i for a, b in r.get("drop", ()) for i in range(a, b + 1)}
    parts, cur = [], ""
    for i, t in enumerate(term):
        if i in cuts:
            if i == cuts[i][0]:
                parts.append(cur)
                cur = ""
        elif i not in drops:
            cur += t
    return parts + [cur]


def parts_condensed(text, r):
    """[part, ...] from the recipe's layout: each token one line of statement numbers joined by a space ("a+b"),
    "fA:B" a ps copy folded in (ps = x; d = ps; -> d = x;), "vA:D" four component writes as one float4, "mN" a + -b as
    a - b, "|" a split for the port's own lines."""
    st = text.split("\n")
    parts, cur = [], []
    for tok in r["layout"].split():
        if tok == "|":
            parts.append("".join(cur))
            cur = []
            continue
        if tok[0] == "f":
            a, b = map(int, tok[1:].split(":"))
            x = re.match(r"^ps = (.*);$", condensed(st[a], r)).group(1)
            d = re.match(r"^(.*) = ps;$", condensed(st[b], r)).group(1)
            cur.append("%s = %s;\n" % (d, x))
        elif tok[0] == "v":
            a, b = map(int, tok[1:].split(":"))
            ms = [re.match(r"^(\w+)\.([xyzw]) = (.*);$", condensed(st[i], r)) for i in range(a, b + 1)]
            assert [m.group(2) for m in ms] == list("xyzw") and len({m.group(1) for m in ms}) == 1, tok
            cur.append("%s = float4(%s);\n" % (ms[0].group(1), ", ".join(m.group(3) for m in ms)))
        elif tok[0] == "m":
            cur.append(condensed(st[int(tok[1:])], r).replace(" + -", " - ") + "\n")
        else:
            cur.append(" ".join(condensed(st[int(i)], r) for i in tok.split("+")) + "\n")
    return parts + ["".join(cur)]


# The recipes: shader (its retail ZZDATA0 offset), the port's constant buffer ("buf", "k": parameter -> register,
# "fold": NAME[i] as one index), textures ("tex": sampler -> the port's sample expression), spelling options and the
# statement ranges the port replaces with its own code (that code stays in materials.m, between the parts).
FOG = dict(WorldViewProj=0, ScreenOffset=4, Fog_Br_Plus_Bm=5, Fog_One_Over_BrBm=6, Fog_Const_1=7, Fog_Const_2=8,
           Fog_Const_3=9, kMaxDist=10, Fog_DistanceScale=11)
GRASS_VS = dict(buf="g", k=dict(FOG, LocalEyePos=12, WorldView=13, World=16, LocalLightVec=18, DiffuseColour=19,
                                MAXSHELLS=20, GRASSHEIGHT=21, DIFFUSESPACE=22, NOISESPACE=23, LOWNOISESPACE=24, SCRUFF=25,
                                CURLTIGHTNESS=26, LODSTART=27, matShadowMapWVP=28, AmbientColour=57))
GRASS_PS = dict(buf="g", k=dict(DiffuseColour=19, GRASSCOLOUR=32, MAXSHELLS=33, DIFFUSEMIPMAPBIAS=34,
                                NOISEMIPMAPBIAS=35, LOWNOISEINTENSITY=36, g_fShadowMapBias=37, g_iShadowMapEnabled=38,
                                AmbientShellTable=39, AmbientNoiseTable=45, DiffuseShellTable=51, LocalLightVec=58,
                                SpecularColour=59, SpecularPower=60, IS_PARALLEX_MAPPED=61),
                tex=dict(SHADOWMAP_SAMPLER="grass_shadow(ts, {uv}, {off})",
                         DIFFUSEMAP_SAMPLER="td.sample(diffuse_s, {uv}, bias(g.k[34].x))",
                         VOLUMEMAP_SAMPLER="tv.sample(noise_s, {uv}, bias(g.k[35].x))",
                         MISCMAP1_SAMPLER="tn.sample(noise_s, {uv}, bias(g.k[35].x))",
                         NORMALMAP_SAMPLER="tm.sample(diffuse_s, {uv})", SPECULARMAP_SAMPLER="tw.sample(diffuse_s, {uv})",
                         OPACITYMAP_SAMPLER="to.sample(diffuse_s, {uv})"),
                # the a0-indexed shell literal: the port passes the shell's value in k[63].x (literal-only specialization)
                subst=[(r"\bc\[254 \+ a0\]\.[xyzw]\b", "g.k[63].x")])
AUX = dict(buf="a", fold=True, trim=True, k=dict(WorldViewProj=0, ScreenOffset=4, LocalEyePos=5, MipMapBias=6, Lights=7,
                                                  **{"ActiveAuxiliaryLight%d" % i: 39 + i for i in range(6)}))
AUX_TEX = dict(DIFFUSEMAP_SAMPLER="td.sample(wrap,{uv},bias(-1.0f))", SPECULARMAP_SAMPLER="ts.sample(wrap,{uv},bias(-1.0f))",
               NORMALMAP_SAMPLER="tn.sample(wrap,{uv},bias(-1.0f))")
GLASS_PS = dict(buf="g", fold=True, k=dict(Brightness=24, g_iShadowMapEnabled=26, g_fShadowMapBias=27),
                tex=dict(DIFFUSEMAP_SAMPLER="tex0.sample(smp0, {uv}, bias(aux.lodbias[0]))",
                         MISCMAP1_SAMPLER="GLASS_REFL.sample(GLASS_REFL_S, {uv}, bias(aux.lodbias[GLASS_REFL_N]))"))
RECIPES = {
    # 04_grass Grass Shell0-4 VS / Shell0, Shell1-4 PS
    "GRASS_VS_BODY0": dict(GRASS_VS, shader="09bcbda4"),
    "GRASS_VS_BODY1": dict(GRASS_VS, shader="09bcc710"),
    "GRASS_VS_BODY2": dict(GRASS_VS, shader="09bcd0b8"),
    "GRASS_VS_BODY3": dict(GRASS_VS, shader="09bcda60"),
    "GRASS_VS_BODY4": dict(GRASS_VS, shader="09bce418"),
    "GRASS_PS_BODY0": dict(GRASS_PS, shader="09bd294c"),
    "GRASS_PS_BODY1": dict(GRASS_PS, shader="09bd31e8"),
    # 04_grass GrassTransition Shell0-4 VS / PS
    "GRASS_TRANSITION_VS_BODY0": dict(GRASS_VS, shader="09bcedd0"),
    "GRASS_TRANSITION_VS_BODY1": dict(GRASS_VS, shader="09bcf7c4"),
    "GRASS_TRANSITION_VS_BODY2": dict(GRASS_VS, shader="09bd020c"),
    "GRASS_TRANSITION_VS_BODY3": dict(GRASS_VS, shader="09bd0c54"),
    "GRASS_TRANSITION_VS_BODY4": dict(GRASS_VS, shader="09bd16ac"),
    "GRASS_TRANSITION_PS_BODY0": dict(GRASS_PS, shader="09bd5428"),
    "GRASS_TRANSITION_PS_BODY1": dict(GRASS_PS, shader="09bd6274"),
    "GRASS_TRANSITION_PS_BODY2": dict(GRASS_PS, shader="09bd6bd4"),
    "GRASS_TRANSITION_PS_BODY3": dict(GRASS_PS, shader="09bd7534"),
    "GRASS_TRANSITION_PS_BODY4": dict(GRASS_PS, shader="09bd7e94"),
    # 13_watersea VS / PS. VS: the position transform (27-36) is the port's full 4-component one (PC creek vertices
    # have z) and WorldView row 2 (121) uses all four components for the same reason. PS: no VPOS register (15).
    "WATER_VS_BODY": dict(shader="09bfc724", buf="w", fold=True, wrap=False, ret="\n", cut=[(27, 36)], full=[121],
                          k=dict(FOG, WorldView=12, World=15, LightDirVec=18, LocalEyePos=19, vTextureOffset=20, wave0=21,
                                 wave1=25)),
    "WATER_PS_BODY": dict(shader="09bfd3e0", buf="w", fold=True, wrap=False, trim=True, ret="\n", alpha_note=True,
                          cut=[(15, 15)], inputs={r"iTexCoord(\d)": r"in.t\1"},
                          k=dict(LightDirVec=18, LocalEyePos=19, vTextureOffset=20, SpecularScale=29),
                          tex=dict(DIFFUSEMAP_SAMPLER="diffuse.sample(wrap, {uv})", MISCMAP1_SAMPLER="waves.sample(wrap, {uv})",
                                   MISCMAP2_SAMPLER="reflection.sample(edge, {uv})")),
    # 16_worldnormalmap aux-light VS / PS. VS: the PC's TextureOffset (k[47]) on the uv; the position (28-38) is the
    # port's (plus the depth nudge). PS: no VPOS (16); the PC normal map decode replaces 27 and 30.
    "AUX_VS_BODY": dict(AUX, shader="09c0c3b4", cut=[(28, 38)], drop=[(134, 134)],
                        subst=[(r"\biTexCoord0\.xy\b", "(iTexCoord0.xy+a.k[47].xy)")]),
    "AUX_PS_BODY": dict(AUX, shader="09c0de10", tex=AUX_TEX, alpha_note=True, cut=[(16, 16), (27, 27), (30, 30)],
                        drop=[(296, 297)]),
    # 03_glassreflect VS / PS (the PS up to its shadow block, then from the shadow factor on; the shadow is the port's)
    "GLASS_VS_BODY": dict(shader="09bc692c", buf="g", fold=True, k=dict(FOG, WorldView=12, DiffuseColour=15, LocalEyePos=16,
                                                                        LightDirVec=17, matShadowMapWVP=18, AmbientColour=22),
                          layout="26+27+28+29+30 31+32+33 34+35 36 37 38 39 40 41 42+43 44 45 46+47 48+49 50 51+52 53 54+55 "
                                 "56 57+58 59 60 61 62 63 64 65 66 67+68 69 70+71+72 73 f74:75 v76:79 80 81 82 83 84 85 86+87 "
                                 "88 89 90+91 92 93+94 95+96 97 98 99 100 101 102"),
    "GLASS_PS_BODY": dict(GLASS_PS, shader="09bc78b4",
                          layout="17 18+19 20 21 22+23 24 25 26+27 28 29+30 31+32 33 34+35 36 37 38+39 40 41 42+43 44 45 46 "
                                 "47+48 49 50 51 f52:53 54 55 58 59 60 61"),
    "GLASS_PS_END": dict(GLASS_PS, shader="09bc78b4", layout="97 98 99 | 100 101 102+103 104 105 106 107"),
    # 11 billboard trees VS (its COLOR0, never read by the PS, dropped: 20, 63) / PS
    "TREES_VS_BODY": dict(shader="09bf5164", buf="g", fold=True, k=dict(FOG, WorldView=12, Params=15, LightDirVec=16,
                                                                        LocalCentre=17, DiffuseColour=18, LocalEyePos=19),
                          layout="19+21 22+23+24 25+26 27 28 29 30 31+32 33 34 35+36 37+38 39+40 f41:42 43 44 45 46 m47 48 "
                                 "49+50 51 52+53 54 55+56 57 58+59 60 61+62 64 65 66+67 68 69 70 71+72 73 74 75 76+77 78 79 "
                                 "80+81 82 83 84 85 86"),
    "TREES_PS_BODY": dict(shader="09bf6634", buf="g", fold=True, k=dict(Params=15),
                          tex=dict(DIFFUSEMAP_SAMPLER="tex0.sample(smp0, {uv}, bias(aux.lodbias[0]))"),
                          layout="9 10 11+12 13 14 15"),
}


def bodies(texts):
    """{macro name: text} for every recipe part, from {shader: readable-pass text}."""
    out = {}
    for name, r in RECIPES.items():
        ps = (parts_condensed if "layout" in r else parts_verbose)(texts[r["shader"]], r)
        if len(ps) == 1:
            out[name] = ps[0]
        else:
            for i, p in enumerate(ps):
                out["%s_%d" % (name, i)] = p
    return out


def c_string(text):
    """text as C string literals, one per line of the text."""
    if not text:
        return ['""']
    out = []
    for ln in re.findall(r"[^\n]*\n|[^\n]+", text):
        out.append('"' + ln.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\n", "\\n") + '"')
    return out


def include_text(macros, source):
    out = ["// Generated by tools_snapshot/x360_shader_body.py from the player's %s. Do not edit." % source,
           "// The X360 shader programs' bodies the port's Metal shaders (materials.m) run, translated at build time.",
           "#define X360_SHADER_BODIES %d" % (1 if macros else 0)]
    names = bodies_names()
    for name in names:
        lines = c_string(macros[name]) if macros else ['""']
        out.append("#define X360B_%s \\\n    " % name + " \\\n    ".join(lines))
    return "\n".join(out) + "\n"


def bodies_names():
    """Every macro name the include defines (the same set with or without ZZDATA0)."""
    names = []
    for name, r in RECIPES.items():
        n = 1 + (r["layout"].split().count("|") if "layout" in r else len(r.get("cut", ())))
        names += [name] if n == 1 else ["%s_%d" % (name, i) for i in range(n)]
    return names


def generate(zz, work):
    d = read_window(zz)
    names = sorted({r["shader"] for r in RECIPES.values()})
    found, missing, n = locate(d, names)
    if missing:
        raise SystemExit("x360_shader_body: refused %s: %d of the %d X360 shaders the port translates are not in it (%s; "
                         "%d shader containers found). It is not the retail USA Xbox 360 NFS Most Wanted NFS/ZZDATA0.BIN "
                         "(another build or a modified/garbage file). Use the disc's own file, or build without X360 "
                         "shaders (gen_tables.py --no-x360)." % (zz, len(missing), len(names), " ".join(missing), n))
    containers, consts = {}, {}
    for name, o in found.items():
        containers[name], consts[name], _ = convert(d, o)
    exe = build_translator(work)
    raw = translate(exe, work, containers)
    texts = {name: readable(raw[name], consts[name]) for name in names}
    macros = bodies(texts)
    assert sorted(macros) == sorted(bodies_names())
    return macros


# ---------------------------------------------------------------------------------------------------------------------
# Proof helper: the shader strings a materials.m holds, with the generated macros expanded


def c_strings(path, incs):
    """{array name: its bytes} for every `static const char NAME[] = ...;` in a C/ObjC file, X360* macros expanded
    from the given generated includes (string-literal macros only; nested macros expanded too)."""
    macros = {}
    for inc in incs:
        t = Path(inc).read_text()
        for m in re.finditer(r"^#define (\w+) ((?:.*\\\n)*.*)$", t, re.M):
            macros[m.group(1)] = m.group(2).replace("\\\n", " ")
    tok = re.compile(r'\s*("(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/|\b[A-Z]\w*\b|;)', re.S)

    def expand(src, pos, stop):
        out, i = [], pos
        while True:
            t = tok.match(src, i)
            if not t:
                if stop:
                    raise ValueError("unterminated string at %d" % i)
                return "".join(out), i
            i, s = t.end(), t.group(1)
            if s == ";":
                return "".join(out), i
            if s.startswith(("//", "/*")):
                continue
            if s.startswith('"'):
                out.append(bytes(s[1:-1], "utf-8").decode("unicode_escape"))
            elif s in macros:
                out.append(expand(macros[s], 0, False)[0])
            else:
                raise ValueError("not a string macro: %s" % s)
    src = Path(path).read_text()
    result = {}
    for m in re.finditer(r"^static const char (\w+)\[\] =", src, re.M):
        result[m.group(1)] = expand(src, m.end(), True)[0]
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zzdata0", type=Path)
    ap.add_argument("--stub", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--work", type=Path, help="directory for the translator build and its files (default: a temporary one)")
    ap.add_argument("--compare", nargs=2, metavar=("OLD.m", "NEW.m"),
                    help="developer proof: the static char arrays of two sources, X360* macros expanded from --inc, "
                         "must be byte-identical; prints the arrays that differ")
    ap.add_argument("--inc", action="append", default=[], help="(with --compare) a generated include for the macros")
    a = ap.parse_args()
    if a.compare:
        old, new = (c_strings(p, a.inc) for p in a.compare)
        names = sorted(set(old) | set(new))
        diff = [n for n in names if old.get(n) != new.get(n)]
        for n in diff:
            print("DIFFERS %s (%s vs %s bytes)" % (n, len(old.get(n, "")), len(new.get(n, ""))))
        print("compare: %d arrays, %d differ" % (len(names), len(diff)))
        sys.exit(1 if diff else 0)
    if a.stub:
        t = include_text(None, "files with no X360 NFS/ZZDATA0.BIN: empty bodies, the X360 shader features stay PC")
    else:
        work = a.work or Path(tempfile.mkdtemp(prefix="x360_shader_body-"))
        work.mkdir(parents=True, exist_ok=True)
        try:
            t = include_text(generate(a.zzdata0, work), "X360 NFS/ZZDATA0.BIN")
        finally:
            if not a.work:
                shutil.rmtree(work, ignore_errors=True)
    a.out.write_text(t)
    print("x360_shader_body: wrote %s (%s)" % (a.out, "stub" if a.stub else "%d bodies" % len(RECIPES)))


if __name__ == "__main__":
    main()
