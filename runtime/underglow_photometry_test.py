"""Measured-strip production proof: good 73/73; 100-mm mutation 8 failures.
Analytic centre reference uses rounded lm/m constants, not sample lumens.
"""
import ctypes as C
import math
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
class V(C.Structure):
    _fields_ = [('x', C.c_double), ('y', C.c_double), ('z', C.c_double)]
A = C.c_double * 3

def proof(lib):
    mix = lib.ug_mix_xyz
    mix.argtypes = [C.POINTER(C.c_double), C.POINTER(C.c_double)]
    preset = lib.ug_preset_rgb
    preset.argtypes = [C.c_uint, C.POINTER(C.c_double)]
    gamut = lib.ug_xyz_rec709
    gamut.argtypes = [C.POINTER(C.c_double), C.POINTER(C.c_double), C.POINTER(C.c_double)]
    decode = lib.ug_srgb_to_linear
    decode.argtypes = [C.c_double, C.POINTER(C.c_double)]
    seg = lib.ug_segment_lux
    seg.argtypes = [C.c_double, V, V, V, V, V, C.POINTER(C.c_double)]
    good = bad = 0
    def check(ok):
        nonlocal good, bad
        good += bool(ok)
        bad += not ok
    def lux(length=2, height=.15, x=0, along=0, normal=V(0,0,1), flux=106.075):
        out = C.c_double(-1)
        ok = seg(flux, V(-length/2,0,height), V(length/2,0,height), V(0,0,-1), V(along,x,0), normal, C.byref(out))
        return ok, out.value
    green=A(); assert mix(A(0,1,0),green)==1
    centre = lux(flux=green[1])[1]
    check(abs(centre/353.090215-1) <= .001)
    check(abs(lux(flux=10.6075)[1]/35.309022-1) <= .001)
    check(lux(flux=0) == (1,0))
    check(lux(length=0) == (0,0))
    check(lux(height=0) == (0,0))
    check(lux(flux=-1) == (0,0))
    check(lux(flux=math.nan) == (0,0))
    check(lux(normal=V(0,0,2)) == (0,0))
    check(lux(normal=V(0,0,-1)) == (1,0))
    check(lux(length=4,height=.3,flux=green[1])[1] == centre/2)
    check(lux(length=4)[1] > centre)
    check(0 < lux(along=1)[1] < centre)
    check(lux(x=.3)[1] < centre*.09)
    for length in (.2,2,4):
        for height in (.05,.15,.4):
            a=length/2
            expected=106.075/math.pi*(a/(height*height+a*a)+math.atan(a/height)/height)
            check(abs(lux(length=length,height=height)[1]/expected-1)<1e-12)
    fractional=A()
    check(mix(A(.25,.5,.75),fractional)==1 and abs(fractional[1]-(51.857*.25+106.075*.5+29.469*.75))<1e-12)
    check(abs(lux(flux=51.857+106.075+29.469)[1]-sum(lux(flux=f)[1] for f in (51.857,106.075,29.469)))<1e-10)
    check(lux(normal=V(math.nan,0,1))==(0,0))
    # Independent midpoint integration covers endpoints, slopes and clipping.
    for along,x,n in [(0,0,V(0,0,1)), (1,0,V(0,0,1)), (1.4,.2,V(0,.6,.8)),
                       (.8,.2,V(.8,0,.6)), (0,.3,V(0,-.8,.6))]:
        total=0
        for i in range(20000):
            dx=-1+(i+.5)*2/20000-along
            r2=dx*dx+x*x+.15**2
            total += .15*max(0,n.x*dx-n.y*x+n.z*.15)/(r2*r2)
        numerical=106.075/math.pi*total*2/20000
        check(abs(lux(along=along,x=x,normal=n)[1]-numerical) < max(1e-7,numerical*1e-6))
    sums=A()
    for p in range(8):
        duties,xyz,mapped=A(),A(),A()
        error=C.c_double()
        check(preset(p,duties)==1 and mix(duties,xyz)==1)
        expected=sum(f for i,f in enumerate([51.857,106.075,29.469]) if p & (1<<i))
        check(abs(xyz[1]-expected)<1e-9)
        check(gamut(xyz,mapped,C.byref(error))==1 and min(mapped)>=0 and abs(error.value)<1e-12)
        if p==0: check(list(xyz)==[0,0,0] and list(mapped)==[0,0,0])
        if p in (1,2,4):
            for i in range(3): sums[i]+=xyz[i]
        if p==7: check(all(abs(sums[i]-xyz[i])<1e-10 for i in range(3)))
    for invalid in [A(-1,1,1), A(math.nan,1,1), A(1,math.inf,1), A(1,0,1)]:
        error=C.c_double(1)
        check(gamut(invalid,invalid,C.byref(error))==0 and list(invalid)==[0,0,0] and error.value==0)
    alias=A(1,1,1); error=C.c_double()
    check(gamut(alias,alias,C.byref(error))==1 and abs(error.value)<1e-12)
    for duties in [A(-1,0,0), A(0,math.nan,0), A(0,0,2)]:
        xyz=A(1,1,1)
        check(mix(duties,xyz)==0 and list(xyz)==[0,0,0])
    out=A(1,1,1)
    check(preset(8,out)==0 and list(out)==[0,0,0])
    for enc,expected in [(0,0),(1,1),(.5,.21404114048223255),(.04045,.04045/12.92)]:
        v=C.c_double()
        check(decode(enc,C.byref(v))==1 and abs(v.value-expected)<1e-12)
    for enc in [-1,2,math.inf,math.nan]:
        v=C.c_double(1)
        check(decode(enc,C.byref(v))==0 and v.value==0)
    print(f'photometry centre={centre:.9f} lx error={abs(centre/353.090215-1)*100:.9f}% good={good} broken={bad}')
    return bad

def main():
    with tempfile.TemporaryDirectory() as d:
        for mutant in (False,True):
            source=(ROOT/'underglow_photometry.c').read_text()
            if mutant: source=source.replace('{106.075,','{10.6075,')
            path=Path(d)/('bad.c' if mutant else 'good.c')
            path.write_text(source)
            binary=path.with_suffix('.dylib')
            r=subprocess.run(['clang','-shared','-fPIC','-Wall','-Wextra','-Werror','-I',str(ROOT),str(path),'-o',str(binary)],capture_output=True,text=True)
            assert r.returncode==0,r.stderr
            failures=proof(C.CDLL(str(binary)))
            assert (failures>0)==mutant
if __name__=='__main__': main()
