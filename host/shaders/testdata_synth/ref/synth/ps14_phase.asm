// synth : ps14_phase
//
ps_1_4
def c0, 0.5, 0.25, 0.125, 1
texcrd r0.xyz, t0_dw
texcrd r1.xy, t1_dz
texld r2, t2_dw
texld r3, t3_dz
bem r4.xy, r2, r3
mov r5, r2_x2
phase
texld r4, r4
texdepth r5
cnd r0, r2, r0, r4
add_x2 r0, r0, -c0

// approximately 11 instruction slots used
