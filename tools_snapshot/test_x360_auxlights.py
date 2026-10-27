#!/usr/bin/env python3
"""R20 offline proof. No game process, guest entry point, or asset writes.

Pass: python -I tools_snapshot/test_x360_auxlights.py
Deliberate failures (must exit 1): append --mutate bbox / --mutate sentinel.
Reimports source assets, compares every generated key/light byte, then compiles a
small accessor-only C executable and checks every key plus all mode choices.
"""
import hashlib
import argparse
import importlib.util
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('x360_auxlights', ROOT / 'tools_snapshot/x360_auxlights.py')
aux = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aux)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def check_matching_rules():
    """Ambiguous lit/unlit and conflicting contexts must never enter the table."""
    light = struct.pack('<8f', 1, 2, 3, 15, 1, 1, 1, 15)
    contexts = [('a', (light,)), ('b', (light,)), ('a', (light,))]
    bbox = bytes(24)
    pc = [(7, bbox, -1)]
    for indices, matched, collision in [([0], 1, 0), ([0, 2], 2, 0),
                                         ([0, 1], 0, 2), ([0, -1], 0, 1), ([0, 9], 0, 1)]:
        rows, stats = aux.match_instances(contexts, [(7, bbox, i) for i in indices], pc)
        expect(stats['matched'] == matched and stats['collisions'] == collision,
               f'wrong collision policy for {indices}')
        expect(len(rows) == bool(matched), 'ambiguous duplicate emitted a key')
    _, stats = aux.match_instances(contexts, [(8, bbox, 0), (7, bbox, 9), (7, bbox, -2)], pc)
    expect(stats['unmatched'] == 1 and stats['invalid'] == 2, 'section/invalid-index check')
    rows, stats = aux.match_instances(contexts, [(7, bbox, 0)], pc * 2)
    expect(len(rows) == 1 and stats['matched'] == 1, 'identical PC bbox duplicates')


def check_accessor(table, stats):
    """Exercise the actual C implementation, including immutable context storage."""
    with tempfile.TemporaryDirectory(prefix='auxlights-test-') as tmp:
        tmp = Path(tmp)
        (tmp / 'auxlights_data.inc').write_text(table)
        implementation = (ROOT / 'runtime/auxlights.c').read_text().replace(
            '#include "gen/auxlights_data.inc"', f'#include "{tmp}/auxlights_data.inc"')
        (tmp / 'auxlights.c').write_text(implementation)
        harness = r'''
#include "auxlights.c"
#include <assert.h>
int main(int argc, char **argv) {
    int on = argc > 1 && argv[1][0] == '1';
    assert(auxlights_enabled() == on);
    assert(!strcmp(aux_contexts[0].name, "TRN_CP_IT_Road_A_14_AUX07_CHOP_R3"));
    assert(aux_contexts[0].count == 4);
    const unsigned char *first = (const unsigned char *)&aux_contexts[0].lights[0];
    unsigned stored = 0, truncated = 0;
    for (size_t i = 0; i < AUX_DATA_CONTEXTS; ++i) {
        const AuxLightContext *ctx = &aux_contexts[i];
        assert(ctx->count == (ctx->source_count < 8 ? ctx->source_count : 8));
        stored += ctx->count; truncated += ctx->source_count > 8;
    }
    assert(stored == AUX_DATA_STORED_LIGHTS && truncated == AUX_DATA_TRUNCATED_CONTEXTS);
    for (size_t i = 0; i < AUX_DATA_ROWS; ++i) {
        const AuxLightKey *key = &aux_keys[i];
        assert(key->context_index < AUX_DATA_CONTEXTS);
        if (i) {
            const AuxLightKey *prev = &aux_keys[i - 1];
            assert(prev->section_id < key->section_id ||
                   (prev->section_id == key->section_id && memcmp(prev->bbox, key->bbox, 24) < 0));
        }
        const AuxLightContext *want = on ? &aux_contexts[key->context_index] : NULL;
        assert(auxlights_find(key->section_id, key->bbox) == want);
    }
    assert(!auxlights_find(0xffffffff, aux_keys[0].bbox));
    assert(!auxlights_find(aux_keys[0].section_id, NULL));
    uint8_t changed[24]; memcpy(changed, aux_keys[0].bbox, 24); changed[0] ^= 1;
    assert(!auxlights_find(aux_keys[0].section_id, changed));
    printf("accessor: keys=%u mode=%s cap=8 altered_bbox=NULL first_light=", AUX_DATA_ROWS, on ? "x360" : "pc");
    for (int i = 0; i < 32; ++i) printf("%02x", first[i]);
    printf("\n");
    return 0;
}
'''
        (tmp / 'test.c').write_text(harness)
        build = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                                '-I', str(ROOT / 'runtime'), str(tmp / 'test.c'),
                                '-o', str(tmp / 'test')], capture_output=True, text=True)
        expect(build.returncode == 0, 'accessor compile failed: ' + build.stderr[-3000:])
        for mode in (None, 'pc', 'x360', 'invalid'):
            env = dict(os.environ, BOOT_DIAG='1')
            env.pop('NFS_AUX_LIGHTS', None)
            if mode is not None:
                env['NFS_AUX_LIGHTS'] = mode
            # X360 is the default; only 'pc' turns it off
            run = subprocess.run([str(tmp / 'test'), '0' if mode == 'pc' else '1'],
                                 env=env, capture_output=True, text=True)
            expect(run.returncode == 0, f'accessor mode {mode}: {run.stderr[-2000:]}')
            expect(f'contexts={stats["contexts"]}' in run.stderr and
                   f'keys={stats["rows"]}' in run.stderr, 'BOOT_DIAG table counts missing')
            out = run.stdout.strip()
            light = bytes.fromhex(out.rsplit('first_light=', 1)[1])
            expect(hashlib.sha256(light).hexdigest() == aux.SENTINEL_LIGHT_SHA256, 'sentinel light differs')
            print(f'mode={mode or "default"}: {out.rsplit(" first_light=", 1)[0]} sentinel=exact')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table', type=Path, default=aux.DEFAULT_TABLE)
    parser.add_argument('--mutate', choices=('bbox', 'sentinel'))
    args = parser.parse_args()
    check_matching_rules()
    contexts, rows, stats = aux.import_data()
    print('source: ' + ' '.join(f'{k}={v}' for k, v in stats.items()), flush=True)
    expected = dict(contexts=1231, lights=2997, stored_lights=2964, truncated_contexts=17,
                    lit_instances=1235, matched=1003, unmatched=232, collisions=0, invalid=0,
                    pc_instances=77783, x360_instances=87292, rows=999)
    expect(stats == expected, f'L2RA reference counts changed: {stats}')
    table = args.table.read_text()
    if args.mutate == 'bbox':
        start = table.index('static const AuxLightKey')
        hit = re.search(r'0x([0-9a-f]{2})', table[start:])
        offset = start + hit.start()
        table = table[:offset] + f'0x{int(hit[1], 16) ^ 1:02x}' + table[offset + 4:]
        print('injected: exactly one bbox byte XOR 1', flush=True)
    elif args.mutate == 'sentinel':
        # Change just the least significant bit of the first position float.
        word, = struct.unpack('<I', contexts[0][1][0][:4])
        changed, = struct.unpack('<f', struct.pack('<I', word ^ 1))
        before = struct.unpack('<f', contexts[0][1][0][:4])[0].hex() + 'f'
        table = table.replace(before, changed.hex() + 'f', 1)
        print('injected: sentinel position.x least significant bit XOR 1', flush=True)
    metadata = {k.lower(): int(v) for k, v in re.findall(r'#define AUX_DATA_([A-Z0-9_]+) (\d+)u', table)}
    expect(metadata == stats, 'generated table match counts differ from fresh import')
    expect(table == aux.render_table(contexts, rows, stats),
           f'generated table differs from fresh byte-exact import ({args.mutate or "source/table"})')
    check_accessor(table, stats)
    print('PASS: sentinel, complete table, matching policy, all accessor keys and modes')


if __name__ == '__main__':
    try:
        main()
    except (AssertionError, ValueError) as error:
        print(f'FAIL: {error}', flush=True)
        raise SystemExit(1)
