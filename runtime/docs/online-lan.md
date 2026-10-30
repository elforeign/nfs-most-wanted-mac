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
| Online free roam code (F10 > Online > Host / Join) | Nine symbols as `XXXX-XXXXX`: eight base32 symbols encode IPv4 plus UDP port offset 47800–48055; the final symbol is a GF(32) checksum. Example: `TE29-43A2W` for 203.0.113.5:47800. Compact nine-symbol and lowercase input are accepted. Every one-symbol substitution and adjacent swap of distinct symbols is rejected immediately as “Invalid code”; old eight-symbol codes must be regenerated. Host tries finite router leases only; routers requiring permanent UPnP mappings need a manual UDP forward. |
