/* Shared fragment epilogue for translated and replacement shaders. */
#pragma once

#define SM_ALPHA_COVERAGE_MSL \
"static inline float sm_alpha_coverage(uint f, float alpha, float ref) {\n" \
"    float a = saturate(alpha);\n" \
"    float width = max(fwidth(a), 1.0f / 255.0f);\n" \
"    float qr = rint(saturate(ref) * 255.0f);\n" \
"    bool reverse = f == 2u || f == 4u;\n" \
"    float edge = (qr + ((f == 5u || f == 4u) ? 0.5f : -0.5f)) / 255.0f;\n" \
"    float d = reverse ? edge - a : a - edge;\n" \
"    // Preserve fully empty/solid texels and comparisons outside the alpha range.\n" \
"    if (edge < 0.0f) return reverse ? 0.0f : 1.0f;\n" \
"    if (edge > 1.0f) return reverse ? 1.0f : 0.0f;\n" \
"    if (a == 0.0f || a == 1.0f) return d > 0.0f ? 1.0f : 0.0f;\n" \
"    return saturate(d / width + 0.5f);\n" \
"}\n"

/* The host sets the flag only for monotone alpha tests on unblended MSAA draws.
 * Keep the original test as the else branch, including its exact 8-bit compare. */
#define SM_ALPHA_COVERAGE_APPLY(alpha) \
"    if (aux.alpha_to_coverage != 0u) " alpha " = sm_alpha_coverage(aux.alpha_func, " alpha ", aux.alpha_ref);\n" \
"    else\n"
