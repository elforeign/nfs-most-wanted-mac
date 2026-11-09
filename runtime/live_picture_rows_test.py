"""The F10 rows that change mid-race (live_picture.c): no restart marking, a live_env export and the right handler.

Reads the rows from settings_port.c / pub_settings.c, one decoded line each: Car reflections
queue the device Reset (live_picture_apply_reset), Contact shadows and Soft smoke reload the cached switches
(live_picture_apply_reload), Full screen mode re-enters the window mode (live_picture_apply_fullscreen); none may
say Restart. HDR output and Fog stay restart rows (shader key). Fails on the pre-change files: 5 rows still marked
restart. Broken control: the same check against the rows with `.restart = 1` put back must reject all 5.
"""
import re
from pathlib import Path

RT = Path(__file__).resolve().parent
ROWS = {          # id -> (source, handler)
    'graphics.look_reflections': ('settings_port.c', 'live_picture_apply_reset'),
    'graphics.car_reflection': ('pub_settings.c', 'live_picture_apply_reset'),
    'graphics.look_contact_shadows': ('settings_port.c', 'live_picture_apply_reload'),
    'graphics.look_soft_smoke': ('settings_port.c', 'live_picture_apply_reload'),
    'display.fullscreen': ('settings_port.c', 'live_picture_apply_fullscreen'),
}
STILL_RESTART = ('graphics.hdr_output', 'graphics.look_fog')


def row(text, rid):
    m = re.search(r'static Setting \w+ = \{\.id = "%s".*?\};' % re.escape(rid), text, re.S)
    assert m, rid
    return m[0]


def problems(sources):
    bad = []
    for rid, (src, handler) in ROWS.items():
        r = row(sources[src], rid)
        if '.restart' in r: bad.append(f'{rid}: still marked restart')
        if '.live_env = 1' not in r: bad.append(f'{rid}: no live_env')
        if f'.apply = {handler}' not in r: bad.append(f'{rid}: handler is not {handler}')
        if re.search(r'Restart game to apply|after Restart game|Takes effect after', r): bad.append(f'{rid}: help still says restart')
    for rid in STILL_RESTART:
        if '.restart = 1' not in row(sources['settings_port.c'], rid): bad.append(f'{rid}: lost its restart marking')
    return bad


def main():
    sources = {n: (RT / n).read_text() for n in ('settings_port.c', 'pub_settings.c')}
    bad = problems(sources)
    assert not bad, bad
    broken = dict(sources)
    for rid, (src, _) in ROWS.items():
        r = row(broken[src], rid)
        broken[src] = broken[src].replace(r, r.replace('.live_env = 1', '.restart = 1'))
    caught = problems(broken)
    assert len([b for b in caught if 'still marked restart' in b]) == len(ROWS), caught
    print(f'live_picture_rows: {len(ROWS)} rows live, {len(STILL_RESTART)} still restart; broken control caught {len(caught)} problems PASS')


if __name__ == '__main__':
    main()
