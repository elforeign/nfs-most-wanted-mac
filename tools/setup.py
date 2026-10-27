#!/usr/bin/env python3
"""Public Setup CLI; -I deliberately ignores external Python import paths."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup_kit.backend import main

if __name__ == "__main__":
    sys.exit(main())
