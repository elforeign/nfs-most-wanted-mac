"""Adapt the shared ProStreet attribute converter to private pack paths."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from setup_kit import ps_attrib


def sidecars(source, reference):
    """Sidecar files for one conversion, when the source attributes are available."""
    attributes = source.parents[1] / 'GLOBAL/ATTRIBUTES.BIN'
    if not attributes.is_file():
        return {}
    donor = reference.parents[1] / 'GLOBAL/attributes.bin'
    return ps_attrib.sidecars(attributes.read_bytes(), donor.read_bytes(), source.name, reference.name)
