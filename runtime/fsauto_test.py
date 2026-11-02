"""Full screen > Auto resolver: built-in display -> Native, any other display -> Borderless.

Proof: resolver as written: 0 failures; broken resolver (Auto always Borderless): 3 failures,
(Auto always Native): 1 failure. Production screen probe + resolver: 0 failures / 5 screens;
probe forced external: 2 failures; probe forced built-in: 3 failures. No window or game.
"""
from pathlib import Path
import subprocess
import tempfile

RT = Path(__file__).resolve().parent
app = (RT / 'app.m').read_text()
probe = app[app.index('int app_screen_is_builtin(void) {'):].split('\n}', 1)[0] + '\n}\n'
policy = app.split('// BEGIN offline fullscreen policy:')[1].split('\n', 1)[1].split('// END offline fullscreen policy')[0]

MAIN = r'''
#import <Cocoa/Cocoa.h>
static int builtin_now;
int app_screen_is_builtin(void) { return builtin_now; }
@POLICY@
static int failures;
#define CHECK(x) do { if(!(x)){fprintf(stderr,"FAIL: %s\n",#x);++failures;} }while(0)
int main(void) {
    CHECK(app_fullscreen_resolve("auto", 1) == 1);
    CHECK(app_fullscreen_resolve("AUTO", 0) == 0);
    CHECK(app_fullscreen_resolve("native", 0) == 1 && app_fullscreen_resolve("native", 1) == 1);
    CHECK(app_fullscreen_resolve("borderless", 1) == 0 && app_fullscreen_resolve("borderless", 0) == 0);
    CHECK(app_fullscreen_resolve("bogus", 1) == 0 && app_fullscreen_resolve("", 1) == 0 && app_fullscreen_resolve(NULL, 1) == 0);
    unsetenv("NFS_FULLSCREEN_MODE"); CHECK(!app_native_fullscreen() && !app_fullscreen_is_auto());
    setenv("NFS_FULLSCREEN_MODE", "auto", 1); builtin_now = 1;
    CHECK(app_native_fullscreen() == 1 && app_fullscreen_is_auto());
    builtin_now = 0; CHECK(app_native_fullscreen() == 1);   // decided once at launch, not re-evaluated
    printf("failures=%d\n", failures); return failures ? 1 : 0;
}
'''

def run(src):
    with tempfile.TemporaryDirectory(prefix='fsauto-test-') as d:
        f, exe = Path(d) / 't.m', Path(d) / 't'
        f.write_text(src)
        p = subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-fobjc-arc', '-framework', 'Cocoa', str(f), '-o', str(exe)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-3000:]
        return subprocess.run([str(exe)], capture_output=True, text=True)

PROBE_MAIN = r'''
#import <Cocoa/Cocoa.h>
static NSDictionary *description_now;
static int probes;
static CGDirectDisplayID seen;
@interface FakeScreen : NSObject
+ (instancetype)mainScreen;
@property(readonly) NSDictionary *deviceDescription;
@end
@implementation FakeScreen
+ (instancetype)mainScreen { return description_now ? [self new] : nil; }
- (NSDictionary *)deviceDescription { return description_now; }
@end
static int fake_builtin(CGDirectDisplayID display) { ++probes; seen=display; return display==41 || display==9001; }
#define NSScreen FakeScreen
#define CGDisplayIsBuiltin fake_builtin
@PROBE@
@POLICY@
int main(int argc, char **argv) {
    @autoreleasepool {
        (void)argc;
        unsigned display=(unsigned)strtoul(argv[1],NULL,10);
        int want=atoi(argv[2]), failures=0;
        if(display) description_now=@{@"NSScreenNumber":@(display)};
        else if(atoi(argv[3])) description_now=@{@"OtherKey":@41};
        setenv("NFS_FULLSCREEN_MODE","auto",1);
        failures+=app_native_fullscreen()!=want;
        failures+=probes!=(display?1:0);
        failures+=display && seen!=display;
        description_now=@{@"NSScreenNumber":@(want?77:41)};
        failures+=app_native_fullscreen()!=want; // launch decision stays cached
        failures+=probes!=(display?1:0);
        printf("failures=%d\n",failures); return failures?1:0;
    }
}
'''

def probe_run(source):
    with tempfile.TemporaryDirectory(prefix='fsauto-probe-') as d:
        f, exe = Path(d)/'t.m', Path(d)/'t'
        f.write_text(PROBE_MAIN.replace('@PROBE@',source).replace('@POLICY@',policy))
        p = subprocess.run(['clang','-Wall','-Wextra','-Werror','-fobjc-arc','-framework','Cocoa',str(f),'-o',str(exe)],capture_output=True,text=True)
        assert p.returncode==0,p.stderr[-3000:]
        failures=0
        for display,want,missing_key in [(41,1,0),(9001,1,0),(77,0,0),(0,0,0),(0,0,1)]:
            p=subprocess.run([str(exe),str(display),str(want),str(missing_key)],capture_output=True,text=True)
            assert p.returncode in (0,1),p.stderr
            failures+=p.returncode
        return failures

def main():
    line='return n && CGDisplayIsBuiltin((CGDirectDisplayID)n.unsignedIntValue);'
    assert probe.count(line)==1
    for name,body,want in [('production',probe,0),
        ('forced-external',probe.replace(line,'return n && (CGDisplayIsBuiltin((CGDirectDisplayID)n.unsignedIntValue), 0);'),2),
        ('forced-builtin',probe.replace(line,'if(n) CGDisplayIsBuiltin((CGDirectDisplayID)n.unsignedIntValue); return 1;'),3)]:
        failures=probe_run(body)
        print('screen probe '+name+': %d failures / 5 screens'%failures)
        assert failures==want,(name,failures)
    good = run(MAIN.replace('@POLICY@', policy))
    assert good.returncode == 0, good.stdout + good.stderr
    print('PASS resolver:', good.stdout.strip())
    line = 'return !strcasecmp(mode, "auto") && builtin;'
    assert policy.count(line) == 1
    for name, new in (('auto always Borderless', 'return builtin && 0;'), ('auto always Native', 'return !strcasecmp(mode, "auto") || builtin * 0;')):
        bad = run(MAIN.replace('@POLICY@', policy.replace(line, new)))
        assert bad.returncode == 1, name
        print('PASS mutant rejected (%s): %d failures' % (name, bad.stderr.count('FAIL:')))

if __name__ == '__main__':
    main()
