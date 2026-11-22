"""Metal drawable fingerprint check: live peak 1 tick, held-image peak 60 ticks, Off 0 encodes.

Guarded Carbon race check: live peak 3 ticks, held-image peak 2279 ticks.
The held image crossed at tick 31 in events.log and the saved F10 report.
Matched 48-second A/B runs recorded 871 encodes enabled and 0 encodes Off.

Uses the actual SDR and EDR compute pipeline and hold blits. Simulates callback
ordering, dropped frames, and sim ticks independently of the game display.
"""
from pathlib import Path
import subprocess
import tempfile

RUNTIME = Path(__file__).resolve().parent

with tempfile.TemporaryDirectory() as tmp:
    binary = str(Path(tmp) / 'present_image_diag_test')
    subprocess.run(['clang', '-fobjc-arc', '-O2', '-framework', 'Foundation',
                    '-framework', 'Metal', '-framework', 'QuartzCore',
                    str(RUNTIME / 'present_image_diag_test.m'), '-o', binary], check=True)
    for mode in ('live', 'hold', 'off'):
        subprocess.run([binary, mode], check=True)

# The shared entry points bypass fingerprint setup, submission and tick tracking with BOOT_DIAG off.
assert 'if (diag_on) present_image_diag_init(layer);' in (RUNTIME / 'd3d9.m').read_text()
present = (RUNTIME / 'render_present.m').read_text()
assert 'present_draw(l, src, clip_headroom, duration, NULL)' in present
assert 'if (timing) present_image_encode(cb, d, ticket.frame);' in present
assert 'if (diag_on && head) present_image_diag_tick(' in (RUNTIME / 'report.m').read_text()
