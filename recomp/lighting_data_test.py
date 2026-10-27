"""Offline R23/R24 proof; no game launch.

Checks the TSV/table byte identity and rejects a one-byte corruption. Compiles
the actual attrib hooks with synthetic guest memory and original-function stubs,
and executes the translator's real 00505B4C instruction output in both modes.
"""
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lighting_data as data
import recomp


def main():
    if not data.TSV.exists():
        print(f"SKIP: no {data.TSV.relative_to(data.ROOT)} (tools/gen_tables.py writes it when an X360 root is present)")
        sys.exit(3)
    rows = data.source_rows()
    data.check(data.TABLE, rows)
    rc = recomp.Recompiler()
    instruction = next(rc.md.disasm(rc.pe.read(0x505B4C, 2), 0x505B4C))
    original = rc.insn(instruction, 0, 0)
    rc.calls = True
    patched = rc.insn(instruction, 0, 0)
    assert patched != original and "attrib_x360_light_data" in patched[0]
    adjacent = next(rc.md.disasm(bytes(instruction.bytes), 0x505B4E))
    assert rc.insn(adjacent, 0, 0) == original, "override leaked to another address"
    records = {}
    for cls, key, off, raw in rows:
        image = records.setdefault((cls, key), bytearray([0xA5] * 0x50))
        image[off:off + len(raw)] = raw
    fixtures = "\n".join(
        "{0x%08Xu, 0x%08Xu, {%s}}," % (cls, key, ",".join(str(x) for x in image))
        for (cls, key), image in records.items())
    source = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdlib.h>
#include "runtime/attrib_x360.c"
uint8_t memory[0xA00000];
uint8_t *G = memory;
int diag_on = 1;
static char last_diag[1024];
static unsigned diag_count, original_count, checked_bytes;
static uint8_t expected[0x50];
static const struct { uint32_t cls, key; uint8_t image[0x50]; } fixtures[] = {
FIXTURES
};
void settings_add(Setting *s) { s->value = s->boot_value = s->def; }
void diag_event(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    vsnprintf(last_diag, sizeof last_diag, fmt, ap); va_end(ap);
    assert(!strncmp(last_diag, "lighting_data:", 14)); ++diag_count;
}
uint32_t gmalloc(uint32_t size) { (void)size; abort(); }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *args) { abort(); }
void x360audio_attrib_patch(uint32_t key, uint32_t cls, uint32_t lay, uint32_t ld) { }
void f_00457F90_orig(Cpu *c) {
    assert(!memcmp(G + 0x300, expected, sizeof expected));
    checked_bytes += sizeof expected; ++original_count;
}
static void collection(Cpu *c, uint32_t cls, uint32_t key, uint32_t lay, const uint8_t *image) {
    memset(G + 0x300, 0xA5, sizeof expected);
    memset(expected, 0xA5, sizeof expected);
    if (attrib_x360_light_data() && lay && image) memcpy(expected, image, sizeof expected);
    c->esp = 0x100; M32(c->esp + 4) = 0x200;
    M32(0x200) = key; M32(0x204) = cls; M32(0x21C) = lay;
    Cpu saved = *c;
    f_00457F90(c);
    assert(!memcmp(&saved, c, sizeof saved));
}
void f_00457670_orig(Cpu *c) {
    for (size_t i = 0; i < sizeof fixtures / sizeof fixtures[0]; ++i)
        collection(c, fixtures[i].cls, fixtures[i].key, 0x300, fixtures[i].image);
    collection(c, 0x399ED882, 0xDEADBEEF, 0x300, NULL); // unmatched key
    collection(c, 0xDEADBEEF, fixtures[0].key, 0x300, NULL); // unmatched class
    collection(c, fixtures[0].cls, fixtures[0].key, 0, fixtures[0].image); // no layout
}
static void alpha_insn(Cpu *c) { PATCHED }
static void alpha_original(Cpu *c) { ORIGINAL }
int main(void) {
    assert(s_light_data.def == 1 && s_light_data.boot_value == 1 && s_light_data.restart == 1);   // X360 default since 2026-10-26
    assert(s_light_data.category == SET_WORLD && s_light_data.nchoices == 2);
    assert(!strcmp(s_light_data.env, "NFS_LIGHT_DATA"));
    assert(!strcmp(s_light_data.values[0], "pc") && !strcmp(s_light_data.values[1], "x360"));
    for (int option = 0; option < 2; ++option) {
        s_light_data.boot_value = option;
        s_light_data.value = !option; // F10 pending changes must affect neither data nor alpha
        for (int gameplay = 0; gameplay < 2; ++gameplay) {
            s_x360_data.boot_value = gameplay;
            for (int vt = 0; vt < 2; ++vt) {
                M32(0x901828) = vt;
                Cpu c = {0}; unsigned before = diag_count;
                f_00457670(&c);
                assert(diag_count == before + 1); // one diagnostic per vault load, including PC
                assert(strstr(last_diag, option ? "option=x360" : "option=pc"));
                assert(strstr(last_diag, option ? "records 399ED882=4 C7C5806D=15 fields=11/32" :
                                                "records 399ED882=0 C7C5806D=0 fields=0/0"));
                assert(strstr(last_diag, vt ? "active_quartet=3DCFA673/B902CFAD/59C373C3/D835D0C8" :
                                             "active_quartet=A01A3942/0E1C797C/94ABDFF0/919B6689"));
            }
        }
        const double inputs[] = {0, 1, 50, 102, 127.5, 128, 204, 255};
        for (unsigned top = 0; top < 8; ++top) for (unsigned v = 0; v < 8; ++v) {
            Cpu c = {0}; c.top = top; c.sw = 0x1234; c.cw = 0x37F;
            for (unsigned j = 0; j < 8; ++j) c.st[j] = 900 + j;
            c.st[top] = inputs[v];
            Cpu expected_cpu = c;
            if (!option) alpha_original(&expected_cpu);
            alpha_insn(&c);
            assert(!memcmp(&c, &expected_cpu, sizeof c));
            assert(fmin(255, c.st[top]) == fmin(255, inputs[v] * (option ? 1 : 2)));
        }
    }
    assert(diag_count == 8 && original_count == 176);
    printf("PASS: 8 synthetic vault loads; 176 pre-original collection checks / %u layout bytes; "
           "PC/X360 x gameplay off/on x both quartets; restart binding; 128 x87 cases\n", checked_bytes);
}
'''.replace("FIXTURES", fixtures).replace("PATCHED", " ".join(patched)).replace("ORIGINAL", " ".join(original))
    with tempfile.TemporaryDirectory(prefix="lightdata-test-") as scratch:
        scratch = Path(scratch)
        corrupt = scratch / "corrupt.inc"
        # Change just the first payload byte (not a key/offset/header).
        text = data.TABLE.read_text()
        first = text.index("{0x", text.index("k_light_patch[]"))
        payload = text.index("{0x", first + 1) + 1
        old = int(text[payload:payload + 4], 16)
        corrupt.write_text(text[:payload] + f"0x{old ^ 1:02X}" + text[payload + 4:])
        try:
            data.check(corrupt, rows)
        except AssertionError as e:
            print(f"PASS: deliberately altered one payload byte rejected: {e}", flush=True)
        else:
            raise AssertionError("corrupted table was accepted")
        harness = scratch / "test.c"
        harness.write_text(source)
        binary = scratch / "test"
        subprocess.run(["clang", "-O1", "-ffunction-sections", "-fdata-sections",
                        "-I", str(data.ROOT), "-I", str(HERE), "-I", str(HERE / "out"),
                        str(harness), "-Wl,-dead_strip", "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
    print("PASS: all 43 table fields / 472 bytes match the TSV X360 side")


if __name__ == "__main__":
    main()
