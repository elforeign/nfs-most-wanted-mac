"""Both retail audio sources through the same Undercover package path.

Red first: original rejects 64/64 PC TMXs and 19/19 PC banks; the PC-folder
picker regression in test_undercover_disc_import fails for all 14 cars.
Fixed: 83/83 files/source; 146/146 packaged player-track PCM hashes match;
32/32 native player topologies pass and 32/32 wrong-input controls fail.
PC secondary: 19 banks, 336 live / 144 null slots, 5,002,331 frames.
--setup-import checks actual conversion/publication for both sources in the
exported Setup tree: 14/14 cars, 51 reachable player resources each.
Missing installed PC sounds, attributes or console image exits 3.
"""
from array import array
import collections
import ctypes
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'tools'), str(Path(__file__).resolve().parent)]
from setup_kit import car_audio_undercover as uc, car_audio_codec as codec, car_audio_pack
from setup_kit.input_paths import input_path
from test_car_audio_undercover import closure


def pc_root():
    return Path(os.environ.get('NFS_UC_PC_AUDIO', input_path('Need for Speed Undercover')))


def resources():
    attr = input_path('Need for Speed Undercover/GLOBAL/ATTRIBUTES.BIN')
    override = os.environ.get('NFS_UC_X360_ISO')
    images = [Path(override)] if override else sorted((input_path().parent / 'ext_untrusted/undercover_x360').glob('*.iso'))
    root = pc_root()
    if not root.is_dir() or not attr.is_file() or len(images) != 1 or not images[0].is_file():
        return None
    paths = closure(attr.read_bytes())
    files = {str(p.relative_to(root)).upper(): p for p in root.rglob('*') if p.is_file()}
    if not set(paths) <= files.keys():
        return None
    with uc.Disc(images[0]) as disc:
        return attr.read_bytes(), {p: files[p].read_bytes() for p in paths}, {p: disc.read(p) for p in paths}


def gin_pcm(data):
    """Decode the original 19-byte/32-frame grains to signed LE PCM16."""
    f, c, frames = struct.unpack_from('<3I', data, 16)
    start = 40 + 4 * (f + c)
    coefficients = ((0, 0), (240, 0), (460, -208), (392, -220))
    pcm = array('h')
    for at in range(start, len(data), 19):
        first, second = struct.unpack_from('<hh', data, at)
        a, b = coefficients[first & 3]
        shift = 20 - (second & 15)
        previous, current = first & ~15, second & ~15
        pcm.extend((previous, current))
        for i in range(30):
            byte = data[at + 4 + i // 2]
            nibble = byte >> 4 if i % 2 == 0 else byte & 15
            value = ((nibble if nibble < 8 else nibble - 16) * (1 << shift) + current*a + previous*b) >> 8
            value = min(32767, max(-32768, value))
            pcm.append(value)
            previous, current = current, value
    if sys.byteorder != 'little':
        pcm.byteswap()
    return pcm.tobytes()[:2*frames]


def packaged_resources(attributes, source):
    """Read the writer's actual source/PCM payload, independent of its reader."""
    result = {}
    for car in sorted(uc.CARS):
        names = sorted(uc.describe(attributes, car)['resources'])
        package = car_audio_pack.build(attributes, car, {p: source[p] for p in names}, game='undercover')
        assert package[:4] == b'A7CA' and struct.unpack_from('<I', package, 4)[0] == 7
        count, tiers = struct.unpack_from('<2I', package, 12)
        assert count == len(names)
        at = 68 + 32 + 40*tiers
        for name in names:
            kind, size, plan, samples = struct.unpack_from('<4I', package, at)
            at += 16
            digest = package[at:at+32]; at += 32
            raw = package[at:at+size]; at += size+plan
            assert raw == source[name] and hashlib.sha256(raw).digest() == digest
            pcm = []
            for _ in range(samples):
                frames, rate, loop, n = struct.unpack_from('<4I', package, at); at += 16
                pcm.append(package[at:at+n]); at += n
            result[name] = (raw, pcm)
        assert at == len(package)
    assert len(result) == 51
    return result


def native_topology(pc):
    with tempfile.TemporaryDirectory(prefix='uc-pc-tmx-') as work:
        library = Path(work) / 'tmx.dylib'
        result = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-dynamiclib',
            str(ROOT / 'runtime/car_audio_tmx.c'), '-o', str(library)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-2000:]
        lib = ctypes.CDLL(str(library)); parse = lib.car_audio_tmx_parse
        parse.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        count = 0
        for name, raw in pc.items():
            if not name.endswith('-PLAYER.TMX'):
                continue
            assert parse(ctypes.create_string_buffer(4096), raw, len(raw)) == 0, name
            changed = bytearray(raw); changed[48] = 2
            assert parse(ctypes.create_string_buffer(4096), bytes(changed), len(changed)) != 0
            count += 1
        assert count == 32
        print('native PC player topology: 32/32 accepted; 32/32 wrong-input controls refused')


def proof():
    source = resources()
    if source is None:
        print('SKIP PC audio: installed sounds, attributes or console image absent')
        return False
    attributes, pc, console = source
    rejected = collections.Counter()
    for path, raw in pc.items():
        try:
            uc.tmx(raw) if path.endswith('.TMX') else uc.bank(raw)
        except ValueError:
            rejected[Path(path).suffix] += 1
    print('PC source parser refusals:', dict(rejected), flush=True)
    assert not rejected, rejected
    native_topology(pc)
    packaged = [packaged_resources(attributes, source) for source in (pc, console)]
    matched = 0
    for path in pc:
        if not path.endswith('-PLAYER.TMX'):
            continue
        left, right = (uc.tmx(source[path][0]) for source in packaged)
        assert left['controls'] == right['controls'] and left['bins'] == right['bins'] and left['rpm'] == right['rpm']
        assert len(left['tracks']) == len(right['tracks'])
        pcm_indices = [0, 0]
        for a, b in zip(left['tracks'], right['tracks']):
            assert a['kind'] == b['kind']
            decode = lambda t: gin_pcm(t['data']) if t['kind'] == 'gin' else codec.decode_sound(t['sound']).pcm
            assert hashlib.sha256(decode(a)).digest() == hashlib.sha256(decode(b)).digest(), path
            if a['kind'] == 'snr':
                for i, track in enumerate((a, b)):
                    assert packaged[i][path][1][pcm_indices[i]] == decode(track)
                    pcm_indices[i] += 1
            matched += 1
    assert matched == 146, matched
    banks = frames = live = empty = 0
    gear_rates = collections.Counter()
    for path, raw in pc.items():
        if path.endswith('.ABK'):
            banks += 1
            for sound, other in zip(uc.bank(raw), uc.bank(console[path])):
                if sound:
                    assert sound.codec == 4 and other.codec == 3
                    assert len(codec.decode_sound(sound).pcm) == sound.frames * 2
                    frames += sound.frames
                    live += 1
                    if '/SHIFTING/' in path:
                        gear_rates[sound.rate, other.rate] += 1
                else:
                    assert other is None
                    empty += 1
    assert (banks, live, empty, frames) == (19, 336, 144, 5002331)
    assert gear_rates == {(32000, 48000): 9, (44100, 44100): 18}, gear_rates
    print(f'PC/X360: 83/83 source files each; {matched}/146 player PCM hashes; PC {banks}/19 secondary banks, {frames} frames')
    return True


def setup_proof():
    """Real installed-car conversion and publication in the exported Setup tree."""
    from setup_kit import car_catalog, car_import, car_selection
    original = input_path('Need for Speed Undercover')
    mw = Path(os.environ.get('NFS_GAME', ROOT.parent / 'Need For Speed Most Wanted Black Edition')).resolve()
    if not (original / 'CARS').is_dir() or not (mw / 'CARS').is_dir():
        print('SKIP installed-car publication: source cars or MW donors absent')
        return False
    override = os.environ.get('NFS_UC_X360_ISO')
    images = [Path(override)] if override else sorted((input_path().parent / 'ext_untrusted/undercover_x360').glob('*.iso'))
    if len(images) != 1 or not images[0].is_file():
        return False
    rows = [r for r in car_selection.load() if r['origin']['game'] == 'undercover']
    variants = car_selection.pc_audio_variants(rows)
    for label, sound_source, expected in (('PC', pc_root(), variants), ('X360', images[0], rows)):
        with tempfile.TemporaryDirectory(prefix='uc-setup-source-') as work:
            work = Path(work).resolve()
            engine = car_import.CarImporter(work / 'catalog', work / 'work')
            specs = [car_selection.spec(r) for r in rows]
            plan = engine.plan(original, mw_pc=mw, cars=specs, uc_x360=sound_source, measure=True)
            assert all(car['approved'] for car in plan['cars']), [car.get('reason') for car in plan['cars']]
            for car, wanted in zip(plan['cars'], expected):
                assert car['contract'] == wanted, wanted['origin']
            engine.import_cars(original, mw_pc=mw, cars=specs, uc_x360=sound_source)
            current = car_catalog.verify_current(engine.root)
            assert not current.error and len(current.selected) == 14, current.error
            paths = {name for row in current.selected for name in row['inputs']['source'] if name.startswith('SOUND/')}
            assert len(paths) == 51
            print(f'Setup {label}: 14/14 cars activated; 51/51 reachable player resources snapshotted from the 83-file corpus', flush=True)
    return True


if __name__ == '__main__':
    if not proof():
        raise SystemExit(3)
    if '--setup-import' in sys.argv and not setup_proof():
        raise SystemExit(3)
