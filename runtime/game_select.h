#pragma once

/* Host compilation only: translated guest code has no game selector. */
#define NFS_GAME_MW 1
#define NFS_GAME_PRIVATE 2
#ifndef NFS_GAME
#define NFS_GAME NFS_GAME_MW
#endif
#if NFS_GAME != NFS_GAME_MW && NFS_GAME != NFS_GAME_PRIVATE
#error Unsupported NFS_GAME
#endif
