"""Draw dumps: 3 frames x 1,000 COM calls, frame-list calls 3,000 -> 3.

Pinned original texdump_frame fails the <=3-call budget (exit 1). Both versions
write identical COM dump bytes. Key/race marks invalidate within a frame;
probe selection retains its live observation. Diag Off does zero frame-list
calls. SSAO: 1,000 draws / 100 records, per-frame clocks 3,200 -> 224;
proof 3,200 -> 224; SSAO off 3,000 -> 24; diagnostics off 0 -> 0. At 10,000
draws / 1,000 records: 32,000 -> 2,024; SSAO off 30,000 -> 24. Budget is
24 clocks/frame + 2/record, independent of scene draw count. Pinned per-draw
and original fragments fail it (exit 1); recorder-only regression still fails
six field checks per fixture size (exit 2). Sampled scene/setup sums 111/68
extrapolate to 13,875/8,500 ticks at 1,000 draws; exact record is 3,000 ticks
(proof 9,000). Distinct CSV scopes, nonzero replay/post, two active frames and
an empty frame verify estimates, exclusion and reset; 100-call frames check
partial sampling. All 8 CSV fields retain
their scopes; draw/setup are sampled estimates. Autodiscovered by
runtime/offline_tests.sh; no assets or GPU required.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

RT=Path(__file__).resolve().parent
BASELINE='int texdump_frame(void) { return diag_on && diag_frame_wanted(dev.frames, "BOOT_DIAG_DRAWS"); }'
BASELINE_SHA='173a860f82a60d8066beb28bc8570c8cf9d4c24d7bb5fab0fc9cbb7699f04b3b'


def function(source,name):
    m=re.search(r'^(?:static )?[\w<>* ]+\b'+name+r'\([^;]*?\)\s*\{',source,re.M)
    assert m,name
    i,depth=m.end(),1
    while depth:
        depth+=(source[i]=='{')-(source[i]=='}');i+=1
    return source[m.start():i]


PRE=r'''
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
static int diag_on=1,parser_calls,probe_active;
static uint64_t race_frame,key_frame;
static float sim_prev=-1,sim_cur=-1;
static const char *list, *diag_dir;
static struct {uint64_t frames;} dev;
static char *test_getenv(const char *n){(void)n;return (char *)list;}
#define getenv test_getenv
static int probe_dump_frame(void){return probe_active;}
'''
MAIN=r'''
int main(int argc,char **argv){
    if(argc<3)return 2;diag_dir=argv[1];list=argv[2];
    for(int frame=0;frame<3;frame++){
        dev.frames=frame;
        for(int call=0;call<1000;call++){
            if(render_dump_active())render_dump_call("fixture(1, 2, 3, 4, 5, 6) -> 0 @12345678");
        }
    }
    // render_dump_call also checks the selector. Budget only the common Off
    // selection run; selected dump runs compare exact output separately.
    printf("parser_calls=%d\n",parser_calls);
    int initial_calls=parser_calls;
    if(!strcmp(list,"key+0")){
        dev.frames=3;if(render_dump_active())return 2;
        key_frame=3;if(!render_dump_active())return 2;
        key_frame=4;if(render_dump_active())return 2;
    }
    if(!strcmp(list,"race+0")){
        dev.frames=3;if(render_dump_active())return 2;
        race_frame=3;if(!render_dump_active())return 2;
    }
    if(!strcmp(list,"probe")){
        dev.frames=3;probe_active=1;if(!render_dump_active())return 2;
        probe_active=0;if(render_dump_active())return 2;
    }
    if(!strcmp(list,"sim+1")){
        dev.frames=3;sim_prev=0;sim_cur=1;
        if(!render_dump_active())return 2;
    }
    diag_on=0;int before=parser_calls;
    for(int i=0;i<1000;i++)if(render_dump_active())return 2;
    if(parser_calls!=before)return 2;
    return !strcmp(list,"99") && initial_calls>3;
}
'''


def main():
    assert hashlib.sha256(BASELINE.encode()).hexdigest()==BASELINE_SHA
    render=(RT/'render.m').read_text();diag=(RT/'diag.c').read_text()
    parser=function(diag,'diag_frame_wanted').replace('int diag_frame_wanted(', 'static int parsed_frame_wanted(')
    counter='\nstatic int diag_frame_wanted(uint64_t frame,const char *name){++parser_calls;return parsed_frame_wanted(frame,name);}\n'
    selector=function(diag,'diag_draw_frame_wanted')
    dump='\n'.join(function(render,n) for n in ('dump_file','render_dump_active','render_dump_call'))
    results={}
    with tempfile.TemporaryDirectory(prefix='draw-diag-') as td:
        td=Path(td)
        for label,body in (('old',BASELINE),('current',function(render,'texdump_frame'))):
            src=td/(label+'.c');exe=td/label
            src.write_text(PRE+parser+counter+(selector if label=='current' else '')+body+dump+MAIN)
            p=subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror',str(src),'-o',str(exe)],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-2500:]
            for selection in ('99','0,2','race+0','key+0','sim+1','probe'):
                out=td/(label+'-'+selection);out.mkdir()
                p=subprocess.run([str(exe),str(out),selection],capture_output=True,text=True)
                expected=1 if label=='old' and selection=='99' else 0
                assert p.returncode==expected,(label,selection,p.returncode,p.stdout,p.stderr)
                if selection=='99':
                    calls=3000 if label=='old' else 3
                    assert p.stdout.strip()==f'parser_calls={calls}',p.stdout
                    print(f'{label}: frame-list calls {calls}/3000 COM calls; budget exit {p.returncode}')
                results[label,selection]={f.name:f.read_bytes() for f in out.iterdir()}
        for selection in ('0,2','race+0','key+0','sim+1','probe'):
            assert results['old',selection]==results['current',selection],selection
        assert len(results['current','0,2'])==2
        assert all(len(content.splitlines())==1000 for content in results['current','0,2'].values())
        print('dump bytes identical: 2 frames x 1000 lines; race/key/sim/probe transitions PASS; Off=0 parser calls')

CLOCK_BASELINE = 'static void ssao_record(uint32_t type,uint32_t prims,int indexed,int32_t base_vertex,uint32_t min_index,uint32_t num_vertices,\n                        uint32_t start,uint32_t up_vertices,uint32_t up_stride,uint32_t up_indices,uint32_t up_index_fmt) {\n    uint64_t t0=ssao_cpu_diag?host_ticks():0;\n    MatTreesDraw saved_tree=mat_trees_draw;\n    ssao_saved_enc=enc;ssao_saved_eb=eb;ssao_saved_dirty=pass_dirty;ssao_saved_samples=enc_samples;\n    memcpy(ssao_saved_ms,enc_ms,sizeof enc_ms);memcpy(ssao_saved_resolve,enc_resolve,sizeof enc_resolve);\n    uint32_t saved_alpha=dev.rs[15];\n    for(int reference=0;reference<=(ssao_proof()?2:0);++reference){\n    // Each replay must prepare the same tree material. The previous replay\n    // consumed its skip state; without restoring it the proof used PC trees.\n    mat_trees_draw=saved_tree;if(ssao_tree_fringe)mat_trees_draw.skip=TREES_NO_DRAW;\n    dev.rs[15]=reference==1?saved_alpha:0;\n    ssao_reference_record=reference==1;ssao_cpu_reference_record=reference==2;ssao_coverage_z=reference==1?ssao_depth:ssao_half_depth;\n    enc=(id<MTLRenderCommandEncoder>)(reference==2?ssao_cpu_reference_rec:reference?ssao_reference_rec:ssao_rec);eb=reference==2?ssao_cpu_reference_eb:reference?ssao_reference_eb:ssao_eb;pass_dirty=0;\n    memset(enc_ms,0,sizeof enc_ms);memset(enc_resolve,0,sizeof enc_resolve);\n    RenderTex *target=ssao_target();enc_samples=(uint32_t)(target->ms?target->ms.sampleCount:1);ssao_replay=2;\n    draw_impl(type,prims,indexed,base_vertex,min_index,num_vertices,start,up_vertices,up_stride,up_indices,up_index_fmt);\n    if(reference==2)ssao_cpu_reference_eb=eb;else if(reference)ssao_reference_eb=eb;else ssao_eb=eb;\n    }\n    dev.rs[15]=saved_alpha;ssao_reference_record=ssao_cpu_reference_record=0;ssao_coverage_z=ssao_half_depth;ssao_replay=0;enc=ssao_saved_enc;eb=ssao_saved_eb;pass_dirty=ssao_saved_dirty;enc_samples=ssao_saved_samples;\n    memcpy(enc_ms,ssao_saved_ms,sizeof enc_ms);memcpy(enc_resolve,ssao_saved_resolve,sizeof enc_resolve);ssao_saved_enc=nil;mat_trees_draw=saved_tree;\n    if(ssao_cpu_diag){ssao_diag_record_ns+=host_ticks()-t0;++ssao_diag_draws;}\n}\n// draw timing\n    uint64_t draw_t0=ssao_cpu_diag?host_ticks():0;\n    if(ssao_cpu_diag)ssao_diag_draw_ns+=host_ticks()-draw_t0;\n    uint64_t setup_t0=ssao_cpu_diag?host_ticks():0,record_before=ssao_diag_record_ns;\n    if(ssao_cpu_diag)ssao_diag_setup_ns+=host_ticks()-setup_t0-(ssao_diag_record_ns-record_before);'
CLOCK_SHA = '19918d283eceed4629bf49e6b1c6ed63187062dc2711a2676cf9d72d095ca4c3'
PER_DRAW_SCOPES = (
    '    uint64_t draw_t0=ssao_cpu_diag?host_ticks():0;\n',
    '    uint64_t setup_t0=ssao_cpu_diag?host_ticks():0,record_before=ssao_diag_record_ns;\n'
    '    if(ssao_cpu_diag)ssao_diag_draw_ns+=setup_t0-draw_t0;\n',
    '    if(ssao_cpu_diag)ssao_diag_setup_ns+=host_ticks()-setup_t0-(ssao_diag_record_ns-record_before);\n')
PER_DRAW_SHA = '59bbb0b5afd47b4167abd17b0df30d83cacf4b70819a670f1b804bb9a11d6fa8'
CLOCK_PRE = r'''
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static int ssao_cpu_diag=1,clocks,proof,draws,failures;
static uint64_t now;
static uint64_t host_ticks(void){++clocks;return now;}
static uint64_t frame_serial;
static const char *diag_dir;
static float sim=1;
#define MF32(a) sim
static uint64_t ssao_diag_record_ns,ssao_diag_draw_ns,ssao_diag_setup_ns,ssao_diag_replay_ns,ssao_diag_post_ns,ssao_diag_draws;
static uint64_t ssao_diag_scene_calls,ssao_diag_scene_samples;
typedef struct{int skip;}MatTreesDraw;
#define TREES_NO_DRAW 0
static MatTreesDraw mat_trees_draw={7};
typedef struct{id<MTLTexture>ms;}RenderTex;
static RenderTex target;
static RenderTex *ssao_target(void){now+=5;return &target;}
static int ssao_proof(void){return proof;}
static int ssao_tree_fringe=1,ssao_reference_record,ssao_cpu_reference_record,ssao_replay;
static struct{int value;}eb,ssao_eb,ssao_reference_eb,ssao_cpu_reference_eb,ssao_saved_eb;
static id<MTLRenderCommandEncoder>enc,ssao_saved_enc;
static id ssao_rec,ssao_reference_rec,ssao_cpu_reference_rec;
static id<MTLTexture>ssao_coverage_z,ssao_depth,ssao_half_depth;
static RenderTex *enc_ms[5],*ssao_saved_ms[5];
static uint8_t enc_resolve[5],ssao_saved_resolve[5];
static int pass_dirty,ssao_saved_dirty;
static uint32_t enc_samples,ssao_saved_samples;
static struct{uint32_t rs[256];}dev;
#define draw_impl(...) do{now+=23+draws%5;mat_trees_draw.skip=99;++draws;}while(0)
'''


def clock_scopes(render):
    """Retain production timing statements around the two mocked work scopes."""
    wrapper=function(render,'render_draw')
    begin=wrapper.index('    if(ssao_cpu_diag)ssao_diag_frame();')
    begin=wrapper.index('\n',begin)+1
    scene=wrapper.index('    ssao_tree_fringe=0;',begin)
    setup=wrapper.index('    if (!ssao_can_record())',scene)
    finish=wrapper.index('    if (sign_freeze)',setup)
    scene_end=wrapper.rindex('    }\n',scene,setup)+len('    }\n')
    setup_end=wrapper.rindex('    }\n',setup,finish)+len('    }\n')
    # The work scopes include lamp submissions and coverage preparation, while
    # their timing statements remain verbatim from the production wrapper.
    assert 'draw_impl(' in wrapper[scene:scene_end]
    assert 'ssao_record(' in wrapper[setup:setup_end]
    return wrapper[begin:scene],wrapper[scene_end:setup],wrapper[setup_end:finish]


def clock_check(render, td):
    assert hashlib.sha256(CLOCK_BASELINE.encode()).hexdigest()==CLOCK_SHA
    assert hashlib.sha256('\n'.join(PER_DRAW_SCOPES).encode()).hexdigest()==PER_DRAW_SHA
    old_record, old_timing=CLOCK_BASELINE.split('\n// draw timing\n')
    timing=old_timing.splitlines()
    old_scopes=(timing[0], '\n'.join(timing[1:3]), timing[3])
    # Reconstruct the reviewed regression from the pinned original fragment,
    # including its recorder-only draw/setup accounting. Pin the result too.
    reviewed_record=old_record.replace(
        '    MatTreesDraw saved_tree=', '    uint64_t draw_ns=0;\n    MatTreesDraw saved_tree=')
    draw_call='    draw_impl(type,prims,indexed,base_vertex,min_index,num_vertices,start,up_vertices,up_stride,up_indices,up_index_fmt);'
    reviewed_record=reviewed_record.replace(draw_call,
        '    uint64_t draw_t0=ssao_cpu_diag?host_ticks():0;\n'+draw_call+
        '\n    if(ssao_cpu_diag)draw_ns+=host_ticks()-draw_t0;')
    reviewed_record=reviewed_record.replace(
        '    if(ssao_cpu_diag){ssao_diag_record_ns+=host_ticks()-t0;++ssao_diag_draws;}',
        '''    if(ssao_cpu_diag){
        uint64_t elapsed=host_ticks()-t0;
        ssao_diag_record_ns+=elapsed;ssao_diag_draw_ns+=draw_ns;
        ssao_diag_setup_ns+=elapsed-draw_ns;++ssao_diag_draws;
    }''')
    assert hashlib.sha256(reviewed_record.encode()).hexdigest()=='8b95be8e5d16d7930649daa67619b3db3381c5b2faa9aece052eecda467951cb'
    csv=function(render,'ssao_diag_frame')
    results={}
    regression_failures=0
    variants=(('old',old_record,old_scopes),
              ('reviewed',reviewed_record,('', '', '')),
              ('per_draw',old_record,PER_DRAW_SCOPES),
              ('current',function(render,'ssao_record'),clock_scopes(render)))
    for label,body,(begin,between,end) in variants:
        driver=r'''
static void scene(int n,int coverage){
    for(int i=0;i<n;i++){
''' +begin+r'''
        now+=13+i%3;
'''+between+r'''
        now+=7+i%4;
        if(coverage && i%10==0)ssao_record(1,1,0,0,0,0,0,0,0,0,0);
'''+end+r'''
    }
}
int main(int argc,char **argv){@autoreleasepool{
    if(argc!=4)return 3;diag_dir=argv[1];int mode=atoi(argv[2]),n=atoi(argv[3]);
    ssao_cpu_diag=mode!=0;proof=mode==2;int coverage=mode!=3;
    frame_serial=1;if(ssao_cpu_diag)ssao_diag_frame();
    scene(n,coverage);
    int expected_draws=coverage?n/10*(proof?3:1):0;
    if(draws!=expected_draws || mat_trees_draw.skip!=7 || ssao_replay || ssao_reference_record || ssao_cpu_reference_record)return 3;
    uint64_t scene_ns=ssao_diag_scene_samples?(n>=1000?111:13):13*n+(n/3)*3+(n%3==2);
    uint64_t setup_ns=ssao_diag_scene_samples?(n>=1000?68:7):7*n+(n/4)*6;
    if(ssao_diag_draw_ns!=(ssao_cpu_diag?scene_ns:0))++failures;
    if(ssao_diag_setup_ns!=(ssao_cpu_diag?setup_ns:0))++failures;
    if(ssao_diag_record_ns!=(ssao_cpu_diag?expected_draws*30:0))++failures;
    if(ssao_diag_draws!=(ssao_cpu_diag && coverage?n/10:0))++failures;
    int frame_clocks=clocks;
    uint64_t first_scene=ssao_diag_draw_ns,first_setup=ssao_diag_setup_ns,first_record=ssao_diag_record_ns;
    printf("failures=%d clocks=%d draws=%d scene=%llu setup=%llu record=%llu\n",failures,clocks,draws,
           (unsigned long long)ssao_diag_draw_ns,(unsigned long long)ssao_diag_setup_ns,(unsigned long long)ssao_diag_record_ns);
    if(ssao_cpu_diag){
        ssao_diag_replay_ns=1700;ssao_diag_post_ns=2900;
        frame_serial=2;ssao_diag_frame();
        if(ssao_diag_draw_ns || ssao_diag_setup_ns || ssao_diag_record_ns || ssao_diag_draws ||
           ssao_diag_scene_calls || ssao_diag_scene_samples || ssao_diag_replay_ns || ssao_diag_post_ns)return 3;
        scene(n,coverage);
        if(clocks!=2*frame_clocks || draws!=2*expected_draws ||
           ssao_diag_draw_ns!=first_scene || ssao_diag_setup_ns!=first_setup || ssao_diag_record_ns!=first_record)return 3;
        ssao_diag_replay_ns=1700;ssao_diag_post_ns=2900;
        frame_serial=3;ssao_diag_frame();frame_serial=121;ssao_diag_frame();
    }
    else if(ssao_diag_replay_ns || ssao_diag_post_ns)return 3;
    // Independent budget: eight samples/frame, three reads/sample, two/record.
    return failures?2:frame_clocks>(ssao_cpu_diag?24+(coverage?2*n/10:0):0);
}}
'''
        src=td/(label+'-clock.m');exe=td/(label+'-clock')
        src.write_text(CLOCK_PRE+csv+body+driver)
        p=subprocess.run(['clang','-O1','-w','-fobjc-arc',str(src),'-framework','Metal','-framework','Foundation','-o',str(exe)],capture_output=True,text=True)
        assert p.returncode==0,p.stderr[-2500:]
        for n,mode in ((n,mode) for n in (100,1000,10000) for mode in (0,1,2,3)):
            out=td/(label+'-clock-'+str(n)+'-'+str(mode));out.mkdir()
            p=subprocess.run([str(exe),str(out),str(mode),str(n)],capture_output=True,text=True)
            expected_exit=2 if mode and label=='reviewed' else 1 if mode and label in ('old','per_draw') else 0
            assert p.returncode==expected_exit,(label,mode,p.returncode,p.stdout,p.stderr)
            expected_clocks={'old':(0,42*n//10,42*n//10,4*n),
                             'reviewed':(0,4*n//10,8*n//10,0),
                             'per_draw':(0,32*n//10,32*n//10,3*n),
                             'current':(0,(24 if n>=1000 else 3)+2*n//10,
                                        (24 if n>=1000 else 3)+2*n//10,24 if n>=1000 else 3)}[label][mode]
            expected_draws=(n//10,n//10,3*n//10,0)[mode]
            fields=dict(re.findall(r'(\w+)=(\d+)',p.stdout))
            assert int(fields['clocks'])==expected_clocks,p.stdout
            assert int(fields['draws'])==expected_draws,p.stdout
            failures=2 if label=='reviewed' and mode else 0
            assert int(fields['failures'])==failures,p.stdout
            if label=='reviewed':regression_failures+=failures
            if mode:
                lines=(out/'ssao_cpu.csv').read_text().splitlines()
                assert lines[0]=='frame,sim,draw_ms,setup_ms,record_ms,replay_ms,post_ms,coverage_draws'
                assert len(lines)==4
                if label!='reviewed':
                    scene_ticks=(111*n//8 if n>=1000 else 13*n) if label=='current' else 13*n+3*(n//3)+(n%3==2)
                    setup_ticks=(8.5*n if n>=1000 else 7*n) if label=='current' else 8.5*n
                    expected=['1','1.0000',f'{scene_ticks/1e6:.6f}',f'{setup_ticks/1e6:.6f}',
                              f'{expected_draws*30/1e6:.6f}','0.001700','0.002900',str(n//10 if mode!=3 else 0)]
                    assert lines[1].split(',')==expected,(label,mode,lines[1])
                    assert lines[2].split(',')==['2']+expected[1:],(label,mode,lines[2])
                    assert lines[3].split(',')==['3','1.0000']+['0.000000']*5+['0']
                results[label,n,mode]=lines
            else:
                assert not (out/'ssao_cpu.csv').exists()
            print(f'{label} SSAO {n} calls mode {mode}: '+p.stdout.strip()+f'; exit {p.returncode}')
    for n in (100,1000,10000):
        for mode in (1,2,3):
            assert results['old',n,mode]==results['per_draw',n,mode],(n,mode,'baseline scope differs')
            assert results['reviewed',n,mode]!=results['current',n,mode],(n,mode,'regression escaped')
    assert regression_failures==18
    print('SSAO CSV: sampled scene/setup, exact record/replay/post; reviewed regression 18 failures -> 0; frame reset PASS')


if __name__=='__main__':
    main()
    with tempfile.TemporaryDirectory(prefix='ssao-clock-') as scratch:
        clock_check((RT/'render.m').read_text(),Path(scratch))
