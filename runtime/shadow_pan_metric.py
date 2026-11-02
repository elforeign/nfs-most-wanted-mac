"""Registered shadow-edge temporal error, with stationary and detail controls.

Capture fixture (omit BOOT_AUTODRIVE entirely; even '=0' enables it):
  BOOT_RACE=15.1.1 BOOT_RACE_AI=0 NFS_LOCKSTEP=1 NFS_SIMHZ=60
  BOOT_DIAG_SHADOW_PAN=yaw BOOT_DIAG_SHADOW_AB=1 NFS_SPEED_BLUR=off
  BOOT_DIAG_SEQ=sim+12,sim+16,sim+20:64
  BOOT_DIAG_SIMSHOTS=10.50,...,10.80,14.50,...,14.80,18.50,...,18.80
The three bounded bursts are Off, repeated Off, On. Eye/FOV are identical;
the yaw repeats at 2 degrees/s from sim 12, 16 and 20. Holds precede each.
For both presets in one run, use BOOT_DIAG_SHADOW_AB=both, start with PC,
add bursts at sim 32,36,40 and holds at 30.50..30.80,34.50..34.80,
38.50..38.80. F10's normal reset selects X360 at sim 26. Use --phase-offset 20
to measure those last three bursts. The A/B fixture freezes render weather and
the effective shadow light from sim 10; gameplay weather is unaffected outside
the fixture. The earliest two development captures with autodrive are invalid.

Example: shadow_pan_metric.py DIAG --shadow-roi x,y,w,h --lit-roi x,y,w,h
  --json metrics.json --jpeg run_shots/shadow_steady_ab.jpg
Select an actual thin-caster shadow on a flat surface, and a nearby lit patch
without shadows, HUD, moving foliage, particles or reflections. A supplied
--edge-mask grayscale PNG (white=band) is preferable to automatic gradients.
Masks, contrast normalization and area threshold come from the first baseline
and are reused for all states. Registration uses recorded camera rotation and
projection, never the moving shadow itself. JPEG crops are registered and use
the same coordinates/scale. No fit/filter tuning is inferred from the score.
"""
import argparse
import csv
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def box(text):
    result = tuple(int(x) for x in text.split(','))
    if len(result) != 4 or min(result[2:]) <= 0 or min(result[:2]) < 0:
        raise argparse.ArgumentTypeError('expected nonnegative x,y and positive w,h')
    return result


def luminance(rgb):
    # Display-referred Rec.709 luminance in [0,1], identical for every capture.
    return np.einsum('...c,c->...', np.asarray(rgb, dtype=np.float32)[..., :3],
                     np.array([.2126, .7152, .0722], np.float32)) / 255


def intrinsics(width, height, sx, sy):
    return np.array([[width*sx/2, 0, width/2], [0, -height*sy/2, height/2], [0, 0, 1.]])


def homography(reference, current, width, height):
    kr = intrinsics(width, height, reference['sx'], reference['sy'])
    kc = intrinsics(width, height, current['sx'], current['sy'])
    # Row-vector world->view rotations: reference ray * Rref.T * Rcurrent.
    return kc @ current['view'][:3, :3].T @ reference['view'][:3, :3] @ np.linalg.inv(kr)


def sample(image, transform, roi):
    """Map reference pixel centres into the current frame; bilinear sampling."""
    x, y, w, h = roi
    yy, xx = np.mgrid[y:y+h, x:x+w].astype(float)
    rays = np.einsum('ij,jk->ik', transform, np.stack([xx.ravel()+.5, yy.ravel()+.5, np.ones(w*h)]))
    px = (rays[0]/rays[2]-.5).reshape(h, w)
    py = (rays[1]/rays[2]-.5).reshape(h, w)
    ih, iw = image.shape[:2]
    valid = (px >= 0) & (py >= 0) & (px < iw-1) & (py < ih-1) & (rays[2].reshape(h,w) > 0)
    ix = np.floor(np.clip(px, 0, iw-2)).astype(int)
    iy = np.floor(np.clip(py, 0, ih-2)).astype(int)
    fx, fy = np.clip(px-ix, 0, 1), np.clip(py-iy, 0, 1)
    if image.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]
    result = ((1-fy)*((1-fx)*image[iy,ix]+fx*image[iy,ix+1]) +
              fy*((1-fx)*image[iy+1,ix]+fx*image[iy+1,ix+1]))
    return result, valid


def edge_band(reference, radius=2):
    dy, dx = np.gradient(reference)
    gradient = np.hypot(dx, dy)
    band = gradient >= max(float(np.quantile(gradient, .85)), .005)
    expanded = band.copy()
    for y in range(-radius, radius+1):
        for x in range(-radius, radius+1):
            expanded |= np.roll(np.roll(band, x, 1), y, 0)
    expanded[:radius+1] = False; expanded[-radius-1:] = False
    expanded[:, :radius+1] = False; expanded[:, -radius-1:] = False
    if expanded.sum() < 16:
        raise ValueError('edge band has fewer than 16 pixels')
    return expanded


def temporal(frames, band, contrast, changed_threshold=None):
    differences = np.abs(np.diff(np.asarray(frames), axis=0))[:, band] / contrast
    if differences.shape[0] < 2 or differences.shape[1] < 16:
        raise ValueError('need >=3 frames and >=16 edge pixels')
    result = {'pairs': len(differences), 'edge_pixels': int(band.sum()),
              'mean': float(differences.mean()), 'p95': float(np.quantile(differences, .95)),
              'pair_mean_p95': float(np.quantile(differences.mean(axis=1), .95))}
    if changed_threshold is not None:
        result['changed_fraction'] = float((differences > changed_threshold).mean())
    return result


def detail(frame, low, high):
    """Fixed baseline thresholds expose lost thin shadows and broadened edges."""
    p10, p90 = np.quantile(frame, [.1, .9])
    return {'contrast': float(p90-p10),
            'shadow_area': float((frame < (low+high)/2).mean()),
            'transition_area': float(((frame > low+.1*(high-low)) & (frame < high-.1*(high-low))).mean())}


def detail_retained(before, after, tolerance=.05):
    contrast = (1-tolerance)*before['contrast'] <= after['contrast'] <= 1.10*before['contrast']
    area = abs(after['shadow_area']-before['shadow_area']) <= tolerance*max(before['shadow_area'], .001)
    width = after['transition_area'] <= before['transition_area']+tolerance
    return bool(contrast and area and width)


def fence_detail(frame, line, roi, reference_contrast=None):
    """Average across a manually identified straight thin-caster shadow.

    A broad image histogram mostly measures road texture and can miss loss of
    the actual fence. Here outer samples estimate the local lit background;
    the same cross-section and baseline contrast serve every state.
    """
    x0,y0,x1,y1=line
    dx,dy=x1-x0,y1-y0
    length=np.hypot(dx,dy)
    if length < 8:
        raise ValueError('fence line must be at least eight pixels long')
    offsets=np.linspace(-8,8,65)
    along=np.linspace(.05,.95,max(32,int(length)))
    nx,ny=-dy/length,dx/length
    px=x0+along[None,:]*dx+offsets[:,None]*nx-roi[0]
    py=y0+along[None,:]*dy+offsets[:,None]*ny-roi[1]
    ix,iy=np.floor(px).astype(int),np.floor(py).astype(int)
    if ix.min()<0 or iy.min()<0 or ix.max()>=frame.shape[1]-1 or iy.max()>=frame.shape[0]-1:
        raise ValueError('fence cross-section needs eight pixels of margin inside the shadow ROI')
    fx,fy=px-ix,py-iy
    values=(1-fy)*((1-fx)*frame[iy,ix]+fx*frame[iy,ix+1])+fy*((1-fx)*frame[iy+1,ix]+fx*frame[iy+1,ix+1])
    profile=values.mean(axis=1)
    outside=np.abs(offsets)>=6
    fit=np.polyfit(offsets[outside],profile[outside],1)
    attenuation=np.maximum(0,np.polyval(fit,offsets)-profile)
    contrast=float(attenuation.max())
    norm=reference_contrast if reference_contrast is not None else max(contrast,.001)
    return {'contrast':contrast,'shadow_area':float(attenuation.sum()*.25/norm),
            'half_contrast_area':float((attenuation>.5*norm).sum()*.25*.9*length),
            'edge_width':float(((attenuation>.1*norm).sum()-(attenuation>.9*norm).sum())*.125),
            'profile':profile.tolist(),'attenuation':attenuation.tolist()}


def fence_retained(before, after):
    return bool(before['contrast']>.01 and .95*before['contrast']<=after['contrast']<=1.10*before['contrast'] and
                abs(after['shadow_area']/max(before['shadow_area'],.001)-1)<=.05 and
                abs(after['half_contrast_area']/max(before['half_contrast_area'],.001)-1)<=.05 and
                after['edge_width']<=before['edge_width']+.5)


def shimmer_target(excess, p95):
    return bool(p95 <= .05 and all(
        excess[2][k] <= .75*min(excess[0][k],excess[1][k]) and
        max(excess[0][k],excess[1][k])-excess[2][k] > abs(excess[0][k]-excess[1][k])
        for k in ['mean','p95']))


def read_records(directory):
    records = []
    with (directory/'shadow_pan.tsv').open() as source:
        for row in csv.DictReader(source, delimiter='\t'):
            row = dict(row)
            row['view'] = np.array([struct.unpack('<f', struct.pack('<I', int(row['view_hex'][i:i+8],16)))[0]
                                    for i in range(0,128,8)]).reshape(4,4)
            for key in ['sim','race_sim','yaw_deg','sx','sy']:
                row[key] = float(row[key])
            # CubicCameraMover updates before the next Present. The log's
            # frame number is the preceding Present, not the captured frame.
            row['frame'] = int(row['frame']) + 1
            records.append(row)
    if not records:
        raise ValueError('no diagnostic camera records')
    return records


def bursts(directory, phase_offset=0):
    groups = []
    for path in sorted(directory.glob('seq_*.png')):
        frame = int(path.stem.split('_')[1])
        if not groups or frame != groups[-1][-1][0]+1:
            groups.append([])
        groups[-1].append((frame,path))
    if len(groups) not in (3,6) or any(len(g) != 64 for g in groups):
        raise ValueError(f'expected three 64-frame bursts, got {[len(g) for g in groups]}')
    if phase_offset and len(groups)!=6:
        raise ValueError('phase offset 20 needs the six-burst both-preset fixture')
    return groups[3:6] if phase_offset else groups[:3]


def analyse(directory, shadow_roi, lit_roi, edge_mask=None, phase_offset=0, fence_line=None):
    records = read_records(directory)
    groups = bursts(directory, phase_offset)
    by_frame = {r['frame']: r for r in records}
    def record(frame):
        if frame not in by_frame:
            raise ValueError(f'no camera record for captured frame {frame}')
        return by_frame[frame]
    ref = record(groups[0][0][0])
    with Image.open(groups[0][0][1]) as im:
        width,height = im.size
    def load(path, r):
        rgb = np.asarray(Image.open(path).convert('RGB'), dtype=float)
        h = homography(ref, r, width, height)
        shade, valid = sample(luminance(rgb), h, shadow_roi)
        lit, vl = sample(luminance(rgb), h, lit_roi)
        crop, _ = sample(rgb, h, shadow_roi)
        if not valid.all() or not vl.all():
            raise ValueError('registered ROI leaves image')
        return shade, float(lit.mean()), crop, lit
    sequences, lights, crops, lit_patches = [], [], [], []
    for group in groups:
        samples = [load(path,record(frame)) for frame,path in group]
        sequences.append(np.array([s[0] for s in samples]))
        lights.append(np.array([s[1] for s in samples]))
        crops.append([s[2] for s in samples])
        lit_patches.append(np.array([s[3] for s in samples]))
    # Correct illumination using the separately selected lit patch. Do not fit
    # registration or gain to the shadow edge: that would hide its movement.
    lit_reference = float(lights[0][0])
    sequences = [s * (lit_reference/np.maximum(l,.001))[:,None,None] for s,l in zip(sequences,lights)]
    initial = sequences[0][0]
    low,high = np.quantile(initial,[.1,.9])
    contrast = float(high-low)
    if contrast < .01:
        raise ValueError('baseline shadow contrast <.01; choose a visible shadow')
    if edge_mask:
        mask = np.asarray(Image.open(edge_mask).convert('L')) > 127
        x,y,w,h = shadow_roi
        band = mask[y:y+h,x:x+w] if mask.shape == (height,width) else mask
        if band.shape != initial.shape:
            raise ValueError('edge mask must match full image or shadow ROI')
    else:
        band = edge_band(initial)
    fence_stats=None
    if fence_line:
        # Limit temporal samples to this actual shadow, not nearby road marks.
        yy,xx=np.mgrid[:initial.shape[0],:initial.shape[1]]
        x0,y0,x1,y1=fence_line
        dx,dy=x1-x0,y1-y0; length=np.hypot(dx,dy)
        u=((xx+shadow_roi[0]-x0)*dx+(yy+shadow_roi[1]-y0)*dy)/length
        v=((xx+shadow_roi[0]-x0)*(-dy)+(yy+shadow_roi[1]-y0)*dx)/length
        band &= (u>0)&(u<length)&(np.abs(v)<5)
        first=fence_detail(sequences[0].mean(axis=0),fence_line,shadow_roi)
        contrast=first['contrast']
        if contrast < .01 or band.sum()<16:
            raise ValueError('selected fence shadow has insufficient contrast or edge pixels')
        fence_stats=[fence_detail(s.mean(axis=0),fence_line,shadow_roi,contrast) for s in sequences]
    holds = []
    for start in [10.5+phase_offset,14.5+phase_offset,18.5+phase_offset]:
        samples=[]
        for path in sorted(directory.glob('simshot_*.png')):
            t=float(path.stem.split('_')[1])
            if start-.001 <= t <= start+.301:
                # Simshot times are race-relative; nearest subsequent update.
                r=min(records,key=lambda r:abs(r['race_sim']-t))
                shade,lit,_,_=load(path,r)
                samples.append(shade*lit_reference/max(lit,.001))
        if len(samples)<3:
            raise ValueError(f'missing stationary hold at {start}')
        holds.append(np.array(samples))
    noise=[temporal(s,band,contrast) for s in holds]
    threshold=max(n['p95'] for n in noise)+1/(255*contrast)
    stats=[temporal(s,band,contrast,threshold) for s in sequences]
    details=[detail(s.mean(axis=0),low,high) for s in sequences]
    # Registration sanity: a static lit patch should show little residual after
    # gain correction. Report it independently; never subtract it from shadows.
    poses=[record(frame) for group in groups for frame,_ in group]
    eyes=np.array([-np.einsum('ij,j->i',r['view'][:3,:3],r['view'][3,:3]) for r in poses])
    eye_drift=float(np.linalg.norm(eyes-eyes[0],axis=1).max())
    rates=[(record(g[-1][0])['yaw_deg']-record(g[0][0])['yaw_deg']) /
           (record(g[-1][0])['sim']-record(g[0][0])['sim']) for g in groups]
    pose_error=0.
    for r in poses:
        angle=np.deg2rad(r['yaw_deg']-ref['yaw_deg'])
        rot=np.array([[np.cos(angle),-np.sin(angle),0],[np.sin(angle),np.cos(angle),0],[0,0,1]])
        pose_error=max(pose_error,float(np.abs(rot@ref['view'][:3,:3]-r['view'][:3,:3]).max()))
    fixed_fov=len({r['fov'] for r in poses})==1
    pose_valid=eye_drift < .02 and pose_error < .00001 and fixed_fov and all(abs(rate-2)<.002 for rate in rates)
    lit_errors=[]
    for patches,l in zip(lit_patches,lights):
        corrected=patches*(lit_reference/np.maximum(l,.001))[:,None,None]
        lit_errors.append(float(np.abs(np.diff(corrected,axis=0)).mean()/max(lit_reference,.001)))
    result={'size':[width,height], 'shadow_roi':shadow_roi,'lit_roi':lit_roi,
            'camera':{'fixed_eye_max_drift':eye_drift,'yaw_matrix_max_error':pose_error,
                      'rates_deg_s':rates,'fixed_fov':fixed_fov,'valid':pose_valid},
            'lit_registration_mean_relative_error':lit_errors,
            'normalization_contrast':contrast,'changed_threshold':threshold,
            'stationary':noise,'sweeps':stats,'detail':details,
            'fence_line':fence_line,'fence_detail':fence_stats,
            'detail_retained':fence_retained(fence_stats[0],fence_stats[2]) if fence_stats else False,
            'baseline_repeat_mean_delta':abs(stats[0]['mean']-stats[1]['mean']),
            'lit_mean_ranges':[[float(l.min()),float(l.max())] for l in lights]}
    excess=[{k:max(0,s[k]-n[k]) for k in ['mean','p95']} for s,n in zip(stats,noise)]
    result['excess_above_stationary']=excess
    result['shimmer_target_met']=shimmer_target(excess, stats[2]['p95'])
    result['absolute_p95_met']=stats[2]['p95'] <= .05
    result['accepted']=result['shimmer_target_met'] and result['absolute_p95_met'] and result['detail_retained'] and pose_valid
    return result,crops


def montage(crops, result, path):
    w,h = result['shadow_roi'][2:]
    scale=min(2, 1800/(4*w))
    cw,ch = max(1,round(w*scale)), max(1,round(h*scale))
    out=Image.new('RGB',(4*cw,2*(ch+30)+45),(24,24,24)); draw=ImageDraw.Draw(out)
    font=ImageFont.load_default(size=16)
    draw.text((8,8),'Steady shadow edges: same registered edge / frames 0, 21, 42, 63',fill='white',font=font)
    for row,(index,label) in enumerate([(0,'Off'),(2,'On')]):
        for col,frame in enumerate([0,21,42,63]):
            tile=Image.fromarray(np.uint8(np.clip(crops[index][frame],0,255)))
            out.paste(tile.resize((cw,ch)),(col*cw,45+row*(ch+30)+25))
            draw.text((col*cw+6,45+row*(ch+30)+4),f'{label} / frame {frame}',fill='white',font=font)
    out.save(path,quality=86,optimize=True)
    if Path(path).stat().st_size > 500_000:
        out.save(path,quality=65,optimize=True)



def contact_edge(profile, dark, lit, spacing_mm=None):
    """Normalized 10–90 width and 50% contour position, kept separate.

    Samples must run from dark to light along the pinned receiver-plane normal.
    The first rising crossing of each threshold is used; no smoothing or fit
    hides a staircase. Dark/lit levels and physical calibration are explicit.
    """
    values=np.asarray(profile,dtype=float)
    if values.ndim!=1 or len(values)<4 or not np.isfinite(values).all() or lit-dark<=.01:
        raise ValueError('need finite samples and a measured nonempty edge')
    normalized=(values-dark)/(lit-dark)
    crossings=[]
    for level in (.1,.5,.9):
        indices=np.flatnonzero((normalized[:-1]<=level)&(normalized[1:]>level))
        if len(indices)!=1:
            raise ValueError('edge needs one rising crossing per threshold; pin a clean normal')
        i=int(indices[0]);crossings.append(i+(level-normalized[i])/(normalized[i+1]-normalized[i]))
    width=crossings[2]-crossings[0]
    if width<=0:
        raise ValueError('unordered contact edge')
    if spacing_mm is not None and (not np.isfinite(spacing_mm) or spacing_mm<=0):
        raise ValueError('millimetre calibration must be positive')
    return {'width_10_90_px':float(width),
            'width_10_90_mm':None if spacing_mm is None else float(width*spacing_mm),
            'contour_50_px':float(crossings[1]),'dark':float(dark),'lit':float(lit)}


def solar_disc_width(gap_m, incidence_cos=1., edge_light_dot=0.):
    """Uniform .53-degree solar disk, parallel planar blocker/receiver model.

    Return full support and normalized 10–90 widths in receiver millimetres.
    A camera/receiver Jacobian is still required to predict screen pixels.
    """
    if not np.isfinite([gap_m,incidence_cos,edge_light_dot]).all() or gap_m<0 or not 0<incidence_cos<=1:
        raise ValueError('need finite gap and non-grazing plane incidence')
    lo,hi=0.,1.
    for _ in range(60):
        q=(lo+hi)/2
        cumulative=.5+(np.arcsin(q)+q*np.sqrt(1-q*q))/np.pi
        if cumulative<.9:lo=q
        else:hi=q
    fraction=(lo+hi)/2
    full=2000*gap_m*np.tan(np.deg2rad(.265))/incidence_cos*np.sqrt(1+(edge_light_dot/incidence_cos)**2)
    return {'support_mm':float(full),'width_10_90_mm':float(full*fraction),'fraction':float(fraction)}


def near_variant_records(directory, dimensions, require_hood=False, hood_mask=None):
    """Group held passes without merging burst, sample or camera identities.

    This validates captures at their native dimensions. Historical ROIs cannot
    be scaled to a different output size. A hood proof needs a nonempty pinned
    mask supplied by the caller, then a separately checked crossing shadow.
    """
    directory=Path(directory);groups={}
    if require_hood:
        if hood_mask is None or np.asarray(hood_mask).shape!=tuple(dimensions[::-1]) or not np.asarray(hood_mask).any():
            raise ValueError('empty hood mask fails proof')
    for path in sorted(directory.glob('near_*_ids.json')):
        record=json.loads(path.read_text());key=(record['burst'],record['sample'])
        variant=record['variant']
        if variant in groups.setdefault(key,{}):raise ValueError('duplicate variant identity')
        shot=path.with_name(path.name.replace('_ids.json','.png'))
        if not shot.exists():shot=shot.with_suffix('.bmp')
        with Image.open(shot) as im:
            if tuple(im.size)!=tuple(dimensions):raise ValueError('ROI dimensions differ; establish new masks')
        groups[key][variant]=record
    for variants in groups.values():
        if set(variants)!={'retail','projection','filter','full'}:raise ValueError('incomplete four-way sample')
        base=variants['retail']
        for record in variants.values():
            for field in ('sim_word','time_frames','view_words','projection_words','camera_words','light_words','post_values'):
                if record[field]!=base[field]:raise ValueError('held state changed: '+field)
    if not groups:raise ValueError('no held variant records')
    return groups

def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('diag',type=Path)
    parser.add_argument('--shadow-roi',type=box,required=True)
    parser.add_argument('--lit-roi',type=box,required=True)
    parser.add_argument('--edge-mask',type=Path)
    parser.add_argument('--fence-line',type=box,help='x0,y0,x1,y1 along a thin fence shadow; required for acceptance')
    parser.add_argument('--phase-offset',type=int,choices=[0,20],default=0)
    parser.add_argument('--json',type=Path)
    parser.add_argument('--jpeg',type=Path)
    args=parser.parse_args()
    result,crops=analyse(args.diag,args.shadow_roi,args.lit_roi,args.edge_mask,args.phase_offset,args.fence_line)
    output=json.dumps(result,indent=2)
    if args.json: args.json.write_text(output+'\n')
    if args.jpeg: montage(crops,result,args.jpeg)
    print(output)


if __name__=='__main__':
    main()
