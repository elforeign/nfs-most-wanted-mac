// synth : vs20_math
//
vs_2_0
def c95, 0.1591549, 6.283185, 0.5, 2
dcl_position v0
dcl_normal v1
dcl_color v2
nrm r0.xyz, v1
crs r1.xyz, r0, v0
sgn r2, v0, r8, r9
abs r3, v0
sincos r4.xy, v2.x, c0, c1
pow r5.w, v2.y, c95.w
lit r6, v0
dst r7, v0, v1
logp r8, v2.z
expp r9, v2.w
log r10.x, v2.x
exp r10.y, v0.y
frc r10.zw, v0
slt r11, v0, v1
sge r11.zw, v1, v0
min r2.w, v0.x, v1.y
max r3.w, v0.z, v1.w
rcp r0.w, v0.x
rsq r1.w, v0.y
mov oPos, v0
mov oT0, r0
mov oT1, r1
mov oT2, r2
mov oT3, r3
mov oT4, r4
mov oT5, r6
mov oT6, r7
add oT7, r8, r9
mad oD0, r10, r11, r5.w
mov oFog, r10.x
mov oPts, v2.w

// approximately 31 instruction slots used
