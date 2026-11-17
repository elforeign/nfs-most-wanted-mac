#pragma once
#include "game_select.h"
#if NFS_GAME == NFS_GAME_MW
#define GAME_EXECUTABLE "speed.exe"
#else
#include "game_image.h"
#define f_006C1990 mw_f_006C1990
#define f_006C1990_orig mw_f_006C1990_orig
#include "game_display_bindings.h"
#endif
