"""Independent optional outputs; shared converter inputs remain available until validation.

Plan: select conversion and validation before doing work, bind every reusable
preparation to the selection, and remember choices only after installation.
Older saved choices and callers retain the complete original installation.
"""

ALL = ("arrest", "textures", "ui")
FOLDERS = dict(arrest="nis_arrest", textures="hd_textures_mips_alpha", ui="hd_textures_ui")
# Decimal bytes, rounded up from the converted inventories. Arrest source
# bundles are 145,716,352 bytes; converted meshes also occupy up to 93 MB.
BYTES = dict(arrest=146_000_000 + 93_000_000, textures=253_000_000, ui=39_000_000)


def selection(value=None):
    if value is None:
        return ALL
    names = value.split(",") if isinstance(value, str) and value else value
    if names == "":
        names = []
    if not isinstance(names, (list, tuple)) or any(n not in ALL for n in names) or len(set(names)) != len(names):
        raise ValueError("Xbox 360 extras must be a unique list of arrest,textures,ui (empty selects none).")
    return tuple(n for n in ALL if n in names)


def identity(value):
    chosen = selection(value)
    return {} if chosen == ALL else {"x360_extras": list(chosen)}


def required_bytes(value=None):
    return sum(BYTES[n] for n in selection(value))
