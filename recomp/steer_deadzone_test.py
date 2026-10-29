"""Offline I5 proof; no game launch. Run with the project Python and -I.

Uses the real PC drive_analog collection/arrays and both retail X360 lows,
cross-checks the parity TSV, and compiles the actual constructor hook. Compares
all 65536 guest bytes: only the two low words may change (8 bytes total).
The deliberate +0x18 (high instead of low) mutation must fail with exit 23.
Game feel and GameController.framework preprocessing require a hand test.
"""
import csv
from pathlib import Path
import re
import os
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools_snapshot/vaults"))
# Cross-check tables (vault offsets, field diffs) derived from both game versions: NFS_PARITY_DATA, else a
# <top-level folder>/parity/parity_data in a development checkout (absent from a public checkout: the test skips).
PARITY = Path(os.environ.get("NFS_PARITY_DATA") or next(ROOT.glob("*/parity/parity_data"), ROOT / "parity_data"))
from vaultlib import Vault, read_vpak

KEY, CLS = 0xAD37E45A, 0x2DEE1998
FIELDS = (0x37B1B6A8, 0xB3A37C41)
PC = ROOT.parent / "Need For Speed Most Wanted Black Edition/GLOBAL/attributes.bin"
XB = ROOT.parent / "x360/files/GLOBAL/ATTRIBUTES.BIN"


def collection(path, endian):
    for name, b, v, bo, vo in read_vpak(path):
        vault = Vault(name, b, v, endian, bo, vo)
        for _, typ, _, off in vault.exports:
            if typ == 0x8E112EB7 and vault.u32(v, off) == KEY:
                assert vault.u32(v, off + 4) == CLS
                n, nt = struct.unpack_from(endian + "II", v, off + 0x14)
                arrays = {}
                for i in range(n):
                    entry = off + 0x20 + 4 * nt + 12 * i
                    field = vault.u32(v, entry)
                    assert v[entry + 10] == 2
                    dep, at = vault.deref(0, entry + 4)
                    buf = vault.bufs[dep]
                    cap, count, size, flags = struct.unpack_from(endian + "4H", buf, at)
                    assert size == 32 and flags == 0
                    raw = buf[at:at + 8 + cap * size]
                    arrays[field] = (entry - off, raw, count)
                return vo + off, v[off:off + 0x20 + 4 * nt + 12 * n], arrays
    raise AssertionError("drive_analog missing")


def main():
    if not (PARITY.is_dir() and PC.is_file() and XB.is_file()):
        print("SKIP steer_deadzone_test: needs both game versions and the cross-check tables (NFS_PARITY_DATA)", flush=True)
        return
    pc_off, header, arrays = collection(PC, "<")
    xb_off, _, xb_arrays = collection(XB, ">")
    with (PARITY / "attrib_collections_nonidentical.tsv").open() as f:
        row = next(r for r in csv.DictReader(f, delimiter="\t") if r["collection"] == "drive_analog")
    assert (pc_off, xb_off) == (int(row["pc_vlt_off"], 16), int(row["x360_vlt_off"], 16))
    with (PARITY / "attrib_field_diffs.tsv").open() as f:
        rows = {int(r["field_hash"], 16): r for r in csv.DictReader(f, delimiter="\t")
                if r["class"] == "controller" and r["collection"] == "drive_analog"}
    fixture = bytearray([0xA5] * 0x10000)
    fixture[0x1000:0x1000 + len(header)] = header
    lows, entries, records, xb_lows = [], [], [], []
    for i, (field, (entry, raw, count)) in enumerate(arrays.items()):
        addr = 0x2000 + 0x100 * i
        fixture[addr:addr + len(raw)] = raw
        struct.pack_into("<I", fixture, 0x1000 + entry + 4, addr)
        if field not in FIELDS:
            continue
        assert count == 2 and xb_arrays[field][2] == 1
        # Record-relative mode/low/high offsets independently fixed by the
        # decomp's InputMapping copy (+10/+14/+18), not the runtime macro.
        assert struct.unpack_from("<Iff", raw, 8 + 0x10) == (0, 0, 0)
        xb_low = xb_arrays[field][1][8 + 0x14:8 + 0x18][::-1]
        match = re.search(r"'ALX_[LR]' 0 ([^ ]+) 0 0", rows[field]["x360_value"])
        assert match and xb_low == struct.pack("<f", float(match[1]))   # the data and the cross-check table agree
        xb_lows.append(xb_low)
        lows.append(addr + 8 + 0x14)
        records.append(addr + 8)
        entries.append(0x1000 + entry)
    assert len(lows) == 2
    # The expected low comes from the player's own X360 data (nothing copied into this file): both sticks share one
    # value, a sane dead zone fraction.
    assert len(set(xb_lows)) == 1
    low = struct.unpack("<f", xb_lows[0])[0]
    assert 0 < low < 0.5
    low_word = struct.unpack("<I", xb_lows[0])[0]
    initial = ",".join(str(x) for x in fixture)
    source = r'''
#include <stdarg.h>
#include <stdlib.h>
#include "attrib_x360.c"
uint8_t memory[0x10000], *G = memory;
int diag_on = 1;
static const uint8_t fixture[] = {FIXTURE};
static uint8_t expected[sizeof memory];
static const uint32_t lows[] = {LOWS}, entries[] = {ENTRIES}, records[] = {RECORDS};
static unsigned checked, diag_count, changed;
static char last_diag[512];
void settings_add(Setting *s) { s->value = s->boot_value = s->def; }
void car85_collection_seen(uint32_t ld, uint32_t vault) { (void)ld; (void)vault; }
void car85_vault_loaded(Cpu *c, uint32_t vault) { (void)c; (void)vault; }
void car85_vault_begin(uint32_t vault) { (void)vault; }
void diag_event(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt);
    vsnprintf(last_diag, sizeof last_diag, fmt, ap); va_end(ap); ++diag_count;
}
uint32_t gmalloc(uint32_t n) { abort(); }
uint32_t call_guest(Cpu *c, uint32_t fn, int n, const uint32_t *a) { abort(); }
void x360audio_attrib_patch(uint32_t key, uint32_t cls, uint32_t lay, uint32_t ld) { }
void f_00457670_orig(Cpu *c) { abort(); }
void f_00457F90_orig(Cpu *c) {
    for (unsigned i = 0; i < sizeof memory; ++i) if (G[i] != expected[i]) {
        fprintf(stderr, "FAIL: guest byte +%04X actual=%02X expected=%02X\n", i, G[i], expected[i]);
        exit(23);
    }
    ++checked;
}
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL: %s\n", #x); exit(24); } } while (0)
int main(int argc, char **argv) {
    int option = atoi(argv[1]);
    CHECK(s_steer_deadzone.def == 0 && s_steer_deadzone.boot_value == 0);
    CHECK(s_steer_deadzone.restart == 1 && s_steer_deadzone.category == SET_CONTROLS);
    CHECK(s_steer_deadzone.nchoices == 2 && !strcmp(s_steer_deadzone.env, "NFS_STEER_DEADZONE"));
    CHECK(!strcmp(s_steer_deadzone.values[0], "pc") && !strcmp(s_steer_deadzone.values[1], "x360"));
    for (int gameplay = 0; gameplay < 2; ++gameplay) for (int test = 0; test < 8; ++test) {
        memcpy(G, fixture, sizeof memory);
        Cpu c = {0}; c.esp = 0x100; c.ecx = 0x900;
        M32(c.esp + 4) = 0x1000;
        unsigned mask = 3;
        if (test == 1) { M32(0x1000) ^= 1; mask = 0; } // wrong collection
        if (test == 2) { M32(0x1004) ^= 1; mask = 0; } // wrong class
        if (test == 3) { M32(entries[0] + 4) = 0; mask = 2; }
        if (test == 4) { M8(entries[0] + 10) |= 0x20; mask = 2; } // inline value
        if (test == 5) { M32(records[0] + 0x10) = 1; mask = 2; } // edge action
        if (test == 6) { M32(records[0] + 8) ^= 1; mask = 2; } // different source
        if (test == 7) { M16(records[0] - 8 + 4) = 16; mask = 2; } // different stride
        memcpy(expected, G, sizeof memory);
        if (option) for (int j = 0; j < 2; ++j) if (mask & (1u << j)) {
            uint32_t low = LOWWORD;
            memcpy(expected + lows[j], &low, 4);
        }
        s_steer_deadzone.boot_value = option;
        s_steer_deadzone.value = !option; // pending F10 change must wait for restart
        s_x360_data.boot_value = gameplay;
        Cpu saved = c;
        f_00457F90(&c);
        CHECK(!memcmp(&saved, &c, sizeof c));
        if (test == 0 && gameplay == 0) {
            for (unsigned i = 0; i < sizeof memory; ++i) changed += G[i] != fixture[i];
            // Exclude the four stack pointer bytes introduced by the harness.
            unsigned stack_bytes = 0;
            for (unsigned i = c.esp + 4; i < c.esp + 8; ++i) stack_bytes += G[i] != fixture[i];
            CHECK(changed - stack_bytes == (option ? 8 : 0));
            CHECK(strstr(last_diag, option ? "option=x360" : "option=pc"));
            CHECK(strstr(last_diag, option ? "matched=2 patched=2 low=LOWTEXT bits=LOWBITS" :
                                           "matched=2 patched=0 low=0.00 bits=00000000"));
        }
    }
    CHECK(diag_count == 1 && checked == 16);
    printf("PASS: option=%s, 16 constructor calls / 1048576 compared bytes, %u low bytes changed, one diagnostic\n",
           option ? "x360" : "pc", option ? 8 : 0);
}
'''.replace("FIXTURE", initial).replace("LOWWORD", "0x%08Xu" % low_word).replace("LOWTEXT", "%.2f" % low).replace("LOWBITS", "%08X" % low_word).replace("LOWS", ",".join(map(str, lows))).replace(
        "ENTRIES", ",".join(map(str, entries))).replace("RECORDS", ",".join(map(str, records)))
    with tempfile.TemporaryDirectory(prefix="i5-deadzone-") as tmp:
        tmp = Path(tmp)
        harness, binary = tmp / "test.c", tmp / "test"
        harness.write_text(source)
        command = ["clang", "-O1", "-ffunction-sections", "-fdata-sections", "-I", str(tmp),
                   "-I", str(ROOT / "runtime"), "-I", str(ROOT / "recomp"),
                   "-I", str(ROOT / "recomp/out"), str(harness), "-Wl,-dead_strip", "-o", str(binary)]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode: sys.exit(built.stderr[-3000:])
        for option in (0, 1):
            subprocess.run([str(binary), str(option)], check=True)
        original = (ROOT / "runtime/attrib_x360.c").read_text()
        assert original.count("#define STEER_RAMP_LOW 0x14u") == 1
        (tmp / "attrib_x360.c").write_text(original.replace(
            "#define STEER_RAMP_LOW 0x14u", "#define STEER_RAMP_LOW 0x18u"))
        subprocess.run(command, check=True, capture_output=True)
        broken = subprocess.run([str(binary), "1"], capture_output=True, text=True)
        assert broken.returncode == 23, broken.stderr
        print("PASS: deliberately wrong +0x18 offset rejected (exit 23): " + broken.stderr.strip())
    ramp = [max(0, min(1, (u - low) / (1 - low))) for u in (0, low, (1 + low) / 2, 1)]
    assert all(abs(a - b) < 1e-6 for a, b in zip(ramp, (0, 0, .5, 1)))
    print(f"PASS: PC collection @{pc_off:#x}, X360 @{xb_off:#x}; TSV and X360 lows agree (value read from the data); ramp 0/0/.5/1")


if __name__ == "__main__":
    main()
