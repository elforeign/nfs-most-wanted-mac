// synth : ps13_m3x3tex
//
ps_1_3
tex t0
texm3x3pad t1, t0
texm3x3pad t2, t0
texm3x3tex t3, t0
texdp3 t3, t0
lrp r0, t0.w, t3, t0

// approximately 6 instruction slots used
