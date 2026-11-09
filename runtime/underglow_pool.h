/* Optional broad ground contribution; strip radiance is independent of this.
 * Reference and the distinction between a test-plane proxy and an installed
 * source are documented in runtime/docs/underglow.md. */
#pragma once
#include "underglow_render.h"
#include <math.h>

/* CX6 green: 50 lx at 1 m, 120 degree emitting angle. A Lambertian
 * intensity has a 120 degree FWHM. Project its one-metre test plane:
 * E(r) = 50 / (1 + r*r)^2, including receiver cosine and inverse square.
 * Preserve the measured strip spectrum, rather than borrowing RGB spectra
 * from the separate RGBW reference (whose white uses dedicated LEDs). */
static inline double ug_pool_broad(const UGStrip strips[UG_STRIPS], UGVec3 p) {
    double e=0;
    for(unsigned i=0;i<UG_STRIPS;++i) {
        double x=p.x-(strips[i].a.x+strips[i].b.x)*.5;
        double y=p.y-(strips[i].a.y+strips[i].b.y)*.5;
        double r2=1+x*x+y*y;
        e+=50/(r2*r2);
    }
    return e/106.075; /* green reference divided by measured green lm/m */
}
