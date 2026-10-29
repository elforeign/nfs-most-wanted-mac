#ifndef NFS_GPUCLIENTS_H
#define NFS_GPUCLIENTS_H
#include <stdio.h>

typedef struct {
    double dev_util, self_ms, other_ms; // NAN = unavailable; ms per second, normalized by sample interval
    char top[2048], mhz[96];           // MHz, a dominant state label, or "-"
} GPUClientsSample;
typedef struct GPUClients GPUClients;
GPUClients *gpuclients_create(void);
void gpuclients_destroy(GPUClients *s);
void gpuclients_sample(GPUClients *s, GPUClientsSample *out); // caller's background thread only
void gpuclients_start(void);           // starts a utility-priority once-a-second sampler
void gpuclients_cached(GPUClientsSample *out); // no registry calls, never waits for sampler
void gpuclients_print(FILE *f, const GPUClientsSample *s); // appends five TSV columns (no newline)
#endif
