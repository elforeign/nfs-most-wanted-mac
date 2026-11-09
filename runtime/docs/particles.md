# Particles

F10 World > **Particles: Retail / Physical / PC / X360**,
`NFS_PARTICLES=retail|physical|pc|x360`, applies live. Physical is the default
(a saved Retail, PC or X360 stays). Retail uses the X360 shading and size clamp when its data is available. PC and X360
retain their original shading and size behavior in saved settings, environment
overrides and live changes. Physical adds fading and lighting to the X360 style.
PC/X360 look presets restore their respective style with Physical disabled.

Physical uses the existing textures and vertex colour/alpha. Standard alpha
blended smoke, dust and spray get a 0.6 m fade against linear opaque scene depth,
with the existing near-camera exception. Opaque depth uses the nearest MSAA
sample. Native post replays ordered particles into a 1x layer and composes it
with the existing AO pass; the fallback snapshots depth before particle draws.
Both split-screen player views use that fallback, each with its own viewport,
inverse view-projection and depth camera. Shadowless t0 uses fully lit shadow
visibility; t1/t2 retain their existing shadow samples.
Rain streaks and additive sparks retain their authored lighting. The car ground shadow
(CARSHADOW strip, `decal_depth.m`) uses the particle shader but lies on the road, so it skips the depth fade;
check `runtime/carshadow_check.sh <dir>` (one guarded race; the old code fails it, 17.3 levels). These are
cards with single scattering, without volume density or multiple scattering.

The shader uses the world's current ambient and direct sun inputs, including
the Neutral lighting adjustment. The existing particle shadow visibility gates
only the direct sun term. A normalized Henyey–Greenstein phase function uses
the incoming light direction and outgoing camera ray; its spherical integral
is 1 and first moment is g. The renderer uses 4π times that function because
the game's direct colour is normalized relative to its isotropic baseline.
Scene lighting changes RGB only; depth fading changes alpha.

Up to eight current-frame front lamp submissions provide world positions,
directions and effective lamp state before camera-facing flare attenuation.
The two front marker keys are identified by their left/right headlight state
branches in the game's car flare renderer. Mirror/reflection/environment views,
stale lamps and other flare types are excluded. Headlights use the same phase
function, a white emitter, a 15°–35° smooth cone, an 8 m attenuation scale and
60 m range. These beam parameters and the white emitter are approximations,
not photometric measurements. Headlight occlusion is not implemented; the
world shadow map describes sun visibility only.

Constants: **g=0.682, single-scattering albedo=0.93** form a mineral aerosol
proxy. The 550 nm laboratory asymmetry measurement for Arizona Road Dust is
0.682 ± 0.019 in
[Curtis et al., 2008, table 4](https://doi.org/10.1029/2007JD009387).
The green-wavelength ambient pure-dust albedo median of 0.93 is from
[Teri et al., 2025, section 3](https://acp.copernicus.org/articles/25/6633/2025/index.html).
Combining those different sample populations is an approximation, not a
measurement of this road's dust or of tyre smoke. The same optical proxy covers
all eligible aerosol batches; emitter groups identify tyre smoke upstream, but
their identities are lost at these shared shader draws. No tyre-specific
g/albedo calibration is claimed.
[Bennett et al., 2011](https://eodg.atm.ox.ac.uk/eodg/papers/2011Peters1.pdf)
measured aircraft landing smoke and found mixed coarse material and fine
thermal-degradation aerosol; that paper does not supply a transferable
tyre-smoke g/albedo pair. HG itself is an approximation; measured smoke phase
functions can differ from it, as discussed by
[Ahern et al., 2025, section 3.1](https://doi.org/10.1029/2024JD042091).

`NFS_SOFT_SMOKE=off` retains the diagnostic bypass, but cannot enable Physical
under Retail. Held `BOOT_DIAG_LOOK_AB=softoff,softon,softbroken` captures Retail,
Physical, and Physical with depth fading disabled. `BOOT_DIAG_SOFT_SMOKE=1`
adds same-command raw scene proof images, including an unfaded control and a
lighting-disabled control; exclude that flag from cost runs.
`BOOT_DIAG_SOFT_SMOKE_COST=1` with GPU pass diagnostics adds batch counts,
scene light inputs, lamp counts and held-frame pass costs. Diagnostics are
inactive outside BOOT_DIAG.

Offline proof: `particles_physical_test.py` executes the production phase
function, selector, inverse transform and lamp capture contracts, plus native
Metal lighting. `soft_smoke_test.py` compiles all six actual shader variants,
checks 90 fade cases, and executes the actual draw selector. Broken phase,
stale-lamp, inverse and Retail-fade controls must fail. `particles_review_test.py`
checks saved style compatibility, viewport reconstruction, P2 draw selection
and shadowless t0, with all four regressions failing on the pinned original.
Mirror/reflection aerosol lighting and calibrated tyre optics remain unproven.

Captured proof: [Retail/Physical and lighting control](../../run_shots/physical_particles_retail_physical.jpg)
and [depth control](../../run_shots/physical_particles_depth.jpg). At the captured
start-line burnout, the fixed road/car contact-gradient counts fall by 8.11% /
28.33%; the lighting-disabled control differs at 259,808 raw pixels by more than
3 levels. These are scene-specific observations, not moving-camera acceptance.

[Shadowless split-screen comparison](../../run_shots/physical_particles_review_split.jpg)
shows both player views at a held frame with Physical Off, On and lighting
disabled while fading remains enabled.

Shared diagnostic run: nine held Off/On pairs at sim 6, 15 and 25 s give an all-pair median GPU pass-sum delta of
+0.107 ms (first pair cold, +6.768 ms). Retail particles share the main scene encoder, so their cost is not isolated.

**Half-resolution smoke** (default; `NFS_SOFT_SMOKE_HALF=0` selects the full-resolution layer for A/B;
`soft_smoke_half.m`): off-screen particles after Cantlay, "High-Speed, Off-Screen Particles", GPU Gems 3
ch. 23. The late lists replay into a layer of half the width and height (viewports and scissors scaled at replay,
`rt_render_list_replay_scaled`) against a half-size copy of the opaque depth keeping the nearest of each 2x2. The AO
multiply (without AO, its own pass) blends it back with nearest-depth upsampling: bilinear where the four texels
lie within 10 % of the pixel's view depth, else the texel nearest in depth. Thin rain streaks in the layer soften.
`soft_smoke_half_test.m` runs the production downsample and upsample on a 64x32 edge fixture: 0 wrong pixels;
plain bilinear 97 (holes), farthest depth + bilinear 92 (halo). `BOOT_DIAG_SOFT_SMOKE_HALF_AB=1` (with
`BOOT_DIAG_GPUPASS=1`) replays each frame's lists again at the other size and once with the depth fade off.

Cost at the race 15.1.1 start burnout (3840x2160, 2x AA, the same draws in one frame): full layer 4.90 ms, full
with the fade off 4.90 ms, half layer 1.27 ms + depth copy 0.04 ms + upsample in the AO pass 0.07 ms = 1.38 ms
(28 %). The pass is fill-bound; the depth fade costs nothing measurable. Busiest seconds: 71-72 fps (max 15.1 ms)
full, 96-102 fps (max 10.8 ms) half. Same held frame: [half](../../run_shots/softsmoke_lowres_on.jpg),
[full](../../run_shots/softsmoke_lowres_off.jpg).
