"""Resolve optional development inputs without accessing the filesystem."""
import os
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]


def input_path(relative=''):
    """Use the shared input root, independent of the working directory and HOME."""
    return Path(os.environ.get('NFS_INPUTS_ROOT') or CHECKOUT.parent / 'inputs') / relative
