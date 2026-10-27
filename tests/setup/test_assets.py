"""













































"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT / "tools_snapshot")]
from setup_kit import huff, x360, xdvdfs
import texture_compare as compare
import x360_tex as tex


def chunk(tag, payload):
    return struct.pack("<II", tag, len(payload)) + payload


def texture(key, name, w, ht, fmt, compressed=True):
    info = bytearray(124)
    info[12:12 + len(name)] = name.encode()
    struct.pack_into("<I", info, 0x24, key)
    struct.pack_into("<I", info, 0x38, 256 * ht)
    struct.pack_into("<HH", info, 0x44, w, ht)
    platform = bytearray(32)
    struct.pack_into("<I", platform, 0x14, fmt)
    pixels = bytes(range(256)) * ht
    if compressed:
        blob = huff.fixture(pixels + info + platform)
        metadata = chunk(0x33310003, struct.pack("<6I", key, 0, len(blob), len(pixels) + 156, 0, 0))
        return chunk(0xB3300000, chunk(0xB3310000, metadata) + chunk(0xB3320000, chunk(0x33320002, blob)))
    metadata = chunk(0x33310004, info) + chunk(0x33310005, platform)
    return chunk(0xB3300000, chunk(0xB3310000, metadata) + chunk(0xB3320000, chunk(0x33320002, pixels)))


def make_iso(files, path):
    """Small genuine sector-backed directory tables with bounded tree pointers."""
    tree = {}
    for path_name, data in files.items():
        parts = path_name.split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = data
    content, next_sector = {}, 40
    def emit(node):
        nonlocal next_sector
        sector = next_sector
        positions, at = [], 0
        for name in sorted(node):
            positions.append(at)
            at = (at + 14 + len(name.encode()) + 3) & ~3
        next_sector += (at + 2047) // 2048
        table = bytearray(at)
        for i, name in enumerate(sorted(node)):
            value = node[name]
            if isinstance(value, dict):
                start, size = emit(value)
                attr = 16
            else:
                start, size, attr = next_sector, len(value), 0
                next_sector += max(1, (size + 2047) // 2048)
                content[start] = value
            right = positions[i + 1] // 4 if i + 1 < len(positions) else 0
            struct.pack_into("<HHIIBB", table, positions[i], 0, right, start, size, attr, len(name.encode()))
            table[positions[i] + 14:positions[i] + 14 + len(name.encode())] = name.encode()
        content[sector] = table
        return sector, len(table)
    rs, rz = emit(tree)
    image = bytearray(next_sector * 2048)
    image[32 * 2048:32 * 2048 + 20] = xdvdfs.MAGIC
    struct.pack_into("<II", image, 32 * 2048 + 20, rs, rz)
    for sector, data in content.items():
        image[sector * 2048:sector * 2048 + len(data)] = data
    path.write_bytes(image)


class AssetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="setup-assets-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.pc, self.folder = self.base / "PC copy ü", self.base / "X360 copy ü"
        self.pc.mkdir()
        self.folder.mkdir()
        self.files = list(x360.REQUIRED) + ["CARS/TEST/VINYLS.BIN", "NIS/SCENE_ARRESTF01_BUNDLEB.BUN", "GLOBAL/GAME_ONLY.BIN"]
        self.disc = {"default.xex": b"XEX2 synthetic", "Movies/clip.wmv": b"synthetic movie"}
        archive, rows = bytearray(), []
        for i, name in enumerate(self.files):
            payload = ("asset " + name).encode()
            rows.append(struct.pack("<6I", x360.path_hash(name), 0, i, i, len(payload), 0))
            archive += payload + bytes(2048 - len(payload))
            if name != "GLOBAL/GAME_ONLY.BIN":
                p = self.pc / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"PC")
        (self.pc / "speed.exe").write_bytes(b"synthetic executable")
        self.disc.update({"NFS/ZDIR.BIN": b"".join(rows), "NFS/ZZDATA0.BIN": bytes(archive)})
        self.iso = self.base / "own disc.iso"
        self.refresh()

    def refresh(self):
        for name, data in self.disc.items():
            p = self.folder / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        make_iso(self.disc, self.iso)

    @contextlib.contextmanager
    def extraction_only(self):
        # T5 isolates both extraction layers from retail-specific converters/XEX.
        # T7 below exercises the real native decoder, texture parser and validator.
        with mock.patch.object(x360, "_game_names", return_value=["GLOBAL/GAME_ONLY.BIN"]), \
                mock.patch.object(x360, "_convert"), mock.patch.object(x360, "_validate"):
            yield

    def reject(self, source=None):
        out = self.base / "out"
        with self.extraction_only(), self.assertRaises(x360.X360Error) as error:
            x360.prepare(source or self.iso, out, self.pc)
        self.assertTrue(error.exception.msg)
        self.assertTrue(error.exception.fix)
        self.assertEqual(list(out.iterdir()), [], "failed transaction published partial output")

    def test_t5_folder_equals_iso(self):
        with self.extraction_only():
            folder = x360.prepare(self.folder, self.base / "folder out", self.pc)
            iso = x360.prepare(self.iso, self.base / "iso out", self.pc)
        self.assertEqual(folder.names_found, len(self.files))
        self.assertEqual(folder.entries, len(self.files))
        self.assertEqual(hashes(folder.roots["x360_root"].parent), hashes(iso.roots["x360_root"].parent))
        self.assertEqual(iso.roots["x360_root"].joinpath("GLOBAL/GAME_ONLY.BIN").read_bytes(), "asset GLOBAL/GAME_ONLY.BIN".encode())
        self.assertEqual(iso.roots["x360_root"].joinpath("NFS/ZZDATA0.BIN").read_bytes(), self.disc["NFS/ZZDATA0.BIN"])
        self.assertEqual(hashes(self.folder), {k: hashlib.sha256(v).hexdigest() for k, v in self.disc.items()})

    def test_t5_missing_zdir_entry(self):
        self.disc["NFS/ZDIR.BIN"] = self.disc["NFS/ZDIR.BIN"][24:]
        self.refresh()
        self.reject()

    def test_t5_missing_archive(self):
        (self.folder / "NFS/ZZDATA0.BIN").unlink()
        self.reject(self.folder)

    def test_t5_truncated_sector(self):
        self.iso.write_bytes(self.iso.read_bytes()[:-1])
        self.reject()

    def test_t5_zdir_out_of_range(self):
        data = bytearray(self.disc["NFS/ZDIR.BIN"])
        struct.pack_into("<I", data, 8, 0xFFFFFFFF)
        self.disc["NFS/ZDIR.BIN"] = data
        self.refresh()
        self.reject()

    def test_t5_conflicting_duplicate_zdir(self):
        row = bytearray(self.disc["NFS/ZDIR.BIN"][:24])
        struct.pack_into("<I", row, 16, 100)
        self.disc["NFS/ZDIR.BIN"] += row
        self.refresh()
        self.reject()

    def test_t5_truncated_zdir(self):
        self.disc["NFS/ZDIR.BIN"] = self.disc["NFS/ZDIR.BIN"][:-1]
        self.refresh()
        self.reject()

    def test_existing_publication_preserved(self):
        out = self.base / "out"
        (out / "prepared").mkdir(parents=True)
        sentinel = out / "prepared/previous"
        sentinel.write_bytes(b"keep")
        with self.assertRaisesRegex(x360.X360Error, "already exists"):
            x360.prepare(self.iso, out, self.pc)
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_t5_disc_out_of_range(self):
        data = bytearray(self.iso.read_bytes())
        struct.pack_into("<I", data, 40 * 2048 + 4, 0xFFFFFFFF)
        self.iso.write_bytes(data)
        self.reject()

    def test_t5_path_escape(self):
        data = bytearray(self.iso.read_bytes())
        data[40 * 2048 + 13] = 2
        data[40 * 2048 + 14:40 * 2048 + 16] = b".."
        self.iso.write_bytes(data)
        self.reject()
        self.assertFalse((self.base / "escape").exists())

    def test_t5_cyclic_directory(self):
        data = bytearray(self.iso.read_bytes())
        struct.pack_into("<II", data, 40 * 2048 + 4, 40, 128)
        data[40 * 2048 + 12] = 16
        self.iso.write_bytes(data)
        self.reject()

    def test_t5_symlink_escape(self):
        (self.folder / "Movies/escape").symlink_to(self.pc)
        self.reject(self.folder)

    def test_t5_converter_failure_rollback(self):
        out = self.base / "out"
        out.mkdir()
        (out / "previous").write_bytes("keep previous install".encode())
        with self.extraction_only(), mock.patch.object(x360, "_convert", side_effect=x360.X360Error("conversion failed")):
            with self.assertRaises(x360.X360Error):
                x360.prepare(self.iso, out, self.pc)
        self.assertEqual([p.name for p in out.iterdir()], ["previous"])
        self.assertEqual((out / "previous").read_bytes(), "keep previous install".encode())

    def test_huff_known_bytes_and_broken_controls(self):
        payload = bytes(range(256)) * 3 + b"AAA\x00\xff"
        native = huff.NativeHuff(self.base)
        self.addCleanup(native.close)
        blob = huff.fixture(payload)
        self.assertEqual(native(blob), payload)
        self.assertEqual(native.bytes, len(payload))
        for bad in (blob[:-8], b"HUFF\x01\x10" + bytes(10), blob[:8] + struct.pack("<I", len(payload) + 1) + blob[12:]):
            with self.assertRaises(ValueError):
                native(bad)
        with mock.patch.object(huff.NativeHuff, "__call__", return_value=bytes(19)):
            with self.assertRaisesRegex(ValueError, "known-byte"):
                huff.NativeHuff(self.base)

    def test_t7_zero_exit_missing_asset(self):
        folder = self.base / "hd"
        folder.mkdir()
        (folder / "index.tsv").write_text("hash\tname\twidth\theight\tformat\tsource\n")
        with self.assertRaisesRegex(x360.X360Error, "inventory incomplete"):
            x360._texture_inventory(folder, {1: (4, 4)}, True)

    def validation_stage(self):
        """Actual HUFF TPKs and DDSs, with a synthetic arrest-mesh oracle."""
        stage = self.base / "validation"
        raw, assets, work = stage / "files", stage / "assets", stage / ".work"
        work.mkdir(parents=True)
        for name in ("default.xex", "NFS/ZDIR.BIN", "NFS/ZZDATA0.BIN"):
            p = raw / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"synthetic input")
        for key, name, pw, ph, pf in ((11, "HD", 4, 4, 0x31545844), (22, "UI", 8, 8, 0x33545844)):
            p = self.pc / "GLOBAL" / (name + ".BIN")
            p.write_bytes(texture(key, name, pw, ph, pf, False))
            p = raw / "GLOBAL" / (name + ".BIN")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(texture(key, name, 8, 8, 0x18280086))
            folder = assets / ("hd_textures_mips_alpha" if key == 11 else "hd_textures_ui")
            folder.mkdir(parents=True)
            (folder / "index.tsv").write_text("hash\tname\twidth\theight\tformat\tsource\n" +
                f"{key:08x}\t{name}\t8\t8\tA8R8G8B8\tGLOBAL/{name}.BIN\n")
            pixels = bytes(range(256))
            levels = tex.mip_chain("A8R8G8B8", 8, 8, pixels)[0]
            (folder / f"{key:08x}_{name}.dds").write_bytes(tex.dds("A8R8G8B8", 8, 8, pixels, levels))
        name = "NIS/SCENE_ARRESTF01_BUNDLEB.BUN"
        p = raw / name
        p.parent.mkdir(parents=True)
        p.write_bytes(texture(33, "ARREST", 8, 8, 0x18280086))
        folder = assets / "nis_arrest"
        folder.mkdir()
        (folder / "Scene_ArrestF01_BundleB.bun").write_bytes(b"converted mesh")
        (folder / "textures.tsv").write_text("hash\talias\tname\tfile\twidth\theight\n" +
            f"00000021\t{33 ^ 0x58333630:08x}\tARREST\t{name}\t8\t8\n")
        return stage, work

    def test_t7_validator_rejects_successful_incomplete_converter(self):
        import x360_nis_arrest as arrest
        stage, work = self.validation_stage()
        with mock.patch.object(arrest, "convert_bundle", return_value=(b"converted mesh", {33}, 1)):
            x360._validate(stage, self.pc, work, {"required": []})
            proof = json.loads((stage / "validation.json").read_text())
            self.assertGreater(proof["huff_bytes"], 0)
            (stage / "assets/hd_textures_mips_alpha/0000000b_HD.dds").unlink()
            with self.assertRaises((x360.X360Error, FileNotFoundError)):
                x360._validate(stage, self.pc, work, {"required": []})

    def test_t7_validator_missing_arrest_reference(self):
        import x360_nis_arrest as arrest
        stage, work = self.validation_stage()
        refs = stage / "assets/nis_arrest/textures.tsv"
        refs.write_text(refs.read_text().splitlines()[0] + "\n")
        with mock.patch.object(arrest, "convert_bundle", return_value=(b"converted mesh", {33}, 1)):
            with self.assertRaisesRegex(x360.X360Error, "Arrest texture references"):
                x360._validate(stage, self.pc, work, {"required": []})

    def test_t7_validator_disabled_huff_despite_successful_converter(self):
        stage, work = self.validation_stage()
        with mock.patch.object(huff.NativeHuff, "__call__", return_value=bytes(19)):
            with self.assertRaisesRegex(ValueError, "known-byte"):
                x360._validate(stage, self.pc, work, {"required": []})

    def test_t7_ui_size_independent_of_frontend_hd_size(self):
        stage, work = self.validation_stage()
        folder = stage / "files/FRONTEND/PLATFORMS"
        folder.mkdir(parents=True)
        for scene in x360.SCENES:
            (folder / (scene + ".LZC")).write_bytes(texture(22, "UI", 8, 8, 0x18280086))
        native = huff.NativeHuff(work)
        self.addCleanup(native.close)
        with mock.patch.object(tex, "frontend_inventory", return_value={"scene": {22: (("UI", 22, 4, 4), None)}}), \
                mock.patch.object(tex, "huff", native), mock.patch.object(compare, "huff", native):
            hd, ui, _ = x360._expected_textures(stage, tex, self.pc)
        self.assertEqual(hd, {11: (8, 8), 22: (8, 8)})
        self.assertEqual(ui, {22: (8, 8)})

    def test_glass_decode_failure_does_not_publish_cache(self):
        import x360_glass_uv1 as glass
        work = self.base / "glass"
        work.mkdir()
        out = work / "x360_stream.bun"
        out.write_bytes(b"previous cache")
        packed = huff.fixture(b"synthetic glass")[:-8]
        source = self.base / "stream.bun"
        source.write_bytes(struct.pack("<6I", 0x55441122, 15, 24 + len(packed), 0, 0, 0) + packed)
        with self.assertRaises(ValueError):
            glass.unpack_x360(source, work, self.pc / "speed.exe")
        self.assertEqual(out.read_bytes(), b"previous cache")
        self.assertFalse((work / "x360_stream.partial").exists())

    def test_t7_dds_metadata_and_pixel_controls(self):
        path = self.base / "test.dds"
        pixels = bytes(range(64))
        levels = tex.mip_chain("A8R8G8B8", 4, 4, pixels)[0]
        good = tex.dds("A8R8G8B8", 4, 4, pixels, levels)
        path.write_bytes(good)
        x360._dds(path, 4, 4, "A8R8G8B8", True)
        for off, value in ((16, 8), (28, 1), (76, 31), (80, 0), (108, 0), (20, 3), (96, 0)):
            bad = bytearray(good)
            struct.pack_into("<I", bad, off, value)
            path.write_bytes(bad)
            with self.assertRaises(x360.X360Error):
                x360._dds(path, 4, 4, "A8R8G8B8", True)
        path.write_bytes(good[:-1])
        with self.assertRaisesRegex(x360.X360Error, "pixel bytes"):
            x360._dds(path, 4, 4, "A8R8G8B8", True)

    def test_t7_huff_texture_decoded_bytes(self):
        native = huff.NativeHuff(self.base)
        self.addCleanup(native.close)
        data = texture(123, "SYNTHETIC", 8, 8, 0x18280086)
        with mock.patch.object(tex, "huff", native):
            rows = list(tex.textures_in(data))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0:4], ("SYNTHETIC", 123, 8, 8))
        self.assertEqual(rows[0][-1], bytes(range(256)) * 8)
        self.assertGreater(native.bytes, 2048)
        with mock.patch.object(tex, "huff", side_effect=ValueError("disabled HUFF")):
            with self.assertRaisesRegex(ValueError, "disabled HUFF"):
                list(tex.textures_in(data))

    def test_stream_zero_size_no_hang(self):
        data = struct.pack("<6I", 0x55441122, 32, 0, 0, 0, 0) + bytes(32)
        with self.assertRaisesRegex(ValueError, "streaming block"):
            compare.unblock(data)

    def test_stream_reordering_and_broken_destinations(self):
        def block(data, dst, rel=0):
            return struct.pack("<6I", 0x55441122, len(data), 24 + len(data), dst, rel, 0) + data
        a, b = block(b"EFGH", 4), block(b"ABCD", 0, 28)
        self.assertEqual(compare.unblock(a + b), b"ABCDEFGH")
        for bad in (a, block(b"ABCD", 0) + block(b"EFGH", 0, 28),
                    block(b"ABCD", 0) + b"bad stream padding" * 2):
            with self.assertRaises(ValueError):
                compare.unblock(bad)

    def test_native_jdlz_bytes_and_broken_match(self):
        native = huff.NativeHuff(self.base)
        self.addCleanup(native.close)
        packed = b"JDLZ\x02\x10\x00\x00" + struct.pack("<II", 8, 26) + bytes(2) + b"abcdefgh"
        self.assertEqual(native.jdlz(packed), b"abcdefgh")
        broken = packed[:16] + b"\x01\x01\x00\x00" + packed[20:]
        with self.assertRaises(ValueError):
            native.jdlz(broken)

    def test_folder_path_hash_escape_rejected(self):
        for path in ("../escape", "/escape", "A/../../escape", "A\\..\\escape", "C:/escape"):
            with self.assertRaises(xdvdfs.DiscError):
                xdvdfs.safe_path(path)

    def test_worker_memory_budget(self):
        with mock.patch.dict(os.environ, {"NFSMW_SETUP_JOBS": "999"}), mock.patch.object(os, "cpu_count", return_value=18):
            self.assertEqual(huff.job_count(), 4)
        with mock.patch.dict(os.environ, {"NFSMW_SETUP_JOBS": "2"}), mock.patch.object(os, "cpu_count", return_value=1):
            self.assertEqual(huff.job_count(), 1)

    def test_parallel_scan_order_and_huff_counts(self):
        folder = self.base / "parallel"
        folder.mkdir()
        for i in range(6):
            (folder / f"{i}.BIN").write_bytes(texture(123, f"COPY_{i}", 8, 8, 0x18280086))
        serial, parallel = huff.NativeHuff(self.base), huff.NativeHuff(self.base)
        self.addCleanup(serial.close)
        self.addCleanup(parallel.close)
        with mock.patch.dict(os.environ, {"NFSMW_SETUP_JOBS": "1"}), mock.patch.object(compare, "huff", serial):
            expected = compare.scan(str(folder), serial)
        with mock.patch.dict(os.environ, {"NFSMW_SETUP_JOBS": "4"}):
            actual = compare.scan(str(folder), parallel)
        self.assertEqual(expected[123][0], "COPY_0")
        self.assertEqual(actual, expected)
        self.assertEqual((parallel.blocks, parallel.bytes), (serial.blocks, serial.bytes))
        self.assertEqual(parallel.blocks, 6)

    def test_tree_identity_detects_paths_and_bytes(self):
        before, after = self.base / "before", self.base / "after"
        before.mkdir()
        after.mkdir()
        for folder in (before, after):
            (folder / "index.tsv").write_bytes(b"header\nfirst\nlast\n")
        self.assertEqual(tree_identity(before, after)["changed"], [])
        (after / "index.tsv").write_bytes(b"header\nlast\nfirst\n")
        self.assertEqual(tree_identity(before, after)["changed"], ["index.tsv"])
        (after / "extra").write_bytes(b"extra")
        (after / "index.tsv").unlink()
        difference = tree_identity(before, after)
        self.assertEqual(difference["added"], ["extra"])
        self.assertEqual(difference["removed"], ["index.tsv"])


def hashes(folder):
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            h = hashlib.sha256()
            with path.open("rb") as f:
                for data in iter(lambda: f.read(4 << 20), b""):
                    h.update(data)
            result[path.relative_to(folder).as_posix()] = h.hexdigest()
    return result


def tree_identity(before, after, out=None):
    """Compare every path and byte; optionally retain both complete SHA-256 manifests."""
    if not before.is_dir() or not after.is_dir():
        raise ValueError("Both identity inputs must be prepared output directories")
    previous, current = hashes(before), hashes(after)
    if not previous or not current:
        raise ValueError("Identity inputs must contain prepared output files")
    result = {"before_files": len(previous), "after_files": len(current),
              "added": sorted(current.keys() - previous.keys()),
              "removed": sorted(previous.keys() - current.keys()),
              "changed": sorted(p for p in previous.keys() & current.keys() if previous[p] != current[p])}
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        for name, data in (("before-sha256", previous), ("after-sha256", current), ("identity", result)):
            (out / (name + ".json")).write_text(json.dumps(data, indent=2) + "\n")
    return result


def equivalence(result, owner_assets, out):
    print(f"coverage {result.names_found}/{result.entries}")
    print((result.roots["assets_root"].parent / "validation.json").read_text())
    folders = ("hd_textures_mips_alpha", "hd_textures_ui", "nis_arrest")
    new_sets = {name: hashes(result.roots["assets_root"] / name) for name in folders}
    old_sets = {name: hashes(owner_assets / name) for name in folders}
    failures = 0
    for name in folders:
        current, previous = new_sets[name], old_sets[name]
        added, removed = sorted(current.keys() - previous.keys()), sorted(previous.keys() - current.keys())
        changed = sorted(k for k in current.keys() & previous.keys() if current[k] != previous[k])
        print(f"{name}: new={len(current)} owner={len(previous)} added={len(added)} removed={len(removed)} changed={len(changed)}")
        reasons = {}
        index = result.roots["assets_root"] / name / "index.tsv"
        sources = {r["hash"]: r["source"] for r in x360._rows(index)} if index.exists() else {}
        for path in added:
            moved = next((other for other in folders if other != name and old_sets[other].get(path) == current[path]), None)
            if moved:
                reasons[path] = f"Identical DDS moved from {moved}; deterministic comparison classifies its duplicate PC hash differently."
            elif sources.get(path.split("_", 1)[0], "").startswith("FRONTEND/PLATFORMS/"):
                reasons[path] = "Newly extracted frontend scene also participates in UI conversion: " + sources[path.split("_", 1)[0]]
            else:
                reasons[path] = "UNEXPLAINED addition; inspect source inventory."
        for path in removed:
            moved = next((other for other in folders if other != name and new_sets[other].get(path) == previous[path]), None)
            reasons[path] = (f"Identical DDS now in {moved}." if moved else
                "Owner audit report, unused by runtime." if path in ("frontend_audit.tsv", "frontend_memory.tsv") else
                "UNEXPLAINED removal; inspect source inventory.")
        for path in changed:
            reasons[path] = ("Inventory regenerated for the differences above." if path == "index.tsv" else
                             "UNEXPLAINED byte difference; inspect decoder/output.")
        report = dict(added=added, removed=removed, changed=changed, reasons=reasons)
        (out / (name + "-equivalence.json")).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: report[k][:8] for k in ("added", "removed", "changed")}))
        print("unexplained", sum(v.startswith("UNEXPLAINED") for v in reasons.values()))
        failures += bool(added or removed or changed)
    return bool(failures)


def owner(args):
    result = x360.prepare(args.owner_iso, args.out, args.pc)
    return equivalence(result, args.owner_assets, args.out)


if __name__ == "__main__":
    if "--identity-before" in sys.argv:
        parser = argparse.ArgumentParser()
        for name in ("identity-before", "identity-after", "out"):
            parser.add_argument("--" + name, type=Path, required=True)
        args = parser.parse_args()
        difference = tree_identity(args.identity_before, args.identity_after, args.out)
        print(json.dumps({k: len(v) if isinstance(v, list) else v for k, v in difference.items()}, sort_keys=True))
        raise SystemExit(int(any(difference[k] for k in ("added", "removed", "changed"))))
    if "--owner-iso" in sys.argv:
        parser = argparse.ArgumentParser()
        for name in ("owner-iso", "pc", "owner-assets", "out"):
            parser.add_argument("--" + name, type=Path, required=True)
        raise SystemExit(owner(parser.parse_args()))
    if "--broken" in sys.argv:
        at = sys.argv.index("--broken")
        control = sys.argv[at + 1]
        del sys.argv[at:at + 2]
        if control == "inventory":
            x360._texture_inventory = lambda *args: None
        elif control == "huff":
            huff.NativeHuff.__call__ = lambda self, data, offset=0: bytes(struct.unpack_from("<I", data, offset + 8)[0])
        elif control == "order":
            ordered_map = huff.ordered_map
            huff.ordered_map = lambda function, jobs, **kwargs: reversed(list(ordered_map(function, jobs, **kwargs)))
        elif control == "jobs":
            huff.job_count = lambda: int(os.environ.get("NFSMW_SETUP_JOBS", "4"))
        else:
            raise SystemExit("--broken inventory|huff|order|jobs")
    unittest.main(verbosity=2)
