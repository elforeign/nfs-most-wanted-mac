// Matched sign sampling experiment. Every hook is guarded by sign_diag_on.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include "runtime.h"
extern int sign_diag_on;
int sign_diag_active(void);
int sign_diag_hold(void);
void sign_diag_world(Cpu *c);
int sign_diag_selected(uint32_t hash, const char *name);
void sign_diag_texture(uint32_t ti, uint32_t pi);
int sign_diag_canonical(unsigned stage);
void sign_diag_freeze(const char *name, void *bytes, size_t size);
void sign_diag_record_history(const char *name, const void *bytes, size_t size);
#ifdef __OBJC__
#import <Metal/Metal.h>
#include "../host/shaders/sm_translate.h"
id<MTLTexture> sign_diag_texture_view(uint32_t guest, id<MTLTexture> full);
id<MTLFunction> sign_diag_direct(uint64_t key, const char *source);
void sign_diag_pipeline(uint64_t key, MTLRenderPipelineDescriptor *descriptor, const char *source);
id<MTLRenderCommandEncoder> sign_diag_tee(id<MTLRenderCommandEncoder> encoder, uint64_t key,
    id<MTLTexture> color, id<MTLTexture> depth, id<MTLDepthStencilState> readonly_depth);
void sign_diag_replay(id<MTLRenderCommandEncoder> tee);
void sign_diag_draw(uint64_t vs, uint64_t ps, unsigned type, unsigned prims, int indexed,
    int32_t base, unsigned start, const SmResult *vsinfo, const SmResult *psinfo);
void sign_diag_scene(id<MTLCommandBuffer> cb, id<MTLTexture> scene);
void sign_diag_present(id<MTLCommandBuffer> cb, id<MTLTexture> final);
void sign_diag_finish_present(id<MTLCommandBuffer> cb);
#endif

/*
## Matched sign mip-tail experiment

`BOOT_DIAG_SIGN_AB=1` enables a manually armed diagnostic. Unset, it makes no
captures or changes to time, camera, textures, shaders, or samplers. It does not
select a bank camera automatically. Position the camera so the bank on the right,
uphill street on the left, ticker, and the REF_NEONS dots/strips/outline are all
visible. Use the supplied bank-intersection screenshot as the framing reference;
there is no verified numeric camera preset. The baseline tail completion is an
ordinary upload fix and needs no diagnostic switch.

After reviewing/building the checkout, the guarded launch is:

```sh
NFS_ENV_ROOT="$PWD"
. runtime/env.sh
runtime/run_guarded.sh 600 "$NFS_PARENT/.runs/sign-tail-ab" \
  BOOT_RACE=15.1.1 BOOT_RACE_CAR=gallardo BOOT_RACE_AI=0 BOOT_RACE_COPS=0 \
  BOOT_RACE_TRAFFIC=0 NFS_DISPLAY_PIXELS=3840x2160 NFS_SIMHZ=60 NFS_LOCKSTEP=1 \
  NFS_POSTFX=x360 BOOT_REG=g_RacingResolution=4,g_FSAALevel=1,g_TextureFiltering=2 \
  BOOT_DIAG_SIGN_AB=1
```

Once positioned, create or touch `arm` inside that launch's BOOT_DIAG directory (stale arm files from earlier launches are ignored). The
harness records C0's full camera, FOV, view/projection matrices, quality drop,
selected draw identities, guest texture IDs, shader keys, requested/effective
sampling, per-level GPU counts, and complete VS/PS/transform constants. It checks
actual 3840×2160 and 2× MSAA, rather than trusting launch settings. It holds real
and simulation time, freezes named UV/time/animation constants, retains camera
and lighting constants, and restores the same adaptation/measurement feedback
before each image. The captured history blobs and constants remain beside the
frames for independent checking. Unnamed frame-driven animation is detected by
stationary and matched-constant checks; unsupported evidence is rejected.

Each variant captures eight stationary control frames, the same 64 yaw poses
`−0.25° + i×0.5°/63`, then the same retreat along C0's viewing axis. A determines
the retreat endpoint when the measured unclamped ticker LOD exceeds the original
terminal mip by 0.25 (96-frame limit); B/C/D repeat its exact endpoint and path.
The FOV and initial history stay fixed throughout. These are rendered camera
poses, not changes to the car's physical position.

- A: selected HD sign texture views expose only retail Init's original safe prefix.
- B: complete source DDS chains with the requested production sampling/shaders.
- C: B with canonical trilinear/16× sampling, minimum clamp zero, sampler bias zero.
- D: B with the actual diffuse RGB sent directly to colour output, retaining
  its original UV calculation, sampling expression, output alpha, blend, alpha
  test, coverage, and kill.

PC REF_NEONS draws remain unchanged controls. Selection uses both sign hashes
and texture names locally; `BOOT_DIAG_UI_TEXTURES` still accepts one hash. The
instrumentation recognizes one translated diffuse sample at stage 0 and rejects
multiple/unsupported sample layouts (including supplied gradients), material replacements, MRT/query draws,
missing subjects, allocation/capture failures, or changed draw identities. The
original and instrumented shader source are evidence, not an assumption about
which UV varying the sign uses. UV/LOD evidence replays the selected draw's actual
bindings against read-only depth, into a separate float target; it cannot write
scene colour, depth, or stencil. Edge samples with mixed MSAA coverage are omitted.

For every image, `sign_<variant>_<phase>_<index>_scene.png` is the native-resolution
scene before post processing and `_final.png` is the final output. Compressed
`_samples.z` records native pixel positions, plane IDs, exact sampled UVs,
unclamped hardware LOD (including the actual shader/sampler bias), or explicit
requested LOD, and the derivative minor-axis
footprint, plus unclipped pre-post luminance (including float scene values above
1; the PNG is an SDR preview). `sign_manifest.txt` ends with `CAPTURE_COMPLETE` or `REJECTED` and a
reason. Capture is synchronous and bounded to one frame's readbacks; do not use
this run for timing. It can write substantial image data.

Analyze only a completed capture with the repository's configured Python:

```sh
"$TOOLS_PY" -I runtime/sign_diag_test.py --analyze "$NFS_PARENT/.runs/sign-tail-ab"
```

Analysis requires the exact same camera/view/projection, clock, FOV, selected
constants and frame paths across A/B/C/D; both signs and neon controls must be
present. It fits each sign plane from the shader UVs (p95 reprojection error under
0.35 native pixels), retains common interior support, and exports native ROIs,
registered luminance arrays, `metrics.csv`, and `metrics.json` under
`sign_analysis/`. Metrics include temporal luminance residual mean/p95,
changed-pixel fraction above 1/255, mean light, spatial contrast, and LOD
p50/p95/max with the fraction beyond the original terminal mip. Stationary drift
and changed pre-post neon controls reject the comparison. Review the native
frames against the required subjects/framing as well as these numeric gates.

B improving A where LOD reaches the discarded tail implicates the upload cutoff.
C improving B implicates sampling state. D improving B warrants shader
investigation. Compare mean light/contrast as well as residuals: making a sign
darker or blurrier is not by itself a temporal-quality improvement. If the C0 yaw
footprint never reaches the old terminal mip, tail completion cannot explain its
sparkle there. No visual benefit, numeric C0, or successful game capture is
established by offline checks alone. PC mip generation, positive bias, and
supersampling remain separate appearance experiments and are not enabled here.

Offline: `hd_mip_tail_test.py` executes the production swap, translated Init,
production resource release and `gpu_tex`; it pins ORIGINAL `13984ab7` by SHA-256
and demonstrates 4/10 and 5/10 failing against complete DDS chains. Drops 0/1/2,
byte equality through 1×1, malformed fallback, allocation rollback, repeated
release, and existing one-level UI policy are checked. `sign_diag_test.py` checks
the production pose math, matched capture scheduler (292 frames; unreachable-tail
control rejected at 168), and shader instrumentation, including real dependent
UV/direct-colour/LOD Metal readback and deliberately wrong endpoint, ambiguous
sample, nonplanar registration, and flicker controls. Both are discovered by the
whole offline suite. The private DDS test exits 3 when its fixtures are absent.
*/
