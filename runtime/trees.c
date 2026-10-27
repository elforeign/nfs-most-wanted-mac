// trees.c - R28: X360 lightmaterials Params for PC tree materials.
#include <string.h>
#include "trees.h"

static const struct { const char *name; uint32_t key; union { uint32_t bits[4]; float f[4]; } p; int aref; } k_trees[] = {
#include "gen/trees_params.inc"   // tools/gen_tables.py, from the player's files
};
const float *trees_params(const char *name) {
    for (size_t i = 0; name && i < sizeof k_trees / sizeof *k_trees; ++i)
        if (!strcmp(k_trees[i].name, name)) return k_trees[i].p.f;
    return NULL;
}
int trees_alpha_ref(const char *name) {
    for (size_t i = 0; name && i < sizeof k_trees / sizeof *k_trees; ++i)
        if (!strcmp(k_trees[i].name, name)) return k_trees[i].aref;
    return 0;
}
