// HLSL subset for ordinary and masked front-end SM1.1 programs.
#pragma once
#include "../../../host/shaders/sm_ir.h"
namespace sm {
// Entry sm_main; VS float constants b0. DCL semantics map v# to
// POSITION0/COLOR0/TEXCOORD0..1; IR texture register N binds tN/sN.
// Varyings: SV_Position, COLOR0, TEXCOORD0 and (when used) TEXCOORD1.
// Texture outputs preserve their masks; unwritten varying lanes are zero.
// Only MOV/ADD/MUL/DP4/TEX are accepted. Sources allow identity/negation,
// arbitrary swizzles; destination masks/saturation are preserved. Nonzero
// result shifts, other modifiers/opcodes/interfaces fail explicitly.
// Coissued MOV/MUL requires disjoint writes and no read-after-write hazard.
bool emit_hlsl(const Shader &shader, std::string &source, std::string &error);
}
