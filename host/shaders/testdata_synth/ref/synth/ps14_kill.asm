// synth : ps14_kill
//
ps_1_4
texcrd r0.xyz, t0
texld r1, t1
texkill t2
texkill r0
mul r0, r1, r0

// approximately 5 instruction slots used
