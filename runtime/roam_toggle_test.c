// roam_toggle_test.c - the ghost-mode toggle's edge and gating (roam_core.c roam_toggle_*), pure logic, no socket.
#include <stdio.h>
#include "roam_core.h"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL: "); printf(__VA_ARGS__); printf("\n"); } } while (0)

int main(void) {
    RoamToggle t = {0};
    int fired = 0;
    // a held press fires once; release and press again fires again
    for (int f = 0; f < 10; ++f) fired += roam_toggle_press(&t, f >= 2 && f < 8, 1);
    CHECK(fired == 1, "edge: a 6-frame hold fired %d times (want 1)", fired);
    fired += roam_toggle_press(&t, 1, 1);
    CHECK(fired == 2, "edge: a second press did not fire (%d)", fired);
    // gating: every refusing condition alone blocks, all good allows
    CHECK(roam_toggle_allowed(1, 1, 1, 0), "gate: online, car, driving, no menu must allow");
    CHECK(!roam_toggle_allowed(0, 1, 1, 0), "gate: not welcomed must refuse");
    CHECK(!roam_toggle_allowed(1, 0, 1, 0), "gate: no local car must refuse");
    CHECK(!roam_toggle_allowed(1, 1, 0, 0), "gate: not driving (pause / FE screen / map) must refuse");
    CHECK(!roam_toggle_allowed(1, 1, 1, 1), "gate: F10 menu or keyboard open must refuse");
    // a press while refused is lost, and a hold carried out of a menu does not fire on exit
    RoamToggle u = {0};
    int a = roam_toggle_press(&u, 1, roam_toggle_allowed(1, 1, 1, 1));
    int b = roam_toggle_press(&u, 1, roam_toggle_allowed(1, 1, 1, 0));
    int c = roam_toggle_press(&u, 0, 1), d = roam_toggle_press(&u, 1, 1);
    CHECK(!a && !b, "gate: press in a menu fired (%d), or the hold fired on leaving it (%d)", a, b);
    CHECK(!c && d, "gate: a fresh press after release must fire (%d %d)", c, d);
    printf("toggle: edge %d, refused %d %d, fresh %d\n", fired, a, b, d);
    printf(fails ? "TOGGLE FAIL (%d)\n" : "TOGGLE PASS\n", fails);
    return fails ? 1 : 0;
}
