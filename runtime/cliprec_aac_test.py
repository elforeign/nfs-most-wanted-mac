#!/usr/bin/env python3
"""Host AAC content/priming/padding proof; exit 3 with the codec failure reason.
Scheduling proof: old startup exits 1 after 2.1 s injected delay; fixed exits 0.
Mandatory PCM coverage runs independently in cliprec_audio_test.py.
"""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from cliprec_audio_test import main

if __name__=='__main__':
    raise SystemExit(main('aac'))
