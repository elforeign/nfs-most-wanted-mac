"""Load a game directory: games/<id>/game.toml plus its curated globals file.

This module is the only place that knows the schema. Python 3.9 has no
tomllib, so the pinned tomli is the fallback."""

from pathlib import Path
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

REQUIRED_GAME_KEYS = ("id", "name", "app_name", "bundle_id", "executable", "sha256",
                      "image_base", "entry_point", "guest_root", "developer_exe")

HEAP_BASE_DEFAULT = 0x01000000

# The settings page's rows, in mods/display_settings.h DisplayRow order; the
# controls' seven rows are one entry. [settings] rows names the ones a game
# shows. Without the key a game shows every row. "keypad" is the pre-touch-
# controls spelling of "controls"; SETTINGS_ROW_ALIASES keeps it loading.
SETTINGS_ROWS = ("rendering", "ui_scale", "wide_view", "window", "resolution", "frame_limit",
                 "performance_overlay", "textures", "filtering", "controls")
SETTINGS_ROW_ALIASES = {"keypad": "controls"}

# [controls] default_layout and the on-screen layout picker's choices.
CONTROLS_LAYOUTS = ("pad", "keys", "pad+keys", "hidden")
# [controls.mapped] left_stick/right_stick/dpad modes.
STICK_MODES = ("cursor", "arrows", "horizontal_arrows", "wasd", "scroll", "wheel", "none")
# [controls.native] buttons: the pad button names a physical button maps to.
PAD_BUTTONS = ("cross", "circle", "square", "triangle", "l1", "r1", "l2", "r2", "l3", "r3",
               "select", "start", "ps")
MAPPED_DEFAULTS = {"left_stick": "arrows", "right_stick": "cursor", "dpad": "arrows",
                   "cursor_speed": 900, "cross": "mouse_left", "circle": "mouse_right",
                   "square": "key:Space", "triangle": "key:Tab", "l1": "key:PageUp",
                   "r1": "key:PageDown", "l2": "mouse_middle", "r2": "key:LShift",
                   "start": "key:Escape", "select": "key:F10", "l3": "none", "r3": "none",
                   "ps": "action:settings"}
# [controls.native] axes: the six DirectInput/XInput axis names a physical
# axis maps to.
NATIVE_AXES = ("x", "y", "z", "rx", "ry", "rz")
# Mirrors the name column of host/controls/layout.cpp's kScancodes table (its
# scancode_from_name); keep both lists in sync.
KEY_NAMES = (
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S",
    "T", "U", "V", "W", "X", "Y", "Z", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "Return",
    "Escape", "Backspace", "Tab", "Space", "Minus", "Equals", "LeftBracket", "RightBracket",
    "Backslash", "Semicolon", "Apostrophe", "Grave", "Comma", "Period", "Slash", "F1", "F2", "F3",
    "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12", "Insert", "Home", "PageUp", "Delete",
    "End", "PageDown", "Right", "Left", "Down", "Up", "LCtrl", "LShift", "LAlt",
)
# key:<name>, mouse_left/right/middle, wheel_up/down, action:<name> or none.
BUTTON_TARGET_RE = re.compile(
    r"^(key:[A-Za-z0-9]+|mouse_(left|right|middle)|wheel_(up|down)|"
    r"action:(settings|system_keyboard|edit_layout)|none)$")
HEAP_END = 0x0e000000        # runtime/x86.h GUEST_HEAP_END; the mods' heap starts there
GUEST_SIZE_DEFAULT = 0x10000000   # runtime/x86.h GUEST_SIZE: the arena, 256 MB unless a module needs more
AUX_REQUIRED_KEYS = ("name", "path", "sha256", "base", "size")


def windows_version(value):
    """Decode major.minor[.build]; keep the historical 9x default and 6.1 SP1."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", value):
        raise ValueError("[game] windows_version must be major.minor[.build]")
    parts = [int(part) for part in value.split(".")]
    major, minor = parts[:2]
    build = parts[2] if len(parts) == 3 else {(4, 10): 2222, (6, 1): 7601}.get((major, minor), 0)
    if major > 255 or minor > 255 or build > 32767:
        raise ValueError("[game] windows_version requires byte-sized major/minor and a 15-bit build")
    return major, minor, build, 2 if major >= 5 else 1


#: [game] cpu_features: CPUID leaf 1 EDX bits a game may report beyond the kit's FPU | TSC | CMOV. NFS Underground's
#: EA movie YUV->RGB converter has only an MMX path and writes nothing when CPUID says no MMX (run 2).
CPU_FEATURES = {"mmx": 1 << 23}


def cpu_features_edx(value):
    """The EDX bits for [game] cpu_features (a list of names from CPU_FEATURES; default none)."""
    if not isinstance(value, list) or not all(isinstance(v, str) and v in CPU_FEATURES for v in value):
        raise ValueError("[game] cpu_features must be a list of: %s" % ", ".join(sorted(CPU_FEATURES)))
    edx = 0
    for v in value:
        edx |= CPU_FEATURES[v]
    return edx


#: [game] cpu_brand_index: CPUID leaf 1 EBX bits 0-7 (Intel's brand index; default 0 = none, as on the Pentium II the
#: kit reports). NFS Underground's auto-detect rates the CPU from it: index 0 scores 0 and forces the lowest video
#: preset whatever the GPU (run 7).


#: [game] data_dir_name: the per-user folder the profile lives in when RECOMP_PROFILE_DIR is unset
#: (~/Library/Application Support/<name> on macOS); default app_name. A player-facing name may hold spaces
#: (NFS Underground run 8: "NFS Underground Native").


def data_dir_name(value):
    """[game] data_dir_name: one folder name (no separators, no control characters, not . or ..)."""
    if (not isinstance(value, str) or not value or value != value.strip() or value in (".", "..")
            or any(c in value for c in '/\\:') or any(ord(c) < 32 for c in value)):
        raise ValueError("[game] data_dir_name must be a single folder name")
    return value


def cpu_brand_index(value):
    """[game] cpu_brand_index: an int 0-255."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xff:
        raise ValueError("[game] cpu_brand_index must be an integer 0-255")
    return value


#: [game] adapter: the display adapter D3D9's GetAdapterIdentifier names. Games of this era pick their detail level by
#: vendor and device id. The default is a GeForce 7800 GTX (what the renderer covers; Most Wanted offers shadows only
#: on NVIDIA and ATI). NFS Underground scores only the NVIDIA ids it knew: an unknown id scores -1 and its auto-detect
#: picks the lowest video preset (no headlights, no reflections; run 7), so it names a card its table rates.
# modes: "desktop" = one 1280x960 mode, whatever is asked (NFSMW's released behaviour); "list" = the kit's 4:3, 5:4,
# 16:9 and 16:10 sizes, for a game that builds its menu from them or picks a mode only when it is listed (NFSU1/NFSU2).
ADAPTER_DEFAULT = {"vendor": 0x10de, "device": 0x0091, "name": "NVIDIA GeForce 7800 GTX", "modes": "desktop"}


def adapter(value):
    """[game] adapter {vendor, device, name, modes}: missing fields take ADAPTER_DEFAULT; ids are 16-bit, the name
    ASCII, modes "desktop" or "list"."""
    if not isinstance(value, dict) or set(value) - set(ADAPTER_DEFAULT):
        raise ValueError("[game] adapter must be a table of: vendor, device, name, modes")
    out = dict(ADAPTER_DEFAULT, **value)
    for key in ("vendor", "device"):
        if not isinstance(out[key], int) or isinstance(out[key], bool) or not 0 < out[key] <= 0xffff:
            raise ValueError("[game] adapter %s must be a 16-bit id" % key)
    name = out["name"]
    if not isinstance(name, str) or not name or len(name) > 511 or not all(32 <= ord(ch) < 127 for ch in name):
        raise ValueError("[game] adapter name must be 1-511 printable ASCII characters")
    if out["modes"] not in ("desktop", "list"):
        raise ValueError('[game] adapter modes must be "desktop" or "list"')
    return out


def validate_heap_base(value):
    """The heap arena start: page aligned, above the image base, below the arena end."""
    if value % 0x1000 or not (0x00400000 < value < HEAP_END):
        raise ValueError("[game] heap_base %#x must be page aligned and between 0x00400000 and %#x" % (value, HEAP_END))
    return value


def load_controls(controls, touch, source):
    """Validate [controls], merging [controls.mapped]/[controls.native] over their
    defaults. `touch` is the already-defaulted [touch] table: its `keypad` knob
    only sets `default_layout` when the game has not named one itself."""
    controls = dict(controls)
    if "default_layout" not in controls:
        controls["default_layout"] = {"auto": "keys", "hidden": "hidden"}[touch["keypad"]]
    if controls["default_layout"] not in CONTROLS_LAYOUTS:
        raise ValueError("%s: [controls] default_layout must be one of %s, not %r"
                         % (source, ", ".join(CONTROLS_LAYOUTS), controls["default_layout"]))
    pad = controls.setdefault("pad", "mapped")
    if pad not in ("native", "mapped", "off"):
        raise ValueError('%s: [controls] pad must be "native", "mapped" or "off", not %r' % (source, pad))

    mapped = dict(MAPPED_DEFAULTS)
    mapped.update(controls.get("mapped", {}))
    unknown = sorted(k for k in mapped if k not in MAPPED_DEFAULTS)
    if unknown:
        raise ValueError("%s: [controls.mapped] may name only %s, not %s"
                         % (source, ", ".join(sorted(MAPPED_DEFAULTS)), ", ".join(unknown)))
    for key, value in mapped.items():
        if key == "cursor_speed":
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError("%s: [controls.mapped] cursor_speed must be a positive integer, not %r"
                                 % (source, value))
        elif key in ("left_stick", "right_stick"):
            if value not in STICK_MODES:
                raise ValueError("%s: [controls.mapped] %s must be one of %s, not %r"
                                 % (source, key, ", ".join(STICK_MODES), value))
        elif key == "dpad":
            if value not in ("arrows", "wasd", "none"):
                raise ValueError('%s: [controls.mapped] dpad must be "arrows", "wasd" or "none", not %r'
                                 % (source, value))
        elif not isinstance(value, str) or not BUTTON_TARGET_RE.match(value):
            raise ValueError("%s: [controls.mapped] %s is not a valid target: %r" % (source, key, value))
        elif value.startswith("key:") and value[len("key:"):] not in KEY_NAMES:
            raise ValueError("%s: [controls.mapped] %s names no key: %r" % (source, key, value))
    controls["mapped"] = mapped

    native = dict(controls.get("native", {}))
    unknown_native = sorted(k for k in native if k not in ("xinput", "dinput", "axes", "buttons"))
    if unknown_native:
        raise ValueError("%s: [controls.native] may name only xinput, dinput, axes, buttons, not %s"
                         % (source, ", ".join(unknown_native)))
    native.setdefault("xinput", True)
    native.setdefault("dinput", True)
    if not isinstance(native["xinput"], bool) or not isinstance(native["dinput"], bool):
        raise ValueError("%s: [controls.native] xinput and dinput must be booleans" % source)
    axes = native.setdefault("axes", ["x", "y", "z", "rz", "rx", "ry"])
    if not isinstance(axes, list) or sorted(axes) != sorted(NATIVE_AXES):
        raise ValueError("%s: [controls.native] axes must list all six of %s exactly once, not %r"
                         % (source, ", ".join(NATIVE_AXES), axes))
    buttons = native.setdefault(
        "buttons", ["square", "cross", "circle", "triangle", "l1", "r1", "l2", "r2", "select",
                   "start", "l3", "r3", "ps"])
    if not isinstance(buttons, list) or sorted(buttons) != sorted(PAD_BUTTONS):
        raise ValueError("%s: [controls.native] buttons must list all thirteen of %s exactly once, not %r"
                         % (source, ", ".join(PAD_BUTTONS), buttons))
    controls["native"] = native
    return controls


def load(game_dir):
    """Return the parsed config with `globals` merged in and `dir`/`source` recorded."""
    game_dir = Path(game_dir)
    source = game_dir / "game.toml"
    with source.open("rb") as fh:
        cfg = tomllib.load(fh)
    game = cfg.get("game", {})
    missing = [key for key in REQUIRED_GAME_KEYS if key not in game]
    if missing:
        raise ValueError("%s: missing [game] keys: %s" % (source, ", ".join(missing)))
    game["heap_base"] = validate_heap_base(int(game.get("heap_base", HEAP_BASE_DEFAULT)))
    windows_version(game.setdefault("windows_version", "4.10"))
    cpu_features_edx(game.setdefault("cpu_features", []))
    cpu_brand_index(game.setdefault("cpu_brand_index", 0))
    # [game] qpc_us: QueryPerformanceCounter in real microseconds (NFSU1 run 5: its limiter spins on it, 17 -> 16.67 ms
    # frames). false (default) keeps the 1000-tick millisecond steps NFSMW was released with (D-203).
    if not isinstance(game.setdefault("qpc_us", False), bool):
        raise ValueError("%s: [game] qpc_us must be true or false" % source)
    data_dir_name(game.setdefault("data_dir_name", game["app_name"]))
    # NFSU1 run 8: the guest address of the game's own frame-time cap (a float, seconds per frame, e.g. 1/60). The kit
    # writes 1/limit there when the Frame limit setting is 40/60/120 so the game itself runs at that rate; 0 = none.
    frame_cap = game.setdefault("frame_cap", 0)
    if not isinstance(frame_cap, int) or isinstance(frame_cap, bool) or not 0 <= frame_cap <= 0xFFFFFFFF:
        raise ValueError("%s: [game] frame_cap must be a guest address (int) or 0" % source)
    game["adapter"] = adapter(game.get("adapter", {}))
    translate = cfg.setdefault("translate", {})
    resumable = translate.setdefault("resumable_stacks", False)
    if not isinstance(resumable, bool):
        raise ValueError("%s: [translate] resumable_stacks must be a boolean" % source)
    # NFSMW run 31 (D-177): false compiles the translated stores without the watch / dirty-range check (a game
    # that never opens a DirectDraw dirty range; the kit fails closed if one asks).
    store_hooks = translate.setdefault("store_hooks", True)
    if not isinstance(store_hooks, bool):
        raise ValueError("%s: [translate] store_hooks must be a boolean" % source)
    alignment = translate.setdefault("function_alignment", 16)
    if type(alignment) is not int or alignment <= 0:
        raise ValueError("%s: [translate] function_alignment must be a positive integer" % source)
    tracks = cfg.setdefault("media", {}).setdefault("cd_tracks", [])
    if not isinstance(tracks, list) or not all(isinstance(v, str) for v in tracks):
        raise ValueError("%s: [media] cd_tracks must be a list of strings" % source)
    cfg.setdefault("hooks", {})
    cfg.setdefault("bundle", {}).setdefault("exclude", [])
    touch = cfg.setdefault("touch", {})
    touch.setdefault("keypad", "auto")
    if touch["keypad"] not in ("auto", "hidden"):
        raise ValueError('%s: [touch] keypad must be "auto" or "hidden", not %r' % (source, touch["keypad"]))
    cfg["controls"] = load_controls(cfg.get("controls", {}), touch, source)
    settings = cfg.setdefault("settings", {})
    rows = settings.setdefault("rows", list(SETTINGS_ROWS))
    if not isinstance(rows, list):
        raise ValueError("%s: [settings] rows must be a list, not %r" % (source, rows))
    rows = [SETTINGS_ROW_ALIASES.get(row, row) for row in rows]
    unknown = [row for row in rows if row not in SETTINGS_ROWS]
    if unknown:
        raise ValueError("%s: [settings] rows may name only %s, not %s"
                         % (source, ", ".join(SETTINGS_ROWS), ", ".join(map(repr, unknown))))
    settings["rows"] = rows
    # The World filtering row's value for a profile that never chose one: 3 (8x, the kit's
    # original default) unless the game keeps its own texture filtering (0 = original).
    fd = settings.setdefault("filtering_default", 3)
    if not isinstance(fd, int) or not 0 <= fd <= 4:
        raise ValueError("%s: [settings] filtering_default must be 0-4, not %r" % (source, fd))
    # Its saved key. A game that once stored a value it never used (the row unlisted) names a new
    # key here, so that stale value is not suddenly applied when the row is listed.
    fk = settings.setdefault("filtering_key", "texture_filtering")
    if not isinstance(fk, str) or not fk.replace("_", "").isalnum():
        raise ValueError("%s: [settings] filtering_key must be a plain name, not %r" % (source, fk))
    # The settings page's form: "list", the original single page, or "tabs", the Graphics / Mods /
    # Controls menu with an Apply/Discard draft for renderer changes.
    page = settings.setdefault("page", "list")
    if page not in ("list", "tabs"):
        raise ValueError('%s: [settings] page must be "list" or "tabs", not %r' % (source, page))
    # NFSMW 3.0.4: the game's core mod, whose settings the host reads for its own features (mute in background,
    # deadzones, brightness): "<mod>/<key>". "" = none, the features keep their defaults.
    core_mod = settings.setdefault("core_mod", "")
    if not isinstance(core_mod, str) or (core_mod and not re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)*", core_mod)):
        raise ValueError("%s: [settings] core_mod must be a mod id like core.game, not %r" % (source, core_mod))
    launcher = cfg.setdefault("launcher", {})
    launcher.setdefault("title", game["name"])
    launcher.setdefault("store", "")
    for key in ("install_names", "gog_ids", "steam_ids"):
        value = launcher.setdefault(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError("%s: [launcher] %s must be a list of strings" % (source, key))
    for key in ("title", "store"):
        if not isinstance(launcher[key], str):
            raise ValueError("%s: [launcher] %s must be a string" % (source, key))
    # NFSMW 3.0.4: the GitHub owner/name whose latest release "Check for updates" asks; "" = no such button.
    # NFSMW 3.0.4: the Setup app's build log, relative to the home folder, which Save bug report adds (it holds the
    # compiler and SDK every player's Setup used); "" = none.
    setup_log = launcher.setdefault("setup_log", "")
    if not isinstance(setup_log, str) or setup_log.startswith("/") or ".." in setup_log.split("/"):
        raise ValueError("%s: [launcher] setup_log must be a path relative to the home folder, not %r" % (source, setup_log))
    repo = launcher.setdefault("releases_repo", "")
    if not isinstance(repo, str) or (repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)):
        raise ValueError("%s: [launcher] releases_repo must be owner/name, not %r" % (source, repo))
    min_free = launcher.setdefault("min_free_mb", 0)
    if not isinstance(min_free, int) or min_free < 0:
        raise ValueError("%s: [launcher] min_free_mb must be a non-negative integer" % source)
    setup_dirs = cfg.setdefault("setup", {}).setdefault("required_dirs", [])
    if not isinstance(setup_dirs, list):
        raise ValueError("%s: [setup] required_dirs must be a list" % source)
    globals_path = game_dir / translate.get("globals", "globals.toml")
    with globals_path.open("rb") as fh:
        cfg["globals"] = tomllib.load(fh).get("globals", {})
    cfg["dir"] = game_dir
    cfg["source"] = str(source)
    # Developer inputs live beside game.toml: a game repository holds its own
    # ignored original/ and analysis/ directories.
    cfg["developer_exe_path"] = (game_dir / game["developer_exe"]).resolve()
    cfg["listings_path"] = (game_dir / translate.get("listings", "analysis")).resolve()
    # [translate] overrides: a header the generated sources include before they
    # define FN_<addr>, so a game can replace one translated function with a
    # native one (translate.py's RECOMP_OVERRIDE_HEADER). Absent by default,
    # and required to exist when named: a path that silently does not resolve
    # would leave the build looking replaced while running the original.
    overrides = translate.get("overrides")
    cfg["overrides_header"] = None
    if overrides is not None:
        path = (game_dir / overrides).resolve()
        if not path.is_file():
            raise ValueError("%s: [translate] overrides names no file: %s" % (source, path))
        cfg["overrides_header"] = path
    cfg["aux_modules"] = load_aux_modules(cfg, game_dir, source)
    return cfg


def load_aux_modules(cfg, game_dir, source):
    """[modules.aux.<key>]: a DLL the guest loads at run time (LoadLibrary) that
    the kit translates as a second image and maps at its preferred base, so
    its code runs as translated code and its exports answer GetProcAddress.
    Keys: name (the file name the guest asks for), path (developer copy,
    relative to game.toml), sha256, base and size (the PE's preferred base
    and SizeOfImage), listings (Ghidra export directory, relative), and
    function_alignment (default 4). [game] guest_size must reach past every
    module; the default arena is 0x10000000."""
    game = cfg["game"]
    guest_size = int(game.setdefault("guest_size", GUEST_SIZE_DEFAULT))
    if guest_size % 0x1000 or not GUEST_SIZE_DEFAULT <= guest_size <= 0x7ffff000:
        raise ValueError("%s: [game] guest_size %#x must be page aligned and at least %#x"
                         % (source, guest_size, GUEST_SIZE_DEFAULT))
    game["guest_size"] = guest_size
    modules = []
    for key, entry in sorted(cfg.get("modules", {}).get("aux", {}).items()):
        missing = [k for k in AUX_REQUIRED_KEYS if k not in entry]
        if missing:
            raise ValueError("%s: [modules.aux.%s] missing keys: %s" % (source, key, ", ".join(missing)))
        base, size = int(entry["base"]), int(entry["size"])
        if base % 0x1000 or size <= 0 or base + size > guest_size:
            raise ValueError("%s: [modules.aux.%s] base %#x size %#x must fit below guest_size %#x"
                             % (source, key, base, size, guest_size))
        alignment = entry.get("function_alignment", 4)
        if type(alignment) is not int or alignment <= 0:
            raise ValueError("%s: [modules.aux.%s] function_alignment must be a positive integer" % (source, key))
        entries = entry.get("entry_points", [])
        if not isinstance(entries, list) or any(type(a) is not int or not base <= a < base + size
                                               for a in entries):
            raise ValueError("%s: [modules.aux.%s] entry_points must be addresses inside the module"
                             % (source, key))
        modules.append({
            "key": key, "name": entry["name"], "sha256": entry["sha256"], "base": base, "size": size,
            "path": (game_dir / entry["path"]).resolve(),
            "listings_path": (game_dir / entry.get("listings", "analysis/" + entry["name"])).resolve(),
            "function_alignment": alignment,
            "entry_points": entries,
        })
    extra_base = game.setdefault("heap_extra_base", 0)
    extra_size = game.setdefault("heap_extra_size", 0)
    if (type(extra_base) is not int or type(extra_size) is not int
            or bool(extra_base) != bool(extra_size)
            or extra_base < 0 or extra_size < 0
            or (extra_base and (extra_base < GUEST_SIZE_DEFAULT or extra_base % 0x1000
                                or extra_size % 0x1000 or extra_base + extra_size > guest_size))):
        raise ValueError("%s: [game] heap_extra_base/heap_extra_size must define a page-aligned "
                         "range above the fixed runtime layout and inside guest_size" % source)
    for module in modules:
        if extra_size and extra_base < module["base"] + module["size"] and module["base"] < extra_base + extra_size:
            raise ValueError("%s: extra heap overlaps auxiliary module %s" % (source, module["key"]))
    return modules
