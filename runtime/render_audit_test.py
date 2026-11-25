#!/usr/bin/env python3
"""Offline render audit regression with production function fragments and a real Metal recorder.

LockRect RGB mean absolute difference vs an independent same-frame capture: 80 -> 0.
DrawPrimitiveUP padding bytes taken from beyond the 96-byte submission: 24 -> 0.
Queries retained after 2,048 final releases: 2,048 -> 1,024 (the retirement
quarantine); query and encoder still active on final release: 1 -> 0 each.
Glass UV side buffers held after 32 distinct vertex buffers are released: 32 -> 0.
Extra lamp-stream vertex-buffer lookups per static draw with both switches off: 2 -> 0.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RT = Path(__file__).resolve().parent
sys.path.insert(0, str(RT))
from live_picture_regression_test import function
from save_file_fixture import mw_source

OLD_RELEASE = 'o->kind == K_IB || (o->kind == K_SURFACE && !o->parent)'
OLD_COPY = '        memcpy(p, G + (uint32_t)(src + from), bytes);'
OLD_GLASS_START = '    if (!vb || !vb->size || vb->size % 36) return nil;'
OLD_GLASS_GET = '    GlassUV1 *u = (__bridge GlassUV1 *)map_get(&glass_uv1_cache, vb->guest);'
OLD_GLASS_PUT = '    map_put(&glass_uv1_cache, vb->guest, u);'
OLD_LAMP = ('            daynight_stream_bind(enc, mtl, obj(dev.stream[0]), gpu_buf(obj(dev.stream[0])), '
            'dev.stream_stride[0], dev.stream_off[0], vs->tokens_hash, dev.frames);')
OLD_SHA = 'd3fe9a8a0ac9ea05b14ff60cb1ed3cb678203528868133d4856d77f140f008be'


def section(source, start, end):
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def replace_once(source, current, old=''):
    assert source.count(current) == 1, current
    return source.replace(current, old, 1)


def fragments(old):
    d3d = (RT / 'd3d9.m').read_text()
    render = mw_source(RT / 'render.m')
    query = (RT / 'render_query.m').read_text()
    lamps = (RT / 'daynight_stream.m').read_text()

    release = function(d3d, 'static uint32_t obj_release(')
    create = function(d3d, 'static int v_CreateRenderTarget(')
    lock = function(d3d, 'static int s_LockRect(')
    glass = function(render, 'static GlassUV1 *glass_uv1_for(')
    upload = section(render, '    if (up_vertices && !indexed) first = 0;',
                     '    if (glass_now.pso)')
    bind = section(render, '            if (daynight_stream_wanted(', '            if (rw_pane_active)')
    gpu_release = function(render, 'void render_release(')
    old_map = ''
    if old:
        release = replace_once(release, 'o->kind == K_IB || o->kind == K_QUERY || (o->kind == K_SURFACE && !o->parent)', OLD_RELEASE)
        create = replace_once(create, '    s->lockable_rt = arg(c, 6) != 0;\n')
        lock = replace_once(lock, next(line for line in lock.splitlines(keepends=True) if 'render_lock_readback' in line))
        upload = replace_once(upload, section(upload, '        uint32_t copy_bytes', '        // the buffer'), OLD_COPY + '\n')
        glass = replace_once(glass, section(glass, '    if (!vb', '    const uint8_t *b'), OLD_GLASS_START + '\n')
        glass = replace_once(glass, '    GlassUV1 *u = g->glass_uv1;', OLD_GLASS_GET)
        glass = replace_once(glass, '    g->glass_uv1 = u;', OLD_GLASS_PUT)
        old_map = section(render, 'typedef struct { uint64_t key; void *obj; } MapSlot;', '\n// ----------------')
        old_map = old_map[:old_map.index('\n}', old_map.index('static void map_put')) + 2]
        old_map += '\nstatic Map glass_uv1_cache;\n'
        gpu_release = replace_once(gpu_release, '    if (o->kind == K_QUERY && o->q_state == 1) render_query_issue(o, 1);\n')
        gpu_release = replace_once(gpu_release, '        g->glass_uv1 = nil;\n')
        bind = OLD_LAMP + '\n'

    return '\n'.join((
        '#include "rt_scene.h"\nint rt_scene_enabled(void) { return 0; }\n'
        'void rt_scene_forget(uint64_t resource_id) { (void)resource_id; }',
        section(d3d, 'static Obj **objs;', '// diag mem.log'),
        '#define THIS obj(arg(c, 0))',
        function(d3d, 'int is_dxt('), function(d3d, 'uint32_t fmt_bpp('),
        function(d3d, 'static uint32_t level_pitch('), function(d3d, 'uint32_t level_rows('),
        function(d3d, 'static Obj *make_surface('), function(d3d, 'static uint32_t surface_mem('),
        function(d3d, 'static void set_multisample('), create, lock,
        function(d3d, 'static Obj *ref_owner('), release, function(d3d, 'static int m_Release('),
        function(query, 'void render_query_issue('),
        section(render, '@class GlassUV1;', 'static id<MTLBuffer> gpu_buf('),
        gpu_release, function(render, 'static uint64_t fnv('), old_map,
        section(render, '@interface GlassUV1', 'static struct { __unsafe_unretained'), glass,
        function(render, 'int render_lock_readback('),
        section(lamps, 'static NSString *folder;', 'static NSString *hex_digest('),
        function(lamps, 'int daynight_stream_wanted('),
        'static struct { void *vbuf[16]; } eb;',  # copied ring upload invalidates cached static bindings
        'static void upload_vertices(uint32_t up_vertices, uint32_t stride, uint32_t count) {',
        '    uint32_t strides[] = {stride}, used_streams = 1, first = 0;',
        '    int indexed = 0, base_vertex = 0;',
        upload, '}',
        '#define obj obj_lookup_for_lamps\nstatic void bind_lamp_stream(Obj *vs) {\n' + bind + '}\n#undef obj',
    ))


def main():
    fragments_before = (OLD_RELEASE, OLD_COPY, OLD_GLASS_START, OLD_GLASS_GET, OLD_GLASS_PUT, OLD_LAMP)
    assert hashlib.sha256('\n'.join(fragments_before).encode()).hexdigest() == OLD_SHA
    with tempfile.TemporaryDirectory(prefix='render-audit-') as tmp:
        tmp = Path(tmp)
        for old in (True, False):
            label = 'old' if old else 'fixed'
            (tmp / 'render_audit_fragments.inc').write_text(fragments(old))
            exe = tmp / label
            built = subprocess.run([
                'clang', '-fobjc-arc', '-O1', '-I', str(tmp), '-I', str(RT), '-I', str(RT.parent / 'recomp'),
                str(RT / 'render_audit_test.m'), str(RT / 'render_thread.m'),
                '-framework', 'Foundation', '-framework', 'Metal', '-o', str(exe),
            ], capture_output=True, text=True)
            assert built.returncode == 0, built.stderr[-5000:]
            for threaded in ('0', '1'):
                result = subprocess.run([str(exe)], env=dict(os.environ, NFS_RENDER_THREAD=threaded),
                                        capture_output=True, text=True, timeout=30)
                print(f'{label} recorder={threaded}: {result.stdout.strip()}', flush=True)
                if result.returncode == 3:
                    return 3
                assert result.returncode == int(old), result.stderr
                expected = ('mad=80.00 padding=24 query_objects=2048 query_active=1 encoder_active=1 glass_live=32 lamp_off=2'
                            if old else
                            'mad=0.00 padding=0 query_objects=1024 query_active=0 encoder_active=0 glass_live=0 lamp_off=0')
                assert result.stdout.strip() == expected, (result.stdout, result.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
