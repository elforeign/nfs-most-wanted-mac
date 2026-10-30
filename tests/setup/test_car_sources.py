"""Later-game source recognition: synthetic PE/XEX/XDVDFS, no game files needed.

Good: 68/68 synthetic tests pass; four optional real-input checks are skipped.
Real later-game copies: 91 ProStreet pairs and 87 Undercover pairs, plus four
shared dependencies each, pass (70 checks, two Carbon checks skipped).
Broken (same 68 synthetic tests, exit 1): dependency bypass gives 30 failures;
identity bypass 3 failures / 9 errors; executable hash gating 6 failures /
13 errors; whole-source car rejection 12 errors; ZDIR bounds bypass 17 failures.
Changed/renamed PC exes and changed XEX versions pass; missing GLOBAL and wrong
title IDs fail; changed/missing car files affect only their own pair.
Set NFS_CARBON_PC / NFS_CARBON_X360 to additionally verify real inputs.
NFS_PROSTREET_PC / NFS_UNDERCOVER_X360 enable the later-game real checks.
The synthetic catalog is temporary and never adds accepted public fingerprints.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from setup_kit import car_sources as cs, xdvdfs

TITLE = 0x454107EC
ASSETS = {"CARS/MODEL/GEOMETRY.BIN": b"fake mesh",
          "CARS/MODEL/TEXTURES.BIN": b"fake texture",
          "CARS/SECOND/GEOMETRY.BIN": b"second mesh",
          "CARS/SECOND/TEXTURES.BIN": b"second texture",
          "GLOBAL/GLOBALB.LZC": b"fake global textures",
          "GLOBAL/GLOBALB.BUN": b"fake global bundle",
          "CARS/TEXTURES.BIN": b"fake car globals",
          "GLOBAL/ATTRIBUTES.BIN": b"fake dependency"}


def fake_pe(version=True):
    data = bytearray(1024)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 0x84, 0x14C, 1, 0, 0, 0, 224, 0x102)
    struct.pack_into("<H", data, 0x98, 0x10B)
    struct.pack_into("<I", data, 0x98 + 92, 16)
    data[0x178:0x180] = b".rsrc\0\0\0"
    struct.pack_into("<4I", data, 0x180, 512, 0x1000, 512, 512)
    if version:
        struct.pack_into("<II", data, 0x98 + 112, 0x1000, 512)
        for offset, key, target in ((0, 16, 0x80000020), (32, 1, 0x80000040), (64, 1033, 96)):
            struct.pack_into("<HH", data, 512 + offset + 12, 0, 1)
            struct.pack_into("<II", data, 512 + offset + 16, key, target)
        value = bytearray(92)
        struct.pack_into("<HHH", value, 0, 92, 52, 0)
        value[6:38] = "VS_VERSION_INFO\0".encode("utf-16le")
        struct.pack_into("<13I", value, 40, 0xFEEF04BD, 0x10000, 0x10004, 0,
                         0x10004, 0, 0, 0, 0, 0, 0, 0, 0)
        struct.pack_into("<4I", data, 512 + 96, 0x1080, len(value), 0, 0)
        data[640:640 + len(value)] = value
    return bytes(data)


def fake_xex(title=TITLE):
    data = bytearray(256)
    data[:4] = b"XEX2"
    struct.pack_into(">I", data, 20, 1)
    struct.pack_into(">II", data, 24, 0x40006, 64)
    struct.pack_into(">IIII4BI", data, 64, 0, 7, 7, title, 0, 0, 1, 1, 0)
    return bytes(data)


def fingerprint(data, **metadata):
    return dict(metadata, size=len(data), sha256=hashlib.sha256(data).hexdigest())


def release(platform, exe, assets, game="carbon"):
    files = []
    for name, data in assets.items():
        record = fingerprint(data, path_hash=cs.path_hash(name), role="shared")
        if name.startswith("CARS/") and name.count("/") == 2:
            record.update(car_hash=cs.path_hash(name.rsplit("/", 1)[0]),
                          role="geometry" if name.endswith("GEOMETRY.BIN") else "textures")
        files.append(record)
    info = cs.pe_info(exe) if platform == "pc" else cs.xex_info(exe)
    if game == "undercover":
        for row, name in zip(files, assets):
            row["archive"] = uc_archive(name)
    return {"game": game, "platform": platform, "edition": "standard" if platform == "pc" else "Collector's Edition", "executable": fingerprint(exe, **info),
            "files": files}


def archive_files(exe, assets, extra_rows=()):
    archive = bytearray()
    rows = []
    for name, data in assets.items():
        offset = (len(archive) + 2047) // 2048 * 2048
        archive.extend(bytes(offset - len(archive)) + data)
        rows.append((int(cs.path_hash(name), 16), 0, offset // 2048, 0, len(data), 0))
    rows.extend(extra_rows)
    return {"DEFAULT.XEX": exe,
            "NFS/ZDIR.BIN": b"".join(struct.pack("<6I", *r) for r in rows),
            "NFS/ZZDATA0.BIN": bytes(archive)}


def uc_archive(name):
    if name.startswith("GLOBAL/"):
        return 0
    return {"CARS/MODEL/GEOMETRY.BIN": 16, "CARS/MODEL/TEXTURES.BIN": 17,
            "CARS/SECOND/GEOMETRY.BIN": 18, "CARS/SECOND/TEXTURES.BIN": 19,
            "CARS/TEXTURES.BIN": 20}[name]


def undercover_files(exe, assets):
    archives, rows = {}, []
    for name, data in assets.items():
        number = uc_archive(name)
        archive = archives.setdefault(number, bytearray())
        offset = (len(archive) + 2047) // 2048 * 2048
        archive.extend(bytes(offset - len(archive)) + data)
        rows.append((int(cs.path_hash(name), 16), number, offset // 2048, 0, len(data), 0))
    files = {"DEFAULT.XEX": exe, "NFS/ZDIR.BIN": b"".join(struct.pack("<6I", *r) for r in rows)}
    for number, data in archives.items():
        name = "00GLOBAL.BIN" if number == 0 else f"{number:02}CARS.BIN"
        files["NFS/" + name] = bytes(data)
    return files


def write_files(root, files):
    for name, data in files.items():
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def tiny_iso(files):
    """Lay out tiny directory sectors and file extents, without a disc tool."""
    directories = {"": {}}
    for name in sorted(files):
        parts = name.split("/")
        for i in range(len(parts) - 1):
            parent, child = "/".join(parts[:i]), parts[i]
            directories.setdefault(parent, {})[child] = (True, "/".join(parts[:i + 1]))
            directories.setdefault("/".join(parts[:i + 1]), {})
        directories["/".join(parts[:-1])][parts[-1]] = (False, name)
    sectors, next_sector = {}, 33
    for name in directories:
        sectors[(True, name)] = next_sector
        next_sector += 1
    for name, data in files.items():
        sectors[(False, name)] = next_sector
        next_sector += max(1, (len(data) + 2047) // 2048)
    image = bytearray(next_sector * 2048)
    image[32 * 2048:32 * 2048 + 20] = xdvdfs.MAGIC
    struct.pack_into("<II", image, 32 * 2048 + 20, sectors[(True, "")], 2048)
    for directory, entries in directories.items():
        offset = sectors[(True, directory)] * 2048
        for name, (is_dir, full) in entries.items():
            raw = name.encode("ascii")
            size = 2048 if is_dir else len(files[full])
            struct.pack_into("<HHIIBB", image, offset, 0, 0, sectors[(is_dir, full)],
                             size, 0x10 if is_dir else 0, len(raw))
            image[offset + 14:offset + 14 + len(raw)] = raw
            offset += (14 + len(raw) + 3) & ~3
    for name, data in files.items():
        offset = sectors[(False, name)] * 2048
        image[offset:offset + len(data)] = data
    return bytes(image)


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.pe, self.xex = fake_pe(), fake_xex()
        self.xassets = {name: b"x360 " + data for name, data in ASSETS.items()}
        self.pc = self.work / "arbitrary-folder"
        write_files(self.pc, dict(ASSETS, **{"renamed.exe": self.pe}))
        self.physical = archive_files(self.xex, self.xassets)
        self.extracted = self.work / "extracted"
        write_files(self.extracted, self.physical)
        self.iso = self.work / "source.iso"
        self.iso.write_bytes(tiny_iso(self.physical))
        self.catalog = {"schema": 1, "releases": [release("pc", self.pe, ASSETS),
                                                 release("x360", self.xex, self.xassets)]}
        self.catalog_path = self.work / "fingerprints.json"
        self.save_catalog()
        self.patch = mock.patch.object(cs, "MANIFEST", self.catalog_path)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def save_catalog(self):
        self.catalog_path.write_text(json.dumps(self.catalog))

    def rebuild_archive(self):
        write_files(self.extracted, self.physical)
        self.iso.write_bytes(tiny_iso(self.physical))

    def reject(self, path):
        with self.assertRaises(cs.SourceError):
            cs.recognise(path)

    def assert_car_statuses(self, path, model_status):
        result = cs.recognise(path)
        statuses = {c["car_hash"]: c["status"] for c in result["cars"]}
        self.assertEqual(statuses, {cs.path_hash("CARS/MODEL"): model_status,
                                    cs.path_hash("CARS/SECOND"): "importable"})
        return result

    def test_changed_and_renamed_pc_executable_accepted(self):
        (self.pc / "renamed.exe").unlink()
        data = b"community executable with different bytes and size"
        (self.pc / "community.EXE").write_bytes(data)
        result = cs.recognise(self.pc)
        self.assertEqual(result, cs.recognise(self.pc / "community.EXE"))
        self.assertEqual(result["fingerprints"]["executable"], fingerprint(data))
        self.assertTrue(all(c["status"] == "importable" for c in result["cars"]))

    def test_pc_one_level_wrapper(self):
        result = cs.recognise(self.pc)
        wrapper = self.work / "wrapper"
        wrapper.mkdir()
        self.pc.rename(wrapper / "installed")
        self.assertEqual(result, cs.recognise(wrapper))

    def test_missing_global_directory(self):
        for p in (self.pc / "GLOBAL").iterdir():
            p.unlink()
        (self.pc / "GLOBAL").rmdir()
        with self.assertRaisesRegex(cs.SourceError, "Choose the folder that contains CARS and GLOBAL"):
            cs.recognise(self.pc)

    def test_shared_errors_name_files(self):
        for name in ("GLOBAL/GLOBALB.LZC", "GLOBAL/ATTRIBUTES.BIN", "CARS/TEXTURES.BIN"):
            with self.subTest(name=name):
                p = self.pc / name
                data = p.read_bytes()
                p.write_bytes(b"changed")
                with self.assertRaisesRegex(cs.SourceError, name.replace(".", r"\.")):
                    cs.recognise(self.pc)
                p.write_bytes(data)

    def test_pc_globalb_bun_is_optional(self):
        p = self.pc / "GLOBAL/GLOBALB.BUN"
        p.write_bytes(b"other companion bundle")
        result = cs.recognise(self.pc)
        self.assertEqual(len(result["shared"]), 3)
        self.assertTrue(all(c["status"] == "importable" for c in result["cars"]))
        p.unlink()
        self.assertEqual(result, cs.recognise(self.pc))

    def test_one_changed_car_hash_is_isolated(self):
        p = self.pc / "CARS/MODEL/TEXTURES.BIN"
        data = p.read_bytes()
        p.write_bytes(bytes([data[0] ^ 1]) + data[1:])
        result = self.assert_car_statuses(self.pc, "unknown")
        car = next(c for c in result["cars"] if c["car_hash"] == cs.path_hash("CARS/MODEL"))
        self.assertEqual(sorted(f["status"] for f in car["files"]), ["importable", "unknown"])

    def test_x360_changed_car_is_isolated(self):
        assets = dict(self.xassets)
        assets["CARS/MODEL/GEOMETRY.BIN"] = b"changed mesh"
        self.physical = archive_files(self.xex, assets)
        self.rebuild_archive()
        self.assert_car_statuses(self.iso, "unknown")
        self.assert_car_statuses(self.extracted, "unknown")

    def test_x360_missing_car_is_isolated(self):
        assets = {n: d for n, d in self.xassets.items() if not n.startswith("CARS/MODEL/")}
        self.physical = archive_files(self.xex, assets)
        self.rebuild_archive()
        self.assert_car_statuses(self.iso, "missing")

    def test_known_alternative_pair(self):
        assets = dict(ASSETS)
        assets["CARS/MODEL/GEOMETRY.BIN"] = b"another known mesh"
        assets["CARS/MODEL/TEXTURES.BIN"] = b"another known texture"
        self.catalog["releases"].append(release("pc", self.pe, assets))
        self.save_catalog()
        write_files(self.pc, assets)
        self.assert_car_statuses(self.pc, "importable")
        write_files(self.pc, {"CARS/MODEL/GEOMETRY.BIN": ASSETS["CARS/MODEL/GEOMETRY.BIN"]})
        self.assert_car_statuses(self.pc, "unknown")

    def test_pc_folder_and_selected_executable(self):
        folder = cs.recognise(self.pc)
        self.assertEqual(folder, cs.recognise(self.pc / "renamed.exe"))
        self.assertEqual((folder["platform"], len(folder["cars"]), len(folder["shared"])), ("pc", 2, 4))

    def test_same_inventory_iso_and_extracted_archive(self):
        self.assertEqual(cs.recognise(self.iso), cs.recognise(self.extracted))

    def test_logical_extraction_matches_disc(self):
        folder = self.work / "logical"
        write_files(folder, dict(self.xassets, **{"default.xex": self.xex}))
        self.assertEqual(cs.recognise(self.iso), cs.recognise(folder))

    def test_logical_extraction_can_omit_executable(self):
        folder = self.work / "logical"
        write_files(folder, self.xassets)
        result = cs.recognise(folder)
        self.assertEqual(result["platform"], "x360")
        self.assertIsNone(result["fingerprints"]["executable"])
        self.assertEqual(result["cars"], cs.recognise(self.iso)["cars"])

    def test_extracted_files_wrapper(self):
        folder = self.work / "wrapper"
        write_files(folder / "FILES", self.physical)
        self.assertEqual(cs.recognise(self.iso), cs.recognise(folder))

    def test_case_insensitive_assets(self):
        folder = self.work / "lowercase"
        write_files(folder, {name.lower(): data for name, data in dict(ASSETS, **{"ANY.EXE": self.pe}).items()})
        self.assertEqual(cs.recognise(folder), cs.recognise(self.pc))

    def test_no_source_writes(self):
        before = {p.relative_to(self.work): p.read_bytes() for p in self.work.rglob("*") if p.is_file()}
        cs.recognise(self.pc)
        cs.recognise(self.iso)
        after = {p.relative_to(self.work): p.read_bytes() for p in self.work.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_pe_version_resource(self):
        info = cs.pe_info(self.pe)
        self.assertEqual(info["file_version"], [1, 4, 0, 0])
        self.assertEqual(info["product_version"], [1, 4, 0, 0])
        self.assertIsNone(cs.pe_info(fake_pe(False))["file_version"])

    def test_xex_execution_info(self):
        self.assertEqual(cs.xex_info(self.xex), {"title_id": "0x454107EC", "version": 7, "base_version": 7})

    def test_wrong_title_even_with_matching_executable_hash(self):
        self.physical["DEFAULT.XEX"] = fake_xex(TITLE + 1)
        self.catalog["releases"][1]["executable"]["sha256"] = hashlib.sha256(self.physical["DEFAULT.XEX"]).hexdigest()
        self.save_catalog()
        self.rebuild_archive()
        self.reject(self.iso)

    def test_changed_xex_hash_and_version_accepted(self):
        data = bytearray(self.xex)
        data[-1] ^= 1
        struct.pack_into(">II", data, 68, 99, 42)
        data.extend(b"another edition")
        self.physical["DEFAULT.XEX"] = bytes(data)
        self.rebuild_archive()
        result = cs.recognise(self.iso)
        self.assertEqual(result["fingerprints"]["executable"],
                         fingerprint(bytes(data), **cs.xex_info(data)))
        self.assertTrue(all(c["status"] == "importable" for c in result["cars"]))

    def test_changed_shared_dependency_hash(self):
        p = self.pc / "GLOBAL/ATTRIBUTES.BIN"
        p.write_bytes(bytes([p.read_bytes()[0] ^ 1]) + p.read_bytes()[1:])
        self.reject(self.pc)

    def test_changed_archive_dependency_hash(self):
        data = bytearray(self.physical["NFS/ZZDATA0.BIN"])
        data[-1] ^= 1
        self.physical["NFS/ZZDATA0.BIN"] = bytes(data)
        self.rebuild_archive()
        self.reject(self.iso)

    def test_changed_asset_size(self):
        (self.pc / "CARS/MODEL/GEOMETRY.BIN").write_bytes(b"short")
        self.assert_car_statuses(self.pc, "unknown")

    def test_missing_car(self):
        (self.pc / "CARS/MODEL/TEXTURES.BIN").unlink()
        self.assert_car_statuses(self.pc, "missing")

    def test_missing_pc_executable(self):
        (self.pc / "renamed.exe").unlink()
        result = cs.recognise(self.pc)
        self.assertEqual(result["platform"], "pc")
        self.assertIsNone(result["fingerprints"]["executable"])

    def test_selected_unrelated_executable(self):
        (self.pc / "other.exe").write_bytes(b"unrelated")
        result = cs.recognise(self.pc / "other.exe")
        self.assertEqual(result["fingerprints"]["executable"], fingerprint(b"unrelated"))

    def test_missing_xex_in_disc_and_archive(self):
        self.physical.pop("DEFAULT.XEX")
        (self.extracted / "DEFAULT.XEX").unlink()
        self.rebuild_archive()
        self.reject(self.iso)
        self.reject(self.extracted)

    def test_truncated_iso(self):
        self.iso.write_bytes(self.iso.read_bytes()[:-1])
        self.reject(self.iso)

    def test_truncated_archive_in_folder(self):
        p = self.extracted / "NFS/ZZDATA0.BIN"
        p.write_bytes(p.read_bytes()[:-1])
        self.reject(self.extracted)

    def test_outside_archive_range(self):
        rows = bytearray(self.physical["NFS/ZDIR.BIN"])
        struct.pack_into("<I", rows, 8, 0xFFFFFFFF)
        self.physical["NFS/ZDIR.BIN"] = bytes(rows)
        self.rebuild_archive()
        self.reject(self.iso)

    def test_missing_archive(self):
        self.physical.pop("NFS/ZZDATA0.BIN")
        self.iso.write_bytes(tiny_iso(self.physical))
        self.reject(self.iso)

    def test_endian_swapped_xex_header(self):
        data = bytearray(self.xex)
        data[20:24] = data[20:24][::-1]
        self.physical["DEFAULT.XEX"] = bytes(data)
        self.catalog["releases"][1]["executable"]["sha256"] = hashlib.sha256(data).hexdigest()
        self.save_catalog()
        self.rebuild_archive()
        self.reject(self.iso)

    def test_endian_swapped_archive_header(self):
        rows = self.physical["NFS/ZDIR.BIN"]
        self.physical["NFS/ZDIR.BIN"] = b"".join(struct.pack(">6I", *r) for r in struct.iter_unpack("<6I", rows))
        self.rebuild_archive()
        self.reject(self.iso)

    def test_endian_swapped_pe_header(self):
        data = bytearray(self.pe)
        data[60:64] = data[60:64][::-1]
        (self.pc / "renamed.exe").write_bytes(data)
        self.catalog["releases"][0]["executable"]["sha256"] = hashlib.sha256(data).hexdigest()
        self.save_catalog()
        self.assertEqual(cs.recognise(self.pc)["fingerprints"]["executable"], fingerprint(data))
        with self.assertRaises(cs.SourceError):
            cs.pe_info(data)

    def test_truncated_pe_resources(self):
        data = bytearray(self.pe)
        struct.pack_into("<I", data, 512 + 96 + 4, 0xFFFF)
        with self.assertRaises(cs.SourceError):
            cs.pe_info(data)

    def test_cyclic_pe_resources(self):
        data = bytearray(self.pe)
        struct.pack_into("<I", data, 512 + 20, 0x80000000)
        with self.assertRaises(cs.SourceError):
            cs.pe_info(data)

    def test_xex_header_outside_file(self):
        data = bytearray(self.xex)
        struct.pack_into(">I", data, 28, 0xFFFFFFFF)
        with self.assertRaises(cs.SourceError):
            cs.xex_info(data)

    def test_matching_replicated_archive_file(self):
        rows = self.physical["NFS/ZDIR.BIN"]
        first = list(struct.unpack_from("<6I", rows))
        archive = self.physical["NFS/ZZDATA0.BIN"]
        start = (len(archive) + 2047) // 2048 * 2048
        duplicate = archive[:first[4]]
        self.physical["NFS/ZZDATA0.BIN"] = archive + bytes(start - len(archive)) + duplicate
        first[2] = start // 2048
        self.physical["NFS/ZDIR.BIN"] = rows + struct.pack("<6I", *first)
        self.rebuild_archive()
        self.assertEqual(cs.recognise(self.iso)["platform"], "x360")

    def test_conflicting_replicated_archive_file(self):
        self.test_matching_replicated_archive_file()
        data = bytearray(self.physical["NFS/ZZDATA0.BIN"])
        data[-1] ^= 1
        self.physical["NFS/ZZDATA0.BIN"] = bytes(data)
        self.rebuild_archive()
        self.reject(self.iso)

    def test_conflicting_duplicate_zdir_metadata(self):
        rows = self.physical["NFS/ZDIR.BIN"]
        first = list(struct.unpack_from("<6I", rows))
        first[4] += 1
        self.physical["NFS/ZDIR.BIN"] = rows + struct.pack("<6I", *first)
        self.rebuild_archive()
        self.reject(self.iso)

    def test_links_rejected(self):
        (self.pc / "link").symlink_to(self.pc / "CARS", target_is_directory=True)
        self.reject(self.pc)

    def test_ambiguous_files_wrapper(self):
        write_files(self.extracted / "FILES", {"DEFAULT.XEX": self.xex})
        self.reject(self.extracted)


class LaterRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.ps_assets = {n: b"ProStreet " + d for n, d in ASSETS.items()}
        self.uc_assets = {n: b"Undercover " + d for n, d in ASSETS.items()}
        self.xex = fake_xex(0x45410876)
        self.pc = self.work / "pc"
        write_files(self.pc, self.ps_assets)
        self.physical = undercover_files(self.xex, self.uc_assets)
        self.extracted = self.work / "disc"
        self.iso = self.work / "disc.iso"
        self.rebuild()
        self.catalog = {"schema": 1, "releases": [
            release("pc", fake_pe(), ASSETS),
            release("x360", fake_xex(), ASSETS),
            release("pc", fake_pe(), self.ps_assets, "prostreet"),
            release("x360", self.xex, self.uc_assets, "undercover")]}
        self.catalog_path = self.work / "catalog.json"
        self.save_catalog()
        patch = mock.patch.object(cs, "MANIFEST", self.catalog_path)
        patch.start()
        self.addCleanup(patch.stop)

    def save_catalog(self):
        self.catalog_path.write_text(json.dumps(self.catalog))

    def rebuild(self):
        write_files(self.extracted, self.physical)
        self.iso.write_bytes(tiny_iso(self.physical))

    def statuses(self, path, model="importable"):
        result = cs.recognise(path)
        self.assertEqual({c["car_hash"]: c["status"] for c in result["cars"]},
                         {cs.path_hash("CARS/MODEL"): model,
                          cs.path_hash("CARS/SECOND"): "importable"})
        return result

    def reject_disc(self, message=None):
        self.rebuild()
        for path in (self.iso, self.extracted):
            with self.subTest(path=path.name):
                with self.assertRaisesRegex(cs.SourceError, message or "."):
                    cs.recognise(path)

    def test_prostreet_folder_without_executable(self):
        result = self.statuses(self.pc)
        self.assertEqual((result["game"], result["platform"], len(result["shared"])),
                         ("prostreet", "pc", 4))
        self.assertIsNone(result["fingerprints"]["executable"])

    def test_prostreet_any_selected_executable(self):
        data = b"community edition, no PE header"
        write_files(self.pc, {"custom.EXE": data})
        result = self.statuses(self.pc / "custom.EXE")
        self.assertEqual(result["game"], "prostreet")
        self.assertEqual(result["fingerprints"]["executable"], fingerprint(data))

    def test_prostreet_enclosing_folder_and_lowercase(self):
        wrapper = self.work / "wrapper"
        write_files(wrapper / "copy", {n.lower(): d for n, d in self.ps_assets.items()})
        self.assertEqual(cs.recognise(wrapper), cs.recognise(self.pc))

    def test_prostreet_shared_dependencies_are_required(self):
        for name in cs.SHARED_NAMES.values():
            with self.subTest(name=name):
                p = self.pc / name
                original = p.read_bytes()
                p.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                with self.assertRaisesRegex(cs.SourceError, name.replace(".", r"\.")):
                    cs.recognise(self.pc)
                p.unlink()
                with self.assertRaises(cs.SourceError):
                    cs.recognise(self.pc)
                p.write_bytes(original)

    def test_prostreet_changed_car_is_isolated(self):
        name = "CARS/MODEL/TEXTURES.BIN"
        data = self.ps_assets[name]
        write_files(self.pc, {name: bytes([data[0] ^ 1]) + data[1:]})
        result = self.statuses(self.pc, "unknown")
        model = next(c for c in result["cars"] if c["car_hash"] == cs.path_hash("CARS/MODEL"))
        self.assertEqual(sorted(f["status"] for f in model["files"]), ["importable", "unknown"])

    def test_prostreet_missing_car_is_isolated(self):
        (self.pc / "CARS/MODEL/GEOMETRY.BIN").unlink()
        self.statuses(self.pc, "missing")

    def test_prostreet_missing_global_directory(self):
        for p in (self.pc / "GLOBAL").iterdir():
            p.unlink()
        (self.pc / "GLOBAL").rmdir()
        with self.assertRaisesRegex(cs.SourceError, "contains CARS and GLOBAL"):
            cs.recognise(self.pc)

    def test_game_rosters_do_not_mix(self):
        other = release("pc", fake_pe(), self.ps_assets)
        for row in other["files"]:
            if "car_hash" in row:
                row["car_hash"] = "ffffffff"
        self.catalog["releases"].insert(0, other)
        self.save_catalog()
        # Directly match the PS release to exercise isolation even when another
        # game's fixture deliberately has the same dependency fingerprints.
        with cs._source(self.pc) as source:
            files = cs._physical(source)
            result = cs._match(source, files, cs._logical(source, files),
                               self.catalog["releases"][3], None, self.catalog["releases"])
        self.assertEqual(len(result["cars"]), 2)
        self.assertEqual(result["game"], "prostreet")

    def test_undercover_iso_and_extracted_fingerprints_agree(self):
        result = self.statuses(self.iso)
        self.assertEqual(result, cs.recognise(self.extracted))
        self.assertEqual((result["game"], result["platform"]), ("undercover", "x360"))
        self.assertEqual(result["fingerprints"]["executable"]["title_id"], "0x45410876")
        self.assertEqual({r["archive"] for r in result["shared"]}, {0, 20})

    def test_undercover_files_wrapper(self):
        folder = self.work / "wrapper"
        write_files(folder / "FILES", {n.lower(): d for n, d in self.physical.items()})
        self.assertEqual(cs.recognise(folder), cs.recognise(self.iso))

    def test_undercover_wrong_title(self):
        self.physical["DEFAULT.XEX"] = fake_xex(0x45410877)
        self.reject_disc("title ID")

    def test_undercover_carbon_title_cannot_approve_undercover(self):
        self.physical["DEFAULT.XEX"] = fake_xex(TITLE)
        self.reject_disc()

    def test_undercover_edition_and_executable_hash_are_unrestricted(self):
        data = bytearray(self.xex)
        struct.pack_into(">II", data, 68, 123, 99)
        data.extend(b"different edition")
        self.physical["DEFAULT.XEX"] = bytes(data)
        self.rebuild()
        result = self.statuses(self.iso)
        self.assertEqual(result["fingerprints"]["executable"],
                         fingerprint(data, **cs.xex_info(data)))

    def test_undercover_missing_executable(self):
        self.physical.pop("DEFAULT.XEX")
        (self.extracted / "DEFAULT.XEX").unlink()
        self.reject_disc("default.xex")

    def test_undercover_changed_globals_and_attributes(self):
        for name in cs.SHARED_NAMES.values():
            with self.subTest(name=name):
                assets = dict(self.uc_assets)
                data = assets[name]
                assets[name] = bytes([data[0] ^ 1]) + data[1:]
                self.physical = undercover_files(self.xex, assets)
                self.reject_disc(name.replace(".", r"\."))

    def test_undercover_changed_car_is_isolated(self):
        for name in ("CARS/MODEL/GEOMETRY.BIN", "CARS/MODEL/TEXTURES.BIN"):
            with self.subTest(name=name):
                assets = dict(self.uc_assets)
                data = assets[name]
                assets[name] = bytes([data[0] ^ 1]) + data[1:]
                self.physical = undercover_files(self.xex, assets)
                self.rebuild()
                self.statuses(self.iso, "unknown")
                self.statuses(self.extracted, "unknown")

    def test_undercover_missing_car_is_isolated(self):
        assets = {n: d for n, d in self.uc_assets.items() if n != "CARS/MODEL/GEOMETRY.BIN"}
        self.physical = undercover_files(self.xex, assets)
        self.rebuild()
        self.statuses(self.iso, "missing")

    def test_undercover_truncated_car_and_global_archives(self):
        original = dict(self.physical)
        for name in (n for n in original if n.endswith(".BIN") and "ZDIR" not in n):
            with self.subTest(name=name):
                self.physical = dict(original)
                self.physical[name] = original[name][:-1]
                self.reject_disc("range exceeds")

    def test_undercover_range_outside_archive(self):
        rows = bytearray(self.physical["NFS/ZDIR.BIN"])
        struct.pack_into("<I", rows, 8, 0xFFFFFFFF)
        self.physical["NFS/ZDIR.BIN"] = bytes(rows)
        self.reject_disc("range exceeds")

    def test_undercover_bounds_checked_before_car_read(self):
        rows = bytearray(self.physical["NFS/ZDIR.BIN"])
        struct.pack_into("<I", rows, 8, 0xFFFFFFFF)
        self.physical["NFS/ZDIR.BIN"] = bytes(rows)
        self.rebuild()
        with cs._source(self.iso) as source:
            with mock.patch.object(cs, "_digest", side_effect=AssertionError("Unbounded read")):
                with self.assertRaisesRegex(cs.SourceError, "range exceeds"):
                    cs._logical(source, cs._physical(source))

    def test_undercover_oversized_range(self):
        rows = bytearray(self.physical["NFS/ZDIR.BIN"])
        struct.pack_into("<I", rows, 16, (128 << 20) + 1)
        self.physical["NFS/ZDIR.BIN"] = bytes(rows)
        self.reject_disc("range size")

    def test_undercover_wrong_archive_number(self):
        rows = bytearray(self.physical["NFS/ZDIR.BIN"])
        struct.pack_into("<I", rows, 4, 21)
        self.physical["NFS/ZDIR.BIN"] = bytes(rows)
        self.rebuild()
        self.statuses(self.iso, "missing")

    def test_undercover_duplicate_zdir_entry_rejected(self):
        rows = self.physical["NFS/ZDIR.BIN"]
        self.physical["NFS/ZDIR.BIN"] = rows + rows[:24]
        self.reject_disc("Duplicate Undercover")

    def test_undercover_other_archives_not_required(self):
        rows = self.physical["NFS/ZDIR.BIN"]
        self.physical["NFS/ZDIR.BIN"] = rows + struct.pack("<6I", 1, 13, 123, 0, 4096, 0)
        self.rebuild()
        self.statuses(self.iso)

    def test_undercover_truncated_zdir(self):
        self.physical["NFS/ZDIR.BIN"] = self.physical["NFS/ZDIR.BIN"][:-1]
        self.reject_disc("ZDIR table size")

    def test_later_inputs_are_read_only(self):
        before = {p: p.read_bytes() for p in self.work.rglob("*") if p.is_file()}
        self.statuses(self.pc)
        self.statuses(self.iso)
        self.statuses(self.extracted)
        self.assertEqual(before, {p: p.read_bytes() for p in self.work.rglob("*") if p.is_file()})


class RealInputs(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("NFS_CARBON_PC"), "NFS_CARBON_PC not supplied")
    def test_real_pc(self):
        path = Path(os.environ["NFS_CARBON_PC"])
        result = cs.recognise(path)
        self.assertEqual(result["platform"], "pc")
        self.assertEqual(len(result["cars"]), 116)
        self.assertEqual(len(result["shared"]), 4)
        self.assertEqual(result["edition"], "standard")
        self.assertTrue(all(c["status"] == "importable" for c in result["cars"]))
        print("real PC: 116 car pairs + 4 shared inputs verified")

    @unittest.skipUnless(os.environ.get("NFS_CARBON_X360"), "NFS_CARBON_X360 not supplied")
    def test_real_x360(self):
        result = cs.recognise(os.environ["NFS_CARBON_X360"])
        self.assertEqual(result["platform"], "x360")
        self.assertEqual(len(result["cars"]), 116)
        self.assertEqual(len(result["shared"]), 4)
        self.assertEqual(result["edition"], "Collector's Edition")
        self.assertTrue(all(c["status"] == "importable" for c in result["cars"]))
        self.assertEqual(result["fingerprints"]["executable"]["title_id"], "0x454107EC")
        print("real X360: 116 car pairs + 4 shared inputs verified")

    @unittest.skipUnless(os.environ.get("NFS_PROSTREET_PC"), "NFS_PROSTREET_PC not supplied")
    def test_real_prostreet(self):
        result = cs.recognise(os.environ["NFS_PROSTREET_PC"])
        self.assertEqual((result["game"], len(result["cars"]), len(result["shared"])),
                         ("prostreet", 91, 4))
        self.assertTrue(all(c["status"] == "importable" for c in result["cars"]))

    @unittest.skipUnless(os.environ.get("NFS_UNDERCOVER_X360"), "NFS_UNDERCOVER_X360 not supplied")
    def test_real_undercover(self):
        result = cs.recognise(os.environ["NFS_UNDERCOVER_X360"])
        self.assertEqual((result["game"], len(result["cars"]), len(result["shared"])),
                         ("undercover", 87, 4))
        self.assertTrue(all(c["status"] == "importable" for c in result["cars"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--broken", choices=("dependency", "identity", "executable", "cars", "range"))
    args, remaining = parser.parse_known_args()
    if args.broken == "dependency":
        cs._file_status = lambda source, logical, expected: ("importable" if expected["path_hash"] in logical else "missing")
    elif args.broken == "identity":
        cs.xex_info = lambda data: {"title_id": "0x454107EC", "version": 7, "base_version": 7}
    elif args.broken == "executable":
        executable = cs._executable
        def strict_executable(source, files, platform, selected):
            result = executable(source, files, platform, selected)
            catalog = json.loads(cs.MANIFEST.read_text())
            expected = [r["executable"] for r in catalog["releases"] if r["platform"] == platform]
            cs._require(result is not None and any(result["sha256"] == e["sha256"]
                        and result["size"] == e["size"] for e in expected), "Executable hash changed")
            return result
        cs._executable = strict_executable
    elif args.broken == "cars":
        match = cs._match
        def strict_cars(*args):
            result = match(*args)
            cs._require(all(c["status"] == "importable" for c in result["cars"]),
                        "Source contains a changed or missing car")
            return result
        cs._match = strict_cars
    elif args.broken == "range":
        require = cs._require
        def unbounded(ok, message):
            if message not in ("ZDIR file range exceeds its archive", "Invalid Undercover ZDIR range size"):
                require(ok, message)
        cs._require = unbounded
    unittest.main(argv=[sys.argv[0]] + remaining)


if __name__ == "__main__":
    main()
