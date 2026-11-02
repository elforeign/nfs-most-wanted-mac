"""Production shared-service selection and authenticated UDP loopback lifecycle.
Proof: original silent-drop behavior (dddd7880) 247 checks/5 failures; fixed 245/0.
Broken recovery 314/21; silent ban 246/2; live notification 249/3; client state 248/2.
Review pins: backoff 5 checks/3 failures -> 5/0; sticky ban 17/5 -> 17/0; signature 9/5 -> 8/0.
Fixed review total: 50 checks/0 failures, including 5 s unban recovery within the cookie lifetime.
All controls are rejected; counted UDP sends can vary with receive timing. Loopback only.
Sockets denied locally use run_tests; short scratch paths fit Darwin Unix sockets.
"""
from pathlib import Path
import hashlib
import re
import subprocess
import sys
import tempfile
RT=Path(__file__).resolve().parent
CF=['clang','-std=c11','-D_DEFAULT_SOURCE','-D_DARWIN_C_SOURCE','-Wall','-Wextra','-Werror','-g','-pthread',
    '-fsanitize=address,undefined','-I',str(RT)]
LINK=['-framework','Security','-framework','CoreFoundation'] if sys.platform=='darwin' else ['-lcrypto','-lm']

# Pinned review fragments; no repository history or whole-file fingerprints.
CHALLENGE_BASELINE = 'c->welcomed = 0; c->id = 0; c->last_err = 0;'
assert hashlib.sha256(CHALLENGE_BASELINE.encode()).hexdigest() == 'd6033e56286657fdded139963fb63282b7b4b18a7a1a7215c7af8716bdbb8fbe'
ERROR_BASELINE = 'c->last_err = b[0];'
assert hashlib.sha256(ERROR_BASELINE.encode()).hexdigest() == 'bc0a106894bba0fa3820b347d866ed3dc1c55e25b957ebdd4f8efb2f26ccb5e6'
BAN_BASELINE = '        if (roam_moderation_banned(r->moderation, identity, roam_moderation_utc())) {\n            r->st.ban_rejected++; banned_reply(r, from, now);\n            if (k) drop_client(r, k); return;\n        }\n'
assert hashlib.sha256(BAN_BASELINE.encode()).hexdigest() == 'a2399cc36a4e2db4396320cf8482cc8d4d31b51c6b912464fe44f4dfa96a1235'
HELLO_BASELINE = '    if (now - roam_last_hello >= (roam.welcomed ? 2000u : 500u)) {'
assert hashlib.sha256(HELLO_BASELINE.encode()).hexdigest() == '805ff10689e3365b59427be87924a7432c4eff2bbc8a34c034e7b0be46e3e7f7'

def hello_fixture(line):
    assert line.startswith('    if (') and line.endswith(') {')
    predicate=line[len('    if ('):-len(') {')]
    return ('static int review_hello_due(const RoamClient *client, uint32_t now, uint32_t roam_last_hello) {\n'
            '#define roam (*client)\n    return '+predicate+';\n#undef roam\n}\n')

def review_build(td, name, baseline=None):
    core=(RT/'roam_core.c').read_text(); relay=(RT/'roam_relay.c').read_text()
    hello=next(line for line in (RT/'roam_game.c').read_text().splitlines() if 'if (now - roam_last_hello >=' in line)
    if baseline=='backoff': hello=HELLO_BASELINE
    if baseline=='sticky_ban':
        for fixed, original in [
            ('c->welcomed = 0; c->id = 0;\n        if (c->last_err != ROAM_ERR_BANNED) c->last_err = 0;',CHALLENGE_BASELINE),
            ('if (c->last_err != ROAM_ERR_BANNED) c->last_err = b[0];',ERROR_BASELINE),
        ]:
            assert core.count(fixed)==1,('sticky ban anchor',fixed)
            core=core.replace(fixed,original)
    if baseline=='ban_signature':
        assert relay.count(BAN_BASELINE)==1
        relay=relay.replace(BAN_BASELINE,'')
        anchor='        // A live endpoint cannot replace its verified installation identity in place.'
        assert relay.count(anchor)==1
        relay=relay.replace(anchor,BAN_BASELINE+anchor)
    (td/'roam_hello_due.inc').write_text(hello_fixture(hello))
    sources=[]
    for file in ['roam_shared_test.c','roam_core.c','roam_relay.c','roam_code.c','roam_auth.c','roam_moderation.c']:
        text={'roam_core.c':core,'roam_relay.c':relay}.get(file)
        if text is None:text=(RT/file).read_text()
        path=td/file;path.write_text(text);sources.append(str(path))
    exe=td/name
    p=subprocess.run(CF+['-I',str(td)]+sources+LINK+['-o',str(exe)],capture_output=True,text=True)
    assert p.returncode==0,p.stderr[-4000:]
    return exe

def review_run(td,name,baseline=None):
    exe=review_build(td,name,baseline)
    state=td/(name+'-state');admin=td/(name+'-admin');state.mkdir(mode=0o700);admin.mkdir(mode=0o700)
    p=subprocess.run([str(exe),str(state),str(admin),'review'],capture_output=True,text=True,timeout=30)
    print(name+': '+p.stdout.strip(),flush=True)
    log=RT.parent/'.metal-cache'/('roam-shared-'+name+'.log')
    log.parent.mkdir(exist_ok=True);log.write_text(p.stdout+p.stderr)
    assert p.returncode==(1 if baseline else 0),p.stderr[-3000:]
    groups={case:(int(total),int(bad)) for case,total,bad in re.findall(r'review (\w+): (\d+) checks, (\d+) failures',p.stdout)}
    expected={'backoff':(5,0),'sticky_ban':(17,0),'ban_signature':(8,0)}
    if baseline: expected[baseline]={'backoff':(5,3),'sticky_ban':(17,5),'ban_signature':(9,5)}[baseline]
    assert groups==expected,(groups,expected)

def main():
    # Fixtures override both macros before including the controller, independent of release assignment.
    config=(RT/'roam_shared_endpoint.h').read_text()
    assert '#ifndef ROAM_SHARED_IPV4' in config and '#ifndef ROAM_SHARED_PORT' in config
    with tempfile.TemporaryDirectory(prefix='roam-shared-',dir='/tmp') as tmp:
        td=Path(tmp)
        for baseline in [None,'backoff','sticky_ban','ban_signature']:
            review_run(td,'review_'+(baseline or 'fixed'),baseline)
        # Cookie recovery must survive process-local key changes, without replacing installation credentials.
        mutants={'normal':None,'recovery':('roam_core.c','c->welcomed = 0; c->id = 0;\n        if (c->last_err != ROAM_ERR_BANNED) c->last_err = 0;',
                                          'c->has_cookie = 0; c->welcomed = 0; c->id = 0;\n        if (c->last_err != ROAM_ERR_BANNED) c->last_err = 0;'),
                 'silent_ban':('roam_relay.c','r->st.ban_rejected++; banned_reply(r, from, now);',
                               'r->st.ban_rejected++;'),
                 'live_ban':('roam_relay.c','banned_reply(r, &r->c[i].addr, r->now);','(void)r->now;'),
                 'ban_state':('roam_core.c','if (c->last_err == ROAM_ERR_BANNED)', 'if (0 && c->last_err == ROAM_ERR_BANNED)')}
        for name,mutant in mutants.items():
            files=['roam_shared_test.c','roam_code.c','roam_core.c','roam_relay.c','roam_auth.c','roam_moderation.c']
            sources=[]
            for file in files:
                path=RT/file
                if mutant and mutant[0]==file:
                    text=path.read_text();assert text.count(mutant[1])==1,(file,mutant[1])
                    path=td/file;path.write_text(text.replace(mutant[1],mutant[2]))
                sources.append(str(path))
            exe=td/name
            p=subprocess.run(CF+['-I',str(td)]+sources+LINK+['-o',str(exe)],capture_output=True,text=True)
            assert p.returncode==0,p.stderr[-4000:]
            state=td/(name+'-state');admin=td/(name+'-admin')
            state.mkdir(mode=0o700);admin.mkdir(mode=0o700)
            p=subprocess.run([str(exe),str(state),str(admin)],capture_output=True,text=True,timeout=30)
            print(name+': '+p.stdout.strip(),flush=True)
            if p.returncode==3:return 3
            assert p.returncode==(1 if mutant else 0),p.stderr[-3000:]
            if mutant:
                expected={
                    'recovery':'clients[i].welcomed && clients[i].has_cookie && clients[i].auth.ready',
                    'silent_ban':'clients[0].has_cookie && !clients[0].welcomed && clients[0].last_err==ROAM_ONLINE_BANNED',
                    'live_ban':'clients[0].last_err==ROAM_ONLINE_BANNED',
                    'ban_state':'!clients[0].welcomed && !clients[0].id && !clients[0].nmembers && !clients[0].has_members',
                }[name]
                assert expected in p.stdout,(name,'intended behavioral assertion did not fail')
    return 0
if __name__=='__main__':raise SystemExit(main())
