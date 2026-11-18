#!/usr/bin/env python3
"""Carbon MODIFY/history regression, using synthetic records and literal streams.

Good-chain proof: legacy MW Update reader, same chain with MW container label:
--baseline CarbonPatchTests.test_good_chain -> 0/1 (no whole-file records).
Carbon reader: CarbonPatchTests.test_good_chain -> 1/1, expected bytes and
unchanged source tree. The legacy reader is still production rtpatch.Update;
no git history, private bytes or copied implementation are used for the control.

Private check: CARBON_PATCH_EXE + CARBON_PATCH_SOURCE select an official update
and an unpatched extracted disc tree. --private runs only that check, exiting 3
when absent. Every data record's final size and both checksums are compared;
all source files are hashed before/after. NFSC.exe is an edition witness only,
and nfsc_link.exe is a MODIFY/history executable, not an absent-file creation.
For the CE update there are 162 records, two executable-record skips, 160 data
outputs, and four additional Windows installer executables excluded from copies
(the investigative 161-present-files count included the main executable).
"""
import collections
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from setup_kit import carbon_patch
from setup_kit import rtpatch

BASELINE = "--baseline" in sys.argv
PRIVATE = "--private" in sys.argv
for flag in ("--baseline", "--private"):
    if flag in sys.argv:
        sys.argv.remove(flag)


def vli(value):
    sign = 0x80 if value < 0 else 0
    value = abs(value)
    if value < 64:
        return bytes([sign | value])
    for count in range(1, 7):
        if value < 1 << (8 * count + 6 - count):
            lead = sum(0x40 >> i for i in range(count))
            return bytes([sign | lead | (value >> (8 * count))]) + (value & ((1 << (8 * count)) - 1)).to_bytes(count, "little")
    raise ValueError("fixture integer too large")


def lp(value):
    data = value.encode("latin-1") + b"\0"
    return (bytes([len(data)]) if len(data) < 255 else b"\xff" + struct.pack("<H", len(data))) + data


def sums(data):
    return bytes(2) + struct.pack("<II", rtpatch.rolling(data, 31), rtpatch.rolling(data, 30))


def entry(data, name):
    return bytes(16) + struct.pack("<II", len(data), 0) + sums(data) + bytes(8) + lp(name.split("\\")[-1])


def stream(data):
    # Raw literals: independent of the Huffman coder's implementation/model.
    bits = format(rtpatch.DIFF_MAGIC, "016b") + "0000000100000000" + "0000000100000000000001001000"
    bits += "".join("0" + format(byte, "08b") for byte in data)
    bits += "0" * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, "big")


def literal(data):
    return b"\x02\x00\x05" + data + b"\x01"


def record(name, versions, codes=None):
    codes = codes if codes is not None else [literal(data) for data in versions[1:]]
    history = len(versions) > 2
    executable = name.lower().endswith(".exe")
    header = struct.pack("<HH", 0x5006 if history else 0x4446, 0x38 if executable else 0x1B8) + lp(name)
    if not executable:
        header += vli(0) + vli(1)
    diffs = bytearray()
    for index, code in enumerate(codes):
        packed = stream(code)
        if history:
            diffs += struct.pack("<H", 0x2C4)
        diffs += struct.pack("<H", 9) + (vli(1) if index == 0 else b"") + vli(1)
        diffs += struct.pack("<II", len(code), len(packed))
        diffs += entry(versions[index], name) if index == 0 else sums(versions[index])
        diffs += entry(versions[index + 1], name) + packed
    if history:
        header += struct.pack("<IH", len(diffs), len(codes))
    return header + bytes(10) + diffs


def update_bytes(records, baseline=False):
    label = b"Need for Speed Most Wanted" if baseline else b"Need for Speed Carbon"
    header = b"K*" + struct.pack("<HHIH", 0x2BC, 0x83E4, 0x10000, 0x38) + bytes(18)
    return header + struct.pack("<I", len(records)) + label + b"\0" + b"".join(records) + b"\x00\x10\x00\xa6\x03\x00DKNJ"


DATA = (b"01234567", b"01xy4567", b"!1xy45Z7??")
EXE = (b"MZdiscCE", b"MZpatch1", b"MZpatch2")
LINK = (b"MZlink00", b"MZlink01", b"MZlink02")
MODIFY = (b"abcdefgh", b"ab0000gh")
CHAIN_CODES = (b"\x02\x00\x03\x00\x02\x04\x02\x04\x04\x05xy\x01",
               b"\x02\x00\x04\x01\x01\x05\x04\x01\x07\x01\x05!Z??\x01")
MODIFY_CODE = b"\x02\x00\x03\x00\x02\x11" + b"0\x04\x03\x06\x02\x01"


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class CarbonPatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.output = self.root / "disc", self.root / "patched"
        self.source.mkdir()
        self.records = [record("GLOBAL\\data.bin", DATA, CHAIN_CODES),
                        record("FRONTEND\\front.bun", MODIFY, [MODIFY_CODE]),
                        record("nfsc.exe", EXE), record("nfsc_link.exe", LINK)]
        self.path = self.root / "patch.exe"
        self.path.write_bytes(update_bytes(self.records))
        for name, data in (("Global/data.bin", DATA[0]), ("frontend/FRONT.BUN", MODIFY[0]),
                           ("NFSC.exe", EXE[0]), ("MOVIES/intro.vp6", b"unaltered movie")):
            path = self.source / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)

    def test_good_chain(self):
        before = hashes(self.source)
        if BASELINE:
            self.path.write_bytes(update_bytes(self.records, baseline=True))
            update = rtpatch.Update(self.path)  # control must fail before the byte oracle
        else:
            update = carbon_patch.Update(self.path)
        progress = []
        result = update.apply(self.source, self.output, progress=lambda *n: progress.append(n))
        self.assertEqual((self.output / "Global/data.bin").read_bytes(), DATA[-1])
        self.assertEqual((self.output / "frontend/FRONT.BUN").read_bytes(), MODIFY[-1])
        self.assertEqual((self.output / "MOVIES/intro.vp6").read_bytes(), b"unaltered movie")
        self.assertEqual(len(result.written), 2)
        self.assertEqual(result.copied, 1)
        self.assertEqual(result.skipped_executables, ("nfsc.exe", "nfsc_link.exe"))
        self.assertEqual(result.absent_executables, ("nfsc_link.exe",))
        self.assertFalse(any(p.suffix.lower() == ".exe" for p in self.output.rglob("*")))
        self.assertEqual(hashes(self.source), before)
        self.assertEqual(progress[-1][0], progress[-1][1])
        self.assertEqual(progress, sorted(progress))

    def test_history_selects_original_intermediate_and_final(self):
        update = carbon_patch.Update(self.path)
        rec = update.record("global\\DATA.bin")
        for source in DATA:
            with self.subTest(source=source):
                self.assertEqual(update.decode(rec, source), DATA[-1])

    def test_both_source_checksums_are_required(self):
        update = carbon_patch.Update(self.path)
        rec = update.records[0]
        for field in ("w1", "w2", "size"):
            step = rec.steps[0]
            bad = replace(step.source, **{field: getattr(step.source, field) ^ 1})
            changed = replace(rec, steps=(replace(step, source=bad), *rec.steps[1:]))
            with self.subTest(field=field), self.assertRaisesRegex(carbon_patch.PatchError, "source size/checksums"):
                update.decode(changed, DATA[0])

    def test_wrong_edition_rejected_before_output(self):
        (self.source / "NFSC.exe").write_bytes(b"MZdiscSE")
        before = hashes(self.source)
        with self.assertRaisesRegex(carbon_patch.PatchError, "wrong edition"):
            carbon_patch.Update(self.path).apply(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(hashes(self.source), before)

    def test_missing_modify_source_is_not_created(self):
        (self.source / "Global/data.bin").unlink()
        with self.assertRaisesRegex(carbon_patch.PatchError, "needs a source file"):
            carbon_patch.Update(self.path).apply(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_executable_witness_is_required(self):
        (self.source / "NFSC.exe").unlink()
        with self.assertRaisesRegex(carbon_patch.PatchError, "needs a source file: nfsc.exe"):
            carbon_patch.Update(self.path).apply(self.source, self.output)

    def test_present_link_and_other_executables_are_excluded(self):
        (self.source / "NFSC_LINK.exe").write_bytes(b"not executed or patched")
        (self.source / "helper.EXE").write_bytes(b"installer helper")
        result = carbon_patch.Update(self.path).apply(self.source, self.output)
        self.assertEqual(result.absent_executables, ())
        self.assertEqual(len(result.skipped_executables), 3)
        self.assertFalse(any(p.suffix.lower() == ".exe" for p in self.output.rglob("*")))
        update = carbon_patch.Update(self.path)
        with self.assertRaisesRegex(carbon_patch.PatchError, "executable records are skipped"):
            update.decode(update.record("nfsc.exe"), EXE[0])

    def test_corrupt_compressed_delta_and_output_checksums(self):
        update = carbon_patch.Update(self.path)
        rec = update.records[0]
        step = rec.steps[0]
        for field in ("w1", "w2"):
            target = replace(step.target, **{field: getattr(step.target, field) ^ 1})
            bad = replace(rec, steps=(replace(step, target=target),))
            with self.subTest(field=field), self.assertRaisesRegex(carbon_patch.PatchError, "output checksum"):
                update.decode(bad, DATA[0])
        update.data = update.data[:step.offset] + b"\0\0" + update.data[step.offset + 2:]
        with self.assertRaisesRegex(carbon_patch.PatchError, "damaged"):
            update.decode(rec, DATA[0])
        self.path.write_bytes(update.data)
        with self.assertRaises(carbon_patch.PatchError):
            carbon_patch.Update(self.path)

    def test_corrupt_literal_payload_is_refused(self):
        bad_codes = [CHAIN_CODES[0].replace(b"xy", b"xz"), CHAIN_CODES[1]]
        self.records[0] = record("GLOBAL\\data.bin", DATA, bad_codes)
        self.path.write_bytes(update_bytes(self.records))
        before = hashes(self.source)
        with self.assertRaisesRegex(carbon_patch.PatchError, "output checksum"):
            carbon_patch.Update(self.path).apply(self.source, self.output)
        self.assertEqual(hashes(self.source), before)
        self.assertFalse((self.output / "Global/data.bin").exists())
        self.assertEqual(list(self.output.rglob("*.update-*")), [])

    def test_unsupported_record_options_flags_and_counts(self):
        # Preserve a valid first record so scanning cannot conceal a bad later one.
        for offset, replacement in ((0, b"\x46\x24"), (2, b"\xff\xff")):
            records = self.records.copy()
            data = records[1]
            records[1] = data[:offset] + replacement + data[offset + 2:]
            self.path.write_bytes(update_bytes(records))
            with self.subTest(offset=offset), self.assertRaisesRegex(carbon_patch.PatchError, "unsupported"):
                carbon_patch.Update(self.path)
        reader = rtpatch._Reader(self.records[1], 4)
        reader.lp(), reader.vli(), reader.vli(), reader.take(10)
        delta = reader.pos
        for offset, replacement in ((delta, b"\x08\x00"), (delta + 2, b"\x02"),
                                    (delta + 3, b"\x02"),
                                    (delta + 4, struct.pack("<I", carbon_patch.MAX_CODE + 1)),
                                    (delta + 8, struct.pack("<I", rtpatch.MAX_UPDATE + 1))):
            records = self.records.copy()
            data = records[1]
            records[1] = data[:offset] + replacement + data[offset + len(replacement):]
            self.path.write_bytes(update_bytes(records))
            with self.subTest(delta_offset=offset), self.assertRaisesRegex(carbon_patch.PatchError, "unsupported"):
                carbon_patch.Update(self.path)
        data = update_bytes(self.records)
        for malformed in (data[:-1], data + b"garbage", data[:30] + struct.pack("<I", 5) + data[34:]):
            self.path.write_bytes(malformed)
            with self.assertRaises(carbon_patch.PatchError):
                carbon_patch.Update(self.path)

    def test_broken_history_link_and_lengths(self):
        rec = self.records[0]
        marker = struct.pack("<H", 0x2C4)
        second = rec.index(marker, rec.index(marker) + 2)
        reader = rtpatch._Reader(rec, 4)
        reader.lp(), reader.vli(), reader.vli()
        history_length = reader.pos
        for offset in (second, second + 2, second + 15, history_length):
            changed = bytearray(rec)
            changed[offset] ^= 1
            self.path.write_bytes(update_bytes([*self.records[1:], bytes(changed)]))
            with self.subTest(offset=offset), self.assertRaises(carbon_patch.PatchError):
                carbon_patch.Update(self.path)

    def test_unsafe_and_duplicate_record_paths(self):
        for name in ("..\\escape", "GLOBAL/data.bin", "C:\\file", "GLOBAL\\DATA.bin"):
            self.path.write_bytes(update_bytes([*self.records, record(name, (b"a", b"b"))]))
            with self.subTest(name=name), self.assertRaises(carbon_patch.PatchError):
                carbon_patch.Update(self.path)

    def test_tree_safety_and_existing_output(self):
        update = carbon_patch.Update(self.path)
        for path in (self.source, self.source / "output", self.root):
            with self.subTest(path=path), self.assertRaises(carbon_patch.PatchError):
                update.apply(self.source, path)
        self.output.mkdir()
        with self.assertRaisesRegex(carbon_patch.PatchError, "new tree"):
            update.apply(self.source, self.output)
        self.output.rmdir()
        (self.source / "link").symlink_to(self.root)
        with self.assertRaisesRegex(carbon_patch.PatchError, "link or special"):
            update.apply(self.source, self.output)
        (self.source / "link").unlink()
        # APFS may be case-insensitive; inject duplicate directory entries rather
        # than requiring two spellings to coexist on the test host's filesystem.
        with os.scandir(self.source) as scan:
            regular = next(e for e in scan if e.name == "NFSC.exe")
        entries = mock.MagicMock()
        entries.__enter__.return_value = [regular, regular]
        with mock.patch.object(carbon_patch.os, "scandir", return_value=entries):
            with self.assertRaisesRegex(carbon_patch.PatchError, "duplicate"):
                update.apply(self.source, self.output)
        (self.source / "unsafe\\name").write_bytes(b"ambiguous Windows name")
        with self.assertRaisesRegex(carbon_patch.PatchError, "unsafe"):
            update.apply(self.source, self.output)

    def test_cancellation_and_atomic_cleanup(self):
        update = carbon_patch.Update(self.path)
        before = hashes(self.source)
        with self.assertRaises(carbon_patch.Cancelled):
            update.apply(self.source, self.output, cancel=lambda: True)
        self.assertFalse(self.output.exists())
        def interrupt(*_):
            raise carbon_patch.Cancelled("interrupted during write")
        with self.assertRaises(carbon_patch.Cancelled):
            update.apply(self.source, self.output, progress=interrupt)
        self.assertEqual(list(self.output.rglob("*.update-*")), [])
        self.assertFalse(any(p.is_file() for p in self.output.rglob("*")))
        self.assertEqual(hashes(self.source), before)
        with self.assertRaises(carbon_patch.Cancelled):
            update.decode(update.records[0], DATA[0], cancel=lambda: True)

    def test_unchanged_copy_is_chunked_and_atomic(self):
        path = self.source / "MOVIES/large.vp6"
        path.write_bytes(b"movie" * (carbon_patch.CHUNK // 2))
        seen = []
        update = carbon_patch.Update(self.path)
        # Unchanged movies can exceed the delta-file bound: they must stream.
        with mock.patch.object(rtpatch, "MAX_FILE", 16):
            result = update.apply(self.source, self.output, progress=lambda *p: seen.append(p))
        self.assertEqual(result.copied, 2)
        self.assertEqual((self.output / "MOVIES/large.vp6").read_bytes(), path.read_bytes())
        self.assertTrue(all(b[0] - a[0] <= carbon_patch.CHUNK for a, b in zip(seen, seen[1:])))

    def test_chunked_fingerprints_equal_shared_rolling(self):
        for size in (0, 1, carbon_patch.CHUNK - 1, carbon_patch.CHUNK, carbon_patch.CHUNK + 1, 130001):
            data = (bytes(range(251)) * (size // 251 + 1))[:size]
            self.assertEqual(carbon_patch.fingerprint(data),
                             carbon_patch.Fingerprint(size, rtpatch.rolling(data, 31), rtpatch.rolling(data, 30)))

    def test_delta_copy_templates_fills_and_edits(self):
        # Opcodes not needed by the simple chain, including byte/word/dword edits.
        cases = [
            (b"\x08\x01\x02\x09\x00", b"bc"),
            (b"\x08\x01\x02\x0a\x01\x00\x05!", b"!bc"),
            (b"\x0b\x03", bytes(3)),
            (b"\x0c\x01\x02\x05!", b"!\0\0"),
            (b"\x12xy\x03", b"xyx"),
            (b"\x13wxyz\x06", b"wxyzwx"),
            (b"\x14\x01Q\x02\x05!", b"!QQ"),
            (b"\x15\x01xy\x03\x05!", b"!xyx"),
            (b"\x16\x01wxyz\x05\x05!", b"!wxyzw"),
            (b"\x03\x00\x04\x06\x01\x01", b"accd"),
            (b"\x03\x00\x04\x07\x01\x02\x00\x02", b"bbdd"),
            (b"\x03\x00\x04\x0d\x02\x00\x01\x02\x02", b"bbed"),
            (b"\x03\x00\x04\x0e\x01\x01\x03", b"abce"),
            (b"\x03\x00\x04\x0f\x01\x00\x01\x01", b"accd"),
            (b"\x03\x00\x04\x10\x01\x00\x00\x00\x01\x00", b"bbcd"),
        ]
        for instructions, expected in cases:
            with self.subTest(instructions=instructions):
                code = b"\x02\x00" + instructions + b"\x01"
                self.assertEqual(carbon_patch.apply_delta(b"abcd", code, len(expected)), expected)

    def test_delta_rejects_ranges_truncation_and_unknown_opcodes(self):
        cases = [b"\x02\x01\x01", b"\x02\x00\xff", b"\x02\x00\x03\x03\x02\x01",
                 b"\x02\x00\x09\x00\x01", b"\x02\x00\x0b\x81\x01",
                 b"\x02\x00\x0b\x03\x01", b"\x02\x00\x05x", b"\x02\x00\x01junk",
                 b"\x02\x00\x03\x00\x02", b"\x02\x00\x0d\x81\x01",
                 b"\x02\x00\x04\x01\x00\x01\x01", b"\x02\x00\x06\x02\x01\x01"]
        for code in cases:
            with self.subTest(code=code), self.assertRaises(carbon_patch.PatchError):
                carbon_patch.apply_delta(b"abcd", code, 2)
        with self.assertRaises(carbon_patch.PatchError):
            carbon_patch.apply_delta(b"", b"\x02\x00\x01", rtpatch.MAX_FILE + 1)
        with mock.patch.object(carbon_patch, "MAX_TABLE", 1):
            with self.assertRaises(carbon_patch.PatchError):
                carbon_patch.apply_delta(b"abcd", b"\x02\x00\x08\x00\x01\x08\x01\x01\x01", 2)


def tree_hashes(root):
    result = {}
    for path in root.rglob("*"):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                while chunk := handle.read(1 << 20):
                    digest.update(chunk)
            result[str(path.relative_to(root))] = digest.hexdigest()
    return result


class PrivateCarbonPatchTests(unittest.TestCase):
    def test_private_disc(self):
        patch, source = os.environ.get("CARBON_PATCH_EXE"), os.environ.get("CARBON_PATCH_SOURCE")
        if not patch or not source:
            self.skipTest("needs CARBON_PATCH_EXE and CARBON_PATCH_SOURCE")
        source = Path(source)
        update = carbon_patch.Update(patch)
        witness = next(p for p in source.iterdir() if p.name.casefold() == "nfsc.exe")
        # Prove this is an unpatched disc, rather than reusing patched evidence.
        self.assertEqual(carbon_patch.fingerprint(witness.read_bytes()), update.record("nfsc.exe").steps[0].source)
        before = tree_hashes(source)
        with tempfile.TemporaryDirectory(prefix="carbon-patch-private-", dir=source.parent) as work:
            output = Path(work) / "output"
            result = update.apply(source, output)
            files = {str(p.relative_to(output)).replace(os.sep, "\\").casefold(): p
                     for p in output.rglob("*") if p.is_file()}
            checked = 0
            for rec in update.records:
                if rec.path.lower().endswith(".exe"):
                    self.assertNotIn(rec.path.casefold(), files)
                    continue
                self.assertEqual(carbon_patch.fingerprint(files[rec.path.casefold()].read_bytes()), rec.steps[-1].target)
                checked += 1
            self.assertEqual(checked, len(result.written))
            after = tree_hashes(output)
            self.assertEqual(set(after), {name for name in before if not name.lower().endswith(".exe")})
            expected_skips = {name.replace(os.sep, "\\").casefold() for name in before if name.lower().endswith(".exe")}
            expected_skips.update(rec.path.casefold() for rec in update.records if rec.path.lower().endswith(".exe"))
            self.assertEqual({name.casefold() for name in result.skipped_executables}, expected_skips)
            patched = {rec.path.casefold() for rec in update.records}
            unchanged = 0
            for relative, digest in after.items():
                if relative.replace(os.sep, "\\").casefold() not in patched:
                    self.assertEqual(digest, before[relative])
                    unchanged += 1
            self.assertEqual(unchanged, result.copied)
            if len(update.records) == 162:
                self.assertEqual(collections.Counter(rec.kind for rec in update.records), {5: 107, 4: 55})
                self.assertEqual(checked, 160)
                self.assertEqual({rec.path.casefold() for rec in update.records if rec.path.lower().endswith(".exe")},
                                 {"nfsc.exe", "nfsc_link.exe"})
                self.assertEqual(result.absent_executables, ("nfsc_link.exe",))
            print(f"PRIVATE: {len(update.records)} records; {checked} data outputs size+dual-checksum OK; "
                  f"{len(result.skipped_executables)} executable skips; {result.copied} unchanged copies", flush=True)
            self.assertEqual(tree_hashes(source), before)


if __name__ == "__main__":
    if PRIVATE and not (os.environ.get("CARBON_PATCH_EXE") and os.environ.get("CARBON_PATCH_SOURCE")):
        print("SKIP: needs CARBON_PATCH_EXE and CARBON_PATCH_SOURCE")
        sys.exit(3)
    unittest.main(defaultTest="PrivateCarbonPatchTests" if PRIVATE else None)
