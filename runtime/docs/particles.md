# Particles

F10 World > **Particles: Retail / Physical / PC / X360**,
`NFS_PARTICLES=retail|physical|pc|x360`, applies live. Retail is the default,
using the X360 shading and size clamp when its data is available. PC and X360
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
Rain streaks and additive sparks retain their authored lighting. These are
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

Shared diagnostic run: nine held Off/On pairs at sim 6, 15 and 25 s give median
GPU pass-sum deltas +0.464 / -0.243 / +0.107 ms; all-pair median +0.107 ms.
The smoke-heavy frame keeps 47 non-rain particle batches in both modes, with
48 batches (including rain) replayed into one Physical layer. The first Physical
pair is cold (+6.768 ms); it remains in these statistics. Retail particles share
the main scene encoder, so their GPU cost is not isolated from world geometry.
The Physical particle encoder median is 6.017 ms at that 4K burnout, replacing
the original particle work. These are diagnostics, not solo timing acceptance.
