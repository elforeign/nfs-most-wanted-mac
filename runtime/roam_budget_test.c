// Production six-car/60 fps budget, fake guest factory; no game or network execution.
#define main switch_test_main
#include "roam_switch_test.c"
#undef main
static void fresh(uint32_t now) {
    for (int i = 0; i < ROAM_MAX_REMOTE; ++i) if (roam.view.r[i].used) {
        roam.view.r[i].last_rx = now; roam.view.r[i].s[0].t = now;
    }
}
static int active(void) {
    int n = 0; for (int i = 0; i < ROAM_CARS; ++i) n += !!roam_car[i].used; return n;
}
int main(void) {
    G = calloc(1, 16*1024*1024); if (!G) return 2;
    Cpu c = {0}; budget_mode = 1; roam_mem = 0x10000;
    M32(0x008EF998u) = 1; M32(0x009142D0u) = 0x1000; M32(0x1034) = 0x2000;
    M32(0x0092CD7Cu) = 0x8000; M32(0x0092CD84u) = 1; M32(0x8000) = 0x9000;
    M32(0x9028) = 0xa000; M32(0xa020) = 123;
    roam_view_init(&roam.view); roam_clones = 16;
    // Reverse arrival order: nearest must win independently of buffer slot/clone count.
    for (int i = 0; i < 20; ++i) {
        RoamState s = {.t=1000, .pos={200-i*10.0f,0,0}, .q={0,0,0,32767}, .flags=ROAM_FLAG_CAR};
        roam_view_add(&roam.view, (uint16_t)(i+1), 1, &s, 1000);
    }
    for (uint32_t now = 1000; now <= 4000; now += 10) {
        fresh(now); int old = factory_calls + kill_calls;
        roam_cars(&c, now, 1);
        CHECK(active() <= 6);
        CHECK(factory_calls + kill_calls - old <= 1);
        if ((now-1000)%200) CHECK(factory_calls + kill_calls == old);
    }
    CHECK(active() == 6 && factory_calls == 6 && kill_calls == 0);
    CHECK(roam_car_find(20, 0) && roam_car_find(20, 1) && roam_car_find(19, 0));
    // Losing the local player does not mean the world freed its remote vehicles.
    roam_cars(&c, 4100, 0); CHECK(active()==5 && kill_calls==1);
    // A pinned distant friend displaces an unpinned nearby car, paced retirement first.
    roam.pins[0] = 1; roam.npins = 1;
    for (uint32_t now = 4200; now <= 6800; now += 200) { fresh(now); roam_cars(&c, now, 1); CHECK(active() <= 6); }
    CHECK(roam_car_find(1, 0));
    CHECK(kill_calls > 0);
    // Tiny motion near the cutoff retains incumbents through rank hysteresis.
    int before = factory_calls + kill_calls;
    fresh(6900); roam_cars(&c, 6900, 1); CHECK(factory_calls + kill_calls == before);
    // Stale snapshots retire, one per lifecycle interval.
    for (uint32_t now = 9000; now <= 10400; now += 200) { int a = active(); roam_cars(&c, now, 1); CHECK(a-active() <= 1); }
    CHECK(active() == 0);
    factory_fails = 1; fresh(11000); before = factory_calls; roam_cars(&c, 11000, 1);
    for (uint32_t now = 11010; now < 12000; now += 10) { fresh(now); roam_cars(&c, now, 1); }
    CHECK(factory_calls == before+1); fresh(12000); roam_cars(&c, 12000, 1); CHECK(factory_calls == before+2);
    const int caps[] = {0,120,60,40,30};
    const int expected[] = {60,60,60,40,30};
    for (int i = 0; i < 5; ++i) { CHECK(roam_online_limit(caps[i], 1) == expected[i]); CHECK(roam_online_limit(caps[i], 0) == caps[i]); }
    // Virgin timers at long uptime, the signed boundary and a lifecycle crossing clock wrap.
    const uint32_t starts[] = {30u*24u*60u*60u*1000u, 0x80000000u, UINT32_MAX-99u};
    for (unsigned i = 0; i < sizeof starts / sizeof *starts; ++i) {
        uint32_t now = starts[i];
        memset(roam_car, 0, sizeof roam_car); roam_view_init(&roam.view);
        roam_lifecycle_wait = roam_retry_wait = (RoamWait){0};
        M32(0x0092CD84u) = 1; M32(0x8000) = 0x9000;
        roam_clones = 2; roam.npins = 0; factory_fails = 0;
        RoamState s = {.t=now, .q={0,0,0,32767}, .flags=ROAM_FLAG_CAR};
        roam_view_add(&roam.view, 1, 1, &s, now);
        before = factory_calls; roam_cars(&c, now, 1);
        CHECK(factory_calls == before+1 && active() == 1);
        fresh(now+199u); roam_cars(&c, now+199u, 1); CHECK(factory_calls == before+1);
        fresh(now+200u); roam_cars(&c, now+200u, 1); CHECK(factory_calls == before+2);
        // Failed spawns back off exactly one second, including across wrap.
        memset(roam_car, 0, sizeof roam_car); M32(0x0092CD84u) = 1;
        roam_lifecycle_wait = roam_retry_wait = (RoamWait){0}; factory_fails = 1;
        fresh(now); before = factory_calls; roam_cars(&c, now, 1); CHECK(factory_calls == before+1);
        fresh(now+999u); roam_cars(&c, now+999u, 1); CHECK(factory_calls == before+1);
        fresh(now+1000u); roam_cars(&c, now+1000u, 1); CHECK(factory_calls == before+2);
        // A long pause must expire an armed retry as well as an unused timer.
        uint32_t later = now + 30u*24u*60u*60u*1000u;
        fresh(later); roam_cars(&c, later, 1); CHECK(factory_calls == before+3);
    }
    printf("budget: %d checks, %d failures; limit=60 cars<=6, factory=%d kills=%d\n", checks, failures, factory_calls, kill_calls);
    free(G); return failures ? 1 : 0;
}
