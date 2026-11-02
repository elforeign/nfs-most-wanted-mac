// Release assignment is separate from room codes. Unassigned builds fail closed.
#pragma once
#ifndef ROAM_SHARED_IPV4
#define ROAM_SHARED_IPV4 ""
#endif
#ifndef ROAM_SHARED_PORT
#define ROAM_SHARED_PORT 0
#endif
// Packaging supplies a numeric service IPv4 and UDP port at build time.
// Offline fixtures override these macros with loopback and an ephemeral port.
