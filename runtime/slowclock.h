#ifndef NFS_SLOWCLOCK_H
#define NFS_SLOWCLOCK_H
#include <stdint.h>
#include <stdio.h>

// GPU-clock drop evidence for the session log (slowclock.c). Game thread only, called once a second by
// report.m's perf.tsv pass: no per-frame work, five system calls a second, a file only when the clock drops.
// 1 when the GPU state with the largest share of the second is P1, P2 or P3 ("OFF:1.9,P1:61.5,P2:36.6").
int slowclock_low(const char *states);
// Appends its eight values (cpu_game_ms, proc_cpu_ms, proc_pcore_pct, proc_ghz, proc_faults, sys_cpu_pct,
// mem_pressure, clock_low: the header is in report.m's session_open) to the open perf.tsv row. `fresh` marks a new clock sample; at each
// entry into P1-P3 the last frames are written to <session_dir>/clock_<n>.tsv and `note` gets one line.
void slowclock_second(FILE *perf, int enabled, uint64_t frame, const char *states, int fresh,
                      const char *session_dir, double t, void (*note)(const char *fmt, ...));
#endif
