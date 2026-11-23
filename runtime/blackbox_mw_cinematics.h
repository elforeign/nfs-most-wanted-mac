#ifndef NFS_BLACKBOX_MW_CINEMATICS_H
#define NFS_BLACKBOX_MW_CINEMATICS_H
#include "blackbox.h"

void blackbox_mw_cinematics(uint64_t frame, uint64_t now);
void blackbox_mw_movie_edge(BlackboxCinematicEdge edge, uint32_t player);
void blackbox_mw_nis_edge(BlackboxCinematicEdge edge, uint32_t activity, uint32_t name);
#endif
