#!/usr/bin/env python3
"""Callout meanings against embedded main output; no assets or history needed.
Fixed: 119/119 meanings match (85 Xbox, 17 DualSense, 17 DualShock 4).
Current branch before fix: 98/119 match, 21 D-pad mismatches, exit 1.
The ordinary run also restores the three reviewed wrong meanings: 21 rejected.
"""
from pathlib import Path
import hashlib
import importlib.util
import re

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pad_metadata", ROOT / "tools_snapshot/pad_metadata.py")
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)

# Main's English callout output: 17 Xbox rows (five configurations), then
# the shared PlayStation row. This fragment pins meanings, not just IDs.
MAIN_OUTPUT = r'''Brake / Reverse|Speedbreaker|Brake / Reverse|Brake / Reverse|E-brake
Steer|Steer|Steer|Steer|Steer
Accelerate|Nitrous|Accelerate|Accelerate|Nitrous
Look Back|Look Back|Change Camera|Shift Up|Change Camera
Nitrous|E-brake|E-brake|E-brake|Not Used
E-brake|Accelerate|Not Used|Nitrous|Look Back
Speedbreaker|Brake / Reverse|Look Back|Shift Down|Not Used
Enter Shop /^Engage Event|Enter Shop /^Engage Event|Enter Shop /^Engage Event|Enter Shop /^Engage Event|Enter Shop /^Engage Event
World^Map|World^Map|World^Map|World^Map|World^Map
SMS^System|SMS^System|SMS^System|SMS^System|SMS^System
Blacklist|Blacklist|Blacklist|Blacklist|Blacklist
Look Back|Shift Down|Speedbreaker|Look Back|Shift Down
Change Camera|Shift Up|Nitrous|Speedbreaker|Shift Up
Reset Car|Reset Car|Reset Car|Reset Car|Reset Car
Shift Up|Change Camera|Shift Up|Change Camera|Accelerate
Shift Down|Not Used|Shift Down|Not Used|Brake / Reverse
Pause Game|Pause Game|Pause Game|Pause Game|Pause Game
Nitrous|Steer|E-brake|Look Back|Speedbreaker|Accelerate|Brake / Reverse|Enter Shop /^Engage Event|World^Map|SMS^System|Blacklist|Shift Down|Shift Up|Reset Car|Accelerate|Brake / Reverse|Pause Game
'''
MAIN_SHA256 = "d4b954b2482678a1067fb07114484358307af72aa182b02d984816354618e1ca"
# Port-written synonyms preserve the actions while allowing our own wording.
SYNONYMS = {
    "e-brake": "handbrake", "speedbreaker": "slow time",
    "reset car": "reset vehicle", "change camera": "change view",
    "look back": "look behind", "enter shop / engage event": "enter event",
    "sms system": "messages", "pause game": "pause", "not used": "unassigned",
}


def meaning(text):
    text = " ".join(text.replace("^", " ").casefold().split())
    return SYNONYMS.get(text, text)


def mismatches(labels):
    generated = metadata.callouts_text()
    xbox = generated.split("callout_labels[17 * 5] = {", 1)[1].split("}", 1)[0]
    ids = [int(h, 16) for h in re.findall(r"0x[0-9A-Fa-f]+", xbox)]
    prompts = (ROOT / "runtime/padprompts.c").read_text()
    ps = prompts.split("static const uint32_t ps_labels[17] = {", 1)[1].split("}", 1)[0]
    ps_ids = [int(h, 16) for h in re.findall(r"0x[0-9A-Fa-f]+", ps)]
    expected_rows = MAIN_OUTPUT.splitlines()
    expected = [t for row in expected_rows[:17] for t in row.split("|")]
    expected += expected_rows[17].split("|") * 2
    actual_ids = ids + ps_ids * 2
    assert len(ids) == 85 and len(ps_ids) == 17 and len(expected) == 119
    return [(i, labels[h], expected[i]) for i, h in enumerate(actual_ids)
            if meaning(labels[h]) != meaning(expected[i])]


def main():
    assert hashlib.sha256(MAIN_OUTPUT.encode()).hexdigest() == MAIN_SHA256
    # Read emitted text too: an accidental English string edit must fail even
    # if every action name and every ID remains identical.
    labels = {int(h, 16): text for h, text in re.findall(
        r'\{0x([0-9A-Fa-f]+)u, "([^"]*)"\}', metadata.callouts_text())}
    bad = mismatches(labels)
    print(f"callout meanings: {119 - len(bad)}/119 match; {len(bad)} mismatches")
    if bad:
        print("mismatches:", bad)
        return 1
    labels.update({0xFF0E7232: "Blacklist", 0xB864880D: "World map", 0xDC03C093: "Messages"})
    broken = mismatches(labels)
    assert len(broken) == 21, broken
    print("reviewed wrong meanings: 21/119 mismatches rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
