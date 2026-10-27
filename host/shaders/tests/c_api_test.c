/* c_api_test.c - plain C99 use of sm_translate.h: translates one shader file and prints the
 * reflection. Usage: c_api_test file.bin */
#include "../sm_translate.h"
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s shader.bin\n", argv[0]); return 2; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror(argv[1]); return 2; }
    uint32_t tok[16384];
    size_t n = fread(tok, 4, 16384, f);
    fclose(f);
    SmOptions opt;
    sm_default_options(&opt);
    SmResult r;
    if (sm_translate(tok, n, &opt, &r) != 0) { printf("error: %s\n", r.error); sm_result_free(&r); return 1; }
    printf("%s_%d_%d  tokens=%zu  instructions=%d  msl=%zu bytes  entry=%s\n", r.shader_type == SM_VERTEX ? "vs" : "ps",
           r.major, r.minor, sm_token_count(tok, n), r.n_instructions, r.msl_len, r.entry);
    printf("float constants read:");
    for (int i = 0; i < 256; i++) if (r.float_used[i / 32] >> (i % 32) & 1) printf(" c%d", i);
    printf("%s  (max %d)\n", r.float_rel ? "  + relative" : "", r.float_max);
    for (int i = 0; i < r.n_inputs; i++)
        printf("input v%d usage %d index %d mask 0x%x\n", r.inputs[i].reg, r.inputs[i].usage, r.inputs[i].usage_index, r.inputs[i].mask);
    for (int i = 0; i < r.n_samplers; i++) printf("sampler s%d type %d%s\n", r.samplers[i].reg, r.samplers[i].type, r.samplers[i].shadow ? " shadow" : "");
    printf("varyings read 0x%x written 0x%x  color outputs 0x%x depth %d kill %d\n", r.varyings_read, r.varyings_written,
           r.color_outputs, r.writes_depth, r.uses_kill);
    for (int i = 0; i < r.n_constants; i++)
        printf("ctab %-24s set %d reg %d count %d type %d\n", r.constants[i].name, r.constants[i].set, r.constants[i].reg,
               r.constants[i].count, r.constants[i].type);
    char *d = sm_disassemble(tok, n);
    if (d) { printf("--\n%s", d); free(d); }
    sm_result_free(&r);
    return 0;
}
