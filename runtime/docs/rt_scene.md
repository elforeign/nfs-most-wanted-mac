# RT1 scene capture

`NFS_RT1=1` enables diagnostic rigid draw capture and Metal BLAS/TLAS builds.
The default is Off. RT1 has no visible consumer; RT2 lighting is separate.

Off returns before scene allocation, geometry reads, device capability checks,
native operations and diagnostic handlers. It adds no Metal commands or image
resources. On requires both the ray-tracing API and Apple GPU family 9 support.
Unsupported devices keep the existing raster renderer.

The successful base-draw boundary is shared by all four static/ring emissions.
Auxiliary material passes, query draws, SSAO/lamp/headlight replays, HUD and post
are excluded. Deferred or failed shaders never submit packets. The prepared
declaration is reused. Capture copies accepted position/index bytes and shader
constants before returning; replay never reads guest memory or live device state.

Admission starts with one bytecode-pinned rigid program per game: the MW world
t0 VS1.1 and the private WORLDREFLECT VS3. Both dot POSITION v0 with c0..c3.
These uploaded WVP columns are combined with the matching primary camera's
inverse row-vector VP. Raster half-pixel/Y corrections are not part of this
inverse. Affine, finite, nonsingular placements are required. The private camera
adapter and its binding manifest are separate; no temporal module is imported.
Only the primary view is captured; secondary split views and mirrors are absent.

BLAS contain packed float3 object-space positions and UInt32 triangle lists.
Source UInt16/UInt32 indices, signed bases, stream/element offsets and actual
indexed bounds are honored. Strips alternate winding; fans expand to lists;
repeated-index and zero-area triangles are removed. Points, lines, POSITIONT,
unsupported decoders and instanced D3D streams fail closed. Known skin/morph
declarations, material-tagged foliage/glass, alpha testing (including shader
texkill), blending and water are counted and skipped. Other programs increment
`unknown`; they are never inferred to be rigid.

The geometry key includes never-reused resource IDs, writable Lock/Unlock
generations, declaration/FVF, decoder, ranges and offsets. UP data are copied
and content-keyed, with byte comparison on cache hits. Opaque material repeats
of the same geometry/pose share one instance. Source Release evicts cache rows;
captured packets retain their snapshots. Reset and leaving racing clear the
scene. Unused geometry expires after 120 frames, with a 256 MiB cache admission
limit and 65,536-instance frame limit. Capacity rejections are counted.

New geometry builds BLAS; rigid motion changes only instance descriptors. TLAS
are rebuilt each frame. There is no deformation refit. AS/build/refit scratch
sizes are queried. Separate instance/TLAS/scratch storage is used for overlapping
frames. BLAS residency is declared explicitly. The native callback runs after
the frame encoder ends and before diagnostic presentation, in both direct and
recorded command paths; its resources survive GPU completion.

This is an incomplete camera-visible scene. Cached offscreen geometry is not
automatically placed in the TLAS. Complete city casters, deformation, secondary
views and any visible lighting use remain future work.

With full diagnostics enabled, `rt1.log` records per-frame capture CPU time,
cold BLAS/warm geometry counts, AS GPU ticks/time, queried memory, live retained
geometry/BLAS/frame storage and cumulative skip counts. Live frame bytes include
instances, TLAS and scratch from overlapping frames. A timestamp-counter sample
buffer is allocated only for diagnostic AS frames. GPU ticks are calibrated
against monotonic host nanoseconds across frames; `as_gpu_ms=-1` means no valid
calibration/sample yet. Cold raw ticks can be converted using the subsequent
calibration. Zero-instance frames encode no AS operation and still report skips.

`rt_scene_test.py/.m` exercise production capture/packing, both adapters and real
Metal BLAS/TLAS. Actual 16 by 16 GPU results: initial 4 by 4 occluder, 16 hits and
0/256 wrong bits; second frame after captured constants move it away, 0/256 wrong
bits. Freezing the old transform produces 16/256 wrong bits. All four combinations
of game and direct/recorded path agree. Each case builds one BLAS and two TLAS;
source clearing, Release and reset before recorded replay preserve the result.
The retained geometry is released after completion. Both Off fixtures execute
4,000 API calls with zero scene allocations/encodes. Detailed output is saved in
`.metal-cache/rt-scene-proof.txt`. The suite discovers this test automatically.

No game was launched for this implementation. Game-frame Off reference/bit
comparisons remain unverified: the available check tool runs offline tests and
does not provide a game-reference capture/comparison operation. The lead should
run the normal Off reference checks before landing.

## Solo timing runs for the lead

Run after play ends, with no concurrent builds or GPU tests. Require
`player_running.sh` exit 1 and solo ledger rows with zero other games at both
endpoints. The following Bash recipe specifies warmups, three Off/On pairs and
separate diagnostic attribution runs. It has not been executed here.

```bash
root=$PWD
parent=$(cd .. && pwd)
out=$(mktemp -d /tmp/rt1-solo.XXXXXX)
mw_reg='FirstTime=0,VERSION=1,SIZE=132,g_CarEnvironmentMapEnable=3,g_CarEnvironmentMapUpdateData=1,g_RoadReflectionEnable=3,g_MotionBlurEnable=1,g_ParticleSystemEnable=1,g_WorldLodLevel=3,g_CarLodLevel=1,g_OverBrightEnable=1,g_FSAALevel=1,g_RainEnable=1,g_TextureFiltering=2,g_RacingResolution=4,g_PerformanceLevel=5,g_VSyncOn=0,g_ShadowDetail=2,g_VisualTreatment=1'
private_reg='FirstTime=0,g_PerformanceLevel=5,g_FSAALevel=1,g_CarEnvironmentMapEnable=3,g_RoadReflectionEnable=2,g_MotionBlurEnable=1,g_ParticleSystemEnable=1,g_WorldLodLevel=3,g_CarLodLevel=1,g_RainEnable=1,g_TextureFiltering=2,g_VisualTreatment=1,g_ShadowDetail=2,g_ShaderDetailLevel=3'
mw() {
  NFS_SLOT=solo BOOT_BIN="$root/.metal-cache/rt-scene/boot_mw" \
  NFS_GAME="$parent/Need For Speed Most Wanted Black Edition" \
  sh runtime/run_guarded.sh 130 "$out/mw-$1-$2" NFS_RT1="$2" \
    RUN_GUARDED_NODIAG="$3" NFS_SIMHZ=120 NFS_DISPLAY_PIXELS=3840x2160 \
    NFS_FRAME_LIMIT=120 BOOT_RACE=15.1.1 BOOT_AUTODRIVE=1 "BOOT_REG=$mw_reg"
}
private_race() {
  NFS_SLOT=solo BOOT_BIN="$root/recomp/out/carbon/boot_carbon" \
  NFS_GAME="$parent/inputs/NFSC" \
  sh runtime/run_guarded.sh 200 "$out/private-$1-$2" NFS_RT1="$2" \
    RUN_GUARDED_NODIAG="$3" NFS_SIMHZ=120 NFS_FRAME_LIMIT=120 \
    NFS_CARBON_RES=3840x2160 NFS_CARBON_SKIPFE=1 \
    NFS_CARBON_SKIPFE_EVENT=qr.3.1:0:7 NFS_CARBON_SKIPFE_CAR=rx8 \
    BOOT_AUTODRIVE=1 "BOOT_REG=$private_reg"
}
for game in mw private_race; do
  "$game" warm 0 1
  "$game" warm 1 1
  for pair in 1 2 3; do
    "$game" "pair$pair" 0 1
    "$game" "pair$pair" 1 1
  done
  "$game" attribution 0 0
  "$game" attribution 1 0
done
```

Verify actual 3840 by 2160 targets and eight-car private race admission. Compare
equal steady-state race windows, holding route, settings, thermal state and
cache state fixed. Report session `busy_p99` and `gpu_p99` separately, and derive
the share exceeding 8.333 ms from frame records rather than per-second averages.
Attribution runs supply capture CPU, AS GPU, cold/warm counts and memory; keep
their full-diagnostic overhead separate from the timing pairs. The prototype
budgets remain at most 0.2 ms added capture and 0.5 ms warmed AS GPU work; these
are targets, not measured game results or a 4K/120 headroom claim.
