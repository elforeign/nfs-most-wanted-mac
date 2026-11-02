#include "host.h"
#include "startup.h"
int main(int argc,char **argv) {
    int prefix=argc==3 && !strcmp(argv[1],"--boot-prefix");
    if (argc!=2 && !prefix) { printf("usage: nfsmw-headless [--boot-prefix] <speed.exe>\n"); return WIN_FAIL; }
    const char *path=argv[prefix?2:1];
    FILE *file=fopen(path,"rb");
    if (!file) win_fail("cannot open PE",0);
    if (fseek(file,0,SEEK_END)) win_fail("PE seek",0);
    long length=ftell(file);
    if (length<64 || length>0x10000000 || fseek(file,0,SEEK_SET)) win_fail("PE size",0);
    uint8_t *data=malloc((size_t)length);
    if (!data || fread(data,1,(size_t)length,file)!=(size_t)length) win_fail("PE read",0);
    fclose(file);
    if (!win_vm_init() || !win_load_pe(data,(size_t)length)) win_fail("invalid PE or VM allocation",0);
    free(data);
    if (!win_vm_commit(WIN_STACK_TOP-WIN_STACK_SIZE,WIN_STACK_SIZE) ||
        !win_vm_commit(WIN_MAIN_TEB,0x2000)) win_fail("main stack/TEB commit",0);
    win_import_bind();
    Cpu cpu;
    win_cpu_init(&cpu,WIN_MAIN_TEB,WIN_STACK_TOP-WIN_STACK_SIZE,WIN_STACK_TOP,1);
    M32(WIN_PEB+8)=0x400000;
    cpu.esp-=4; M32(cpu.esp)=0xdeadbeef;
    win_cpu=&cpu; threads_init();
    if (prefix && !win_startup_init(path)) win_fail("CRT startup registration",0);
    win_boot_prefix=prefix;
    win_first_import=!prefix;
    printf("win: image loaded, %u translated functions, entry %08X, %u imports\n",
           guest_func_count,speed_entry,speed_import_count); fflush(NULL);
    dispatch(&cpu,speed_entry);
    gil_release(); win_cpu=NULL; win_vm_destroy();
    printf("win: entry returned before any import\n"); return WIN_FAIL;
}
