#!/usr/bin/env python3
"""PC bank decoding and native secondary playback on both engine lifecycles.

Shares the independently read source selector/PCM oracle and native engine
harness with the console regression. Original PC parser: 0/64 TMXs, 0/19 banks;
PC picker: 0/14 cars. Missing private assets exits 3.
Fixed: 14/14 cars, 106 tier attachments, 212 layers, 336 live / 144 null
slots, 5,002,331 PCM frames with zero error; half-volume control exits -6.
"""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from setup_kit import car_audio_undercover, car_audio_x360, car_audio_bank

spec = importlib.util.spec_from_file_location('uc_secondary_pc', ROOT / 'runtime/car_audio_undercover_secondary_test.py')
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
if __name__ == '__main__':
    mutant = sys.argv[sys.argv.index('--mutant')+1] if '--mutant' in sys.argv else None
    if not proof.private(pc=True, mutant=mutant):
        raise SystemExit(3)
