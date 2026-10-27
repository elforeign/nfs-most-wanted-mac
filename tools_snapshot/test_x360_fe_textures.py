"""Offline FE HD coverage, runtime-verdict and storage audit; no game launch.

Usage: python -I test_x360_fe_textures.py <X360 ISO> <PC game> <generated HD set> [<old HD set>]
Checks every enlarged hash and all six scenes, including textureless NEXTGENSKY. The negative control
removes each scene in turn and requires the generator's inventory check to reject all six omissions.
Verdicts mirror x360_textures.m:163/668 (encoded covering mip, integer box rounding, RGB <=12, alpha <=16,
and the PC-alpha/DXT1 guard). Storage excludes Metal alignment, allocator overhead and transient scratch.
Verified 2026-10-24: pass 168/168 unique hashes (57/54/34/24/24 per scene), broken 6/6 omissions rejected.
"""
import csv
import hashlib
import tempfile
import os
from pathlib import Path
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import texture_compare as compare
import x360_tex as tex


def level_bytes(fmt, w, h):
    if fmt in ("DXT1", "DXT3", "DXT5"):
        return ((w + 3) // 4) * ((h + 3) // 4) * (8 if fmt == "DXT1" else 16)
    return w * h * (1 if fmt == "P8" else 4)


def storage(fmt, w, h, count):
    return sum(level_bytes(fmt, max(1, w >> l), max(1, h >> l)) for l in range(count))


def verdict(dds, pc, pcfmt):
    pw, ph, ref = pc
    h, w = struct.unpack_from("<II", dds, 12)
    count, = struct.unpack_from("<I", dds, 28)
    kind = dds[84:88].decode("ascii") if dds[84:88] != bytes(4) else "A8R8G8B8"
    assert kind in ("DXT1", "DXT3", "DXT5", "A8R8G8B8")
    off, l, ww, hh = 128, 0, w, h
    while l < count and w > pw and h > ph:
        off += level_bytes(kind, w, h)
        w, h, l = max(1, w // 2), max(1, h // 2), l + 1
    assert l < count and w >= pw and h >= ph and w % pw == 0 and h % ph == 0
    image = tex.decode_preview(kind, w, h, dds[off:off + level_bytes(kind, w, h)])
    fx, fy = w // pw, h // ph
    # hd_verify rounds integer box averages before measuring the difference.
    small = image.astype(np.uint32).reshape(ph, fy, pw, fx, 4).sum((1, 3))
    small = ((small + fx * fy // 2) // (fx * fy)).astype(np.int32)
    delta = np.abs(small - ref.astype(np.int32))
    rgb, alpha = float(delta[:, :, :3].mean()), float(delta[:, :, 3].mean())
    guard = pcfmt in (0x33545844, 0x35545844, 21, 41) and kind == "DXT1"
    levels = sum(kind == "A8R8G8B8" or (max(1, ww >> i) >= 4 and max(1, hh >> i) >= 4)
                 for i in range(count))
    assert len(dds) == 128 + storage(kind, ww, hh, count), "truncated DDS / incomplete mip chain"
    return rgb <= 12 and alpha <= 16 and not guard, rgb, alpha, storage(kind, ww, hh, levels), kind


def main():
    iso, pc_dir, output = sys.argv[1:4]
    sources = list(tex.frontend_sources(iso))
    for omit in tex.FRONTEND_SCENES:
        broken = [(p, d) for p, d in sources if Path(p).stem.upper() != omit]
        try:
            tex.frontend_inventory(broken, pc_dir)
        except ValueError as e:
            assert "missing frontend scenes" in str(e)
        else:
            raise AssertionError(f"omission of {omit} was accepted")
    print("PASS negative control: 6/6 scene omissions rejected", flush=True)
    inventory = tex.frontend_inventory(sources, pc_dir)
    wanted = {h: pc[2:4] for rows in inventory.values() for h, (pc, _) in rows.items()}
    refs = tex.pc_textures(os.path.join(pc_dir, "FRONTEND", "PLATFORMS"), wanted)
    assert refs.keys() == wanted.keys(), f"missing PC references: {wanted.keys() - refs.keys()}"
    output = Path(output)
    files = {int(p.name[:8], 16): p for p in output.glob("????????_*.dds")}
    assert not any("CSHOP_TOOLBOX_NORMAL" in p.name for p in files.values())
    old = set()
    if len(sys.argv) > 4:
        baseline = Path(sys.argv[4])
        old = {int(p.name[:8], 16) for p in baseline.glob("????????_*.dds")}
        count = 0
        for p in baseline.rglob("*.dds"):
            assert hashlib.sha256(p.read_bytes()).digest() == hashlib.sha256((output / p.relative_to(baseline)).read_bytes()).digest(), f"changed baseline DDS: {p}"
            count += 1
        print(f"PASS baseline preserved: {count} DDS files (including tone variants)", flush=True)
    with open(output / "frontend_scenes.tsv") as f:
        manifest = {row["scene"]: row for row in csv.DictReader(f, delimiter="\t")}
    assert manifest.keys() == tex.FRONTEND_SCENES.keys(), "incomplete six-scene generation manifest"
    details, summary, unique = [], [], {}
    pc_paths = {p.stem.upper(): p for p in Path(pc_dir, "FRONTEND", "PLATFORMS").glob("*.BIN")}
    for scene, rows in inventory.items():
        assert int(manifest[scene]["emitted"]) == len(rows)
        pc_data = pc_paths[scene].read_bytes()
        palettes = {}
        list(tex.textures_in(pc_data, 0x80, palettes))
        metadata = {struct.unpack_from("<I", e, 0x24)[0]: e
                    for e, _ in compare.arrays(pc_data)}
        accepted, guest_delta, gpu_delta = 0, 0, 0
        for h, (pc, xbox) in rows.items():
            assert h in files, f"{scene}: missing enlarged hash {h:08x}"
            dds = files[h].read_bytes()
            height, width = struct.unpack_from("<II", dds, 12)
            assert (width, height) == xbox[2:4], f"{scene}: wrong dimensions for {h:08x}"
            pc_kind = tex.PC_FMT.get(pc[4], "P8" if pc[4] == 41 else None)
            assert pc_kind is not None
            # Compare this scene's actual PC pixels, even when another pack has the same hash.
            if pc_kind == "P8":
                palette = np.frombuffer(palettes[h], np.uint8).reshape(256, 4)[:, [2, 1, 0, 3]]
                rgba = palette[np.frombuffer(pc[5][:pc[2] * pc[3]], np.uint8).reshape(pc[3], pc[2])]
            else:
                rgba = tex.decode_preview(pc_kind, pc[2], pc[3], pc[5])
            ok, rgb, alpha, hdbytes, kind = verdict(dds, (pc[2], pc[3], rgba), pc[4])
            pc_levels = metadata[h][0x4E]
            pcbytes = storage(pc_kind, pc[2], pc[3], pc_levels)
            pcgpu = pcbytes * 4 if pc_kind == "P8" else pcbytes
            dg, dv = (hdbytes - pcbytes, hdbytes - pcgpu) if ok else (0, 0)
            accepted += ok
            guest_delta += dg
            gpu_delta += dv
            details.append([scene, f"{h:08x}", pc[0], pc[2], pc[3], width, height, kind,
                            int(ok), f"{rgb:.4f}", f"{alpha:.4f}", hdbytes, dg, dv, int(h in old)])
            unique[h] = (ok, dg, dv)
        assert not rows or accepted, f"{scene}: no enlarged texture would pass the runtime check"
        summary.append([scene, len(rows), accepted, len(rows) - accepted, guest_delta, gpu_delta])
        print(f"{scene}: enlarged={len(rows)} eligible_swaps={accepted} kept={len(rows)-accepted} "
              f"guest_delta={guest_delta} GPU_delta={gpu_delta}", flush=True)
    # The audit tables are reports, not assets: write them to NFS_AUDIT_DIR or a temp dir, never into the player's
    # converted texture folder (it was written on every run; a sandboxed run refused it).
    audit = Path(os.environ.get("NFS_AUDIT_DIR") or tempfile.mkdtemp(prefix="fe_audit_"))
    audit.mkdir(parents=True, exist_ok=True)
    print(f"audit tables: {audit}", flush=True)
    with open(audit / "frontend_audit.tsv", "w") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["scene", "hash", "name", "pc_w", "pc_h", "hd_w", "hd_h", "format", "swap",
                         "rgb_diff", "alpha_diff", "hd_bytes", "guest_delta", "gpu_delta", "in_old_set"])
        writer.writerows(details)
    with open(audit / "frontend_memory.tsv", "w") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["scene", "enlarged", "eligible_swaps", "kept", "guest_delta", "gpu_delta"])
        writer.writerows(summary)
    print(f"PASS: {len(unique)} unique enlarged hashes, {sum(v[0] for v in unique.values())} eligible swaps; "
          f"all-resident guest/GPU deltas {sum(v[1] for v in unique.values())}/{sum(v[2] for v in unique.values())} bytes")


if __name__ == "__main__":
    main()
