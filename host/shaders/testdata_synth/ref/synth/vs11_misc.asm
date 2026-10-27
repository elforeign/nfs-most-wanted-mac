// synth : vs11_misc
//
vs_1_1
def c95, 3, 0.5, 1, 0
dcl_position v0
dcl_color v1
dcl_blendindices v2
mul r0.x, v2.x, c95.x
mov a0.x, r0.x
dp4 oPos.x, v0, c[a0.x + 10]
dp4 oPos.y, v0, c[a0.x + 11]
dp4 oPos.z, v0, c[a0.x + 12]
dp4 oPos.w, v0, c[a0.x + 13]
logp r1, v1.x
expp r2, v1.y
m3x3 r3.xyz, v1, c[a0.x + 1]
mov oT0, r1
mov oT1, r2
mov oT2, r3
mov oD0, v1_abs

// approximately 13 instruction slots used
