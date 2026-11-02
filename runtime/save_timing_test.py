"""Save timing: good 0; removed task timing hook 1 (missing stages).
Production file shims, Trax writer/completion and task hook, scratch files only.
Forces F_FULLFSYNC refusal to exercise and order every fsync fallback.
"""
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}', start) + 2] + '\n'


PREFIX = r'''
#include "host.h"
#include "save_timing.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>
static int test_fcntl(int fd, int command) { (void)fd; assert(command==F_FULLFSYNC); errno=ENOTSUP; return -1; }
#define fcntl test_fcntl
#include "durable.h"
#undef fcntl
uint8_t *G;
int diag_on;
const char *diag_dir;
volatile uint64_t perf_nframes=123;
static const char *userroot;
static char career[4096];
static unsigned callback_count;
__thread uint32_t last_error;   // thread-local like host.h
#define HANDLE_BASE 0x1000u
const char *userdata_dir(void) { return userroot; }
void diag_event(const char *fmt, ...) { (void)fmt; }
static int x360audio_open(const char *a,const char *b) { (void)a; (void)b; return -1; }
static void no_report_yet(void) {
    if (!diag_on) return;
    char path[4096]; snprintf(path,sizeof path,"%s/save_000001.txt",diag_dir);
    assert(access(path,F_OK)!=0);
}
void padbind_save_complete(int ok) { assert(ok); no_report_yet(); ++callback_count; }
void underglow_save_complete(int ok) { assert(ok); no_report_yet(); ++callback_count; }
enum { MAX_LIST=4, MW_SONGS=2 };
typedef struct { int32_t idx; uint8_t play; } TraxRow;
static TraxRow L[MAX_LIST]; static int Ln;
static const struct { const char *id; } kSongs[]={{"extra/00"}};
static int avail[]={0};
static void profile_name(uint32_t prof,char *out,size_t n) { (void)prof; snprintf(out,n,"Player"); }
static void sidecar_path(char *out,size_t n) { snprintf(out,n,"%s/extra_trax.ini",userroot); }
static void xlog(const char *fmt,...) { (void)fmt; }
'''
MAIN = r'''
void f_007F4F11_orig(Cpu *c) {
    (void)c;
    uint32_t h=file_open(career,career,0x40000000,3,2,0); assert(h!=UINT32_MAX);
    assert(file_write(h,"HEADER",6)==6);
    assert(file_write(h,"PAYLOAD",7)==7);
    assert(file_write(h,"END",3)==3);
    assert(file_flush(h));
    assert(file_close(h));
    no_report_yet();
}
int main(int argc,char **argv) {
    assert(argc==3); userroot=argv[1]; diag_dir=argv[2]; diag_on=1;
    assert(!mkdir(userroot,0700)); assert(!mkdir(diag_dir,0700));
    G=calloc(1,8192); assert(G);
    snprintf(career,sizeof career,"%s/career",userroot);
    Cpu cpu={0}; cpu.ecx=0x400;
    M32(cpu.ecx+0x70)=0xb; M32(cpu.ecx+0x300)=1;
    L[0]=(TraxRow){MW_SONGS,2}; Ln=1; sidecar_stage(cpu.ecx);
    errno=E2BIG;
    f_007F4F11(&cpu);
    assert(callback_count==2);
    FILE *f=fopen(career,"rb"); assert(f); char bytes[32]={0};
    assert(fread(bytes,1,sizeof bytes,f)==16); fclose(f);
    assert(!strcmp(bytes,"HEADERPAYLOADEND"));
    char trax[4096]; sidecar_path(trax,sizeof trax); assert(!access(trax,F_OK));
    diag_on=0; sidecar_stage(cpu.ecx); f_007F4F11(&cpu);
    char report[4096]; snprintf(report,sizeof report,"%s/save_000002.txt",diag_dir);
    assert(access(report,F_OK)!=0); free(G); return 0;
}
'''


def check(path):
    lines = path.read_text().splitlines()
    assert 'frame=123' in lines[0] and 'dropped=0' in lines[0]
    rows = []
    for line in lines[1:]:
        row = dict(re.findall(r'(\w+)=([^ ]+)', line))
        for name in ('start_ms', 'end_ms', 'duration_ms'):
            row[name] = float(row[name])
        row['id'], row['parent'] = int(row['id']), int(row['parent'])
        assert 0 <= row['start_ms'] <= row['end_ms'] < 10000, row
        assert abs(row['duration_ms'] - (row['end_ms']-row['start_ms'])) < 0.000003
        if row['parent'] >= 0:
            parent = rows[row['parent']]
            assert parent['start_ms'] <= row['start_ms'] <= row['end_ms'] <= parent['end_ms']
        rows.append(row)
    stages = [row['stage'] for row in rows]
    expected = ['write']*3 + ['flush_file_buffers', 'provenance.prepare', 'provenance.backup',
                             'fullfsync', 'fsync.fallback', 'close', 'rename', 'directory.sync',
                             'fullfsync', 'fsync.fallback', 'directory.close', 'trax.publication',
                             'stream.flush', 'fullfsync', 'fsync.fallback', 'stream.close', 'rename',
                             'directory.sync', 'fullfsync', 'fsync.fallback', 'directory.close',
                             'sidecar.padbind', 'sidecar.underglow', 'sidecar.provenance']
    assert stages == expected, f'stages: {stages}'
    for previous, following in zip(rows, rows[1:]):
        assert previous['start_ms'] <= following['start_ms']
        if following['parent'] == previous['parent']:
            assert previous['end_ms'] <= following['start_ms']
    for row in rows:
        if row['stage'] == 'fullfsync':
            assert int(row['result']) == -1
        if row['stage'] == 'fsync.fallback':
            assert int(row['result']) == 0
    assert rows[-1]['end_ms'] <= float(re.search(r'total_ms=([\d.]+)', lines[0])[1])
    return len(rows)


def main():
    boot = (ROOT/'runtime/boot.c').read_text()
    layer = boot.split('// BEGIN offline file layer:',1)[1].split('\n',1)[1].split('// END offline file layer',1)[0]
    trax = (ROOT/'runtime/extratrax.c').read_text()
    transaction = trax.split('// BEGIN offline trax transaction:',1)[1].split('\n',1)[1].split('// END offline trax transaction',1)[0]
    hook = function(boot, 'void f_007F4F11(Cpu *c)')
    source = PREFIX + layer + function(trax,'static void sidecar_save(') + transaction
    source += 'void f_007F4F11_orig(Cpu *c);\n' + hook + MAIN
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for variant in ('good','removed-hook'):
            code = source
            if variant == 'removed-hook':
                code = code.replace('save_timing_task_begin(self);', '(void)self;')
                assert code != source
            src, exe = tmp/(variant+'.c'), tmp/variant
            src.write_text(code)
            cmd = ['clang','-O1','-g','-Wall','-Wextra','-Werror','-Wno-unused-function','-Wno-sign-compare',
                   '-DSAVE_TIMING_ENABLED','-fsanitize=address,undefined',
                   '-I'+str(ROOT/'runtime'),'-I'+str(ROOT/'recomp'),'-I'+str(ROOT/'recomp/out'),
                   str(src),str(ROOT/'runtime/save_timing.c'),'-o',str(exe)]
            build = subprocess.run(cmd,capture_output=True,text=True)
            assert build.returncode == 0, build.stderr[-6000:]
            run = subprocess.run([str(exe),str(tmp/(variant+'-data')),str(tmp/(variant+'-diag'))],capture_output=True,text=True)
            assert run.returncode == 0, run.stderr[-4000:]
            try:
                count = check(tmp/(variant+'-diag')/'save_000001.txt')
            except AssertionError:
                if variant == 'good':
                    raise
                print('removed task timing hook: check exit 1 (0 stages)')
            else:
                assert variant == 'good', 'removed hook passed'
                print(f'good: check exit 0 ({count} ordered spans; 4 fullfsync + 4 fallback; diag off no report)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
