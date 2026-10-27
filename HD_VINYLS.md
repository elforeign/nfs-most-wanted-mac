# HD car vinyls (X360 assets)

Goal: car skins composited from the X360's 1024² 32-bit vinyl art instead of the PC's 512² 8-bit copies, with the
same look as the PC compositor (or, opt-in, byte-for-byte the X360 compositor), validated against the PC result.

Tags: **[V]** checked in code, data or a run; **[I]** inferred.

## 1. The PC skin pipeline (speed.exe v1.3)

| Step | Address | What it does |
|---|---|---|
| Layer list | `GetVinylLayerHash` `007493A0` | `bStringHash("<CARTYPE>_<TEXTURE attr>")` (or `_PRECOM_` for precomposite cars, `RideInfo[7] == 2`) |
| | `GetVinylLayerMaskHash` `00749460` | the same name + `_MASK` |
| Load | `CarLoader::LoadSkin` `0075B930` → `LoadSkinLayers` `0073A650` / `AllocateSkinLayers` `00753060` | collects layer hashes, `eLoadStreamingTexture` loads them from the streaming pack `CARS\<car>\VINYLS.BIN` (compressed entries, decompressed by `LZDecompress` `00650350`) |
| Create | `TextureInfoPlatInterface::Init` `006DDD90` | creates the D3D texture; **P8 is expanded to A8R8G8B8 here** (palette entries copied as BGRA) and `TextureInfo[0x4A]` (bits per pixel) becomes 0x20 [V] |
| Composite | `CarLoader::CompositeSkin` `0075BA50` → `CompositeSkin(RideInfo*)` `00749520` | target = `GetTextureInfo(RideInfo+0x3C)` (`DUMMY_SKIN1..4`, hashes `530B82B0..B3`, A8R8G8B8 512², 1 mip [V]); base colour from the paint part (`RideInfo+0x178`: RED/GREEN/BLUE/GLOSS → `gloss<<24 \| B<<16 \| G<<8 \| R`); one layer (`RideInfo+0x17C`), its `_MASK`, and with the `REMAP` attribute 4 swatch colours from the vinyl colour parts (`RideInfo+0x184..`; missing part → `0xFF << 8i`); `IsInSkinCompositeCache` `0073AC30` skips unchanged params |
| | `DumpPreComp` `0073B360` | precomposite cars (`UsePrecompositeVinyls`, `RideInfo[7] == 2`): copies the layer image into the target |
| | `CompositeSkin32` `0073B010` | **the PC path**: the target is 32-bit, so this runs, not the 8-bit one [V] |
| | `CompositeSkin` `00748AD0` | 8-bit target path (PS2 heritage: NeuQuant `initnet`/`learn`/`inxsearch` into the target palette); never runs on PC |
| | `GetBlendColour` `0073AE60` | weighted sum per byte, clamped to 255; byte 3 optionally max instead of sum |

Params block (`SkinCompositeParams`, 0x50 bytes, built on the stack by `00749520`): `+0` target TextureInfo,
`+4` base colour, `+8..+0x14` 4 swatch-region colours (= base), `+0x18` layer[1] (0x34 bytes each), `+0x4C` layer
count. Layer: `+0` hash (0 = absent), `+4` image TextureInfo, `+8` mask TextureInfo, `+0xC` locked image, `+0x10`
palette, `+0x14` locked mask, `+0x18` mask palette, `+0x1C` NUMCOLOURS, `+0x20` REMAP flag, `+0x24..+0x30` 4 swatch
colours.

`CompositeSkin32` (all images are locked D3D textures, BGRA in memory, all assumed the size of the target):
1. once per process (`swatch_offset_init`): record target pixels equal to `0xBF0000FF / 0xBF00FF00 / 0xBFFF0000 /
   0xBFFF00FF` (swatch-region markers). **No `DUMMY_SKIN` contains any** (PC or X360) [V], so this is dead code.
2. fill the target with the base colour (R/B swapped into ARGB).
3. per layer pixel: `m` = mask byte 1 (G; masks are grey [V]); `c` = layer pixel; if REMAP and `m != 0`:
   `c = GetBlendColour(swatches, {R, G, B, 0}/255, 3, max_alpha)` (swatches `__flipbgr`ed to ARGB first); then
   `m >= 128`: target = `c`; `0 < m < 128`: target = `GetBlendColour({c, target}, {m/128, 1 - m/128}, 2, sum)`.
4. write the swatch colours into the marker pixels (none).

So a vinyl's RGB channels are weights for swatch colours 0, 1, 2; the result alpha (car paint gloss) is the max of
the weighted swatch glosses. Mask 128-255 replaces, 1-127 blends (anti-aliased edges).

## 2. The X360 pipeline (default.xex)

Same source, same structure [V]: driver `824F8128` (= `00749520`), `CompositeSkin32` `824F6EC0`, 8-bit
`CompositeSkin` `824F72C8`, `GetBlendColour` `824F6860` (unrolled ×4), `RemapColour` `824F6E20`, `DumpPreComp`
`824F7BF0`, `GetVinylLayerHash` `824F7E80`, `CompareCompositeParams` `824F6530`, `CarLoader::LoadSkin` `824F3B80`.
Images are read from `TextureInfo+0x70` directly (no lock). Data:

| | PC | X360 |
|---|---|---|
| Vinyl + mask | 512² P8 (A8R8G8B8 after `Init`) | 1024² `LIN_A8R8G8B8` (`0x18280086`: linear, 8in32), CPU sees bytes A R G B |
| `DUMMY_SKIN1..4` | 512² A8R8G8B8, 1 mip | 1024² `LIN_A8R8G8B8`, 1 mip |
| `DUMMY_SKIN5..12`, PREVINYL (`_PRECOM_`) | 256² | 256² (32-bit instead of P8) |
| Pack | compressed streaming TPK | the same; table `0x33310003` = hash, file offset, packed size, unpacked size (pixels + 156) [V]; blobs are JDLZ (most) or HUFF |

**The art is the same:** every PC vinyl is the X360 one box-filtered to 512² and palettised (BMWM3GTR, all 276
vinyls and masks: mean RGB difference 0.1-2.1, alpha ≤ 0.94 of 255) [V].

**Compositor differences** (from the PPC listing; the X360 build runs the PC's little-endian byte logic on
big-endian ARGB words):
1. `GetBlendColour` treats *byte 3* as alpha. On PC that's A; on X360 it's **B**. So a REMAP recolour on X360 gives
   B = max(Bᵢ·wᵢ) and A (gloss) = Σ glossᵢ·wᵢ (clamped), where PC gives B = Σ and A = max. Same result for
   pixels with one non-zero weight (84-100% of vinyl pixels [V]); different for mixed weights.
2. For edge pixels (`0 < m < 128`) X360 byte-swaps the vinyl colour before blending it with the (unswapped) target
   (`824F71D0..71EC`): A′ = B_c·t + A_t·(1−t), R′ = G_c·t + R_t·(1−t), G′ = R_c·t + G_t·(1−t), B′ = A_c·t + B_t·(1−t).
   A channel-scrambled fringe on the outer half of every anti-aliased mask edge (1-37k pixels per mask [V]).
3. The swatch-marker colours are written without the R/B flip (dead code, see above).

## 3. Plan

The port keeps the PC pipeline and swaps data under it:

1. **Vinyl source** (`runtime/vinyls.m`): index the X360 `CARS/*/VINYLS.BIN` tables (hash → file, offset, sizes) on
   first use; read a blob, decompress it natively (JDLZ `runtime/jdlz.h`, HUFF `runtime/huff.h`; the game's own
   `LZDecompress` for anything else or a blob they reject), reorder A R G B → B G R A.
   No pre-conversion (the 10,874 vinyls would be ~44 GB as DDS).
2. **Swap at `Init`** (`006DDD90`, existing hook, now in x360_textures.m): a vinyl or mask hash found in the index is created as a
   1024² A8R8G8B8 texture, after the X360 copy box-filtered to 512² is checked against the PC texture (mean RGB
   ≤ 6, alpha ≤ 8; otherwise PC is kept). `DUMMY_SKIN1..4` are created at 1024² (+ a full mip chain the compositor
   fills, so the bigger skin doesn't alias when minified).
3. **Native compositor**: `CompositeSkin32` (`0073B010`) and `DumpPreComp` (`0073B360`) are replaced by host code
   (hooks.txt) that reads every image's real size from its D3D texture, so HD and PC layers mix safely (a layer of
   another size is sampled to the target size), and runs the same arithmetic as the PC (exact, including the
   truncating float→int per term). `NFS_VINYL_BLEND=x360` reproduces the X360 byte order quirks (§2). 1M pixels per
   composite natively instead of through the recompiled code.
4. **Validation**: `BOOT_DIAG` writes every composite (`vinyls/<n>_<target>_<layer>.png`) and, for HD composites,
   the same composite at PC size from the PC layers, with the mean difference between the HD result box-filtered
   to 512² and the PC result. Then a same-camera capture (`BOOT_RACE`: AI opponents get random vinyls) with
   `NFS_X360_VINYLS=off` and on.
5. The customization shop by scripted keys (`BOOT_KEYS`, `BOOT_UNLOCKALL`, a scratch `BOOT_USERDATA`): browsing, REMAP
   vinyls with chosen colours, timings and memory (§4). Later: PREVINYL (`_PRECOM_`, 256²) X360 32-bit copies (no
   resolution gain).

Switches: `NFS_X360_VINYLS=off` (PC vinyls), `=<dir>` (another X360 `files` dir; default: the X360 root Setup recorded),
`NFS_VINYL_BLEND=pc|x360` (default pc), `NFS_VINYL_MIPS=0` (no mip chain on the HD skin), `NFS_VINYL_LZ=guest` (JDLZ
and HUFF through the game's `LZDecompress`) / `=verify` (both formats decoded both ways, compared byte for byte, every blob;
diag mode does this for the first 40 blobs of each format unless `NFS_VINYL_LZ=native`), `NFS_VINYL_PNG=0` (no composite images in diag mode, for timing runs).

## 4. Status (2026-10-20)

Done: steps 1-5 (`runtime/vinyls.m`, `runtime/jdlz.h`, `runtime/huff.h`, hooks `0073B010`, `0073B360`, the `Init` hook in `x360.m`).

- Index: 10,874 vinyl and mask textures in 43 X360 packs, 1.6-6.8 ms at the first texture `Init`.
- Career-start race (`BOOT_RACE=16.2.1 BOOT_RACE_CAR=race BOOT_RACE_AI=1`): the M3 GTR (`BMWM3GTRE46_PSRTEST`) and Razor's
  Mustang (`MUSTANGGT_01RAZORCALLAH`) vinyls and masks swap (X360 at 512² vs PC: RGB 0.01-0.61, alpha ≤ 0.36), 5.7-7.2 ms
  each (read, `LZDecompress`, check). Composites take 1.6-3.4 ms at 1024² with the mip chain. The HD composite box-filtered
  to 512² vs the PC composite from the PC layers: RGB 0.27 / 0.59, alpha 0.63 / 0.31.
- `run_shots/33`: Razor's flames in texture space, PC composite (top, upscaled) vs X360 (bottom): no palette banding, sharp
  outline. `34`: the M3 GTR livery in the race intro. `35`: same camera at 1080p (`race+900`, no autodrive, `g_RainEnable=0`), Razor's Mustang, left PC vinyls
  (`NFS_X360_VINYLS=off`), right X360: crisper flame outline and logo. `36`: `NFS_VINYL_BLEND=x360` on a mask edge (left PC arithmetic, right X360: the dark teal
  fringe of the byte-swapped blend; 4-6k pixels per composite, up to 201/765 off).
- The native compositor is byte-identical to the game's recompiled `CompositeSkin32` (`NFS_VINYL_SELFTEST=1` and `=remap`, which forces REMAP with fixed swatch colours, at PC size with `NFS_X360_VINYLS=off`: 0 differing pixels in both composites).
- The car shader samples the skin trilinearly (`filt 2/2/2`), so the mip chain is used: distant cars are filtered instead
  of point-minified as with PC's and X360's single level.

**Customization shop** (My Cars → Customize → Visual → Vinyls on the IS300, `BOOT_UNLOCKALL=1`, scratch profile with
an alias; keys: the playability sweep's My Cars prefix, then `RIGHT,RIGHT,RETURN` Visual, `RIGHT,RETURN` Vinyls, a
category with `RIGHT`/`RETURN`, vinyls with `RIGHT`/`LEFT`, `RETURN` on a REMAP vinyl opens "vinyl color", where the
arrows move the colour-1 cursor):
- Diag run (14 min, 67k frames): 342 swaps of 130 different X360 vinyls and masks (Flame, Tribal, Stripe, Race Flag,
  National Flag, Body, `CE`, Unique; 120 of them a back-and-forth through the 14 tribals, fast `wait:20` steps
  included), 229 composites, 201 of them REMAP, 13 with a custom colour 1 picked in the colour grid. One composite per
  browse step, alternating `DUMMY_SKIN1`/`DUMMY_SKIN2` (the shop double-buffers the preview skin). Every vinyl and mask
  passed the PC check; HD composite at 512² vs PC composite: RGB ≤ 0.70 (mean 0.14), alpha ≤ 0.63, recolours included
  (e.g. composite 19, orange swatch `640051D6`: 0.08 / 0.05). `run_shots/37`: Flame 3, Flame 8, Tribal 5, and Flame 8
  recoloured to colour 3, 25 (orange) and 46 (olive) of 80.
- Cost per texture with the game's `LZDecompress` (that run): read 0.08 ms, JDLZ 2.7 ms in the recompiled code (HUFF, 28%
  of the blobs: 4.1-4.5 ms), reorder 0.25 ms, PC check 3.4 ms (first time only), create 1.8 ms (`Init` + `CreateTexture`
  + upload copy); composite 1.9-2.4 ms. So ~25 ms per first-viewed vinyl (vinyl + mask + composite), ~13 ms on a revisit.
  Frames over 50 ms in that run only came from diag-only work (the composite PNG pair, ~35 ms, now written for the first
  30 composites and every 8th) or had no vinyl work at all (below).
- Faster since: **native JDLZ** (`runtime/jdlz.h`, the game's `JLZDecompress` `0064DB40` with non-overlapping chunked
  match copies): 0.38 ms per 4 MB instead of 2.5 ms. Checked against the game in the diag run (40 blobs decoded both
  ways: identical) and offline against the in-game-verified byte-by-byte decoder on **every** JDLZ blob of the 43 packs
  (`runtime/jdlz_test.c`: 9,179 blobs, 36.7 GB unpacked, 0 differ).
  The PC check box-filters with integer sums (3.4 → 0.9 ms). Since 2026-10-20 also **native HUFF** (`runtime/huff.h`,
  the game's `HUFF_decode` `0064DCA0`: canonical Huffman with an 11-bit first-level table, the clue-byte escapes for
  runs / literals / end, the running-sum variants and the game's quirk for numbers with 16+ leading zeros mirrored,
  every read and write bounded): checked offline against the game's own x86 `HUFF_decode` under Unicorn
  (`runtime/huff_test.py`) on **every** HUFF blob of the 43 packs: 1,695 blobs, 6,780 MB unpacked, 0 differ (all are type
  `0x30FB`; codes up to 15 bits; no number needs 16+ zeros, so that quirk isn't exercised by the data). 0.64 ms per 4 MB
  (`jdlz_test.c` now decodes and times every HUFF blob too). In the game (`NFS_VINYL_LZ=verify`, the shop browse
  below): 128 JDLZ and 48 HUFF blobs decoded both ways, all identical.
- Timing run (`NFS_VINYL_PNG=0 NFS_VINYL_LZ=native`, 7 min, 140 swaps, 127 composites): per JDLZ texture 3.3 ms
  (decode 0.17), per HUFF texture 5.3 ms (decode 2.9), create 1.8 ms, check 0.9 ms (first view), composite 1.8 ms. A
  browse step costs 8-13 ms; frames with vinyl work: median 19 ms, p90 25 ms, max 44 ms (whole frame, ~11 ms of which is
  the normal front-end frame). **No browse hitch over 50 ms.**
- Memory over the long browse is flat: live HD vinyls/masks 2-6 (8-24 MB: up to three vinyl + mask pairs while the shop
  swaps, back to one pair), 2 skins (10 MB with mips), resource heap back to 73.7 MB after every step (peak 90.1 MB), GPU textures
  186-191, RSS no growth (stats.log `vinyls` / `skins` columns, new).
- Recheck on main with the perf merge (`00ed05c`, sim ticks at the display's 120 Hz) and native HUFF, 2026-10-20: the same
  timing browse (`NFS_VINYL_PNG=0 NFS_VINYL_LZ=native`, 9 min, 176 swaps, 141 composites; three other sessions' game
  windows running at the same time). Per texture: JDLZ 1.9 ms median (decode 0.23), HUFF 1.8 ms (decode 0.68; was 5.3 /
  2.9; the game's own decoder now takes 0.85-1.67 ms, median 1.34, in the verify runs, after the perf merge's faster
  translated code), create 0.36 ms (was 1.8: the perf merge's faster translated code made Init's 4 MB `rep movsd` copy cheap, so
  replacing it with a native copy isn't worth a change), check 0.83 ms (first view), composite 1.9 ms. Vinyl work per
  browse frame: median 4.3 ms, p90 7.6, max 11.9; frames with vinyl work: median 8.8 ms, p90 9.8, max 17.9 (whole
  frame). Of 64k frames after loading, one over 50 ms (no vinyl work) and none over 90 ms: the ~100 ms bursts below did
  not appear. One 0.5 s stall report on entering My Cars (`get_shader` waiting for a Metal pipeline compile; not
  vinyl work, not seen in the verify run). Memory flat: live HD vinyls/masks 2-6 (8-24 MB), 2 skins (10 MB), resource
  heap back to 88 MB after each step (peak 104 MB), GPU textures 185-187.
- Not vinyl work: two 1.02 s frames (diag `stall_2/3`: `-[CAMetalLayer nextDrawable]` timing out in `render_present`;
  main has the fix, ee9e043, not on this branch) and two bursts of ~100 ms frames every 3-4 frames (2 s each, run 1 while
  idle after the script, run 2 in the Visual menu) with no vinyl, file or texture events in those frames; not
  reproducible at the same key in the other run, likely the other sessions' game windows competing for the CPU.

Next:
1. Decide the default for `NFS_VINYL_BLEND` with the user (PC arithmetic is the default: it's what the art was made for;
   the X360 quirks look like an endianness bug in the X360 port).
2. PREVINYL (`_PRECOM_`, 256² for traffic/AI precomposites): X360 is 32-bit instead of P8 at the same size; minor.
