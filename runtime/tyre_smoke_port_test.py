#!/usr/bin/env python3
"""Port tyre smoke without a console copy (runtime/attrib_x360.c k_port_smoke); no game launch.

Builds the production constructor hook f_00457F90 with the no-console stub of attrib_x360.inc (tools/gen_tables.py
STUBS) and feeds it the PC's own asphalt emitter-group arrays read from the PC ATTRIBUTES.BIN. The number checked is
the emitters per asphalt group the game then builds from (slide, skid, skid-no-leaves, drift-leaves, drift-no-leaves).
Each added emitter must be a PC emitterdata collection that no PC collection references (found here from the vault,
independently of the recipe), the PC's own elements must stay, other groups and a second call must not change.
Proof: recipe on -> 3/5/4/2/2 emitters; the switch off (today's PC-only build) -> 1/3/2/1/1; the recipe table emptied
(control, below) -> 1/3/2/1/1, rejected.
Run: tools/.venv/bin/python -I runtime/tyre_smoke_port_test.py (exit 3 without the PC game files).
"""
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools_snapshot/vaults"))
sys.path.insert(0, str(ROOT / "tools"))
GAME = Path(os.environ.get("NFS_GAME") or ROOT.parent / "Need For Speed Most Wanted Black Edition")
GROUPS = ["fxtd_sl_asphalt", "fxtd_sk_asphalt", "fxtd_sk_asphalt_no_leaves", "fxtd_dr_asphalt_leaves",
          "fxtd_dr_asphalt_noleaves"]
OTHER = "fxtd_dr_cobble"                     # a non-asphalt group: must stay as the PC has it
WANT = [3, 5, 4, 2, 2]                       # emitters per group with the recipe
PC_COUNTS = [1, 3, 2, 1, 1]                  # as the PC ships them


def pc_arrays():
    import compare_vaults as cv
    from vaultlib import hash32
    gl = GAME / "GLOBAL"
    pc = cv.Side(str(gl), "PC", "<")
    pc.parse(dict(pc.types))
    eg, ed = hash32("emittergroup"), hash32("emitterdata")
    used = set()
    for v in pc.colls.values():
        for f in v["fields"].values():
            for e in f[2]:
                if isinstance(e, tuple) and len(e) > 1 and isinstance(e[1], tuple) and len(e[1]) == 2:
                    used.add(e[1][1])
    unref = {k[3] for k in pc.colls if k[2] == ed} - used
    raw = {}
    for V in pc.vaults:
        if V.file != "ATTRIBUTES.BIN":
            continue
        for k, t, s, do in V.exports:
            if t == cv.T_COLL and V.u32(V.vlt, do + 4) == eg:
                key = V.u32(V.vlt, do)
                for n in GROUPS + [OTHER]:
                    if key == hash32(n):
                        lay = V.deref(0, do + 0x1C)
                        b = V.bufs[lay[0]]
                        cap, cnt, esz, enc = struct.unpack_from("<4H", b, lay[1])
                        raw[n] = bytes(b[lay[1]:lay[1] + 8 + cap * esz])
    assert sorted(raw) == sorted(GROUPS + [OTHER]), sorted(raw)
    return eg, ed, unref, raw


HARNESS = r'''
#include <stdarg.h>
#include <stdlib.h>
#include "attrib_x360.c"
uint8_t memory[0x10000], *G = memory;
int diag_on = 1;
void settings_add(Setting *s) { s->value = s->boot_value = s->def; }
void car85_collection_seen(uint32_t ld, uint32_t vault) { (void)ld; (void)vault; }
void car85_vault_loaded(Cpu *c, uint32_t vault) { (void)c; (void)vault; }
void car85_vault_begin(uint32_t vault) { (void)vault; }
void diag_event(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vfprintf(stderr, fmt, ap); va_end(ap); fputc('\n', stderr); }
uint32_t gmalloc(uint32_t n) { abort(); }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) { abort(); }
void x360audio_attrib_patch(uint32_t key, uint32_t cls, uint32_t lay, uint32_t ld) { }
void f_00457670_orig(Cpu *c) { abort(); }
void f_00457F90_orig(Cpu *c) { }
// argv: switch (0/1), then per group: class key layout-hex; prints the array after two constructor calls
int main(int argc, char **argv) {
    if (strcmp(s_x360_data.id, "gameplay.port_tyre_smoke") || s_x360_data.def != 1 || strcmp(s_x360_data.env, "NFS_X360_DATA")
        || !s_x360_data.restart) { fprintf(stderr, "FAIL: row\n"); return 22; }
    s_x360_data.boot_value = atoi(argv[1]);
    for (int i = 2; i + 2 < argc; i += 3) {
        uint32_t cls = strtoul(argv[i], 0, 16), key = strtoul(argv[i + 1], 0, 16), lay = 0x2000;
        const char *h = argv[i + 2];
        size_t n = strlen(h) / 2;
        memset(G + lay, 0xA5, 0x200);
        for (size_t j = 0; j < n; ++j) { unsigned v; sscanf(h + 2 * j, "%2x", &v); G[lay + j] = (uint8_t)v; }
        for (int call = 0; call < 2; ++call) {
            Cpu c = {0}; c.esp = 0x100; M32(c.esp + 4) = 0x1000;
            M32(0x1000) = key; M32(0x1004) = cls; M32(0x101C) = lay;
            f_00457F90(&c);
        }
        for (size_t j = 0; j < n; ++j) printf("%02x", G[lay + j]);
        printf(" %u\n", G[lay + n] == 0xA5 ? 1u : 0u);   // nothing written past the array
    }
    return 0;
}
'''


def run(tmp, src, switch, eg, raw):
    (tmp / "attrib_x360.c").write_text(src)
    binary = tmp / "t"
    cmd = ["clang", "-O1", "-w", "-ffunction-sections", "-fdata-sections", "-Wl,-dead_strip", "-I", str(tmp), "-I", str(ROOT / "runtime"), "-I", str(ROOT / "recomp"),
           "-I", str(ROOT / "recomp/out"), str(tmp / "harness.c"), "-o", str(binary)]
    built = subprocess.run(cmd, capture_output=True, text=True)
    if built.returncode:
        sys.exit("build failed:\n" + built.stderr[-3000:])
    from vaultlib import hash32
    args = [str(binary), str(switch)]
    for n in GROUPS + [OTHER]:
        args += ["%08X" % eg, "%08X" % hash32(n), raw[n].hex()]
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout.split("\n")
    return {n: (bytes.fromhex(out[i].split()[0]), out[i].split()[1] == "1") for i, n in enumerate(GROUPS + [OTHER])}


def counts(res):
    return [struct.unpack_from("<4H", res[n][0])[1] for n in GROUPS]


def check(res, ed, unref, raw):
    """1 when the recipe is in effect as required."""
    for n in GROUPS + [OTHER]:
        assert res[n][1], n + ": written past the array"
    assert res[OTHER][0] == raw[OTHER], OTHER + " changed"
    if counts(res) != WANT:
        return 0
    for n in GROUPS:
        cap, cnt, esz, enc = struct.unpack_from("<4H", res[n][0])
        pcnt = struct.unpack_from("<4H", raw[n])[1]
        assert res[n][0][8:8 + 12 * pcnt] == raw[n][8:8 + 12 * pcnt], n + ": the PC's own emitters changed"
        assert res[n][0][8 + 12 * cnt:] == raw[n][8 + 12 * cnt:], n + ": tail changed"
        for i in range(pcnt, cnt):
            c, k, z = struct.unpack_from("<3I", res[n][0], 8 + 12 * i)
            assert c == ed and z == 0 and k in unref, (n, hex(k))
    return 1


def main():
    if not (GAME / "GLOBAL").is_dir() or not (ROOT / "runtime/gen").is_dir():
        print("SKIP tyre_smoke_port_test: needs the PC game files (NFS_GAME) and runtime/gen", flush=True)
        sys.exit(3)
    import gen_tables
    eg, ed, unref, raw = pc_arrays()
    assert [struct.unpack_from("<4H", raw[n])[1] for n in GROUPS] == PC_COUNTS
    src = (ROOT / "runtime/attrib_x360.c").read_text()
    table = src[src.index("static const struct { uint32_t group, n, add[2]; } k_port_smoke[] = {"):]
    table = table[:table.index("};") + 2]
    broken = src.replace(table, "static const struct { uint32_t group, n, add[2]; } k_port_smoke[] = {{0, 0, {0}}};")
    assert broken != src
    with tempfile.TemporaryDirectory(prefix="tyre-smoke-") as tmp:
        tmp = Path(tmp)
        (tmp / "gen").mkdir()
        for f in (ROOT / "runtime/gen").glob("*.inc"):
            (tmp / "gen" / f.name).write_bytes(f.read_bytes())
        for name in ("attrib_x360.inc", "attrib_light_data.inc"):
            (tmp / "gen" / name).write_text(gen_tables.STUBS[name])
        (tmp / "harness.c").write_text(HARNESS)
        on = run(tmp, src, 1, eg, raw)
        assert check(on, ed, unref, raw), counts(on)
        off = run(tmp, src, 0, eg, raw)
        assert counts(off) == PC_COUNTS and not check(off, ed, unref, raw)
        ctl = run(tmp, broken, 1, eg, raw)
        print("PASS: recipe %s emitters (slide, skid, skid-no-leaves, drift-leaves, drift-no-leaves); switch off %s; "
              "added emitters all unreferenced PC emitterdata (%d in the vault); cobble group unchanged"
              % (counts(on), counts(off), len(unref)))
        if check(ctl, ed, unref, raw):
            sys.exit("FAIL: the emptied recipe still passed")
        print("PASS: control, recipe table emptied -> %s (rejected)" % counts(ctl))


if __name__ == "__main__":
    main()
