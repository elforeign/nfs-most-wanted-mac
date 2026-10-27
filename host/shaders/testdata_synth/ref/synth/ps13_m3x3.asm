// synth : ps13_m3x3
//
ps_1_3
def c0, 0.2, 0.4, 0.8, 1
tex t0
texm3x3pad t1, t0_bx2
texm3x3pad t2, t0_bx2
texm3x3spec t3, t0_bx2, c0
mov r0, t3

// approximately 5 instruction slots used
