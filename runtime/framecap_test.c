// Offline: clang -std=c11 -Wall -Wextra -Werror runtime/framecap_test.c -o /tmp/framecap_test && /tmp/framecap_test
// --broken deliberately disables the limit: must FAIL the cadence checks.
// Verified 2026-10-23: normal 94 checks / 0 failures; broken 94 / 33 (exit 1); the 1e-9 tolerance fails 16 of the 8.333333 ms cases.
// This models fixed-refresh Metal minimum-duration semantics, not WindowServer.
#include <stdio.h>
#include <string.h>
#include "framecap.h"

static int checks, failures, broken;
#define CHECK(x) do { ++checks; if (!(x)) { ++failures; fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); } } while (0)
static int close_to(double a, double b) { return fabs(a - b) < 1e-8; }
static double duration(double hz, int limit) {
    return broken ? 0 : framecap_duration(hz, limit);
}
// Enumerate physical refreshes independently of the policy's ceil arithmetic.
static double metal_model(double previous, double ready, double hz, double minimum) {
    double next = previous + 1.0 / hz;
    while (next + 1e-10 < ready || next - previous + 1e-10 < minimum) next += 1.0 / hz;
    return next;
}
int main(int argc, char **argv) {
    broken = argc > 1 && !strcmp(argv[1], "--broken");
    const struct { double hz; int limit, refreshes; } cases[] = {
        {60, 0, 0}, {60, 120, 1}, {60, 60, 1}, {60, 40, 2},
        {120, 0, 0}, {120, 120, 1}, {120, 60, 2}, {120, 40, 3},
        {59.94, 60, 1}, {119.88, 60, 2}, {119.88, 40, 3},
        // hz as the port derives it: 1000 / 8.333333 ms (the real displays' minimumRefreshInterval)
        {1000 / 8.333333, 120, 1}, {1000 / 8.333333, 60, 2}, {1000 / 8.333333, 40, 3},
    };
    for (unsigned c = 0; c < sizeof cases / sizeof *cases; ++c) {
        double hz = cases[c].hz, d = duration(hz, cases[c].limit);
        int n = cases[c].refreshes;
        CHECK(close_to(d, n / hz));
        CHECK(framecap_refreshes(hz, cases[c].limit) == n);
        if (!n) { CHECK(d == 0); n = 1; }
        double previous = 10, ready = previous + 0.2 / hz;
        double actual = metal_model(previous, ready, hz, d);
        CHECK(close_to(actual - previous, n / hz));
        // Miss the intended refresh, then recover: no short catch-up interval.
        previous = actual;
        ready = previous + (n + 0.2) / hz;
        actual = metal_model(previous, ready, hz, d);
        CHECK(close_to(actual - previous, (n + 1) / hz));
        previous = actual;
        ready = previous + 0.2 / hz;
        actual = metal_model(previous, ready, hz, d);
        CHECK(close_to(actual - previous, n / hz));
        // Repeated presentation is anchored to actual prior presentation.
        double anchor = actual;
        for (int i = 0; i < 1000; ++i) actual = metal_model(actual, actual + 0.2 / hz, hz, d);
        CHECK(close_to(actual, anchor + 1000.0 * n / hz));
    }
    CHECK(framecap_duration(0, 60) == 0);
    CHECK(framecap_duration(120, -1) == 0);
    CHECK(framecap_interval_bucket(8.3333) == 0);
    CHECK(framecap_interval_bucket(16.6667) == 1);
    CHECK(framecap_interval_bucket(25) == 2);
    CHECK(framecap_interval_bucket(11) == 3);
    CHECK(framecap_interval_bucket(33.3333) == 3);
    CHECK(framecap_interval_bucket(1000) == 3);
    printf("framecap %s: %d checks, %d failures%s\n", failures ? "FAIL" : "PASS", checks, failures, broken ? " (deliberately broken)" : "");
    return failures ? 1 : 0;
}
