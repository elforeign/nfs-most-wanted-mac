#pragma once
#include "runtime.h"

#define SERVER_BASE 0x02000000u
#define SERVER_RESERVED_END 0x02100000u
#define SERVER_THUNK_BASE 0xF0100000u
int server_library_name(const char *name);
uint32_t server_load(Cpu *c, const char *path);
uint32_t server_handle(void);
uint32_t server_proc(uint32_t handle, uint32_t name_or_ordinal);
int server_free(Cpu *c, uint32_t handle);
int server_dispatch(Cpu *c, uint32_t target);
const char *server_import(uint32_t slot, unsigned *index);
int server_thread_attach(Cpu *c);
void server_thread_detach(Cpu *c);
