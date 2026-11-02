"""Admission/traffic guard with production-source bypass controls (ASan/UBSan).
Premise before change: one 28-byte HELLO increased clients 0 -> 1.
Normal: cookie 53/0, global 8941/0, source 7/0, egress 2027/0, parse 8/0,
service 11/0, client 4/0 checks/failures: original 11,051 checks retained.
Security follow-up on 7f28a0fb: 14,400/14,400 STATE dropped, 120/120 timeouts;
3/3 legitimate sources rejected by 1,024 spoofed records; 1 cookie revived at
4,294,967,297 ms. Fixed: all zero; isolation 124/0, spoof 1/0, revival 6/0,
rotation/retention 26/0: 11,208 checks total. Seven controls fail 3/1/1/4/5/2/1 checks.
Broken cookie/admission/ingress/challenge/source/egress/fairness/expiry/service/
parse/error/restart paths fail named assertions: 39/3/3921/4004/1/1999/2/2/1/5/1/1/1 failures.
Service-clock proof: the original sleeping flood let the 200 ms mutant pass 11/0
under load; a 300 us/packet fixture clock gives normal 11/0 and mutant 11/5.
The production 2 ms deadline and packet-count assertions are unchanged.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
RT = Path(__file__).resolve().parent
CF = ['clang', '-std=c11', '-D_DARWIN_C_SOURCE', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror',
      '-g', '-fsanitize=address,undefined', '-I', str(RT)]
LINK = ['-lcrypto', '-lm'] if sys.platform != 'darwin' else []
MUTANTS = {
    'cookie': ('cookie', '''int validated = len == ROAM_HELLO_PROOF &&
            (roam_cookie_valid(r->secret, from, b[2], p, p + ROAM_HELLO_BASE, r->absolute_ms) ||
             (r->previous_key && roam_cookie_valid(r->previous_secret, from, b[2], p, p + ROAM_HELLO_BASE, r->absolute_ms)));''',
               'int validated = 1;', 'cookie_before_signature'),
    'admission': ('global', 'if (r->admissions.tokens < 1000 || src->admission.tokens < 1000)',
                  'if (0 && (r->admissions.tokens < 1000 || src->admission.tokens < 1000))', 'global_admission_bound'),
    'ingress': ('global', 'if (r->ingress_packets.tokens < 1000 || r->ingress_bytes.tokens < (uint64_t)wire * 1000)',
                'if (0 && (r->ingress_packets.tokens < 1000 || r->ingress_bytes.tokens < (uint64_t)wire * 1000))', 'global_ingress_packet_bound'),
    'challenge': ('global', 'if (r->challenges.tokens < 1000 || replies->tokens < 1000)',
                  'if (0 && (r->challenges.tokens < 1000 || replies->tokens < 1000))', 'global_challenge_bound'),
    'source': ('global', 'if (r->admissions.tokens < 1000 || src->admission.tokens < 1000)',
               'if (r->admissions.tokens < 1000)', 'source_admission_bound'),
    'egress': ('egress', 'if (r->egress_packets.tokens < 1000 || r->egress_bytes.tokens < (uint64_t)(n + 28) * 1000)',
               'if (0 && (r->egress_packets.tokens < 1000 || r->egress_bytes.tokens < (uint64_t)(n + 28) * 1000))', 'egress_packet_burst'),
    'fairness': ('egress', 'r->output_cursor = next_cursor;',
                 '(void)next_cursor; r->output_cursor = 0;', 'egress_fair_rotation'),
    'message_fairness': ('egress', 'k->service_turn = (turn + 1) % 3;', 'k->service_turn = 0;', 'egress_fair_message_kinds'),
    'source_expiry': ('source', 'expire_sources(r, now);', 'if (0) expire_sources(r, now);', 'source_idle_expiry'),
    'service': ('service', 'if (elapsed >= 2000)', 'if (elapsed >= 200000)', 'service_elapsed_bound'),
    'parse': ('parse', 'if (len != ROAM_HELLO_BASE && len != ROAM_HELLO_PROOF)', 'if (0 && (len != ROAM_HELLO_BASE && len != ROAM_HELLO_PROOF))', 'malformed_trailing_hello'),
    'client_error': ('client', 'c->welcomed = 1; c->last_err = 0;', 'c->welcomed = 1;', 'client_restart_recovery', 'roam_core.c'),
    'client_restart': ('client', 'c->welcomed = 0; c->id = 0;\n        if (c->last_err != ROAM_ERR_BANNED) c->last_err = 0;', 'if (c->last_err != ROAM_ERR_BANNED) c->last_err = 0;', 'client_restart_challenge', 'roam_core.c'),
    'isolation': ('isolation', 'if (!admitted) {', 'if (1) {', 'isolation_state_progress'),
    'spoof': ('spoof', 'if (!cookie_keys(r))',
              'Source *untrusted = source(r, from, now); if (!untrusted) return; untrusted->last = now;\n        if (!cookie_keys(r))',
              'spoof_legitimate_admission'),
    'absolute_age': ('revival', '(uint8_t)(now >> (i * 8));\n#ifdef __APPLE__',
                     '(uint8_t)((uint64_t)(uint32_t)now >> (i * 8));\n#ifdef __APPLE__',
                     'cookie_absolute_age', 'roam_admission.h'),
    'rotation': ('rotation', 'if (elapsed < ROAM_KEY_MS) return 1;',
                 'if (1) return 1;', 'cookie_scheduled_rotation'),
    'previous_key': ('rotation', '(r->previous_key && roam_cookie_valid(',
                     '(0 && roam_cookie_valid(', 'cookie_previous_key'),
    'retention_refresh': ('rotation', 'return found;', 'found->last = now; return found;',
                          'source_full_retry_no_refresh'),
    'reply_idle': ('rotation', 'if (reply->started) roam_refill(',
                   'if (0) roam_refill(', 'reply_idle_refill'),
}
def build(td, label, replacement=None):
    sources={name:(RT/name).read_text() for name in ['roam_relay.c','roam_core.c','roam_admission.h']}
    if replacement:
        _, old, new, _=replacement[:4]
        file=replacement[4] if len(replacement)>4 else 'roam_relay.c'
        assert sources[file].count(old)==1, (label,'mutant anchor moved')
        sources[file]=sources[file].replace(old,new)
    for name,source in sources.items():
        (td/name).write_text(source)
    (td/'roam_admission_test.c').write_text((RT/'roam_admission_test.c').read_text())
    exe=td/label
    p=subprocess.run(CF+[str(RT/'roam_auth.c'),str(RT/'roam_moderation.c')]+(['-framework','Security','-framework','CoreFoundation'] if __import__('sys').platform=='darwin' else [])+[str(td/'roam_admission_test.c'),str(td/'roam_core.c'),'-o',str(exe)]+LINK,capture_output=True,text=True)
    assert p.returncode==0,p.stderr[-3000:]
    return exe

def run(exe,case):
    p=subprocess.run([str(exe),case],capture_output=True,text=True,timeout=30)
    print(exe.name+': '+p.stdout.strip(),flush=True)
    return p

def main():
    with tempfile.TemporaryDirectory(prefix='roam-admission-') as tmp:
        td=Path(tmp); normal=build(td,'normal')
        for case in ['cookie','global','source','egress','parse','service','client','isolation','spoof','revival','rotation']:
            p=run(normal,case); assert p.returncode==0,p.stderr[-3000:]
        for label,replacement in MUTANTS.items():
            p=run(build(td,label,replacement),replacement[0])
            assert p.returncode==1 and 'FAIL '+replacement[3] in p.stdout,(label,p.returncode,p.stderr[-3000:])
    print('ROAM ADMISSION PASS: 11 normal cases; 13 existing + 7 security controls rejected')
    return 0
if __name__=='__main__':
    raise SystemExit(main())
