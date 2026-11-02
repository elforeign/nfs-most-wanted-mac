"""Synthetic temporal/detail/registration controls for the shadow-pan metric.
Contact 10–90 width 8px/4mm; doubled kernel 16px/8mm is rejected while
its 50% contour stays fixed. A 3px shift retains width but fails contour parity.
Wrong dimensions, empty hood and advanced simulation controls are rejected.
Real fitter coverage and unsnapped control live in x360_shadow_fit_test.py.
Stable/jitter mean=0/.049891, p95=0/.354512. Registration error=.000171;
omitting registration gives .018439. Blur reduces contrast .620000->.407843
and is rejected, as is an omitted thin shadow.
Absolute p95 .050000 passes / .050001 fails despite a 75% relative reduction;
contrast 1.10x passes / 1.100001x fails, with the 5% area checks unchanged.
"""
import importlib.util
import json
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

spec = importlib.util.spec_from_file_location('metric',Path(__file__).with_name('shadow_pan_metric.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    # A measured ramp has width 8 pixels / 4 mm. Doubling the filter fails
    # independently of its unchanged 50% contour; a shifted edge does the reverse.
    x=np.arange(101,dtype=float)
    edge=np.clip((x-45)/10,0,1)*.6+.2
    width=m.contact_edge(edge,.2,.8,.5)
    assert abs(width['width_10_90_px']-8)<1e-10 and abs(width['width_10_90_mm']-4)<1e-10
    assert abs(width['contour_50_px']-50)<1e-10
    broad=m.contact_edge(np.clip((x-40)/20,0,1)*.6+.2,.2,.8,.5)
    shifted=m.contact_edge(np.clip((x-48)/10,0,1)*.6+.2,.2,.8,.5)
    assert broad['width_10_90_px']>1.9*width['width_10_90_px'] and broad['contour_50_px']==width['contour_50_px']
    assert abs(shifted['width_10_90_px']-width['width_10_90_px'])<1e-10 and shifted['contour_50_px']-width['contour_50_px']==3
    solar=m.solar_disc_width(1)
    assert 9.250<solar['support_mm']<9.251 and 6.35<solar['width_10_90_mm']<6.36
    assert abs(m.solar_disc_width(2)['width_10_90_mm']/solar['width_10_90_mm']-2)<1e-12
    assert m.solar_disc_width(1,.5,.5)['width_10_90_mm']>2.8*solar['width_10_90_mm']
    with tempfile.TemporaryDirectory() as d:
        directory=Path(d)
        state=dict(burst=0,sample=0,sim_word=1,time_frames=1,view_words=[1],projection_words=[1],camera_words=[1],light_words=[1],post_values=[1])
        for v in ('retail','projection','filter','full'):
            (directory/f'near_00_000_{v}_ids.json').write_text(json.dumps(dict(state,variant=v)))
            Image.new('RGB',(160,96)).save(directory/f'near_00_000_{v}.png')
        assert len(m.near_variant_records(directory,(160,96)))==1
        for dims,hood in [((320,192),False),((160,96),True)]:
            try:m.near_variant_records(directory,dims,hood)
            except ValueError:pass
            else:raise AssertionError('scaled mask or empty hood accepted')
        p=directory/'near_00_000_full_ids.json';p.write_text(json.dumps(dict(state,variant='full',sim_word=2)))
        try:m.near_variant_records(directory,(160,96))
        except ValueError:pass
        else:raise AssertionError('advanced simulation accepted')
    print(f"contact width={width['width_10_90_px']:.3f}px/{width['width_10_90_mm']:.3f}mm; solar 1m={solar['width_10_90_mm']:.6f}mm; broad/shift/dimension/empty-hood/tick controls rejected")
    height,width = 96,160
    yy,xx = np.mgrid[:height,:width]
    reference = np.where((xx % 24)<6,.18,.8).astype(float)
    band=m.edge_band(reference)
    contrast=.62
    stable=np.repeat(reference[None,...],64,axis=0)
    noisy=stable.copy()
    for i in range(64):
        transform=np.eye(3); transform[0,2]=.45*np.sin(i*1.1)
        noisy[i],_=m.sample(reference,transform,(0,0,width,height))
    still=m.temporal(stable,band,contrast)
    shimmer=m.temporal(noisy,band,contrast)
    assert still['mean']==0 and still['p95']==0
    assert shimmer['mean']>.01 and shimmer['p95']>.04
    blur=np.asarray(Image.fromarray(np.uint8(reference*255)).filter(ImageFilter.GaussianBlur(3)),float)/255
    base=m.detail(reference,.18,.8)
    blurred=m.detail(blur,.18,.8)
    omitted=m.detail(np.full_like(reference,.8),.18,.8)
    assert m.detail_retained(base,base)
    assert not m.detail_retained(base,blurred)
    assert not m.detail_retained(base,omitted)
    assert m.detail_retained(base,dict(base,contrast=1.10*base['contrast']))
    assert not m.detail_retained(base,dict(base,contrast=1.100001*base['contrast']))
    line=(26,12,26,82); roi=(0,0,width,height)
    fence=m.fence_detail(reference,line,roi)
    fence_blur=m.fence_detail(blur,line,roi,fence['contrast'])
    fence_missing=m.fence_detail(np.full_like(reference,.8),line,roi,fence['contrast'])
    assert m.fence_retained(fence,fence)
    assert not m.fence_retained(fence,fence_blur)
    assert not m.fence_retained(fence,fence_missing)
    excess = [{'mean': .2, 'p95': .22}, {'mean': .2, 'p95': .22},
              {'mean': .02, 'p95': .05}]
    assert m.shimmer_target(excess, .05)
    assert not m.shimmer_target(excess, .050001)
    # Stationary noise cannot excuse absolute p95 above the ceiling.
    assert not m.shimmer_target(excess, .08)
    assert m.fence_retained(fence, dict(fence, contrast=1.10*fence['contrast']))
    assert not m.fence_retained(fence, dict(fence, contrast=1.100001*fence['contrast']))
    assert not m.fence_retained(fence, dict(fence, shadow_area=1.051*fence['shadow_area']))
    assert not m.fence_retained(fence, dict(fence, half_contrast_area=.949*fence['half_contrast_area']))
    # Rotation registration uses a textured analytic sphere. Render the same
    # world rays in two cameras, then register the second into the first.
    theta=.012
    current=np.eye(4); current[0,0]=np.cos(theta); current[0,2]=np.sin(theta)
    current[2,0]=-np.sin(theta); current[2,2]=np.cos(theta)
    ref={'sx':1.3,'sy':-1.6,'view':np.eye(4)}
    cur={'sx':1.3,'sy':-1.6,'view':current}
    k=m.intrinsics(width,height,ref['sx'],ref['sy'])
    coords=np.stack([xx.ravel()+.5,yy.ravel()+.5,np.ones(width*height)])
    rays=np.einsum('ij,jk->ik',np.linalg.inv(k),coords)
    def texture(r):
        u,v=r[0]/r[2],r[1]/r[2]
        return (.5+.18*np.sin(12*u)+.15*np.cos(9*v)).reshape(height,width)
    a=texture(rays); b=texture(np.einsum('ij,jk->ik',current[:3,:3],rays))
    roi=(10,10,width-20,height-20)
    registered,valid=m.sample(b,m.homography(ref,cur,width,height),roi)
    broken,_=m.sample(b,np.eye(3),roi)
    truth=a[10:-10,10:-10]
    good=float(np.abs(registered-truth).mean()); bad=float(np.abs(broken-truth).mean())
    assert valid.all() and good<.0004 and bad>.01
    print(f"shadow metric: stable mean/p95={still['mean']:.6f}/{still['p95']:.6f}; "
          f"jitter={shimmer['mean']:.6f}/{shimmer['p95']:.6f}")
    print(f"registration mean error={good:.6f}, broken={bad:.6f}; "
          f"blur contrast={blurred['contrast']:.6f} vs {base['contrast']:.6f}")
    print('SHADOW METRIC PASS (jitter, unregistered, blur and omitted-caster controls rejected)')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
