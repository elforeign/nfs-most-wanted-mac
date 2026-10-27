// synth : ps20_misc
//
ps_2_0
def c10, 0.5, -0.5, 2, 1
dcl t0
dcl t1
dcl_centroid t2.xyz
dcl v0
dcl v1
dcl_2d s0
dcl_cube s1
dcl_volume s2
texld r0, t0, s0
texldp r1, t1, s0
texldb r2, t0, s0
texld r3, t2, s1
texld r4, t1, s2
dp2add r5, r0, r1, c10.x
cmp r6, r0_bx2, r1, r2
texkill t1
mad r7, r3, v0, r4
add r7, r7, -v1_abs
mov oC0, r7
mov oC1, r5
mov oC2, r6
mov oDepth, r0.x

// approximately 14 instruction slots used
