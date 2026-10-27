// controltype_test.c - prints controltype.c's Control Type table for controltype_test.py (no game, no guest memory).
#include "controltype.c"
int main(void) {
    static const int acts[] = {CT_GAS, CT_BRAKE, CT_STEERLEFT, CT_STEERRIGHT, CT_HANDBRAKE, CT_GAMEBREAKER, CT_NOS,
                               CT_SHIFTDOWN, CT_SHIFTUP, CT_RESET, CT_CAMERA, CT_LOOKBACK};
    for (int t = 1; t <= 10; ++t)
        for (size_t i = 0; i < sizeof acts / sizeof acts[0]; ++i) {
            float low;
            const char *s = ct_map(t, acts[i], &low);
            const CtSource *src = s ? ct_source(s) : NULL;
            printf("%d %d %d %s %.3f %d\n", t, ct_table_index(t), acts[i], s ? s : "-", low, s ? src != NULL : 1);
        }
    return 0;
}
