/* Public adapter: only the trusted catalog supplies inventory and snapshots. */
#include "private_cars.h"
#include "car_catalog.h"
#include "carsave_public.h"
#include <stdio.h>
#include <string.h>
static PrivateCar inventory[CAR_CATALOG_SLOTS];
static size_t nslots, nselected;
static unsigned selected[CAR_CATALOG_ADDITIONS];
static int initialized, result;
static char problem[256];
int private_cars_init(const char *root, int enabled, const char *const *originals, size_t count) {
    (void)originals; (void)count;
    if (initialized) return result;
    initialized=1;
    result=car_catalog_init(root,enabled);
    if (result<0) return result;
    /* Retail CarTypeInfo has two fixed 16-byte folder fields. Refuse the whole
     * selection before publishing if a trusted contract cannot fit that ABI. */
    for (size_t i=0;i<car_catalog_inventory_count();++i) {
        const CarCatalogSlot *s=car_catalog_inventory_get(i);
        if (s->ready && (strlen(s->folder)>15 || strlen(s->model_donor)>15)) {
            snprintf(problem,sizeof problem,"Car folder exceeds the retail type record capacity.");
            return result=-1;
        }
    }
    nslots=car_catalog_inventory_count();
    for (size_t i=0;i<nslots;++i) {
        const CarCatalogSlot *s=car_catalog_inventory_get(i);
        PrivateCar *d=&inventory[i];
        d->id=s->id; d->swap=s->swap_index>=0; d->selected=s->ready && s->selected;
#define COPY(field,value) snprintf(d->field,sizeof d->field,"%s",value)
        COPY(name,s->folder); COPY(donor,s->model_donor); COPY(price_donor,s->price_donor);
        COPY(source,s->source_game); COPY(pvehicle,s->pvehicle); COPY(frontend,s->frontend); COPY(ecar,s->ecar);
        COPY(display_name,s->display_name); COPY(manufacturer,s->manufacturer);
        COPY(geometry,s->outputs[CAR_CATALOG_GEOMETRY].path);
        COPY(textures,s->outputs[CAR_CATALOG_TEXTURES].path);
        COPY(solid_map,s->outputs[CAR_CATALOG_SOLID_MAP].path);
        COPY(wheel_fit,s->outputs[CAR_CATALOG_WHEEL_FIT].path);
        COPY(collision,s->outputs[CAR_CATALOG_COLLISION].path);
        COPY(paint,s->outputs[CAR_CATALOG_PAINT].path);
        COPY(physics,s->outputs[CAR_CATALOG_PHYSICS].path);
#undef COPY
        if (d->selected && !d->swap) selected[nselected++]=(unsigned)i;
    }
    return result=0;
}
size_t private_cars_count(void) { return nselected; }
const PrivateCar *private_cars_get(size_t i) { return i<nselected?&inventory[selected[i]]:NULL; }
size_t private_cars_inventory_count(void) { return nslots; }
const PrivateCar *private_cars_inventory_get(size_t i) { return i<nslots?&inventory[i]:NULL; }
const char *private_cars_generation(void) { return result<0?"":car_catalog_generation(); }
const char *private_cars_compatibility(void) { return result<0?"":car_catalog_compatibility(); }
const char *private_cars_error(void) { return problem[0]?problem:car_catalog_error(); }
int private_cars_save_allowed(void) { return carsave_public_save_allowed(); }
int private_cars_save_prepare(const char *path) { return carsave_public_save_prepare(path); }
void private_cars_save_complete(int committed) { carsave_public_save_complete(committed); }
const PrivateCar *private_cars_traffic_get(void) { return NULL; }
int private_cars_backup_before_write(const char *destination,const char *staged) {
    return carsave_public_backup_before_write(destination,staged);
}
