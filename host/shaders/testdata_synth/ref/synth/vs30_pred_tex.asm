// synth : vs30_pred_tex
//
vs_3_0
defi i0, 5, 0, 1, 0
def c200, 0.5, 1, 0, -1
dcl_position v0
dcl_texcoord v1
dcl_texcoord1 v2
dcl_position o0
dcl_texcoord o1
dcl_texcoord1 o2
dcl_color o3
dcl_texcoord5 o4
dcl_2d s0
dcl_cube s1
mov r0, c200.z
rep i0
  add r0, r0, v1
  break_gt r0.x, c200.y
endrep
setp_gt p0, v0, c200.x
(p0) mov r1, c200.y
(!p0.y) mov r1.y, c200.w
if p0.z
  add r1, r1, c1
endif
mov r2, v2
texldl r3, r2, s0
texldl r4, v1, s1
mov o0, v0
mov o1, r0
mov o2, r1
mov o3, r3
mov o4, r4

// approximately 19 instruction slots used
