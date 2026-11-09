"""Cached draw settings: 1,000 simulated draws, getenv 2,000 -> 2.

The SHA-256-checked original selectors below fail the <=2-call budget (exit 1);
current selectors pass for absent/off/mixed-case/unknown values and live look
variant changes. Diagnostic-off flag: 1,000 calls, 0 getenv; on: 1 getenv.
Cache safety: each cached row is restart-only, or live with a reload that resets its cache (PASS);
a live row whose apply skips the reload fails (soft smoke reload removed: exit 1).
Autodiscovered by runtime/offline_tests.sh; no assets or GPU required.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
BASELINE = 'static int soft_smoke_on(void) {\n    if (diag_on) {\n        if (postfx_look_variant("softoff")) return 0;\n        if (postfx_look_variant("softon") || postfx_look_variant("softbroken")) return 1;\n    }\n    const char *e = getenv("NFS_SOFT_SMOKE");\n    return particles_physical_enabled() && (!e || strcmp(e, "off"));\n}\nint x360_post_ssao_enabled(void) {\n    if (look_var_is("ssaooff")) return 0;\n    if (look_var_is("ssaon")) return 1;\n    const char *e = getenv("NFS_SSAO");\n    return !e || strcasecmp(e, "off");\n}'
BASELINE_SHA = 'd3259b60715b632115eb333ae182182338338c83f59c5160c0f886ecc2e4aa82'


def function(source, name):
    match = re.search(r'^(?:static )?[\w<>* ]+\b' + name + r'\([^;]*?\)\s*\{', source, re.M)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


def run(cmd):
    return subprocess.run([str(x) for x in cmd], capture_output=True, text=True)


PRE = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <pthread.h>
static int diag_on, physical=1, calls;
static const char *value, *variant="";
static char *counted_getenv(const char *name) { (void)name; ++calls; return (char *)value; }
#define getenv counted_getenv
static int particles_physical_enabled(void) { return physical; }
static int postfx_look_variant(const char *v) { return !strcmp(variant,v); }
static int look_var_is(const char *v) { return !strcmp(variant,v); }
'''
MAIN = r'''
int main(int argc, char **argv) {
    value=argc>1 && strcmp(argv[1],"absent")?argv[1]:NULL;
    int soft=!(value && !strcmp(value,"off")), ssao=!(value && !strcasecmp(value,"off"));
    for(int i=0;i<1000;i++) {
        physical=i%2;
        if(!!soft_smoke_on()!=(physical&&soft) || !!x360_post_ssao_enabled()!=ssao)return 2;
    }
    int normal=calls;
    diag_on=1; variant="softon"; physical=0;
    if(!soft_smoke_on())return 2;
    variant="softoff"; if(soft_smoke_on())return 2;
    variant="ssaon"; if(!x360_post_ssao_enabled())return 2;
    variant="ssaooff"; if(x360_post_ssao_enabled())return 2;
    variant=""; physical=1;
    if(!!soft_smoke_on()!=soft || !!x360_post_ssao_enabled()!=ssao)return 2;
    printf("getenv=%d behavior=PASS\n",normal);
    return normal>2;
}
'''

def cache_safe(settings, live, src, name, reload):
    if re.search(r'\.env = "'+name+r'", \.restart = 1', settings):
        return True
    row = re.search(r'\.env = "'+name+r'", \.live_env = 1, \.apply = (\w+)', settings)
    if not row:
        return False
    body = re.search(r'void '+row[1]+r'\(int v\) \{.*?\n\}', live, re.S)
    defined = re.search(r'\nvoid '+reload+r'\(void\) \{[^}]*_cached(\s*=|,)\s*-1', src)
    return bool(body and reload+'()' in body[0] and defined)


def main():
    assert hashlib.sha256(BASELINE.encode()).hexdigest() == BASELINE_SHA
    render = (RT/'render.m').read_text()
    post = (RT/'x360_post.m').read_text()
    settings = (RT/'settings_port.c').read_text()
    # A cached switch is safe only if its row is restart-only, or live with a reload that clears the cache.
    live = (RT/'live_picture.c').read_text() if (RT/'live_picture.c').exists() else ''
    for name, reload, src in (('NFS_SOFT_SMOKE', 'render_soft_smoke_reload', render),
                              ('NFS_SSAO', 'x360_post_ssao_reload', post)):
        assert cache_safe(settings, live, src, name, reload), name
    smoke_cache = render[render.index('static int soft_smoke_cached ='):render.index('static int soft_smoke_on(')]
    ssao_cache = post[post.index('static int ssao_cached ='):post.index('int x360_post_ssao_enabled(')]
    current = smoke_cache + function(render, 'soft_smoke_on')+'\n'+ssao_cache+function(post, 'x360_post_ssao_enabled')
    with tempfile.TemporaryDirectory(prefix='draw-env-') as td:
        td = Path(td)
        for label, source in (('old',BASELINE), ('current',current)):
            path = td/(label+'.c'); exe = td/label
            path.write_text(PRE+source+MAIN)
            p = run(['clang','-std=c11','-Wall','-Wextra','-Werror',path,'-o',exe])
            assert p.returncode == 0, p.stderr[-2500:]
            for value in ('absent','off','OFF','on','bogus',''):
                p = run([exe,value])
                assert p.returncode == (1 if label=='old' else 0), (label,value,p.stdout,p.stderr)
                assert f'getenv={2000 if label=="old" else 2} behavior=PASS' in p.stdout
            print(f'{label}: getenv {2000 if label=="old" else 2}/1000 draws; 6 settings; live variants PASS')
        helper = function(render,'soft_smoke_diag_enabled')
        path=td/'diag.c'; exe=td/'diag'
        path.write_text(PRE+helper+r'''
int main(void){
    for(int i=0;i<1000;i++)if(soft_smoke_diag_enabled())return 2;
    if(calls)return 2;
    diag_on=1;value="1";
    for(int i=0;i<1000;i++)if(!soft_smoke_diag_enabled())return 2;
    printf("diag getenv off=0 on=%d\n",calls);return calls!=1;
}
''')
        p=run(['clang','-std=c11','-Wall','-Wextra','-Wno-unused-function',path,'-o',exe])
        assert p.returncode==0,p.stderr[-2500:]
        p=run([exe]);assert p.returncode==0,p.stdout+p.stderr
        print(p.stdout.strip())
    # Replays must share the same cached diagnostic flag.
    assert render.count('getenv("BOOT_DIAG_SOFT_SMOKE")')==1


if __name__ == '__main__':
    main()
