// synth : ps11_m3x2
//
ps_1_1
tex t0
texm3x2pad t1, t0_bx2
texm3x2tex t2, t0_bx2
texreg2ar t3, t0
mul r0, t2, t3
+mov r0.w, t0.z

// approximately 6 instruction slots used
