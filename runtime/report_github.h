// Public issue preview, contained within the F10 report UI.
#pragma once
#include <CoreGraphics/CoreGraphics.h>
#include <stdint.h>
#if defined(NFS_PUBLIC) && NFS_PUBLIC
int report_github_active(void);
void report_github_close(void);
void report_github_key(int key);
void report_github_pad(int direction, uint32_t edge);
void report_github_mouse(int kind, double x, double y, double scroll);
void report_github_draw(CGContextRef ctx, double height, CGRect viewport, int xbox);
#else
static inline int report_github_active(void) { return 0; }
static inline void report_github_close(void) {}
static inline void report_github_key(int key) { (void)key; }
static inline void report_github_pad(int d, uint32_t e) { (void)d; (void)e; }
static inline void report_github_mouse(int k, double x, double y, double s) { (void)k; (void)x; (void)y; (void)s; }
static inline void report_github_draw(CGContextRef c, double h, CGRect v, int x) { (void)c; (void)h; (void)v; (void)x; }
#endif
