"""Packaged selection metadata authenticated by the existing car policy.

The reviewed rows contain names, bindings and file fingerprints, never assets.
Keeping the complete metadata lets the existing contract trust root authenticate
every field without changing any converter dependency or approval.
"""
from pathlib import Path

from . import car_catalog as catalog
from . import car_sources

METADATA = Path(__file__).with_name('car_selections.json')
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
        key = (row['origin']['platform'], slot)
        catalog._require(key not in seen, 'duplicate packaged selection')
        seen.add(key)
        contracts.add((slot, catalog.contract_hash(row)))
    expected = {(r['slot'], v['contract']) for r in approved['slots'] for v in r['variants']}
    catalog._require(contracts == expected, 'packaged selections do not cover the approved contracts')
    return sorted(rows, key=lambda r: (r['origin']['platform'], r['slot']))


def load(path=METADATA):
    data = Path(path).read_bytes()
    rows = validate(catalog._decode(data))
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
            skipped.append(row['display']['name'] + ': missing from this Carbon copy; skipped.')
        else:
            available.append(row)
    return available, skipped


def generate(rows, path=METADATA):
    """Reproduce the payload from measured rows; refuse any unapproved input."""
    data = catalog.encoded(validate(rows))
    Path(path).write_bytes(data)
    return data
