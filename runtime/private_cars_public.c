/* Public capability: no discovery, environment checks, catalog or file access. */
#include "private_cars.h"
int private_cars_init(const char *root, int enabled, const char *const *originals, size_t count) {
    (void)root; (void)enabled; (void)originals; (void)count; return 0;
}
size_t private_cars_count(void) { return 0; }
const PrivateCar *private_cars_get(size_t index) { (void)index; return NULL; }
size_t private_cars_inventory_count(void) { return 0; }
const PrivateCar *private_cars_inventory_get(size_t index) { (void)index; return NULL; }
const char *private_cars_generation(void) { return ""; }
const char *private_cars_compatibility(void) { return ""; }
const char *private_cars_error(void) { return ""; }

int private_cars_backup_before_write(const char *destination,const char *staged) {
    (void)destination; (void)staged; return 1;
}
