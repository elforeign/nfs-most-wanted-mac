"""Offline Metal check/timing of the lamp-source pass (runtime/lampsrc_bench.m): shipped shader vs HEAD~ one, byte-identical
output and a deliberately broken variant that must differ. Never launches the game. Usage: lampsrc_bench.py [base_ref] [blobs]"""
import ast, re, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
_inc = ROOT / 'runtime/gen/x360_shader_lits.inc'   # generated constants spliced between the MSL's string literals
MACROS = dict(re.findall(r'#define (\w+) "(.*)"', _inc.read_text())) if _inc.exists() else {}
def shader(text, name):
    text = text.split(f'static const char *{name} =', 1)[1].split(';\n', 1)[0]
    return ''.join(ast.literal_eval(m.group(0)) if m.group(0).startswith('"') else MACROS[m.group(0)]
                   for m in re.finditer(r'"(?:[^"\\]|\\.)*"|\b(?:X360\w+|PCS_\w+)\b', text))
base = sys.argv[1] if len(sys.argv) > 1 else 'main'
blobs = sys.argv[2] if len(sys.argv) > 2 else '16'
x = shader((ROOT / 'runtime/render_post.m').read_text(), 'x360_src')
cur = shader((ROOT / 'runtime/lampglow_shader.h').read_text(), 'lampglow_src')
old = shader(subprocess.check_output(['git', 'show', f'{base}:runtime/lampglow_shader.h'], cwd=ROOT, text=True), 'lampglow_src')
with tempfile.TemporaryDirectory() as d:
    d = Path(d); (d / 'old.metal').write_text(x + old); (d / 'new.metal').write_text(x + cur)
    subprocess.check_call(['clang', '-fobjc-arc', '-O2', '-framework', 'Metal', '-framework', 'Foundation', str(ROOT / 'runtime/lampsrc_bench.m'), '-o', str(d / 'b')])
    sys.exit(subprocess.call([str(d / 'b'), str(d / 'old.metal'), str(d / 'new.metal'), blobs]))
