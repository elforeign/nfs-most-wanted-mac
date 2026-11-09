// pub_settings.c - F10 rows only the public build has (build.sh compiles it with NFS_PUBLIC=1).
// Car reflections: built in as Sharp for the owner (2026-10-28), but Sharp costs +117 MB, so public players on smaller
                                                                                                                            
// created and after each live Reset (live_picture.c); a change exports the choice and queues that Reset.
#include "settings.h"
#include "live_picture.h"

static const char *const kCarReflection[] = {"Standard", "Sharp"}, *const kCarReflectionV[] = {"pc", "sharp"};
static Setting s_car_reflection = {.id = "graphics.car_reflection", .category = SET_CARS, .order = 15, .label = "Car reflections",
    .help = "Sharp shows finer detail in the paint's reflections and uses about 120 MB more memory; Standard is the original. The picture redraws once.",
    .type = SET_CHOICE, .choices = kCarReflection, .values = kCarReflectionV, .nchoices = 2, .def = 1, .env = "NFS_CAR_REFLECTION", .live_env = 1,
    .apply = live_picture_apply_reset};
__attribute__((constructor)) static void pub_settings_register(void) { settings_add(&s_car_reflection); }
