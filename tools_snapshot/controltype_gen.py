#!/usr/bin/env python3
"""Emit the port-authored controller metadata, without reading any game files.

usage: controltype_gen.py --out DIR
Includes: controltype_buttons.inc, callout_labels.inc, padprompts_l1.inc.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pad_metadata import write_tables


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    write_tables(args.out)


if __name__ == "__main__":
    main()
