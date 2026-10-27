// synth : ps13_misc
//
ps_1_3
tex t0
texdp3tex t1, t0
texreg2rgb t2, t0
texm3x2pad t3, t0
texm3x2depth t4, t0
add r0, t1, t2
+mul r0.w, t1.w, t2.w

// approximately 7 instruction slots used
