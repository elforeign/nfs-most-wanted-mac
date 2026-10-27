# D3D9 shader bytecode → Metal Shading Language

`port/host/shaders` translates Direct3D 9 shader bytecode (vs_1_1 to vs_3_0, ps_1_1 to ps_3_0) into MSL for the Metal D3D9 host layer. The host calls it from `IDirect3DDevice9::CreateVertexShader` and `CreatePixelShader`. It's our own C++17 code with a plain C API and no third-party shader code. The bytecode format follows the public D3D9 shader-token documentation.

All 108 shaders of the game's 31 fx_2_0 effects translate. Every output compiles with `xcrun -sdk macosx metal -c` without errors or warnings. Every shader matches a CPU reference interpreter on the GPU, and all 71 effect passes render pixel-exact end to end (see [Test results](#test-results)).

| File | What it is |
|---|---|
| `sm_translate.h` | C API, binding contract, aux structs, options, reflection |
| `sm_ir.h`, `sm_parse.cpp` | token decoder, CTAB reader, disassembler |
| `sm_msl.cpp` | MSL emitter |
| `build.sh` | builds `build/libsmtranslate.a` and all tests, then runs them (about 40 s) |
| `tests/` | checks against the reference disassembly, CPU reference interpreter (`sm_interp.*`), Metal numeric, link and render checks, C API smoke test |
| `tools/compile_all.sh` | runs `xcrun metal -c` on every generated `.metal` |
                                                                                                                                                                              
                                                                                                                                        

`testdata_synth/` (16 synthetic shaders for features the game doesn't use, with their reference disassembly) is committed.
`testdata/` (the game's own shaders) is not part of the source; the game-shader checks run only where it is present.
                
                                                                                                                                                     
                                                                                                               
                                
                 

## API

```c
SmOptions opt; sm_default_options(&opt);           // set per-state options, see below
SmResult r;
if (sm_translate(tokens, ntokens, &opt, &r) != 0) log("%s", r.error);
else { /* newLibraryWithSource(r.msl), function r.entry ("sm_main") */ }
sm_result_free(&r);

char *txt = sm_disassemble(tokens, n);              // fxc-style listing, free()
size_t len = sm_token_count(tokens, max);           // bytecode length up to and including END
```

**Reflection** (`SmResult`):
- `float_used[8]`: the c# registers the shader reads (bitmask). `float_rel` and `float_max` cover relative addressing. `float_defined` lists the c# registers the shader overrides with `def`. `int_used` and `bool_used` do the same for i# and b#.
- `inputs[]`: for a VS, each `dcl`'d v# with usage, index and mask. Register v<n> is `[[attribute(n)]]`. For a ps_3_0, each v# with its usage.
- `samplers[]`: s#, `SM_TEX_2D/CUBE/3D`, and whether it's a shadow sampler.
- `varyings_read` (PS) and `varyings_written` (VS): bitmasks of `SM_VARY_*`.
- `color_outputs`, `writes_depth`, `uses_kill`, `n_instructions`.
- `constants[]`: the CTAB entries (D3DX names, register set, register, count, type).

### Binding contract

| | Vertex function `sm_main` | Fragment function `sm_main` |
|---|---|---|
| float constants | `buffer(16)` `float4 c[256]` (upload SetVertexShaderConstantF as is) | `buffer(0)` `float4 c[256]` |
| int / bool constants | `buffer(17)` `int4 i[16]`, `buffer(18)` `int b[16]` (BOOL) | `buffer(1)`, `buffer(2)` |
| aux | `buffer(19)` `SmVSAux` | `buffer(3)` `SmPSAux` |
| textures | `texture(n)`/`sampler(n)` = D3DVERTEXTEXTURESAMPLERn (vs_3_0 `texldl`) | `texture(n)`/`sampler(n)` = s#, or texture stage n for ps_1_x |
| vertex streams | buffers 0..15, through the `MTLVertexDescriptor` | |

Constant buffers are always 256 entries. Reads out of range through `a0`/`aL` return 0. Bind every buffer the reflection says is used; unused arguments are inactive.

**Varyings** are one canonical set, so any VS links with any PS: position, `color0`, `color1`, `texcoord0`..`texcoord7`, `fog` (all `float4`) and `[[point_size]]`. The VS always writes all of them: unwritten ones are (0,0,0,0), fog is (1,0,0,0). The PS declares only the ones it reads. vs_1/2 outputs oD#/oT#/oFog/oPts and vs_3_0 `o#` with color/texcoord/fog/psize usages map onto this set, as do ps_1/2 v#/t# inputs and ps_3_0 inputs by usage. The host's fixed-function uber-shaders must output the same names.

**`SmVSAux`**:
- `pos_fixup = (+1/vp_width, −1/vp_height, 0, 0)`: the D3D9 pixel-centre half-pixel offset, applied as `clip.xy += pos_fixup.xy * clip.w`. The render test checks it.
- `point_size`: D3DRS_POINTSIZE, used when the shader doesn't write oPts.

**`SmPSAux`**:
- `alpha_func`: D3DCMPFUNC, 0 = off. Alpha test compares `round(saturate(oC0.a)·255)` with `round(alpha_ref·255)`, and `alpha_ref` = ALPHAREF/255. The game's effects drive ALPHATESTENABLE from a parameter, so it has to be dynamic.
- `bumpenv[8]`, `bumplum[8]`: BUMPENVMAT00/01/10/11 and LSCALE/LOFFSET per stage, for texbem/texbeml/bem.

texkill and the alpha test both `discard_fragment()` after the body has run.

### Options (part of the shader cache key)

| Option | Use |
|---|---|
| `sampler_type[16]` | ps_1_x stage dimensionality. Default: the CTAB sampler type, so the car `envmap_sampler` becomes a cube, otherwise 2D. Overrides the `dcl` for ps_2_0+ when set. |
| `tex_projected[8]` | ps_1_1..1_3 `tex` with D3DTTFF_PROJECTED: 4 = divide by w, 3 = divide by z |
| `shadow_samplers` | bit s = s# is bound to a depth texture (NVIDIA-style hardware shadow map, used by the `world_reflect_shadowhw` technique). Emits `depth2d` + `sample_compare(uv, z)` (divided by w for texldp). The host must create the sampler with `compareFunction = LessEqual`. |
| `vs_input_int`, `vs_input_uint` | attributes fed as SHORT2/4 or UBYTE4. Metal can't convert integer formats to float attributes, so they're declared `int4`/`uint4` and converted. D3DCOLOR is `UChar4Normalized_BGRA` and needs nothing. |
| `ieee_mul` | 1 = plain IEEE multiply (see below). Default 0. |
| `ps1_clamp_consts` | default 1: ps_1_x constant reads are clamped to [−1,1] |
| `emit_test_kernel` | also emits `kernel sm_test_kernel`, used by the tests |

Generated code layout: helper functions, plain register structs, `static void sm_body(...)` (the shader itself, one `{ }` block per instruction with the disassembly as a comment), then the stage wrapper `sm_main`.

## Semantics implemented

**Instructions:**
- Every SM1–3 arithmetic op: mov, add, sub, mad, mul, rcp, rsq, dp2add/dp3/dp4, min, max, slt, sge, exp, log, expp, logp, lit, dst, lrp, frc, m4x4/m4x3/m3x4/m3x3/m3x2, pow, crs, sgn, abs, nrm, sincos, cmp, cnd, mova, dsx/dsy, setp.
- Flow control: if/ifc/if-pred, else, endif, rep, loop/aL, break/breakc/breakp, defb/defi/def.
- ps_1_x texture ops: tex, texcoord, texkill, texbem, texbeml, texreg2ar/gb/rgb, texm3x2pad/tex/depth, texm3x3pad/tex/spec/vspec, texm3x3, texdp3, texdp3tex, texdepth. ps_1_4: texld/texcrd with `_dz`/`_dw`, bem, phase.
- texld, texldp, texldb, texldl and texldd for 2D, cube and volume textures, with sampler swizzles.

**Modifiers and registers:**
- Source modifiers: neg, bias, bx2, comp, x2, abs, not.
- Result modifiers: shifts x2/x4/x8/d2/d4/d8, then `_sat`. `_pp` is computed at full precision.
- Co-issue (`+`): every source of the pair is read before any write.
- Predicated instructions (per-component masks).
- Relative addressing: `c[a0.x + n]` (vs_1_1), `cN[a0.c]`/`cN[aL]` (vs_2+), `v[aL]` (ps_3_0), `o[aL]` (vs_3_0). Def'd constants still win over the buffer when indexed.
- vPos (D3D pixel-centre coordinates) and vFace, MRT oC0–3, oDepth.

**D3D9 float behaviour:**
- **Legacy multiply**: `0 * anything = 0`, including inf and NaN, in mul, mad, dp*, nrm, lit and dst. D3D9 content relies on it, e.g. `rsq(0) = inf` times a zero vector. Set `ieee_mul` to turn it off.
- `rcp(0) = +inf`. `rsq` and `log` take `|x|`: `rsq(0) = +inf`, `log(0) = −inf`. `pow` is `|x|^y` with `pow(x, 0) = 1`.
- vs_1_x `expp`/`logp` give the 4-component partial forms (x = 2^floor, y = fraction, ...); in SM2+ they behave as exp/log.
- `min` = `a < b ? a : b` and `max` = `a >= b ? a : b`, the D3D order for NaN operands.
- Scalar ops use the `.w` of the swizzled source, which is the replicate component.
- `mov a0` floors in vs_1_1. `mova` rounds to nearest in vs_2+.
- vs_1/2 colour outputs saturate. ps_1/2 colour inputs saturate. ps_1_x `texcoord` saturates and sets w = 1.
- `texkill` checks xyz of the texture coordinate in ps_1_1..1_3, xyz in ps_1_4, and the write mask in SM2+.
- `cnd` uses r0.a replicated before ps_1_4 and per component from ps_1_4 on.

## Test results

Run `./build.sh` on the measured host (macOS 27, Metal toolchain 32023)                                                . It reports:

| Check | Result |
|---|---|
| Decoder vs the reference disassembler (`tools/scripts/extract_pc_effects.py`, an independent decoder), line by line | 108/108 identical, synthetic 16/16 |
| Translation | 108/108, synthetic 16/16 |
| `xcrun -sdk macosx metal -c` (host and test-kernel variants) | 216/216 and 32/32 compile, 0 errors, 0 warnings |
| GPU vs CPU reference interpreter, 3 rounds × 1024 random invocations per shader, all outputs | 108/108 match in fast, relaxed and safe math modes; synthetic 16/16 |
| Same with every 2D sampler as a Depth32Float shadow map (`--shadow`) | 108/108 |
| Real `MTLRenderPipelineState` for each effect pass's VS+PS | 71/71 link. Every varying a PS reads is written by its VS. 16 fixed-function passes skipped. |
| End to end: one point per pixel through the real vertex and fragment functions, 64×64 RGBA32F, alpha test on and off | 71/71 passes, 581,632 pixels match. This covers attribute fetch, constant buffers, the half-pixel fix-up, varyings, texkill and alpha-test discard. |

**How the numeric check works.** `numeric_check` runs the generated `sm_body` through a compute kernel on random inputs and textures (RGBA32F with nearest sampling, cube and 3D included). Inputs are sensible per usage: positions, unit normals, 0..15 bone indices. Constants go past [−1,1] so the ps_1_x clamp is exercised. Round 0 injects exact zeros, which drive rsq(0), rcp(0) and 0·inf. Each result is compared with `sm_interp.cpp` within a relative tolerance of 2e-3 (fast math) or 1e-3. When the two disagree, the value is accepted only if the reference itself is ill-conditioned: re-running it with transcendental and dot-product results nudged by ±8 ulp must bracket the GPU value. This happened once, in worldreflect t0 ps_2_0: a near-zero `dp3` of almost orthogonal vectors feeds `pow` with a random negative exponent.

**Mutation testing.** Deliberate translator bugs are caught by the suite: `_bx2`, `_d2`, `_x2`, texldp's divide, `expp`, `texreg2ar` swizzle, `cmp` ≥ vs >, `lrp` operand order, legacy multiply, oD saturation, the ps_1_x constant clamp, `rsq |x|`, the `lit` exponent, swizzle decoding, and the shadow-compare coordinate. Some features can't fail on the game's shaders, and the suite says so rather than hiding it: co-issued pairs never read their partner's result, a0 indices are exact integers, and no inputs are NaN. The game never uses `_bias`; the synthetic suite does.

**Spot checks by hand** of generated MSL against the .asm:
- **fe vs_1_1 / ps_1_1:** oPos from the WorldViewProj rows, oT0 = v2 + TextureOffset, saturated oD0. The `add r0.xyz` / `+mov r0.w` co-issue pair reads before it writes.
- **visualtreatment t0 ps_2_0:** four 2D samplers, the dependent spline lookup `texld r2, r0, s3`, def c7 baked in, partial-mask reads.
- **worldbone vs_1_1:** expp `.y` = frac, a0 floored, `c[a0.x+n]` through `sm_cload` with c82/c83 def'd.
- **screenfilter ps_1_4:** phase 1/2, dependent texld from r#, `lrp`.
- **car ps_1_1:** stage 0 is a cube from the CTAB, `texcoord` saturation.

Each matches the .asm line by line.

**Common-mode limit.** The interpreter and the emitter share the semantic choices listed above, so the numeric checks prove the MSL does what the bytecode means *under those choices*. They don't prove the choices match real D3D9 hardware; no Windows machine was available. The choices follow the D3D9 documentation. The ones most worth checking against a real capture are `mov a0` rounding in vs_1_1, the legacy multiply, the ps_1_x texture-matrix ops, and ps_1_4 phase-2 alpha behaviour.

## Known gaps

**Not supported** (translation fails with a clear error): subroutines (`call`/`callnz`/`label`), `ret` inside flow control, vs_3_0 outputs and ps_3_0 inputs with usages other than position/color/texcoord/fog/psize. The game uses none of these.

**Not emulated:**
- **Fixed-function fog after the pixel shader**, for ps < 3: the game's shader passes all set `FOGENABLE = 0` and do their fog in the shaders. The `fog` varying is there if it's ever needed.
- **User clip planes** (D3DRS_CLIPPLANEENABLE): would need `[[clip_distance]]`.
- **ps_1_x fixed-point register range:** registers are full float, not clamped to ±MaxPixelShaderValue.
- **ps_1_4 phase-2 alpha loss:** the alpha of registers written in phase 1 isn't discarded at the phase marker.
- **`_pp` partial precision:** everything runs at full float precision.
- **D3DTSS_TEXCOORDINDEX:** pixel shaders ignore it, as in D3D9.

**Host responsibilities:**
- **Shader cache key:** the bytecode plus the options, which depend on sampler types for ps_1_x, D3DTTFF_PROJECTED, shadow-map formats and integer vertex formats.
- **Missing vertex elements:** for a declared input the vertex declaration lacks, bind a constant zero/default buffer with stride 0. Metal fails pipeline creation if an attribute is missing.
- **Sampler and texture state:** filtering, addressing, sRGB, LOD bias, and the BGRA/L8/A8 swizzles (`MTLTextureSwizzle`).
- **Math mode:** all tests pass in Fast, Relaxed and Safe. Use `MTLMathModeRelaxed` if inf/NaN-dependent content ever misbehaves under Fast.
- **Compute kernels:** `dsx`/`dsy` work in the fragment function, but the test kernel can't run shaders that use them. No game shader does.
