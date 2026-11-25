"""Execute production movie lifecycle and both boot-flow hooks with guest fixtures.

The decoder, clock and guest calls are controlled boundaries; lifecycle, queue
draining, resource disposal, boot-list edits and splash decisions are production.
Proof: movie lifecycle 17/17, boot hooks 53/53; omitted Stop/EOF each fail
4/2 checks, omitted bumper/prompt each fail 2/1 (all controls exit 1).
No game, movie assets, GPU or audio device is used here.
"""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2]


MOVIE = r'''
#include "host.h"
#include <pthread.h>
#include <math.h>
#include <stdlib.h>
#include <stdarg.h>
#define NFS_GAME 1
#define NFS_GAME_MW 1
typedef void MediaMovie;
typedef void DsBuffer;
typedef void DsDevice;
uint8_t *G;
STATE
static uint32_t playing_player;
static unsigned checks,failures,closed,stops,uploads,synced,yielded,freed,original[4];
static int host_ok=1,pcm,reenter;
static double clock_s;
enum {BB_CIN_PLAY,BB_CIN_STOP,BB_CIN_DESTROY,BB_CIN_END,BB_CIN_TIMEOUT};
#define CHECK(x) do {++checks;if(!(x)){++failures;fprintf(stderr,"line %d: %s\n",__LINE__,#x);}}while(0)
static void blackbox_mw_movie_edge(int edge,uint32_t self){(void)edge;(void)self;}
static void mlog(const char *fmt,...){(void)fmt;}
static void mix_stats(char *out,size_t n){snprintf(out,n,"fixture");}
static double now_s(void){return clock_s;}
static void audio_stop(void){++stops;mv.buf=NULL;}
static void audio_start(void){mv.lost=0;}
static void media_movie_close(MediaMovie *p){if(p)++closed;}
static void upload(Cpu *c,const uint8_t *p){(void)c;(void)p;++uploads;}
static void ds_set_movie_playing(int on){pcm=on;}
static int host_play(Cpu *c,uint32_t self){(void)c;if(!host_ok)return 0;mv.active=1;mv.player=self;mv.dec=(void *)1;return 1;}
void f_00542A40_orig(Cpu *c){++original[0];c->esp+=4;}
void f_00525F70_orig(Cpu *c){++original[1];c->esp+=4;}
void f_00517610_orig(Cpu *c){++original[2];c->esp+=4;}
void f_00525E60_orig(Cpu *c){++original[3];c->esp+=4;}
void f_00525F70(Cpu *c);
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a){
 (void)n;(void)a;
 if(fn==FN_SYNCTASK_run){++synced;if(reenter){reenter=0;Cpu q=*c;f_00525F70(&q);}}
 else if(fn==FN_THREAD_yield)++yielded;
 else if(fn==FN_MovieShapeFree){++freed;M32(MOVIE_SHAPE)=0;}
 else CHECK(0);
 return 0;
}
CLOSE
UPDATE
HOOKS
static Cpu cpu(uint32_t self){Cpu c={0};c.ecx=self;c.esp=0x80000;return c;}
int main(void){
 G=calloc(1,0x1000000);Cpu c=cpu(0x10000);f_00542A40(&c);
 CHECK(c.esp==0x80004 && mv.active && playing_player==0x10000 && pcm && !original[0]);
 // Reentrant loading may call Update again; the production guard runs loading once.
 mv.shown=calloc(1,4);mv.qcount=2;mv.q[0].bgra=calloc(1,4);mv.q[1].bgra=calloc(1,4);
 mv.q[0].pts=.1;mv.q[1].pts=.2;clock_s=.3;reenter=1;
 c=cpu(0x10000);f_00525F70(&c);
 CHECK(c.esp==0x80004 && synced==1 && yielded==1 && !M32(UPDATE_RECURSE));
 CHECK(!mv.qcount && mv.frames_dropped==1 && uploads==1 && mv.active);
 c=cpu(0x20000);f_00525F70(&c);CHECK(original[1]==1 && mv.active);
 c=cpu(0x20000);f_00517610(&c);CHECK(mv.active && playing_player==0x10000 && pcm);
 // Video/audio EOF tells the native state machine that the movie has ended.
 mv.eof=1;mv.buf=(void *)1;mv.ring_w=RATE;M32(0x10000+MP_NEXT)=5;
 c=cpu(0x10000);f_00525F70(&c);CHECK(mv.active && M32(0x10000+MP_NEXT)==5);
 mv.audio_eof=1;c=cpu(0x10000);f_00525F70(&c);CHECK(mv.active && M32(0x10000+MP_NEXT)==5);
 clock_s=2.1;c=cpu(0x10000);f_00525F70(&c);
 CHECK(!mv.active && !M32(0x10000+MP_NEXT) && closed==1 && stops==1);
 c=cpu(0x10000);f_00517610(&c);CHECK(!playing_player && !pcm && original[2]==2);
 // Replacing a movie closes the old decoder before the new one is opened.
 c=cpu(0x10000);f_00542A40(&c);c=cpu(0x20000);f_00542A40(&c);
 CHECK(mv.active && mv.player==0x20000 && closed==2);
 c=cpu(0x10000);f_00525E60(&c);CHECK(mv.active && pcm && playing_player==0x20000);
 shape_is_ours=1;c=cpu(0x20000);f_00525E60(&c);
 CHECK(!mv.active && !pcm && !playing_player && !shape_is_ours && closed==3 && original[3]==2);
 // Failed host decode and SkipMovies both delegate; a host-owned shape is retired.
 host_ok=0;shape_is_ours=1;M32(MOVIE_SHAPE)=0x50000;c=cpu(0x10000);f_00542A40(&c);
 CHECK(original[0]==1 && freed==1 && !shape_is_ours && c.esp==0x80004 && pcm);
 M32(SKIP_MOVIES)=1;host_ok=1;c=cpu(0x10000);f_00542A40(&c);
 CHECK(original[0]==2 && !mv.active);c=cpu(0x10000);f_00517610(&c);
 CHECK(!playing_player && !pcm);
 // A corrupt duration gets the same native state transition as EOF.
 M32(SKIP_MOVIES)=0;c=cpu(0x10000);f_00542A40(&c);mv.duration=1;clock_s=7;
 M32(0x10000+MP_NEXT)=5;c=cpu(0x10000);f_00525F70(&c);
 CHECK(!mv.active && !M32(0x10000+MP_NEXT) && closed==4);
 c=cpu(0x10000);f_00525E60(&c);CHECK(!pcm && !playing_player);
 printf("movie lifecycle checks=%u failures=%u\n",checks,failures);free(G);return failures?1:0;
}
'''

BOOT = r'''
#include "host.h"
#include <stdlib.h>
#include <stdarg.h>
uint8_t *G;int diag_on;
static unsigned checks,failures,heap=0x100000,ws=2,has_movie=1,has_pc=1,hidden,visible,language,notice,layout;
#define CHECK(x) do{++checks;if(!(x)){++failures;fprintf(stderr,"line %d: %s\n",__LINE__,#x);}}while(0)
uint32_t gstr(const char *s){uint32_t p=heap;heap+=(strlen(s)+4)&~3u;strcpy((char *)G+p,s);return p;}
void diag_event(const char *s,...){(void)s;}
int widescreen_boot_assets(void){return ws;}
int movies_x360_has(const char *name){CHECK(!strcmp(name,"eahd_bumper"));return has_movie;}
void widescreen_send_xenon_ws(Cpu *c,int title,uint32_t pkg){(void)c;CHECK(title==1 && pkg==0x50000);++layout;}
void f_0058E9C0_orig(Cpu *c){c->eax=123;c->esp+=4;}
void f_005A3030_orig(Cpu *c){c->eax=456;c->esp+=8;}
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a);
SOURCE
uint32_t call_guest(Cpu *c,uint32_t fn,int n,const uint32_t *a){
 (void)c;(void)n;
 if(fn==FN_malloc){uint32_t p=heap;heap+=a[0];return p;}
 if(fn==FN_FEManager_Get)return 0x60000;
 CHECK(a[0]==0x50000);
 if(fn==FN_FEngFindObject){CHECK(a[1]==OBJ_PROMPT_PC);return has_pc;}
 if(fn==FN_FEngSetInvisible){CHECK(a[1]==OBJ_PROMPT_PC);++hidden;}
 else if(fn==FN_FEngSetVisible){CHECK(a[1]==OBJ_PROMPT_X360);++visible;}
 else if(fn==FN_FEngSetLanguageHash){CHECK(a[1]==OBJ_PROMPT_X360);language=a[2];}
 else if(fn==FN_FEngSetScript){CHECK(a[1]==OBJ_ESRB_ONLINE && a[2]==SCRIPT_SHOW && a[3]==1);++notice;}
 else CHECK(0);
 return 0;
}
static void list(void){
 uint32_t head=0x10004,nodes[]={0x20004,0x20104,0x20204};
 const char *names[]={"LS_EAlogo.fng","LS_PSA.fng","MW_LS_AttractFMV.fng"};
 M32(head)=nodes[0];M32(head+4)=nodes[2];
 for(int i=0;i<3;++i){M32(nodes[i])=i==2?head:nodes[i+1];M32(nodes[i]+4)=i?nodes[i-1]:head;M32(nodes[i]+8)=gstr(names[i]);}
 M32(0x1000c)=nodes[0]-4;
}
static Cpu cpu(void){Cpu c={0};c.ecx=0x10000;c.esp=0x80000;return c;}
int main(void){
 G=calloc(1,0x1000000);
 for(int mode=0;mode<4;++mode){
  unsetenv("NFS_BOOT_FLOW");ws=mode==1?1:2;has_movie=mode!=2;if(mode==3)setenv("NFS_BOOT_FLOW","pc",1);
  list();Cpu c=cpu();f_0058E9C0(&c);CHECK(c.eax==123 && c.esp==0x80004);
  uint32_t first=M32(0x10004);CHECK(M32(M32(first)+4)==first && M32(M32(first+4))==first);
  CHECK(!strcmp(gptr(M32(first+8)),mode==0?"WS_LS_EA_hidef.fng":mode==3?"LS_EAlogo.fng":"WS_LS_EAlogo.fng"));
  CHECK(M32(0x1000c)==first-4);
  if(mode==0)CHECK(M32(0x6000c)==M32(first+8));
 }
 for(int mode=0;mode<4;++mode){
  unsetenv("NFS_SPLASH_PROMPT");if(mode)setenv("NFS_SPLASH_PROMPT",mode==3?"none":"pc",1);
  has_pc=mode!=2;hidden=visible=language=notice=layout=0;M32(0x10010)=0x50000;
  Cpu c=cpu();f_005A3030(&c);CHECK(c.eax==456 && c.esp==0x80008 && layout==1);
  CHECK(hidden==(mode==0) && visible==(mode==0 || mode==2) && notice==(mode==0));
  CHECK(language==(mode==0?STR_PROMPT_X360:mode==2?STR_PROMPT_PC:0));
 }
 printf("boot hooks checks=%u failures=%u\n",checks,failures);free(G);return failures?1:0;
}
'''


def main():
    source = (ROOT / 'runtime/movies.c').read_text()
    state = source[source.index('#define SKIP_MOVIES'):source.index('static uint32_t scratch;')]
    hooks = '\n'.join(function(source, 'void ' + name + '(') for name in
                      ('f_00542A40', 'f_00525F70', 'f_00517610', 'f_00525E60'))
    movie = MOVIE.replace('\nSTATE\n', '\n' + state + '\n')
    movie = movie.replace('\nCLOSE\n', '\n' + function(source, 'static void movie_close(') + '\n')
    movie = movie.replace('\nUPDATE\n', '\n' + function(source, 'static void movie_update(') + '\n').replace('HOOKS', hooks)
    boot = BOOT.replace('SOURCE', (ROOT / 'runtime/bootflow.c').read_text())
    variants = [('movie', movie, False),
                ('movie-no-stop', movie.replace('if (c->ecx == playing_player) playing_player = 0;', '(void)playing_player;'), True),
                ('movie-no-eof', movie.replace('M32(self + MP_NEXT) = 0;', '(void)self;'), True),
                ('boot', boot, False),
                ('boot-no-bumper', boot.replace('logo && ws == 2', 'logo && ws == 3'), True),
                ('boot-no-prompt', boot.replace('a[2] = x360 ? STR_PROMPT_X360 : STR_PROMPT_PC;', 'a[2] = STR_PROMPT_PC;'), True)]
    with tempfile.TemporaryDirectory(prefix='movie-lifecycle-') as scratch:
        tmp = Path(scratch)
        for label, text, broken in variants:
            unit, binary = tmp / 'fixture.c', tmp / 'fixture'
            unit.write_text(text)
            build = subprocess.run(['clang', '-O1', '-Wno-macro-redefined', '-I', str(ROOT / 'runtime'),
                                    '-I', str(ROOT / 'recomp'), str(unit), '-o', str(binary)], capture_output=True, text=True)
            assert build.returncode == 0, build.stderr[-3000:]
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            print(label + ': ' + result.stdout.strip())
            assert result.returncode == int(broken), (label, result.returncode, result.stderr[-2000:])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
