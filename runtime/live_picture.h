// live_picture.h - mid-race picture changes (live_picture.c).
#pragma once
struct Cpu;
void live_picture_request(const char *why);     // queue the game's device Reset (next frame)
void live_picture_on_reset(struct Cpu *c);       // v_Reset: release live rebuild resources and re-read boot state
void live_picture_apply_reset(int v);           // Setting.apply: change needs the Reset (Car reflections)
void live_picture_apply_reload(int v);          // Setting.apply: cached per-frame switches (contact shadows, soft smoke)
void live_picture_apply_fullscreen(int v);      // Setting.apply: Full screen mode
