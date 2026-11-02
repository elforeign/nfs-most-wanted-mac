"""Port-authored controller metadata. No asset readers or extracted records.

The recipes describe controls by action and the texture by named format fields;
the host-independent generator emits the small C includes from these recipes.
"""
import struct

HEADER = "// Generated from port-authored tools_snapshot/pad_metadata.py. Do not edit.\n"

# These language IDs must agree with the guest's Controls actions and padbind's
# Default/Drag label interface. They are lookup identifiers, not language data.
LABELS = {
    "gas": (0x619B23B0, "Accelerate"),
    "brake": (0x6AB80AFA, "Brake / reverse"),
    "handbrake": (0xB70FF4A4, "Handbrake"),
    "nitrous": (0x5E5C5F2E, "Nitrous"),
    "slow_time": (0x018AE810, "Slow time"),
    "shift_down": (0x56AFE190, "Shift down"),
    "shift_up": (0xDC1CD97D, "Shift up"),
    "reset": (0x1CEDC132, "Reset vehicle"),
    "camera": (0xD38DF194, "Change view"),
    "look_back": (0x13121940, "Look behind"),
    "steer": (0x63AA639D, "Steer"),
    "event": (0xAB1032FA, "Enter event"),
    "map": (0xFF0E7232, "World map"),
    "messages": (0xB864880D, "Messages"),
    "blacklist": (0xDC03C093, "Blacklist"),
    "pause": (0x6369F4B8, "Pause"),
    # The diagram's unassigned-action ID stays addressable by its FNG labels.
    "unassigned": (0xF8B4303B, "Unassigned"),
}

# CT_* are the guest GameAction indices, shared with the Controls/Drag pages.
# Scalar names are the existing ct_sources interface: L/R triggers, Black/White
# bumpers, ARY_U/D right-stick halves. Keeping these names preserves bindings.
ACTIONS = (
    ("CT_GAS", "gas"), ("CT_BRAKE", "brake"),
    ("CT_HANDBRAKE", "handbrake"), ("CT_NOS", "nitrous"),
    ("CT_GAMEBREAKER", "slow_time"), ("CT_SHIFTUP", "shift_up"),
    ("CT_SHIFTDOWN", "shift_down"), ("CT_CAMERA", "camera"),
    ("CT_LOOKBACK", "look_back"), ("CT_RESET", "reset"),
)


def preset(*, gas, brake, handbrake, nitrous, slow_time, shift_up, shift_down, camera, look_back):
    return dict(gas=gas, brake=brake, handbrake=handbrake, nitrous=nitrous,
                slow_time=slow_time, shift_up=shift_up, shift_down=shift_down,
                camera=camera, look_back=look_back, reset="Back")


# Our defaults express the existing button behaviour. Type 1 keeps live player
# bindings; the first recipe only supplies that layout's diagram. Types 6-10
# reuse the five variants with D-pad steering in controltype.c.
PRESETS = (
    preset(gas="R", brake="L", handbrake="A", nitrous="B", slow_time="X",
           shift_up="ARY_U", shift_down="ARY_D", camera="White", look_back="Y"),
    preset(gas="A", brake="X", handbrake="B", nitrous="R", slow_time="L",
           shift_up="White", shift_down="Black", camera="RThumb", look_back="Y"),
    preset(gas="R", brake="L", handbrake="B", nitrous="White", slow_time="Black",
           shift_up="ARY_U", shift_down="ARY_D", camera="Y", look_back="X"),
    preset(gas="R", brake="L", handbrake="B", nitrous="A", slow_time="White",
           shift_up="Y", shift_down="X", camera="RThumb", look_back="Black"),
    preset(gas="ARY_U", brake="ARY_D", handbrake="L", nitrous="R", slow_time="RThumb",
           shift_up="White", shift_down="Black", camera="Y", look_back="A"),
)


def button_rows():
    # Preserve the existing right-stick pedal take-up: the usable range starts
    # at 46% travel. Binary buttons and trigger pedals start at zero.
    return [[(ct, p[action], 0.46 if action in ("gas", "brake") and p[action].startswith("ARY_") else 0)
             for ct, action in ACTIONS] for p in PRESETS[1:]]


def buttons_text():
    return HEADER + "".join("    {" + ", ".join('{%s, "%s", %s}' %
                            (a, s, "0.46f" if low else "0") for a, s, low in row) + "},\n"
                            for row in button_rows())


# Callout slot numbers belong to Loading_Controller.fng: the diagram's 17
# CButton_k / BUTTON_k object names are an interoperability interface.
SLOTS = ("L", "steer", "R", "Y", "B", "A", "X", "event", "map", "messages", "blacklist",
         "Black", "White", "Back", "ARY_U", "ARY_D", "pause")


def callout_ids():
    rows = []
    for slot in SLOTS:
        for p in PRESETS:
            if slot in LABELS:
                action = slot
            elif slot == "Black" and p is PRESETS[0]:
                action = "look_back"  # legacy alternate; the runtime hides this unbound bumper
            else:
                by_control = {control: action for action, control in p.items()}
                action = by_control.get(slot)
                if action is None and slot == "ARY_U":
                    action = by_control.get("RThumb")  # upper stick callout also describes its press
            rows.append(LABELS[action or "unassigned"][0])
    return rows


def callouts_text():
    ids = callout_ids()
    text = HEADER + "static const uint32_t callout_labels[17 * 5] = {\n"
    text += "".join("    " + ", ".join("0x%08Xu" % h for h in ids[i:i + 5]) + ",\n"
                    for i in range(0, len(ids), 5)) + "};\n"
    # Text is ours; the IDs still work for both dynamic bindings and FNG labels.
    text += "static const struct { uint32_t id; const char *text; } pad_action_labels[] = {\n"
    text += "".join('    {0x%08Xu, "%s"},\n' % entry for entry in LABELS.values()) + "};\n"
    return text


# TextureInfo's 124-byte little-endian ABI, decoded from its constructor and
# renderer. Zero means no pack, animation, scroll, tiling, palette or pointers.
# The renderer requires class 001A93CF, format enum 0x20 (32-bit pixels), render
# order 5 (front end), alpha usage 2 (blend), blend enum 1 (source alpha), and
# fixed-point unity 0x100 for both UV scales. These are format/enum contracts.
TEXTURE_FIELDS = (
    ("class", 0x28, "I", 0x001A93CF),
    ("width", 0x44, "H", 64), ("height", 0x46, "H", 64),
    ("log_width", 0x48, "B", 6), ("log_height", 0x49, "B", 6),
    ("compression", 0x4A, "B", 0x20), ("mips", 0x4E, "B", 1),
    ("render_order", 0x51, "B", 5),
    ("alpha_usage", 0x55, "B", 2), ("alpha_blend", 0x56, "B", 1),
    ("mip_bias", 0x58, "h", 0),  # neutral for our single-level art; no asset mip-selection bias
    ("u_scale", 0x62, "H", 0x100), ("v_scale", 0x64, "H", 0x100),
)


def texture_record():
    rec = bytearray(124)
    for _, off, fmt, value in TEXTURE_FIELDS:
        struct.pack_into("<" + fmt, rec, off, value)
    return rec


def texture_text():
    # Named, designated fields keep the source reviewable; no opaque byte blob.
    lines = []
    for name, off, fmt, value in TEXTURE_FIELDS:
        for i, b in enumerate(struct.pack("<" + fmt, value)):
            if b:
                lines.append("    [0x%02X] = 0x%02X, // %s\n" % (off + i, b, name))
    return HEADER + "static const uint8_t l1[124] = {\n" + "".join(lines) + "};\n"


def write_tables(out):
    for name, text in (("padprompts_l1.inc", texture_text()),
                       ("controltype_buttons.inc", buttons_text()),
                       ("callout_labels.inc", callouts_text())):
        (out / name).write_text(text)
