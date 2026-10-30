"""Reproduce the 2x MSAA forest A/B: edge-band frame difference and contrast.

Usage: a2c_pan_metric.py OFF_DIAG ON_DIAG OUTPUT.jpg
Eight successive 60 Hz frames per burst. The fixed regions exclude the HUD/car.
Temporal MAE includes expected scene motion; it is a motion proxy, not an
isolated aliasing estimate. Both variants use the same Off-derived edge mask.
"""
from pathlib import Path
import json
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def luma(image):
    a = np.asarray(image.convert('RGB'), dtype=np.float32)
    return a[..., 0] * .2126 + a[..., 1] * .7152 + a[..., 2] * .0722


def measure(off, on):
    assert off.shape == on.shape and off.ndim == 3 and len(off) >= 2
    gradient = np.hypot(*np.gradient(off[0]))
    edge = Image.fromarray(np.uint8(gradient > 4) * 255).filter(ImageFilter.MaxFilter(3))
    mask = np.asarray(edge) > 0
    assert mask.any(), 'no edge band in region'
    temporal = [float(np.abs(np.diff(a, axis=0))[:, mask].mean()) for a in (off, on)]
    contrast = [float(np.mean(np.percentile(a, 95, axis=(1, 2)) -
                              np.percentile(a, 5, axis=(1, 2)))) for a in (off, on)]
    return {'frames': len(off), 'band_pixels': int(mask.sum()),
            'temporal_mae': temporal, 'contrast_p95_p5': contrast}


def compare(off_dir, on_dir, destination):
    # Fence at the start, then the steady forest drive. The final burst at 26s
    # includes a sign collision and is excluded from the cruise comparison.
    regions = [('Wooden fence', (700, 365, 1200, 450), [10], 10),
               ('Foliage left', (20, 180, 490, 335), list(range(12, 25, 2)), 14),
               ('Foliage right', (720, 80, 1000, 290), list(range(12, 25, 2)), 14)]
    records = []
    for label, box, times, shot in regions:
        bursts = []
        for time in times:
            arrays = []
            for folder in (off_dir, on_dir):
                frames = [luma(Image.open(folder / f'simshot_{time + k/60:06.2f}.png').crop(box))
                          for k in range(8)]
                arrays.append(np.array(frames))
            bursts.append(measure(*arrays))
        weights = np.array([r['band_pixels'] * (r['frames']-1) for r in bursts])
        temporal = np.average([r['temporal_mae'] for r in bursts], axis=0, weights=weights)
        contrast = np.mean([r['contrast_p95_p5'] for r in bursts], axis=0)
        records.append({'region': label, 'roi': box, 'burst_seconds': times,
                        'frames': sum(r['frames'] for r in bursts), 'pairs': 7 * len(times),
                        'edge_band_mae_off': float(temporal[0]), 'edge_band_mae_on': float(temporal[1]),
                        'temporal_change_percent': float((temporal[1]/temporal[0]-1)*100),
                        'contrast_off': float(contrast[0]), 'contrast_on': float(contrast[1]),
                        'contrast_retained_percent': float(contrast[1]/contrast[0]*100)})
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    heights = [min(330, round((box[3]-box[1])*800/(box[2]-box[0]))) for _, box, _, _ in regions]
    footer = 510 + sum(h+55 for h in heights)
    canvas = Image.new('RGB', (1600, footer+80), (20, 22, 25))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 18)
    except OSError:
        font = ImageFont.load_default()
    draw.text((15, 10), 'Smooth cut-out edges: Off (left) / On (right), 2x MSAA, 1280x720', fill='white', font=font)
    for i, folder in enumerate((off_dir, on_dir)):
        view = Image.open(folder / 'simshot_010.00.png').convert('RGB').resize((800, 450))
        canvas.paste(view, (800*i, 45))
    y = 510
    for (label, box, times, shot), record, height in zip(regions, records, heights):
        text = (f"{label}: edge-band MAE {record['edge_band_mae_off']:.3f} -> {record['edge_band_mae_on']:.3f} "
                f"({record['temporal_change_percent']:+.2f}%), contrast {record['contrast_retained_percent']:.2f}%")
        draw.text((15, y), text, fill='white', font=font)
        for i, folder in enumerate((off_dir, on_dir)):
            crop = Image.open(folder / f'simshot_{shot:06.2f}.png').convert('RGB').crop(box)
            width = round(crop.width*height/crop.height)
            crop = crop.resize((width, height), Image.Resampling.NEAREST)
            canvas.paste(crop, (i*800+(800-width)//2, y+30))
        y += height+55
    draw.text((15, footer+5), 'MAE is raw frame difference in a shared edge band; it includes camera motion.', fill='white', font=font)
    draw.text((15, footer+35), 'Reference: opaque width/contrast retained. The reported chain-link view remains unverified.', fill='white', font=font)
    canvas.save(destination, quality=85, optimize=True)
    print(json.dumps(records, indent=2))
    return records


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    compare(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
