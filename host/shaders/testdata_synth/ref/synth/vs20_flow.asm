// synth : vs20_flow
//
vs_2_0
defi i0, 3, 2, 4, 0
defi i1, 4, 0, 1, 0
defb b1, true
def c90, 0.5, -0.25, 2, 1
dcl_position v0
dcl_normal v1
dcl_texcoord v2
mov r0, c90.w
mov r1, v0
loop aL, i0
  mad r0, r0, c90.x, c2[aL.x]
  dp4 r2.x, r1, c3[aL.x]
  if_gt r2.x, c90.y
    add r1.xyz, r1, c1
  else
    sub r1.xyz, r1, c1
  endif
  break_ge r2.x, c90.z
endloop
rep i1
  mul r1, r1, c90.x
endrep
if b0
  add r0, r0, c4
endif
if b1
  mul r0, r0, c90.z
endif
mova a0.x, v2.x
mov r3, c10[a0.x]
mova a0.yw, v2.yyzw
add r3, r3, c11[a0.w]
add r3, r3, c12[a0.y]
m4x4 oPos, r1, c20
m3x3 r4.xyz, v1, c24
m3x2 r5.xy, v1, c27
m4x3 r6.xyz, v0, c30
m3x4 r7, v1, c33
mov oT0, r0
mov oT1, r3
mov oT2.xyz, r4
mov oT2.w, r5.y
mov oT3, r6
mov oT4, r7
mov oD0, r0
mov oD1, -r3

// approximately 39 instruction slots used
