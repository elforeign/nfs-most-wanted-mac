// MW-derived converted-lamp treatment. Inputs are the existing car shader's
// diffuse, shadow, specular and environment contributions. Preserve texture
// alpha times material alpha; masks have no independently supported meaning.
// This is the original public MW helper, now selectable for explicit lamp IDs.
// Windows uses the existing MW D3D9 car shader; this Metal helper is not applied.
#pragma once
#define PUBLIC_CAR_LAMP_MSL \
"static float4 carbon_lamp(float4 tex, float4 light, float shade, float3 specular, float3 reflection, float2 masks) {\n" \
"    return float4(light.rgb * shade * tex.rgb + specular + reflection, tex.a * light.a);\n" \
"}\n"
#define PUBLIC_CAR_LAMP_FULL_MSL \
PUBLIC_CAR_LAMP_MSL \
"static float4 carbon_lamp_retail(float4 tex, float4 light, float shade, float3 specular, float3 reflection, float2 masks) { return carbon_lamp(tex, light, shade, specular, reflection, masks); }\n" \
CLEAR_HEADLAMP_MSL
