"""Equivalence evidence: stable fitter subtexel invariance, yaw coverage and grid phase.
21,056 checks pass; removing snapping fails 256 subtexel comparisons.
Normal and deliberately unsnapped results are printed; the latter must fail.
Both changed production and pinned reviewed predecessor execute this fixture.
Each: 21,056 good / 0 failures, unsnapped 256 failures.
This proves retained S1 behavior, not S2 improvement; shadow_near_fit_test.py
separately rejects the predecessor on density and independent-footprint checks.
No GPU, game or emulation; constants are pinned by shadow_near_fit_test.py.
"""
from pathlib import Path
import subprocess
import tempfile
import runpy

ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
#include <cstdio>
#include <cstring>
#include "x360_shadow_fit.cpp"
using namespace nfsmw_x360;
static int checks, failures;
#define CHECK(c) do { ++checks; if (!(c)) { if (failures<4) std::fprintf(stderr,"line %d: %s\n",__LINE__,#c); ++failures; } } while(0)
static Mat4 view(Vec3 eye, float yaw) {
    Mat4 m=Identity();
    // Z is world up. Look horizontally along +Y at zero yaw; rotate about
    // world Z, rather than rolling an identity camera about its forward axis.
    m.m[0][0]=std::cos(yaw); m.m[0][1]=0; m.m[0][2]=-std::sin(yaw);
    m.m[1][0]=std::sin(yaw); m.m[1][1]=0; m.m[1][2]=std::cos(yaw);
    m.m[2][0]=0; m.m[2][1]=-1; m.m[2][2]=0;
    for(int j=0;j<3;++j) m.m[3][j]=-(eye.x*m.m[0][j]+eye.y*m.m[1][j]+eye.z*m.m[2][j]);
    return m;
}
int main(int argc,char **) {
    bool snap=argc==1;
    for(int n: {1024,1600}) for(float far: {50.f,70.f})
    for(float sx: {1.6f,.7f}) for(Vec3 sun: {Vec3{.4f,.3f,.866f},Vec3{0,0,1},Vec3{0,0,0},Vec3{1,0,.01f}}) {
        StableShadowState s;
        Mat4 v=view({0,0,0},0);
        Mat4 a=FitStableShadowCamera(&v.m[0][0],sx,1.4f,sun,far,n,s,snap);
        float scale=std::sqrt(a.m[0][0]*a.m[0][0]+a.m[1][0]*a.m[1][0]+a.m[2][0]*a.m[2][0]);
        float pitch=2/(scale*n);
        Vec3 x{a.m[0][0]/scale,a.m[1][0]/scale,a.m[2][0]/scale};
        Vec3 y{a.m[0][1]/scale,a.m[1][1]/scale,a.m[2][1]/scale};
        // Move within the same cell along both light-plane axes. No output
        // coefficient may change; omitting quantization fails this assertion.
        for(int step=1;step<=8;++step) {
            float d=pitch*step*.04f;
            Mat4 bview=view({d*(x.x+y.x),d*(x.y+y.y),d*(x.z+y.z)},0);
            Mat4 b=FitStableShadowCamera(&bview.m[0][0],sx,1.4f,sun,far,n,s,snap);
            CHECK(!std::memcmp(&a,&b,sizeof a));
        }
        // All yaw angles retain the exact basis/warp/extents, and all focus
        // corners remain inside the padded map and the light-depth interval.
        for(int deg=0;deg<360;deg+=5) {
            float yaw=deg*3.14159265358979323846f/180;
            Mat4 bv=view({0,0,0},yaw);
            Mat4 b=FitStableShadowCamera(&bv.m[0][0],sx,1.4f,sun,far,n,s,snap);
            CHECK(!std::memcmp(&a,&b,sizeof a));
            for(float z: {.01f,far}) for(float signx: {-1.f,1.f}) for(float signy: {-1.f,1.f}) {
                Vec3 p{signx*z/sx,signy*z/1.4f,z};
                Vec3 w{p.x*bv.m[0][0]+p.y*bv.m[0][1]+p.z*bv.m[0][2],
                       p.x*bv.m[1][0]+p.y*bv.m[1][1]+p.z*bv.m[1][2],
                       p.x*bv.m[2][0]+p.y*bv.m[2][1]+p.z*bv.m[2][2]};
                Vec3 q=TransformCoord(w,b);
                CHECK(std::fabs(q.x)<1-1.f/n && std::fabs(q.y)<1-1.f/n && q.z>0 && q.z<1);
            }
        }
        // A whole-cell translation changes the final biased receiver lookup
        // by precisely one texel. The half-texel centre convention is included.
        Mat4 moved=view({pitch*x.x,pitch*x.y,pitch*x.z},0);
        Mat4 b=FitStableShadowCamera(&moved.m[0][0],sx,1.4f,sun,far,n,s,snap);
        float before=(a.m[3][0]*.5f+.5f+.5f/n)*n;
        float after=(b.m[3][0]*.5f+.5f+.5f/n)*n;
        CHECK(std::fabs((before-after)-1)<.0002f);
        // FOV may widen its retained envelope, never shrink it while settling.
        Mat4 wide=FitStableShadowCamera(&v.m[0][0],sx*.8f,1.4f,sun,far,n,s,snap);
        Mat4 settled=FitStableShadowCamera(&v.m[0][0],sx,1.4f,sun,far,n,s,snap);
        CHECK(!std::memcmp(&wide,&settled,sizeof wide));
    }
    std::printf("stable fit: %d checks, %d failures (snapping %s)\n",checks,failures,snap?"on":"off");
    return failures?1:0;
}
'''


def main():
    fixture = runpy.run_path(str(ROOT/'runtime/shadow_near_fit_test.py'))
    scratch = ROOT/'.metal-cache/s2'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as d:
        work = Path(d)
        fixture['pinned'](work)
        (work/'production.cpp').write_text((ROOT/'runtime/x360_shadow_fit.cpp').read_text())
        for label, include in (('current','production.cpp'), ('reviewed S1','predecessor.cpp')):
            source = work / 'fit.cpp'
            source.write_text('#include <initializer_list>\n' + HARNESS.replace(
                '#include "x360_shadow_fit.cpp"', '#include "'+include+'"'))
            binary = work / 'fit'
            compiled = subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I', str(work), '-I', str(ROOT/'runtime'),
                            str(source), '-o', str(binary)], capture_output=True, text=True)
            if compiled.returncode:
                print(compiled.stderr[-3000:])
                return 1
            normal = subprocess.run([str(binary)], capture_output=True, text=True)
            broken = subprocess.run([str(binary), '--without-snapping'], capture_output=True, text=True)
            print(label, normal.stdout.strip())
            print(label, broken.stdout.strip())
            if normal.returncode or broken.returncode != 1:
                print(normal.stderr[-2000:], broken.stderr[-1000:])
                return 1
        print('STABLE FIT PASS (current and pinned S1; unsnapped controls rejected)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
