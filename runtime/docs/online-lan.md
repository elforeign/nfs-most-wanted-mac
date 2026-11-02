# Online race (ws2_32.c)

Part of the runtime documentation; the index and switch lookup hint are in `runtime/README.md`.

                
                                                       
                                                                                                                         
                                                                                                                        
                                                                                                                          
                                                                                                                  
                                                                                                                     
                                                                                                                  
                                                                                                                      
                                                                                                                          
                                                                                                                                  
                                                                                                    
                                                                                                                    
                                                                                                          
                                                                                                                    
                                                                                                                    
                                                                                                                                    
                                                                                                                  
                                                                                                                                 
                                                          
                 

                
                               
             
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   
                                                                                                                                                                                                                                                                                                                                                          
                                                                                                                                                                                                          
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           
                                                                                                                                                                                                                                                                                             
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     
                                                                                                                                                                                                                                                                                          
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            
                 

LAN discovery now enables real SO_BROADCAST; the lobby accepts on the LAN interface (a loopback-only 9900 bind becomes ANY).
The default address policy still refuses internet destinations and DNS. Own-hostname fallback uses the route toward
192.168.1.1, matching the game's NetConn address probe; A1 keeps its configured identity. Offline proof:
`runtime/offline_tests.sh ws2_policy_test ws2_lan_fixture_test ws2_lan_test lanserver_diag_test`.
`ws2_policy` remains an alias for the same fake-socket C harness. Default native LAN tests use a test-only transport
adapter mapping every destination and bind to 127.0.0.1, with a guard against implicit ANY listen after a failed bind.
They test shared-port binding, each receive path independently, socket options and copy identity; unicast does not
establish broadcast fanout. `NFS_LAN_REAL=1 runtime/offline_tests.sh ws2_lan_test` deliberately tests real broadcast
send/receive, both UDP 9999 binders receiving both copies, explicit LAN bind and LAN TCP connection. Both modes use
one socket binary with argument-selected mutations; seven broken controls must fail. Socket/bind EPERM prints
`SOCKET PENDING: run on a socket-enabled host` and exits 3. Guard fixtures reject missing address conversion and unsafe implicit
listen without opening any sockets. Physical LAN delivery and macOS Local Network permission require the
deliberate host run.


                
                                                                                                   
                                                                                                                
                                                                                                                
                                                                                                      
                                                                                                                   
                                                                                                                   
                                                                                               


                                                                            


     
                                            
                                              
                                            
                                                                       
   



                                                                                                             
                                                                                                              
                                                                                                                       
                                                                                                                  
                                                                                                                       
                                                                                                                         
                                                                                                                  
                                                                                                                  
                                                                                                                      
                                                                                                                 
                                                                            
                                                                                                             


                                                                                                               
                                                                                                                        
                                                                                                                    
                                                                                                           
                 

| Feature | Format / behavior |
| --- | --- |
| Online free roam code (F10 > Online > Create room / Join) | `R-XXXX-XXXXX`: required `R-` prefix, eight base32 symbols carrying code-version 1 (8 bits) and nonzero room ID (32 bits), followed by one GF(32) checksum symbol. Alphabet `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`; lowercase accepted, separators required. Room 1 is `R-2622-2223S`. Every one-symbol substitution and adjacent swap of distinct symbols is rejected. Legacy endpoint codes are rejected. Codes identify rooms rather than passwords; access is free. Create uses OS randomness for a nonzero room; rooms have no persistent owner/reservation. Both actions use the same configured service as authenticated clients; neither starts an embedded listener or calls router setup. |
| Loopback free-roam fixture (`roam_loopback_fixture.c`) | `fixture [seconds [port [N]]]`: default 2 seconds, UDP 47811, one peer; port 0 selects an ephemeral port, N=0..6 selects distinct UDP peers in room 1. N=0 succeeds as a host-only baseline; invalid N exits 2 before binding. All sockets bind 127.0.0.1. Each peer drains its own socket, retries HELLO until welcomed, and sends STATE/LOOK at 20 Hz once a local car/appearance is observed. Stable Roadrunner names and the first local appearance are retained; ghosted cars occupy three forward pairs (12/18/24 m ahead, ±4/6/8 m lateral) in the local car's rotation. No pose is invented before a local car arrives. Per-peer summaries give socket port, assigned ID, welcomed, sent/received states, HELLO/LOOK sends and received bundles/entries. Each relay tick logs monotonic nanoseconds, wall/process CPU nanoseconds for the tick, cumulative client/max-tick gauges and counter deltas since the preceding tick (including intervening input); logger cost is outside the tick measurement. `roam_loopback_fixture_test.py` remains in the serial offline suite: N=0/6, default one-peer compatibility, six unique IDs/ports/names, moving/rotated host pose and appearance over UDP, a missing-HELLO failure, and N=7 refusal. Actual camera visibility and frame performance require a game run. |


## Shared relay assignment and beta checks

`roam_shared_endpoint.h` supplies `ROAM_SHARED_IPV4` and `ROAM_SHARED_PORT` independently
of room codes. Packaging must assign a numeric VPS IPv4 and UDP port using build definitions
(or the header defaults) after reviewing the actual service. The checked-in defaults are
empty/zero: no rented address is assumed. Missing/invalid configuration yields an actionable
unavailable status and a Leave request, before sockets or credentials can open. There is no
player-entered public-IP setting. The diagnostic `NFS_ROAM=ip:port` path is retained separately;
it has no address-bearing code generator.

Fixtures inject `127.0.0.1` and an ephemeral port into the same controller. The session fixture
counts forbidden router/embedded calls; shared fixtures use the production standalone relay
core and installation signatures over real loopback UDP. They check separate rooms selecting
one endpoint, state isolation, full/version/banned errors, Leave, retained credentials/cookies and
restart recovery. Code fixtures reject legacy codes and mutate endpoint-style encoding and
checksum validation; session mutations reintroduce mapping/listeners and must fail. Report
fixtures scrub new room codes and legacy endpoint codes from both previews and all six text
attachments. Off, six remote cars, 60 fps and 500-ms join/2-second heartbeat cadence remain.

Endpoint assignment, provider retention audit, packaged visuals and a real two-network game
session remain release checks. Loopback proof does not establish internet delivery or timing.

Observed offline evidence: the pre-change fake-router session had 125 checks with zero
failures, 20 embedded start/stop pairs and 16 mapping deletions. The shared controller
has 59 checks with zero failures and zero router/embedded calls. Restored mapping and
listener mutations each cause one failing assertion and three forbidden calls. The
room codec passes 48,568 checks; endpoint-style encoding, checksum bypass, prefix bypass
and version bypass produce 10,066 / 14,457 / 65 / 3 failures. The shared UDP fixture
observed 245 checks with zero failures; broken cookie recovery produces 21 failures.
The pre-response relay ran 247 checks with five failures; removing join-time ban
replies, live-ban notification and client-state clearing produces 2 / 3 / 2 failures.
The game-hook fixture passes 78 checks, retains four socket open/close pairs in its
explicit session/diagnostic cases, and performs no key/file/socket work over repeated
Off frames. Report preview/bundle proof passes 401 checks; removing the room-code rule causes
failures in both destination snapshots and the six text attachments. Guide requirements and all
25 existing layout/input mutation controls pass.

The relay sends nine-byte ERR_BANNED (5) after a valid-cookie HELLO for a banned
installation and sends the same bounded error before removing live banned sessions.
Source/global reply limits and global egress budgets apply. The production parser
clears connected state, membership and remote cars, and the session UI displays
“This installation is banned from the relay.” The loopback fixture verifies live
ban, fresh challenged reconnect, isolation of the other installation and recovery
after unban. UDP delivery remains best effort; this proof does not assign a release
endpoint or establish internet delivery.
