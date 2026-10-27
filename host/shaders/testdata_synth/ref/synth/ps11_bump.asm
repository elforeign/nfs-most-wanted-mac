// synth : ps11_bump
//
ps_1_1
def c0, 0.5, 0.25, 1, 0.75
tex t0
texbem t1, t0
texbeml t2, t0
texcoord t3
mul_x4 r0.xyz, t1_bias, 1-t2
+add_d4_sat r0.w, t3.z, c0.w
mad_x2 r1, t3_bx2, -c0, t1
cnd r0, r0.w, r0, r1

// approximately 8 instruction slots used
