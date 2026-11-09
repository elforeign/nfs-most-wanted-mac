# Underglow road pool strength

Customize > Visual > Underglow > **Road pool: Measured/Stronger** is one global
choice for the road and garage floor. **Measured is the default**, including
missing or invalid preferences. Hover previews immediately, Back or another
exit restores the opening strength, and Accept saves `cars.underglow_pool` as
`measured` or `stronger` in USERDATA/app.txt. This choice is independent of each
car's colour and Light choice and never changes the mounted strips or save
sidecars. No F10 row is added. `NFS_UNDERGLOW_STRENGTH=measured|stronger` forces
the strength for matched diagnostic captures, prevents native edits from
changing it, and never overwrites the saved preference. Other values are ignored.
Off still submits nothing. Default Measured, Stronger for the owner's eye
(11-06 brighter/wider decision); changing the default awaits matched stills.

The aftermarket reference is [Bushdoof CX6 RGBW underbody lights, specifications](https://bushdoof.co/collections/cx6-rock-lights/products/cx6-rgbw-rock-lights-12-pack):
green **50 lx at 1 m**, emitting angle **120 degrees**. These are manufacturer
specifications, not independent installed-car measurements. Stronger adds four
smooth reference-plane kernels, one at each strip midpoint, to the existing
measured pool. We interpret the angle as a Lambertian intensity FWHM and retain
its **one-metre test-plane footprint**: each green kernel is
`E(r)=50/(1+r*r)^2 lx`, with lateral radius r in metres. This interpretation is
an optical proxy, not a fit to a supplied angular distribution. It deliberately
does not shrink that footprint to the car's small mounting height. It is not
claimed to reproduce a kit mounted under this chassis. Other colours retain
the measured strip spectrum/duties: the scalar is divided by measured green
106.075 lm/m and multiplies the existing mixed RGB. The reference's separate
RGBW spectra/white LEDs are not substituted. The support margin becomes
`tan(60 degrees)*1 m + .25 m = 1.982050808 m`, with the same .25 m edge fade.
Measured retains its original grid, radiance arithmetic and one-metre margin.

`underglow_road_test.py` samples the actual submitted Carrera GT Magenta pool
30 cm behind the rear BODY_A bound, at the wheel-datum mounting height and
horizontal road plane. Retail stationary close/far chase targets are 4.700 /
5.436 m behind the car origin, height 1.620 / 1.700 m (decoded camera spec and
`chasecam_test.py`); the test reports eye-to-patch ranges using the actual car
bounds and pose. Diffuse radiance has no additional inverse-square attenuation
to either camera. These numbers assume rho=.18, 100,000 lx sun or 10,000 lx
shade, and the documented 100 cd/m² renderer unit. Ambient illuminance values
are illustrative assumptions; the game's exposure, sunlight and display are
not calibrated by this model.

| Road-patch luminance (cd/m², before exposure) | Off | Measured | Stronger | Stronger/Off | Stronger/Measured |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sun | 5729.577951 | 5730.682948 | 5732.195477 | 1.000456844 | 1.000263935 |
| Shade | 572.957795 | 574.062792 | 575.575320 | 1.004568443 | 1.002634779 |

The added contribution is 0 / **1.104996440** / **2.617525231 cd/m²**
(Stronger/Measured **2.368809x**). At 60 cm it is 0 / .246627500 / 1.169841977
(**4.743356x**); at 1 m Measured has ended and Stronger adds .486695585 cd/m².
This predicts small daylight contrast even with a wider pool; screen appearance
still needs the matched sun/shade chase stills, with luminance measured per still.

Offline evidence: 98/98 seven-car, seven-colour, two-pose cases retain identical
Measured emitter texels, pool texels and submitted quads against the SHA-256
pinned main pool fragment; Stronger differs in 98/98. Returning from Stronger
restores the exact Measured payload. The original pool and omitted strength
cache each fail 294 checks; changed Measured units differ in all 98 cases.
Production passes 24,108,402 checks. Measured and Stronger colour names each
pass 16/16; the prior colour chain fails 10/16 for each. Both strengths pass
124/124 player models and 14/14 replacements (5,658,694 checks each).
The native menu passes 82/82 plus seven preference/override startup cases;
Back-retains-preview fails two, missing Accept persistence fails four.

The Metal check uploads the production pool and executes `render.m`'s production
depth-state function against synthetic opaque body/wheel depth silhouettes:
**0/1006 forbidden pixels** change for either strength; disabling depth lights
1006 for each (2012 failures). Visible lit pixels are 2938 Measured / 3038
Stronger in that fixture. The pool remains on the same ground+.002 plane,
with the same ePoly blend/depth path and ground-decal tag. This proves depth
occlusion in that fixture; general source visibility and uneven road/body
intersections remain the limitations described above, pending game evidence.

Work stays the same kind: two reused textures, 256x160 pool texels, four strips
and one pool draw, no new per-frame allocation. Each draw adds a cached strength
read and cache-key comparison. On an existing pool refresh, Stronger additionally
evaluates four rational kernels per texel (163,840 evaluations) and adds three
RGB products. There are no new segment integrals, colour solves or GPU passes.
Switching strengths refreshes the existing texture once; an unchanged frame
does no kernel work. The wider quad can cover more fragments, so this is not
a measured claim of equal GPU time. A solo frame-cost check remains pending.
