#ifndef NFS_CAR_PERFORMANCE_H
#define NFS_CAR_PERFORMANCE_H
#include <stddef.h>
#include <stdint.h>

/* Performance records store stock values. Retail computes installed and maximum
 * packages from the projected upgrade endpoints. */
enum {
    CAR_PERFORMANCE_BYTES = 344,
    CAR_PERFORMANCE_TORQUE_KNOTS = 64,
    CAR_PERFORMANCE_ENGINE_LINK = 0,
    CAR_PERFORMANCE_CHASSIS_LINK = 3,
    CAR_PERFORMANCE_NOS_LINK = 6,
    CAR_PERFORMANCE_LINKS = CAR_PERFORMANCE_NOS_LINK + 1
};
enum { CAR_PERFORMANCE_PROSTREET = 1, CAR_PERFORMANCE_UNDERCOVER };
typedef struct {
    uint32_t version;
    uint32_t values[(CAR_PERFORMANCE_BYTES - 16) / 4];
    unsigned source; /* Later-game provenance from the admitted catalog. */
    struct {
        uint32_t count;
        float bounds[4];
        float x[CAR_PERFORMANCE_TORQUE_KNOTS];
        float y[CAR_PERFORMANCE_TORQUE_KNOTS];
        float second[CAR_PERFORMANCE_TORQUE_KNOTS];
    } torque;
} CarPerformance;

typedef struct {
    uint32_t key, size;
    unsigned fit_kind;
} CarPerformanceLink;
extern const CarPerformanceLink car_performance_links[CAR_PERFORMANCE_LINKS];
int car_performance_enabled(void);
int car_performance_read(int fd, uint32_t car, uint32_t donor, CarPerformance *out);
int car_performance_class(uint32_t key);
void car_performance_body(const CarPerformance *record, void *layout, void *handling);
unsigned car_performance_tiers(const CarPerformance *record, unsigned link,
                               unsigned donor_count, unsigned donor_indices[3]);
void car_performance_layout(const CarPerformance *record, unsigned link, int installed,
                            const void *stock, const void *endpoint, void *out);
float car_performance_clamp_top_speed(const void *engine_layout, float speed);
int car_performance_torque(const CarPerformance *record, const void *engine_layout,
                          float rpm, float *torque);
#endif
