#!/usr/bin/env python3
"""Run the production suite scheduler on scratch pass/skip/fail fixtures.
Proof: strict skip policy 0 failures / 6 scenarios; disabled strict guard 1 / 6.
No real feature tests, assets, game, GPU or sockets are invoked by this fixture.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

RT=Path(__file__).resolve().parent

def exercise(script):
    with tempfile.TemporaryDirectory(prefix='offline-noskip-') as d:
        root=Path(d);rt=root/'runtime';rt.mkdir();(root/'tools').mkdir()
        (rt/'offline_tests.sh').write_text(script)
        (rt/'env.sh').write_text('nfs_need_py() { :; }\n')
        (root/'tools/gen_tables.py').write_text('')
        # Use actual release names so the real release list participates in the proof.
        (rt/'fsauto_test.py').write_text('print("SKIP: fixture input missing"); raise SystemExit(3)\n')
        (rt/'maxperf_career_test.py').write_text('print("fixture passed")\n')
        (rt/'optional_test.py').write_text('print("SKIP: optional fixture"); raise SystemExit(3)\n')
        (rt/'broken_test.py').write_text('print("fixture failure"); raise SystemExit(1)\n')
        cases=[('default skip',None,['fsauto_test'],0,'SKIPPED (input missing): fsauto_test'),
               ('strict skip','1',['fsauto_test'],1,'OFFLINE FAIL: fsauto_test'),
               ('zero skip','0',['fsauto_test'],0,'OFFLINE PASS'),
               ('strict optional','1',['optional_test'],0,'SKIPPED (input missing): optional_test'),
               ('strict pass','1',['maxperf_career_test'],0,'OFFLINE PASS (1 tests'),
               ('strict failure','1',['broken_test'],1,'OFFLINE FAIL: broken_test')]
        failures=0
        for label,strict,names,rc,line in cases:
            env=dict(os.environ,TOOLS_PY=sys.executable,OFFLINE_FAIL_LOGS=str(root/'fail-logs'),TMPDIR=str(root),OFFLINE_JOBS='1')
            for key in ('NFS_TESTS_NOSKIP','OFFLINE_TESTS_PLAN','OFFLINE_SERIAL_TESTS'):
                env.pop(key,None)
            if strict is not None:env['NFS_TESTS_NOSKIP']=strict
            p=subprocess.run(['sh',str(rt/'offline_tests.sh'),*names],cwd=root,env=env,capture_output=True,text=True,timeout=30)
            ok=p.returncode==rc and line in p.stdout
            if label=='strict skip' and ok:
                logs=list((root/'fail-logs').glob('fsauto_test.*.log'))
                ok=bool(logs) and 'release-critical test fsauto_test skipped' in logs[0].read_text()
            if not ok:print(label+': '+p.stdout[-1500:]+p.stderr[-500:])
            failures+=not ok
        return failures

def main():
    script=(RT/'offline_tests.sh').read_text()
    line='[ "${NFS_TESTS_NOSKIP:-0}" = 1 ]'
    assert script.count(line)==1
    for label,body,want in [('production',script,0),('strict-disabled',script.replace(line,'[ 0 = 1 ]'),1)]:
        failures=exercise(body)
        print('%s: %d failures / 6 scenarios'%(label,failures))
        assert failures==want,(label,failures)

if __name__=='__main__':main()
