// synth : ps30_loop
//
ps_3_0
def c10, 0.5, -0.5, 2, 1
defi i0, 3, 0, 1, 0
dcl_texcoord v0
dcl_texcoord1 v1
dcl_texcoord2 v2
dcl_color v3
dcl_position vPos.xy
dcl_position vFace
dcl_2d s0
mov r0, c10.y
loop aL, i0
  add r0, r0, v0[aL.x]
  if_ge r0.x, c10.w
    break
  endif
endloop
texld r1, v0, s0
cmp r2, vFace, r1, -r1
mul r3.xy, vPos, c10.x
mov r3.zw, v3
add oC0, r0, r2
mov oC1, r3

// approximately 13 instruction slots used
