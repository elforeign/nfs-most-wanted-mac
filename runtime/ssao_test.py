#!/usr/bin/env python3
"""














































"""
import ast
import ctypes
from pathlib import Path
import re
import subprocess
import tempfile
import sys
import struct
import numpy as np

def dilate(seed,radius):
    h,w=seed.shape
    pad=np.pad(seed,radius)
    out=np.zeros_like(seed)
    for y in range(-radius,radius+1):
        for x in range(-radius,radius+1):
            if x*x+y*y<=radius*radius: out|=pad[y+radius:y+radius+h,x+radius:x+radius+w]
    return out

def classify(on,off,z,projection=(1.,1.)):
    # Classes depend only on depth, never on the AO output or the tested radius.
    edge=np.zeros(z.shape,dtype=bool)
    for axis in (0,1):
        a,b=(z[:-1],z[1:]) if axis==0 else (z[:,:-1],z[:,1:])
        e=np.abs(a-b)>np.maximum(.10,np.minimum(a,b)*.02)
        if axis==0: edge[:-1]|=e;edge[1:]|=e
        else: edge[:,:-1]|=e;edge[:,1:]|=e
    h,w=z.shape
    yy,xx=np.mgrid[:h,:w].astype(np.float32)
    p=np.stack((((xx+.5)/w*2-1)*projection[0]*z,(1-(yy+.5)/h*2)*projection[1]*z,z),axis=-1)
    l=p-np.roll(p,1,axis=1);r=np.roll(p,-1,axis=1)-p
    u=p-np.roll(p,1,axis=0);d=np.roll(p,-1,axis=0)-p
    dx=np.where((np.abs(l[...,2])<np.abs(r[...,2]))[...,None],l,r)
    dy=np.where((np.abs(u[...,2])<np.abs(d[...,2]))[...,None],u,d)
    n=np.cross(dx,dy);n/=np.maximum(np.linalg.norm(n,axis=-1,keepdims=True),1e-20)
    del p,l,r,u,d,dx,dy
    crease=np.zeros(z.shape,dtype=bool)
    for axis in (0,1):
        a,b=(n[:-1],n[1:]) if axis==0 else (n[:,:-1],n[:,1:])
        e=np.abs(np.sum(a*b,axis=-1))<.8660254 # >30 degree normal crease
        if axis==0: crease[:-1]|=e;crease[1:]|=e
        else: crease[:,:-1]|=e;crease[:,1:]|=e
    crease[:2]=False;crease[-2:]=False;crease[:,:2]=False;crease[:,-2:]=False
    contact=dilate(crease,8) # fixed 8 px contact region; independent of AO radius
    region_a=dilate(edge,2)|contact
    lum=np.array([.2125,.7154,.0721],dtype=np.float32)
    delta=np.sum((off.astype(np.float32)-on.astype(np.float32))*lum,axis=-1)
    dark=delta>3.0
    return int(np.count_nonzero(dark&region_a)),int(np.count_nonzero(dark&~region_a))

def read_depth(path):
    raw=Path(path).read_bytes()
    magic,w,h,near,far=struct.unpack('<4sIIff',raw[:20])
    assert magic in (b'AOZ1',b'AOZ2')
    offset=28 if magic==b'AOZ2' else 20
    projection=struct.unpack('<ff',raw[20:28]) if offset==28 else (1.,1.)
    assert len(raw)==offset+w*h*4
    d=np.frombuffer(raw,dtype='<f4',offset=offset).reshape(h,w).copy()
    return d,near*far/(far-d*(far-near)),projection

def read_image(path):
    from PIL import Image
    return np.asarray(Image.open(path).convert('RGB'),dtype=np.uint8)

# Pair images are copied from the same scene submission, before/after AO; each
# radius uses the identical original input, depth, coverage and projection.
if len(sys.argv)>1 and sys.argv[1]=='--compare':
    if len(sys.argv)!=5: raise SystemExit('usage: ssao_test.py --compare ON OFF DEPTH.aodz')
    on,off=[read_image(p) for p in sys.argv[2:4]]
    d,z,projection=read_depth(sys.argv[4])
    assert on.shape==off.shape==(*z.shape,3)
    a,b=classify(on,off,z,projection)
    print(f'same-input AO darkening >3 levels: (a) edge/contact {a}; (b) elsewhere {b}')
    raise SystemExit(0)

if len(sys.argv)>1 and sys.argv[1] in ('--validate','--validate-half','--validate-skipped','--validate-coverage-skipped','--validate-cpu'):
    directory=Path(sys.argv[2]);pairs=sorted(directory.glob('ssao_pair_*_normal_on.png'))
    assert pairs,'no same-input pairs found'
    if sys.argv[1]=='--validate-cpu': assert len(pairs)>=2,'CPU proof requires the 15 s and 25 s paired captures'
    for on_path in pairs:
        frame=re.search(r'ssao_pair_(\d+)_normal_on',on_path.name)[1]
        prefix=directory/f'ssao_pair_{frame}'
        off=read_image(str(prefix)+'_normal_off.png');on=read_image(str(prefix)+'_skipped_on.png') if sys.argv[1]=='--validate-skipped' else read_image(str(prefix)+'_uncovered_on.png') if sys.argv[1]=='--validate-coverage-skipped' else read_image(on_path)
        broken_off=read_image(str(prefix)+'_broken_off.png');broken=read_image(str(prefix)+'_broken_on.png')
        assert np.array_equal(off,broken_off),'negative control consumed a different input'
        d,z,projection=read_depth(directory/f'ssao_depth_{frame}_0.aodz')
        a,b=classify(on,off,z,projection);ba,bb=classify(broken,off,z,projection)
        if sys.argv[1]=='--validate-skipped': print(f'skipped apply (a)/(b)={a}/{b}; look check expected FAIL')
        assert a+b>0,'AO did not darken any pixels by >3 levels'
        assert bb>b,'radius x8 did not increase elsewhere errors'
        coverage=read_image(str(prefix)+'_normal_coverage.png')[...,0]
        if Path(str(prefix)+'_legacy_on.png').exists():
            expected_shape=((d.shape[0]+1)//2,(d.shape[1]+1)//2) if sys.argv[1]=='--validate-half' or Path(str(prefix)+'_legacy_coverage.png').exists() else d.shape
            assert coverage.shape==expected_shape,'coverage target dimensions do not match the selected contract'
        cpu_coverage=Path(str(prefix)+'_cpu_reference_coverage.png')
        if sys.argv[1]=='--validate-cpu': assert cpu_coverage.exists(),'CPU proof requires the original recorder reference'
        if cpu_coverage.exists():
            assert np.array_equal(coverage,read_image(cpu_coverage)[...,0]),'typed recorder changed the original coverage draw list'
            cpu_off=read_image(str(prefix)+'_cpu_reference_off.png')
            cpu_on=read_image(str(prefix)+'_cpu_reference_on.png')
            assert np.array_equal(off,cpu_off),'CPU reference consumed a different input'
            ca,cb=classify(cpu_on,off,z,projection)
            da,db=abs(a-ca)/max(ca,1),abs(b-cb)/max(cb,1)
            print(f'original/typed CPU path counts: {ca}/{cb} -> {a}/{b}; changes {da*100:.3f}%/{db*100:.3f}%')
            if sys.argv[1]!='--validate-coverage-skipped':
                assert da<=.04 and db<=.04,'CPU change altered darkening counts by >4%'
                assert np.array_equal(on,cpu_on),'typed recorder changed same-input AO pixels'
                print('original/typed CPU path: byte-identical image and coverage')
            uncovered=read_image(str(prefix)+'_uncovered_on.png')
            iy=((np.arange(d.shape[0])+.5)*coverage.shape[0]/d.shape[0]).astype(int)
            ix=((np.arange(d.shape[1])+.5)*coverage.shape[1]/d.shape[1]).astype(int)
            coverage_full=coverage[iy[:,None],ix]
            uncovered_errors=int(np.count_nonzero(np.any(uncovered!=off,axis=-1)&(coverage_full<255)))
            assert uncovered_errors>0,'coverage-skipped negative control did not damage transparency'
            print(f'coverage-skipped control: {uncovered_errors} transparent pixels changed; look check REJECTED')
        if coverage.shape!=d.shape:
            iy=((np.arange(d.shape[0])+.5)*coverage.shape[0]/d.shape[0]).astype(int)
            ix=((np.arange(d.shape[1])+.5)*coverage.shape[1]/d.shape[1]).astype(int)
            coverage=coverage[iy[:,None],ix]
        changed=np.any(on!=off,axis=-1)
        sky_errors=int(np.count_nonzero(changed&(d>=.999999)))
        masked_errors=int(np.count_nonzero(changed&(coverage<255)))
        print(f'sky changed={sky_errors}; transparent changed={masked_errors}')
        assert sky_errors==0 and masked_errors==0,'sky/transparency coverage failed'
        original_mask=Path(str(prefix)+'_legacy_coverage.png')
        if original_mask.exists():
            original_errors=int(np.count_nonzero(changed&(read_image(original_mask)[...,0]<255)))
            print(f'full-resolution reference mask changed={original_errors} ({"FAIL" if original_errors else "PASS"})')
            if sys.argv[1]!='--validate-cpu':
                assert original_errors==0,'half coverage missed original transparent fragments'
        cap_errors=int(np.count_nonzero(np.any(on.astype(float)<off.astype(float)*.92-.501,axis=-1)))
        assert cap_errors==0,'8% scene attenuation cap exceeded'
        legacy_path=Path(str(prefix)+'_legacy_on.png')
        if legacy_path.exists():
            legacy_off=read_image(str(prefix)+'_legacy_off.png')
            assert np.array_equal(off,legacy_off)
            la,lb=classify(read_image(legacy_path),off,z,projection)
            da,db=abs(a-la)/max(la,1),abs(b-lb)/max(lb,1)
            print(f'legacy/slim counts: {la}/{lb} -> {a}/{b}; changes {da*100:.3f}%/{db*100:.3f}%')
            assert da<=.10 and db<=.10,'slimming changed contact/elsewhere counts by >10%'
        skipped=Path(str(prefix)+'_skipped_on.png')
        if skipped.exists():
            sa,sb=classify(read_image(skipped),off,z,projection)
            assert sa+sb==0,'skipped apply should have no darkened pixels'
            print(f'skipped apply: {sa}/{sb}; look check REJECTED (no darkening)')
        final=Path(str(prefix)+'_final_on.png')
        if final.exists():
            final_off=read_image(str(prefix)+'_final_off.png')
            fa,fb=classify(read_image(final),final_off,z,projection)
            final_cpu=Path(str(prefix)+'_final_cpu_reference.png')
            if final_cpu.exists():
                cpu_final=read_image(final_cpu)
                cfa,cfb=classify(cpu_final,final_off,z,projection)
                print(f'complete chain original/typed CPU counts: {cfa}/{cfb} -> {fa}/{fb}')
                assert abs(fa-cfa)/max(cfa,1)<=.04 and abs(fb-cfb)/max(cfb,1)<=.04,'CPU change altered final chain counts by >4%'
                assert np.array_equal(read_image(final),cpu_final),'CPU change altered final post-chain pixels'
            ra,rb=classify(read_image(str(prefix)+'_final_reference.png'),final_off,z,projection)
            da,db=abs(fa-ra)/max(ra,1),abs(fb-rb)/max(rb,1)
            print(f'complete chain reference/fused: {ra}/{rb} -> {fa}/{fb}; changes {da*100:.3f}%/{db*100:.3f}%')
            assert fa+fb>0 and da<=.10 and db<=.10,'final post chain changed contact/elsewhere counts by >10%'
        print(f'frame {frame}: normal (a)/(b)={a}/{b}; radius x8={ba}/{bb}; elsewhere increase={bb-b}')
        print(f'identical input: YES; sky changed={sky_errors}; masked changed={masked_errors}; cap errors={cap_errors}')
    print('ssao CPU preservation validation: PASS (full-resolution coverage contract reported separately)' if sys.argv[1]=='--validate-cpu' else 'ssao native paired validation: PASS')
    raise SystemExit(0)

# Flat control proves the threshold and a broken full-plane darkening is rejected.
control_off=np.full((32,32,3),200.,dtype=np.float32)
control_z=np.full((32,32),5.,dtype=np.float32)
assert classify(control_off,control_off,control_z)==(0,0)
assert classify(control_off-3,control_off,control_z)==(0,0)
assert classify(control_off-4,control_off,control_z)[1]>0
print('delta counter: identical 0/0; 3 levels excluded; broken flat darkening detected')

ROOT=Path(__file__).resolve().parents[1]
source=(ROOT/'runtime/render_post.m').read_text()
macros={}
if any(not (ROOT/'runtime/gen'/name).exists() for name in ('x360_shader_lits.inc','x360_engine.inc')):
    print('ssao_test: SKIP (generated private shader tables absent; build first)')
    raise SystemExit(3)
for name in ('x360_shader_lits.inc','x360_engine.inc'):
    macros.update(dict(re.findall(r'#define (\w+) "(.*)"',(ROOT/'runtime/gen'/name).read_text())))
def shader(name):
    body=source.split('static const char *'+name+' =',1)[1].split(';\n',1)[0]
    return ''.join(ast.literal_eval(m[0]) if m[0].startswith('"') else macros[m[0]] for m in re.finditer(r'"(?:[^"\\]|\\.)*"|\bX360\w+\b',body))
def run(cmd):
    r=subprocess.run([str(x) for x in cmd],capture_output=True,text=True)
    if r.returncode: raise RuntimeError((r.stdout+r.stderr)[-3000:])
    return r.stdout

ao=shader('ssao_src')
gain=float(re.search(r'constant float AO_GAIN=([0-9.]+);',ao)[1])
pair=re.search(r'inline float ao_pair\(.*?\n}\n',ao,re.S)[0]
with tempfile.TemporaryDirectory(prefix='ssao-') as tmpname:
    tmp=Path(tmpname)
    # The exact shipping scalar function, compiled for CPU, including its radius/bias.
    c='#include <math.h>\n#define max fmaxf\n#define rsqrt(x) (1.0f/sqrtf(x))\n#define AO_GAIN '+str(gain)+'f\n'+pair.replace('inline float','static inline float')+r'''
void eval(const float *n,const float *v,float *out,int count) {
 for(int i=0;i<count;++i) {
  float sum=0;
  for(int j=0;j<4;++j) {
   const float *q=v+(i*4+j)*3;
   sum+=ao_pair(n[i*3],n[i*3+1],n[i*3+2],q[0],q[1],q[2],0.75f);
  }
  out[i]=fminf(0.08f,AO_GAIN*sum/4);
 }
}
'''
    (tmp/'kernel.c').write_text(c)
    run(['clang','-O2','-shared',tmp/'kernel.c','-o',tmp/'kernel.dylib'])
    lib=ctypes.CDLL(str(tmp/'kernel.dylib'))
    fp=np.ctypeslib.ndpointer(dtype=np.float32,flags='C_CONTIGUOUS')
    lib.eval.argtypes=[fp,fp,fp,ctypes.c_int]
    w,h=256,192
    yy,xx=np.mgrid[:h,:w]
    uv=np.stack(((xx+.5)/w,(yy+.5)/h),axis=-1)
    ray=np.concatenate((uv*np.array([2.,-2.])+[-1.,1.],np.ones((h,w,1))),axis=-1)
    def positions(z): return ray*z[...,None]
    def normals(p):
        l=p-np.roll(p,1,axis=1); r=np.roll(p,-1,axis=1)-p
        u=p-np.roll(p,1,axis=0); d=np.roll(p,-1,axis=0)-p
        dx=np.where((np.abs(l[...,2])<np.abs(r[...,2]))[...,None],l,r)
        dy=np.where((np.abs(u[...,2])<np.abs(d[...,2]))[...,None],u,d)
        n=np.cross(dx,dy); n/=np.maximum(np.linalg.norm(n,axis=-1,keepdims=True),1e-20)
        return np.where((np.sum(n*p,axis=-1)>0)[...,None],-n,n)
    def evaluate(z,flip=False):
        p=positions(z); n=normals(p)*(-1 if flip else 1)
        radius=np.minimum(np.maximum(.75/z[...,None]*.5,np.array([2/w,2/h])),np.array([24/w,24/h]))
        samples=[]
        for tap in range(4):
            a=(xx&1)+2*tap
            r=1+(((xx>>1)+yy)&1)
            q=uv+np.stack((np.cos(a*np.pi/4),np.sin(a*np.pi/4)),axis=-1)*radius*(r[...,None]*.5)
            valid=np.all((q>0)&(q<1),axis=-1)
            ix=np.clip((q[...,0]*w).astype(int),0,w-1);iy=np.clip((q[...,1]*h).astype(int),0,h-1)
            # MSL reconstructs at requested UV with point-sampled depth.
            qp=np.concatenate((q*np.array([2.,-2.])+[-1.,1.],np.ones((h,w,1))),axis=-1)*z[iy,ix,None]
            samples.append(np.where(valid[...,None],qp-p,0))
        v=np.ascontiguousarray(np.stack(samples,axis=-2),dtype=np.float32)
        out=np.empty(w*h,dtype=np.float32)
        lib.eval(np.ascontiguousarray(n,dtype=np.float32),v,out,w*h)
        return out.reshape(h,w)
    floor=1/np.maximum(-ray[...,1],1e-6)
    step=np.minimum(5.,floor)
    good=evaluate(step);broken=evaluate(step,True)
    corner=(np.abs(positions(step)[...,1]+1)<.20)&(np.abs(step-5)<.70)&(xx>40)&(xx<w-40)
    flat=(yy>h*.75)&(xx>40)&(xx<w-40)
    def passes(result):
        return float(result[corner].mean())>.01 and float(result[flat].max())<=.01
    print(f"corner: {good[corner].mean()*100:.4f}% mean darkening; flat floor max {good[flat].max()*100:.4f}%")
    print(f"flipped normals: {broken[corner].mean()*100:.4f}% corner, {broken[flat].max()*100:.4f}% flat; negative control {'REJECTED' if not passes(broken) else 'MISSED'}")
    assert passes(good),'production corner/flat check failed'
    assert not passes(broken),'flipped-normal negative control escaped'
    plane=evaluate(np.full((h,w),5.))
    assert plane[4:-4,4:-4].max()<=.01,'flat wall darkened >1%'
    assert good.max()<=.080001,'V53 stacking cap exceeded'
    metal=run(['xcrun','--find','metal']).strip()
    (tmp/'ssao.metal').write_text(shader('x360_src')+ao)
    run([metal,'-fmodules-cache-path='+str(tmp/'modules'),'-c',tmp/'ssao.metal','-o',tmp/'ssao.air'])
    render=(ROOT/'runtime/render.m').read_text()
    body=render.split('static const char *ssao_depth_src=',1)[1].split(';\n',1)[0]
    depth_shader=''.join(ast.literal_eval(m[0]) for m in re.finditer(r'"(?:[^"\\]|\\.)*"',body))
    (tmp/'coverage-depth.metal').write_text(depth_shader)
    run([metal,'-fmodules-cache-path='+str(tmp/'modules'),'-c',tmp/'coverage-depth.metal','-o',tmp/'coverage-depth.air'])
    print('half coverage: padded D16 per-sample depth shader compiled offline')
    print('ssao_test: PASS; production kernel / flipped negative control / two flat planes / 8% cap / offline MSL compile')
