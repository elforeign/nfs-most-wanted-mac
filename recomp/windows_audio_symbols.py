"""Resolve SDK C++ runtime names against the exact CRT archives being linked.

The SDK-free audio surface has no Microsoft STL ABI and uses the static symbol
rows. Native builds instead prove any additional C++ names against definitions
in the selected SDK/toolchain libraries. Missing host names remain errors.
"""
import os
from pathlib import Path
import subprocess

LIBRARIES = ('libcpmt.lib', 'libvcruntime.lib', 'libcmt.lib', 'libucrt.lib', 'oldnames.lib')


def native_cpp_symbols(nm, needed, directories=None):
    directories = directories if directories is not None else os.environ.get('LIB', '').split(';')
    remaining = set(needed)
    owners = {}
    for library in LIBRARIES:
        path = next((Path(d)/library for d in directories if d and (Path(d)/library).is_file()), None)
        if path is None:
            continue
        result = subprocess.run([nm, '-P', '--extern-only', '--defined-only', str(path)],
                                capture_output=True, text=True, check=True)
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in {'T', 'D', 'B', 'R', 'S', 'C', 'A', 'W', 'I'}:
                if parts[0] in remaining:
                    owners[parts[0]] = library
                    remaining.remove(parts[0])
        if not remaining:
            break
    return owners
