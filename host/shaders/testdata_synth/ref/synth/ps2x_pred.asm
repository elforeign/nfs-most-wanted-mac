// synth : ps2x_pred
//
ps_2_x
def c10, 0.5, -0.5, 2, 1
defi i0, 4, 0, 1, 0
dcl t0
dcl t1
dcl_2d s0
setp_lt p0, t0, c10.x
(p0) mov r0, c10.w
(!p0.y) mov r0.y, c10.z
mov r1, c10.y
rep i0
  add r1, r1, t1
  break p0.x
endrep
if p0.w
  add r1, r1, c10.w
endif
texld r2, t0, s0.wzyx
texld r3, t1, s0
mul r0, r0, r2
mad oC0, r1, r3.w, r0

// approximately 15 instruction slots used
