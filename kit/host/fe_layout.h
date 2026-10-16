// fe_layout.h - the offsets host_script_fe_screen walks from the game's front-end root (globals.toml fe_root
// fields). Defaults are NFSMW's FindPackageWithControl (005b0be0); a game with another layout sets its own.
#pragma once
#include "game_config.h"

#ifdef RECOMP_GLOBAL_FE_ROOT_ADDR
#ifndef RECOMP_GLOBAL_FE_ROOT_ENGINE
#define RECOMP_GLOBAL_FE_ROOT_ENGINE 0x00u
#endif
#ifndef RECOMP_GLOBAL_FE_ROOT_LIST
#define RECOMP_GLOBAL_FE_ROOT_LIST 0xe4u
#endif
#ifndef RECOMP_GLOBAL_FE_ROOT_LINK
#define RECOMP_GLOBAL_FE_ROOT_LINK 0x08u
#endif
#ifndef RECOMP_GLOBAL_FE_ROOT_CONTROL
#define RECOMP_GLOBAL_FE_ROOT_CONTROL 0x20u
#endif
#ifndef RECOMP_GLOBAL_FE_ROOT_NAME
#define RECOMP_GLOBAL_FE_ROOT_NAME 0x0cu
#endif
#ifndef RECOMP_GLOBAL_FE_ROOT_FORWARD
#define RECOMP_GLOBAL_FE_ROOT_FORWARD 0u
#endif
#endif
