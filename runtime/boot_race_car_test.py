#!/usr/bin/env python3
"""Execute production race_setup against scratch guest memory.

Original direct copy: 0/3 uppercase/mixed-case keys normalized; fixed: 3/3.
The original-copy control also fails the uppercase RACE preset sentinel.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = (ROOT/'runtime/boot.c').read_text()
    start = source.index('static void race_setup(void) {')
    body = source[start:source.index('\n#else', start)]
    harness = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static uint8_t memory[0xa00000];
static uint8_t *G=memory;
#define M32(at) (*(uint32_t *)(G+(at)))
RACE_SETUP
int main(int argc,char **argv) {
    if(argc!=3) return 2;
    setenv("BOOT_RACE","15.1.1",1);
    if(strcmp(argv[1],"unset")) setenv("BOOT_RACE_CAR",argv[1],1);
    else unsetenv("BOOT_RACE_CAR");
    race_setup();
    const char *key=(char *)G+0x9b4128;
    if(strcmp(key,argv[2])) return 1;
    if(M32(0x8f86a8)!=(strcmp(argv[2],"race")?0x9b4128u:0)) return 1;
    return M32(0x926064)!=1 || M32(0x926144)!=1;
}
'''
    cases = [('VEYRON','veyron'),('VeYrOn','veyron'),('JAGUARXK','jaguarxk'),
             ('veyron','veyron'),('RACE','race'),('race','race'),
             ('unset','bmwm3gtre46'),('A'*80,'a'*63)]
    # Pin only the old copy operation, independent of repository history.
    original = 'strncpy((char *)G + 0x009B4128, car, 0x3F);'
    assert hashlib.sha256(original.encode()).hexdigest() == '66a9c14a0528aacc4e884c9a3b5e8115c371bf229223ca422f6897e9b0708bfa'
    with tempfile.TemporaryDirectory(prefix='boot-race-car-') as directory:
        folder=Path(directory)
        for broken in (False,True):
            implementation=body
            if broken:
                begin=implementation.index('    /* Normalize the developer car key')
                end=implementation.index('\n    M32(0x008F86A8)',begin)
                implementation=implementation[:begin]+'    '+original+implementation[end:]
            path=folder/'race.c'; path.write_text(harness.replace('RACE_SETUP',implementation))
            binary=folder/'race'
            subprocess.run(['clang','-std=c11','-D_DARWIN_C_SOURCE','-Wall','-Wextra','-Werror',
                            str(path),'-o',str(binary)],check=True,capture_output=True,text=True)
            results=[subprocess.run([str(binary),value,key],capture_output=True,
                                    env=dict(os.environ),timeout=5).returncode for value,key in cases]
            passed=sum(rc==0 for rc in results[:3])
            print(f'{"original copy" if broken else "production"}: normalized {passed}/3; cases {results}')
            if broken:
                assert passed==0 and results[4]==1
            else:
                assert results==[0]*len(cases),results


if __name__=='__main__':
    main()
