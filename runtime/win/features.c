// Link surface for generated inline feature patches. A headless host has no
// renderer/assets/settings implementation; reaching one is a diagnosed failure.
#include "host.h"
// Traffic detail is a Mac renderer option; Windows retains the retail Off path.
int traffic_detail_on(void) { return 0; }
int diag_on;
#define UNSUPPORTED(name) win_fail("headless feature: " #name,0)
uint32_t car85_count(void) { UNSUPPORTED(car85_count); }
uint32_t car85_asset_name(uint32_t a) { (void)a; UNSUPPORTED(car85_asset_name); }
uint32_t extratrax_licensed_song_count(void) { UNSUPPORTED(extratrax_licensed_song_count); }
uint32_t posaudio_mode_read(uint32_t a) { (void)a; UNSUPPORTED(posaudio_mode_read); }
uint32_t posaudio_default_angle(uint32_t a) { (void)a; UNSUPPORTED(posaudio_default_angle); }
int attrib_x360_light_data(void) { UNSUPPORTED(attrib_x360_light_data); }
uint32_t x360_arrest_nis_cap(void) { UNSUPPORTED(x360_arrest_nis_cap); }
uint32_t x360audio_pool_size(void) { UNSUPPORTED(x360audio_pool_size); }
uint32_t x360_water_reflection_level(void) { UNSUPPORTED(x360_water_reflection_level); }
float x360_water_reflection_wetness(uint32_t a,float b) { (void)a;(void)b;UNSUPPORTED(x360_water_reflection_wetness); }
void diag_car_observe(Cpu *c,uint32_t a) { (void)c;(void)a; }
void diag_car_reject(uint32_t a) { (void)a; }
#define ONE(name) uint32_t name(uint32_t a) { (void)a;UNSUPPORTED(name); }
ONE(split_capture_address)
ONE(split_capture_player)
ONE(split_fx_view)
ONE(split_fx_player)
ONE(split_fx_drop_count)
ONE(split_shadow_id)
ONE(split_shadow_section_camera)
uint32_t split_fx_flare_step(uint32_t a,uint32_t b) { (void)a;(void)b;UNSUPPORTED(split_fx_flare_step); }
uint32_t split_fx_dimension(uint32_t a,unsigned b) { (void)a;(void)b;UNSUPPORTED(split_fx_dimension); }
double split_fx_rain_dt(double a) { (void)a;UNSUPPORTED(split_fx_rain_dt); }
float split_fx_rain_scale(float a) { (void)a;UNSUPPORTED(split_fx_rain_scale); }
uint32_t split_shadow_player_plat(void) { UNSUPPORTED(split_shadow_player_plat); }
uint32_t split_shadow_rain(void) { UNSUPPORTED(split_shadow_rain); }
void x360_shadow_casters(Cpu *c,uint32_t cull,uint32_t view) {
    (void)c;(void)cull;(void)view;UNSUPPORTED(x360_shadow_casters);
}
