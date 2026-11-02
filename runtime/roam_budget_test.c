// Production six-car/60 fps budget, fake guest factory; no game or network execution.
// Friend admission regression: e08e62b4 1054 checks/6 failures, cop kept=0, traffic replaced=1;
// fixed 1048/0, cop kept=1, traffic replaced=1 (same harness, fake guest factory).
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
    // Fill the decoded 20-PVehicle cache while six friends are active, including off-road poses.
    // A traffic admission must evict an ordinary candidate, never a tracked friend. A later
    // remote spawn must be able to reclaim the same kind of candidate at the limit.
    memset(roam_car, 0, sizeof roam_car); roam_view_init(&roam.view);
    roam_lifecycle_wait = roam_retry_wait = (RoamWait){0};
    M32(0x0092CD84u) = 1; M32(0x8000) = 0x9000; M32(0x0092CD24u) = 14;
    roam_clones = 1; factory_fails = 0; cache_pressure_mode = 1;
    for (int i = 0; i < 6; ++i) {
        RoamState s = {.t=1000, .pos={12+i*2.0f,8,3}, .q={0,0,0,32767}, .flags=ROAM_FLAG_CAR};
        roam_view_add(&roam.view, (uint16_t)(i+1), 1, &s, 1000);
    }
    before = factory_calls; unsigned failed_before = roam_spawn_fail;
    for (uint32_t now = 1000; now <= 2000; now += 200) { fresh(now); roam_cars(&c, now, 1); }
    CHECK(active() == 6 && factory_calls == before+6 && M32(0x0092CD24u) == 20);
    // An untracked, disposable cached vehicle is appended after the friends to force vetoes.
    M32(0x0092C674u) = 0xb800; M32(0xb080 + 0x94) = 1; // ambient traffic driver class
    M32(0x8000 + 4*M32(0x0092CD84u)) = 0xb080; ++M32(0x0092CD84u);
    CHECK(cache_admit(&c, 0xc000)); ++M32(0x0092CD24u); // traffic's non-null cache owner
    fresh(2200); roam_cars(&c, 2200, 1);
    int listed = 0;
    for (int i = 0; i < ROAM_CARS; ++i) if (roam_car[i].used && roam_vehicle_listed(roam_car[i].sim+0x80)) ++listed;
    CHECK(active() == 6 && listed == 6 && factory_calls == before+6);
    // Simulate a legitimate world removal, preserving the full cache with a disposable entry.
    for (unsigned i = 1; i < M32(0x0092CD84u); ++i)
        if (M32(0x8000+4*i) == roam_car[0].sim+0x80) M32(0x8000+4*i) = 0xb080;
    fresh(2400); roam_cars(&c, 2400, 1);
    CHECK(active() == 6 && roam_spawn_fail == failed_before && cache_evictions == 2);
    // No disposable candidates: preserve friends and refuse admission instead of deleting one.
    CHECK(!cache_admit(&c, 0xc000));
    cache_pressure_mode = 0;
    CHECK(cache_registry_count == 1);
    command = -1; atomic_store(&roam_state, 2); roam_session(&c);
    CHECK(cache_registry_count == 0 && active() == 0);
    // Rejoin reuses the persistent vtable but registers exactly once for the new session.
    RoamState again = {.t=3000, .pos={12,8,3}, .q={0,0,0,32767}, .flags=ROAM_FLAG_CAR};
    roam_view_add(&roam.view, 1, 1, &again, 3000);
    fresh(3000); roam_lifecycle_wait = roam_retry_wait = (RoamWait){0}; roam_cars(&c, 3000, 1);
    fresh(3200); roam_cars(&c, 3200, 1);
    CHECK(cache_registry_count == 1 && cache_host_count == 3);
    // A full cache with exactly one reclaimable candidate: the cop manager permits our
    // unrelated cache, but an ambient request must preserve that active cop (00410DD6).
    memset(roam_car, 0, sizeof roam_car);
    M32(0x0092CD84u) = 2; M32(0x8000) = 0x9000; M32(0x8004) = 0xb080;
    M32(0x0092CD24u) = 20; M32(0xb080 + 0x94) = 3;
    cache_pressure_mode = 1;
    CHECK(!cache_admit(&c, M32(0x0092C674u) + 8) && roam_vehicle_listed(0xb080));
    RoamRemote candidate = {0};
    const float pos[3] = {12,8,3}, mat[4][4] = {{1,0,0,0},{0,1,0,0},{0,0,1,0},{12,8,3,1}};
    before = cache_evictions;
    uint32_t spawned = roam_spawn(&c, pos, mat, &candidate, 0);
    int cop_kept = !spawned && roam_vehicle_listed(0xb080) && cache_evictions == before && M32(0x0092CD24u) == 20;
    CHECK(cop_kept);
    printf("friend admission: cop refused/kept=%d\n", cop_kept);
    // The same pressure must still admit a friend when the candidate is disposable traffic.
    M32(0x0092CD84u) = 2; M32(0x8004) = 0xb080; M32(0x0092CD24u) = 20;
    M32(0xb080 + 0x94) = 1;
    before = cache_evictions;
    spawned = roam_spawn(&c, pos, mat, &candidate, 0);
    int traffic_replaced = spawned && !roam_vehicle_listed(0xb080) && cache_evictions == before+1 && M32(0x0092CD24u) == 20;
    CHECK(traffic_replaced);
    printf("friend admission: traffic evicted/spawned=%d\n", traffic_replaced);
    // A racer is never eligible, even if no other cache claims it.
    M32(0x0092CD84u) = 2; M32(0x8004) = 0xb080; M32(0xb080 + 0x94) = 2;
    CHECK(!roam_spawn(&c, pos, mat, &candidate, 0) && roam_vehicle_listed(0xb080));
    // Do not bypass traffic's own active/loading veto just because the driver class matches.
    M32(0x0092CD84u) = 2; M32(0x8004) = 0xb080; M32(0x0092CD24u) = 20; M32(0xb080 + 0x94) = 1;
    cache_traffic_active = 1;
    CHECK(!roam_spawn(&c, pos, mat, &candidate, 0) && roam_vehicle_listed(0xb080));
    M32(0x0092CD84u) = 2; M32(0x8004) = 0xb080; M32(0x0092CD24u) = 20;
    cache_traffic_active = 0; cache_traffic_loading = 1;
    CHECK(!roam_spawn(&c, pos, mat, &candidate, 0) && roam_vehicle_listed(0xb080));
    cache_traffic_loading = 0;
    CHECK(cache_traffic_queries > 0);
    M32(0x0092CD84u) = 2; M32(0x8004) = 0xb080; M32(0x0092CD24u) = 20;
    M32(0x0092C674u) = 0;
    CHECK(!roam_spawn(&c, pos, mat, &candidate, 0) && roam_vehicle_listed(0xb080));
    cache_pressure_mode = 0;
#ifdef ROAM_FACTORY_DIAG_TEST
    // Exact factory return checks, including the cdecl stack convention and non-spawn isolation.
    roam_spawning = 1; roam_spawn_reason = NULL; resource_result = 0;
    c.esp = 0x78000; f_00687710(&c);
    CHECK(!strcmp(roam_spawn_reason, "vehicle-cache-resources") && c.esp == 0x78004);
    body_result = 0; c.esp = 0x78000; f_006ED260(&c);
    CHECK(!strcmp(roam_spawn_reason, "rigid-body-limit") && c.esp == 0x78004);
    roam_spawning = 0; roam_spawn_reason = "unchanged"; f_00687710(&c); f_006ED260(&c);
    CHECK(!strcmp(roam_spawn_reason, "unchanged"));
    roam_spawning = 1; resource_result = body_result = 1; f_00687710(&c); f_006ED260(&c);
    CHECK(!strcmp(roam_spawn_reason, "unchanged")); roam_spawning = 0;
#endif
    printf("budget: %d checks, %d failures; limit=60 cars<=6, factory=%d kills=%d\n", checks, failures, factory_calls, kill_calls);
    free(G); return failures ? 1 : 0;
}
