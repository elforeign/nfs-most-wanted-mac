// synth : vs30_rel_out
//
vs_3_0
defi i0, 3, 1, 1, 0
dcl_position v0
dcl_texcoord v1
dcl_texcoord1 v2
dcl_texcoord2 v3
dcl_position o0
dcl_texcoord o1
dcl_texcoord1 o2
dcl_texcoord2 o3
mov o0, v0
loop aL, i0
  add o0[aL.x], v0[aL.x], c0[aL.x]
endloop

// approximately 4 instruction slots used
