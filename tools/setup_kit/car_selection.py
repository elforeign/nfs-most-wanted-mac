"""Packaged selection metadata authenticated by the existing car policy.

The reviewed rows contain names, bindings and file fingerprints, never assets.
Keeping the complete metadata lets the existing contract trust root authenticate
every field without changing any converter dependency or approval.
"""
from pathlib import Path

from . import car_catalog as catalog
from . import car_sources

METADATA = Path(__file__).with_name('car_selections.json')
PC_AUDIO = Path(__file__).with_name('car_audio_undercover_pc.json')
SPEC_FIELDS = ('slot', 'display', 'bindings', 'donors', 'parts')


def validate(rows, policy=None):
    policy = policy or catalog.packaged_policy()
    approved = catalog._policy(policy)
    catalog._require(type(rows) is list and bool(rows), 'missing car selections')
    seen, contracts = set(), set()
    for row in rows:
        reservations = catalog.reservations()
        slot = row['slot']
        catalog._integer(slot, 0, catalog.ADDITION_COUNT - 1)
        reservations[slot] = row
        catalog.make_catalog(reservations, policy)
        key = (row['origin']['game'], row['origin']['platform'], slot)
        catalog._require(key not in seen, 'duplicate packaged selection')
        seen.add(key)
        contracts.add((slot, catalog.contract_hash(row)))
    # The same car choice has a second approved sound source, without adding
    # a duplicate car to Setup's list. Everything except sound bytes is shared.
    for row in pc_audio_variants(rows):
        reservations = catalog.reservations()
        reservations[row['slot']] = row
        catalog.make_catalog(reservations, policy)
        contracts.add((row['slot'], catalog.contract_hash(row)))
    expected = {(r['slot'], v['contract']) for r in approved['slots'] for v in r['variants']
                if 'legacy' not in v}
    catalog._require(contracts == expected, 'packaged selections do not cover the approved contracts')
    return sorted(rows, key=lambda r: (r['origin']['game'], r['origin']['platform'], r['slot']))


def pc_audio_variants(rows):
    """Reconstruct PC-sound contracts from their independently pinned assets."""
    import copy
    data = catalog._decode(PC_AUDIO.read_bytes())
    result = []
    for row in rows:
        if row['origin']['game'] != 'undercover' or 'AUDIO.BIN' not in row['outputs']:
            continue
        variant = copy.deepcopy(row)
        for name in variant['inputs']['source']:
            if name.startswith('SOUND/'):
                variant['inputs']['source'][name] = data['resources'][name]
        variant['outputs']['AUDIO.BIN'] = data['packages'][row['origin']['folder']]
        result.append(variant)
    return result


def load(path=METADATA, *, policy=None):
    data = Path(path).read_bytes()
    rows = validate(catalog._decode(data), policy)
    catalog._require(data == catalog.encoded(rows), 'noncanonical car selections')
    return rows


def spec(row):
    return dict({k: row[k] for k in SPEC_FIELDS}, folder=row['origin']['folder'])


def available_specs(specs, recognition):
    """Skip absent optional cars; changed files still fail the importer pins."""
    statuses = {r['car_hash']: r['status'] for r in recognition['cars']}
    available, skipped = [], []
    for row in specs:
        status = statuses.get(car_sources.path_hash('CARS/' + row['folder']), 'missing')
        if status == 'missing':
            game = car_sources.GAME_NAMES[recognition['game']]
            skipped.append(row['display']['name'] + f': missing from this {game} copy; skipped.')
        else:
            available.append(row)
    return available, skipped


def generate(rows, path=METADATA):
    """Reproduce the payload from measured rows; refuse any unapproved input."""
    data = catalog.encoded(validate(rows))
    Path(path).write_bytes(data)
    return data
