# Cubemap mip generation

`NFS_CUBE_BLITMIPS` defaults to **1**. A native Metal mip blit replaces the
software downsampler's 60 render encoders per 1024² reflection cube generation.
`NFS_CUBE_BLITMIPS=0` selects the software reference. The capture cadence, base
faces, 11 levels and car sampler stay the same. The PC reflection option still
uses its original 256² single-level cube.

The render-thread recorder retains the texture in `OP_MIPMAPS` and replays
`generateMipmapsForTexture:` between preceding capture writes and subsequent
receiver draws on the same command buffer. The native path does not create the
reference shader, pipeline or 60 source views unless a comparison needs them.
The affected D3D format is A8R8G8B8 (21), stored as Metal BGRA8Unorm (80).

## Encoder premise and result

BOOT_DIAG race 15.1.1, autodrive:

| Path | Recorded mip render encoders | Recorded mip blit encoders | Complete frame records | Stale samples |
| --- | ---: | ---: | ---: | ---: |
| Software, `cubemip-premise` | 60 | 0 | 2,897 | 0 |
| Native, `cubemip-day-native` | 0 | 1 | 2,692 | 0 |
| Native default, `cubemip-default-dark` | 0 | 1 | 2,374 | 0 |

These are operation counts, not timing measurements. Each lower level/face in
`envcube_generate` opens exactly one render encoder: 10 × 6 = 60. The native
generator opens one blit encoder for the complete cube. A/B screenshot passes
temporarily use the other path. Selected comparison frames add diagnostic work;
`envcube: comparison` reports its extra 120 render encoders and three blit
encoders separately. The ordinary frame counters describe the selected mip
generator, excluding that diagnostic work.

## Pixel proof

[Per-face/mip maxima](cubemip-differences.csv) include every channel of every
texel, with six faces and levels 0–10 for each condition. Each capture compares
8,388,606 texels. Both generators consume the identical held base faces, in
order on one command buffer. Readbacks are encoded in that order; CPU
comparison runs after GPU completion.

| Condition | Capture frames | Face/mip results | Maximum code-value difference |
| --- | --- | ---: | ---: |
| Day, rain drawing disabled | 894, 1374, 1974 | 198 | 0 |
| Day, rain enabled | 870, 902, 1384, 1986 | 264 | 0 |
| Sunset, phase 0.95 | 900, 1382, 1984 | 198 | 0 |
| Night diagnostic, phase 0.95 / sun −20° | 896, 1378, 1980 | 198 | 0 |

The last run leaves `NFS_CUBE_BLITMIPS` unset, confirming the default. Its CSV
headers report completed status 4, format 80, 1024², software/native coverage
60/60, phase 0.950000 and wetness 0.997595 at frame 1378. All 858 live face/mip
results match exactly. Night here means a controlled below-horizon sun test:
the game's phase-0.95 lighting alone gives a +7.7° sunset, not an authored night
scene. The offline matrix additionally checks dark synthetic input values.
The attempted menu-driven phase run cancelled its edit and is excluded.

The current comparator poisons every lower level after the reference readback
and before native generation. The final night diagnostic uses this hardened
comparator; earlier capture rows precede that addition. Omitting the replay
opcode now fails the exhaustive comparison at max difference 252 instead of
inheriting correct reference mips and falsely reporting zero.

[Labelled car comparison](../../run_shots/envcube_blitmips.jpg): software on the
left, native on the right, with rear-deck crops, from the rain run at sim 12 s.
Both simulation and all six captured base faces are held for the second pass.
Rain particles and post-processing may still differ between render passes;
the cube readbacks, rather than the final screen pixels, determine equality.
The JPEG is 1600 × 886 and 223,964 bytes.

## Diagnostics and checks

- `BOOT_DIAG_CUBEMIPS=sim+12,sim+20,sim+30` selects comparison frames using the
  existing frame-list syntax. Files are `cubemips_<frame>_<cube>.csv` under the
  diagnostic directory, with completion status, format, coverage and phase.
- `BOOT_DIAG_CUBEMIPS_AB=1` with `BOOT_DIAG_SIMSHOTS=12,20,30` adds the held
  screenshot pass, named `_softwaremips.png` or `_blitmips.png`. It uses one
  hook each in presentation and pacing.
- `NFS_TEST_CUBEMIPS_PHASE=0.05..0.95`, only with BOOT_DIAG, fixes the game's
  phase by the exact write performed by `SetTimeOfDay` (007696B0). The night
  diagnostic also uses the existing `NFS_TEST_SUN_ELEVATION=-20`.
- `envcube_test` runs CPU and GPU direct/replay paths through the production
  generators and recorder. Main/P1/P2 use 180 software encoders versus three
  native blits. PC guest/device bytes and scoped sampler restoration pass.
- Every lower texel is checked after two different base-level writes. Normal:
  0/60 bad mip faces; omitted generation: 60/60, exit 1; one poisoned level/face:
  1/60, exit 1; omitted native replay opcode: 60/60, exit 1. The missing opcode
  also fails the poisoned full-image comparison at 252. Default-off and old
  PC-default mutations each exit 1.
- Synthetic day, dark and rain-stripe inputs compare every face/mip under both
  direct encoding and replay: all six matrices have maximum difference 0,
  within the one-code-value acceptance limit. Logs remain under
  `.metal-cache/cubemip-proof/patterns-replay{0,1}.txt`.

Build passed. Targeted `run_tests` passed `envcube_test` and `switches_test`,
including the GPU matrix. Final whole-suite `run_tests` result:
The whole offline suite passes, including the cubemap GPU tests.

An earlier full suite hit the unrelated relay load check at 57.719 ms versus
its 50 ms tick budget while builds and game captures overlapped it. The final
run had no overlapping builds or game captures from this job and passed that
check. A solo timing run and the integration game gate remain separate checks.
