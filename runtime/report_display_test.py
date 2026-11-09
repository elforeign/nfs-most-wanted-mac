"""Display formatter proof: live SDR/HDR 2 checks / 0 failures; hard-coded SDR 2 / 1.
Compiles the production formatter; no window, GPU or game is needed.
"""
from pathlib import Path
import subprocess
import tempfile

source = (Path(__file__).with_name('report.m')).read_text()
formatter = source[source.index('static void format_display_output('):source.index('static void cache_system(')]
main = '''
int main(void) {
    char out[256];
    format_display_output(out, sizeof out, 0, 0, "kCGColorSpaceSRGB", 1, 1, 1); puts(out);
    format_display_output(out, sizeof out, 1, 1, "kCGColorSpaceExtendedLinearSRGB", 2, 7, 1); puts(out);
}
'''
expected = [f'EDR current={c}.000 potential={p}.000 reference=1.000; output={mode} wantsEDR={on} FP16={on} colorspace=kCGColorSpace{cs}; HDR transport=unknown'
            for c, p, mode, on, cs in [(1, 1, 'SDR', 0, 'SRGB'), (2, 7, 'HDR', 1, 'ExtendedLinearSRGB')]]
with tempfile.TemporaryDirectory() as tmp:
    for broken in (False, True):
        code = formatter.replace('wants && fp16 ? "HDR" : "SDR"', '"SDR"') if broken else formatter
        src, exe = Path(tmp) / 'display.c', Path(tmp) / 'display'
        src.write_text('#include <stdio.h>\n' + code + main)
        subprocess.run(['clang', '-std=c11', str(src), '-o', str(exe)], check=True, capture_output=True)
        lines = subprocess.check_output([str(exe)], text=True).splitlines()
        failures = sum(a != b for a, b in zip(lines, expected)) + abs(len(lines) - 2)
        print(f'{"hard-coded SDR" if broken else "live SDR/HDR"}: 2 checks / {failures} failures')
        assert failures == int(broken), lines
