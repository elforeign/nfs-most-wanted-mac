#!/usr/bin/env python3
"""Controller metadata compatibility, with independent legacy semantic decoders.

Good: 40/40 binding actions and 85/85 callout IDs match the former output.
Wrong Type-2 gas mapping (A -> B): 1/40 binding mismatch, exit 1.
Authored text returns: 17/17 preserve the guest stack; extra return pop exits 6.
PC-only Setup without GLOBALB.BUN produces the same 3/3 controller includes.
The ordinary run also executes that negative control. No baseline game values
are stored here; private files are read only by this check, never by Setup's
controller generator. Without assets the synthetic/bridge proof runs, then exit 3.
"""
from pathlib import Path
import argparse
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(HERE / "vaults"))
import gen_tables as driver
import pad_metadata as authored
import pubtables_gen as pg

# GameAction ABI: the legacy evaluator, Controls and Drag share these indices.
ACTION_NAMES = {0: "GAMEACTION_GAS", 1: "GAMEACTION_BRAKE", 4: "GAMEACTION_HANDBRAKE",
                6: "GAMEACTION_NOS", 5: "GAMEACTION_GAMEBREAKER", 8: "GAMEACTION_SHIFTUP",
                7: "GAMEACTION_SHIFTDOWN", 15: "CAMERAACTION_CHANGE", 16: "CAMERAACTION_LOOKBACK",
                9: "GAMEACTION_RESET"}


def legacy_buttons(pc, x360):
    """Decode the old generator's binding semantics; never emit or store a table."""
    import compare_vaults as cv
    from vaultlib import hash32
    xb = cv.Side(x360 / "GLOBAL", "X360", ">")
    xb.parse(dict(xb.types))
    collections = {key: c for (file, _, _, key), c in xb.colls.items() if file == "ATTRIBUTES.BIN"}
    exe = (pc / "speed.exe").read_bytes()
    pe = struct.unpack_from("<I", exe, 0x3C)[0]
    ns, opt = struct.unpack_from("<H", exe, pe + 6)[0], struct.unpack_from("<H", exe, pe + 20)[0]
    base = struct.unpack_from("<I", exe, pe + 52)[0]

    def key_at(va):
        for i in range(ns):
            _, size, rva, raw_size, raw = struct.unpack_from("<8sIIII", exe, pe + 24 + opt + 40 * i)
            if rva <= va - base < rva + max(size, raw_size):
                return struct.unpack_from("<I", exe, raw + va - base - rva)[0]
        raise AssertionError("selector outside the executable")

    result = {}
    for variant in range(1, 5):
        for action, name in ACTION_NAMES.items():
            key = key_at(0x0089F6AC + 4 * (1 + 2 * variant))
            while key in collections:
                c = collections[key]
                field = c["fields"].get(hash32(name))
                if field:
                    words = field[2][0][1]
                    raw = b"".join(value if kind == "r" else bytes(4) for kind, value in words)
                    source = words[3][1]
                    low = round(struct.unpack_from(">f", raw, 20)[0], 3)
                    if source not in {"ARY_U", "ARY_D", "L", "R"}:
                        low = 0
                    result[variant + 1, action] = source, low
                    break
                key = c["parent"]
            else:
                raise AssertionError("missing legacy action")
    return result


def legacy_callouts(xex, work):
    exe = work / "xex_read"
    subprocess.run(["clang", "-O2", "-w", "-o", str(exe), str(HERE / "xex_read.c")], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    data = subprocess.check_output([str(exe), str(xex), "0x828FFBB0", str(17 * 5 * 4),
                                    "0x%08X" % pg.XEX_TITLE, pg.XEX_BUILDS["USA"]], stderr=subprocess.PIPE)
    assert len(data) == 17 * 5 * 4
    return list(struct.unpack(">85I", data))


def legacy_texture(pc):
    data = driver.find(pc / "GLOBAL", "GLOBALB.BUN").read_bytes()
    start = 0
    while True:
        name = data.find(b"L1\0", start)
        assert name >= 0, "missing legacy glyph record"
        start = name + 1
        rec = name - 12
        if rec >= 0 and not any(data[rec:rec + 12]) and struct.unpack_from("<I", data, rec + 0x28)[0] == 0x001A93CF:
            return data[rec:rec + 124]


BRIDGE = r'''
typedef struct { uint32_t eax, edx, esp; } Cpu;
static char strings[4096];
static uint32_t used = 1;
static uint32_t gstr(const char *s) {
    uint32_t p = used; used += (uint32_t)strlen(s) + 1;
    assert(used <= sizeof strings); strcpy(strings + p, s); return p;
}
uint32_t underglow_menu_text(uint32_t h) { (void)h; return 0; }
static int diag_on, original_calls;
static void diag_event(const char *fmt, ...) { (void)fmt; }
static int x360_post_chain_on(void) { return 0; }
void f_0056BB80_orig(Cpu *c) {
    ++original_calls; c->eax = 0x123456;
    for (size_t i = 0; i < sizeof pad_action_labels / sizeof *pad_action_labels; ++i)
        if (pad_action_labels[i].id == c->edx) c->eax = 0;
    c->esp += 4;
}
'''
MAIN = r'''
int main(void) {
    for (int type = 2; type <= 5; ++type)
        for (int i = 0; i < CT_NBUTTONS; ++i) {
            const CtMap *m = &ct_buttons[type - 2][i];
            printf("B %d %d %s %.3f\n", type, m->action, m->src, m->low);
            assert(ct_source(m->src));
            float low;
            assert(!strcmp(ct_map(type, m->action, &low), m->src) && low == m->low);
            assert(!strcmp(ct_map(type + 5, m->action, &low), m->src) && low == m->low);
        }
    assert(ct_table_index(1) == 1 && ct_table_index(10) == 8);
    for (unsigned i = 0; i < sizeof l1; ++i) printf("T %u\n", l1[i]);
    for (unsigned i = 0; i < 17 * 5; ++i) printf("L %u\n", callout_labels[i]);
    for (unsigned i = 0; i < sizeof pad_action_labels / sizeof *pad_action_labels; ++i) {
        Cpu c = {.edx = pad_action_labels[i].id, .esp = 0x1000};
        fe_console_search(&c);
        if (c.esp != 0x1004) return 6;
        assert(c.edx == pad_action_labels[i].id);
        assert(c.eax && !strcmp(strings + c.eax, pad_action_labels[i].text));
        uint32_t first = c.eax, before = used;
        c.esp = 0x1000;
        fe_console_search(&c); assert(c.eax == first && used == before && c.esp == 0x1004);
    }
    assert(original_calls == 2 * (int)(sizeof pad_action_labels / sizeof *pad_action_labels));
    original_calls = 0;
    Cpu c = {.edx = 0x1234, .esp = 0x1000}; fe_console_search(&c);
    assert(original_calls == 1 && c.eax == 0x123456 && c.edx == 0x1234 && c.esp == 0x1004);
    return 0;
}
'''


def generated(work, wrong=False, wrong_return=False):
    out = work / ("wrong_return" if wrong_return else "wrong" if wrong else "good")
    # This exact CLI has no player paths; it must succeed in an empty directory.
    subprocess.run([sys.executable, "-I", str(HERE / "controltype_gen.py"), "--out", str(out)],
                   cwd=work, check=True)
    if wrong:
        path = out / "controltype_buttons.inc"
        text = path.read_text()
        assert text.count('{CT_GAS, "A", 0}') == 1
        path.write_text(text.replace('{CT_GAS, "A", 0}', '{CT_GAS, "B", 0}'))
    control = (ROOT / "runtime/controltype.c").read_text().replace(
        '"gen/controltype_buttons.inc"', '"%s"' % (out / "controltype_buttons.inc"))
    prompts = (ROOT / "runtime/padprompts.c").read_text()
    bridge = prompts[prompts.index('uint32_t prompts_action_text('):prompts.index('// The PlayStation layout\'s labels')]
    console = (ROOT / "runtime/fe_console.c").read_text()
    console = console[console.index('static struct { uint32_t from, to;'):]
    if wrong_return:
        needle = "if (!c->eax) c->eax = prompts_action_text(hash);"
        assert console.count(needle) == 1
        console = console.replace(needle, needle + " c->esp += 4;")
    source = '#include <assert.h>\n#define CONTROLTYPE_TEST\n' + control
    source += '\n#include "%s"\n#include "%s"\n' % (out / "padprompts_l1.inc", out / "callout_labels.inc")
    source += BRIDGE + bridge + console + MAIN
    path, exe = out / "check.c", out / "check"
    path.write_text(source)
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", str(path), "-o", str(exe)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    lines = subprocess.check_output([str(exe)], text=True).splitlines()
    bindings, labels, texture = {}, [], bytearray()
    for line in lines:
        fields = line.split()
        if fields[0] == "B":
            _, t, a, src, low = fields
            bindings[int(t), int(a)] = src, float(low)
        elif fields[0] == "L":
            labels.append(int(fields[1]))
        else:
            texture.append(int(fields[1]))
    assert len(bindings) == 40 and len(labels) == 85 and len(texture) == 124
    return bindings, labels, texture


def mismatches(actual, expected):
    return sum(actual.get(key) != value for key, value in expected.items())


def pc_only_setup(pc, work):
    """Real Setup driver, with neither a glyph bundle nor any X360 inputs."""
    source = work / "pc-without-glyph-bundle"
    (source / "GLOBAL").mkdir(parents=True)
    (source / "speed.exe").symlink_to(pc / "speed.exe")
    for name in driver.VAULTS:
        path = driver.find(pc / "GLOBAL", name)
        if path.exists():
            (source / "GLOBAL" / name).symlink_to(path)
    out = work / "pc-only-setup"
    subprocess.run([sys.executable, "-I", str(ROOT / "tools/gen_tables.py"), "--pc", str(source),
                    "--no-x360", "--out", str(out)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert not (source / "GLOBAL/GLOBALB.BUN").exists()
    for name in driver.PADTABLES:
        assert (out / name).read_bytes() == (work / "good" / name).read_bytes(), name
    print("PC-only Setup without glyph bundle/X360 inputs: 3/3 authored includes unchanged")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wrong-mapping", action="store_true")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="pad-metadata-") as scratch:
        work = Path(scratch)
        actual, labels, texture = generated(work, args.wrong_mapping)
        try:
            generated(work, wrong_return=True)
        except subprocess.CalledProcessError as broken:
            assert broken.returncode == 6, "extra guest return pop must fail with exit 6"
        else:
            raise AssertionError("extra guest return pop was accepted")
        print("negative text-return control: exit 6 (rejected)")
        # Synthetic bridge/generator checks run even in a public asset-free tree.
        assert bytes(texture) == authored.texture_record()
        assert struct.unpack_from("<HH", texture, 0x44) == (64, 64)
        assert texture[0x4A] == 0x20 and texture[0x4E] == 1
        assert struct.unpack_from("<h", texture, 0x58)[0] == 0  # no mip chain to select
        assert labels == authored.callout_ids()
        assert "prompts_action_text(hash)" in (ROOT / "runtime/fe_console.c").read_text()
        pub = (HERE / "pubtables_gen.py").read_text()
        assert all(token not in pub for token in ("padprompts_l1", "callout_labels", "828FFBB0", 'b"L1'))
        pc, x360 = driver.resolve(argparse.Namespace(pc=None, x360=None, no_x360=False))
        assert driver.find(pc / "GLOBAL", "GLOBALB.BUN") not in driver.inputs(pc, x360)
        assert set(driver.PADTABLES).isdisjoint(driver.STUBS)
        assert "tools_snapshot/pad_metadata.py" in driver.GENERATORS
        stamp = driver.outputs_text(work / "good" if not args.wrong_mapping else work / "wrong")
        assert all("out " + name + "\t" in stamp for name in driver.PADTABLES)
        if not x360 or not (pc / "speed.exe").exists() or not driver.xex_path(x360).exists():
            print("PASS: generated records and 17 action-text bridge checks; SKIP: private compatibility inputs absent")
            return 3
        expected = legacy_buttons(pc, x360)
        failures = mismatches(actual, expected)
        old_labels = legacy_callouts(driver.xex_path(x360), work)
        label_failures = [i for i, (a, b) in enumerate(zip(labels, old_labels)) if a != b]
        # Compare only renderer semantics. Names/hash, dimensions/format, sizes
        # and pointers are supplied by the caller/extra_texture_add, not a template.
        old_texture = legacy_texture(pc)
        semantic_fields = (("class", 0x28, "I"), ("render_order", 0x51, "B"),
                           ("alpha_sort", 0x54, "B"), ("alpha_usage", 0x55, "B"),
                           ("alpha_blend", 0x56, "B"), ("u_scale", 0x62, "H"), ("v_scale", 0x64, "H"))
        texture_failures = [name for name, off, fmt in semantic_fields
                            if struct.unpack_from("<" + fmt, texture, off) != struct.unpack_from("<" + fmt, old_texture, off)]
        print("%s: bindings %d/40; callout IDs %d/85; texture fields %d/%d; action-text bridge 17/17" %
              ("FAIL" if failures or label_failures or texture_failures else "PASS", 40 - failures,
               85 - len(label_failures), len(semantic_fields) - len(texture_failures), len(semantic_fields)))
        if label_failures:
            print("callout mismatch slots/configs:", [(i // 5 + 1, i % 5 + 1) for i in label_failures])
        if texture_failures:
            print("texture mismatch fields:", ", ".join(texture_failures))
        if not args.wrong_mapping:
            pc_only_setup(pc, work)
            bad, _, _ = generated(work, True)
            count = mismatches(bad, expected)
            assert count == 1, "wrong mapping must produce exactly one mismatch"
            print("negative control: %d/40 binding mismatch (rejected)" % count)
        return int(bool(failures or label_failures or texture_failures))


if __name__ == "__main__":
    sys.exit(main())
