"""Hash -> name dictionary for Attrib keys (Attrib::StringHash32 = Jenkins lookup2 init 0xABCDEF00).
Sources: strings.tsv + functions.tsv of both decomps (whole strings and identifier tokens), every NUL-terminated
string in the vault buffers, the CARS directory names, and a hand list of well-known MW attribute names (each is
only used if its hash actually occurs, so a wrong guess cannot mislabel anything)."""
import os, re
from vaultlib import hash32

# Optional extra name sources (os.pathsep-separated): NFS_NAME_DECOMPS (folders with strings.tsv/functions.tsv) and
# NFS_NAME_CARDIRS (CARS folders). Names only label the TSV columns; the generated tables use hashes.
DECOMPS = [d for d in os.environ.get("NFS_NAME_DECOMPS", "").split(os.pathsep) if d]
CARDIRS = [d for d in os.environ.get("NFS_NAME_CARDIRS", "").split(os.pathsep) if d]

EXTRA = """
pvehicle engine transmission tires chassis brakes nos induction engineaudio aivehicle pursuitlevels pursuitsupport
pursuitescalation trafficpattern gameplay rigidbodyspecs smackable collisionreactions damagespecs camerainfo ecar
frontend presetride presetcar simsurface simsurfaces fe_attrib cars career race milestone milestones bounty rival
TORQUE MAX_RPM MIN_RPM RED_LINE IDLE GEAR_RATIO FINAL_GEAR GEAR_EFFICIENCY TORQUE_SPLIT SHIFT_SPEED CLUTCH_SLIP
OPTIMAL_SHIFT TYPE DIFFERENTIAL TOP_SPEED SPEED_LIMITER ENGINE_BRAKING FLYWHEEL_MASS DRAG_COEFFICIENT TRACTION_BOOST
BRAKES EBRAKE BRAKE_LOCK GRIP_SCALE DYNAMIC_GRIP STATIC_GRIP ASPECT_RATIO RIM_SIZE SECTION_WIDTH YAW_SPEED YAW_CONTROL
STEERING TIRE_OFFSETS MASS TENSOR_SCALE SHOCK_STIFFNESS SHOCK_EXT_STIFFNESS SHOCK_VALVING SHOCK_DIGRESSION
SPRING_STIFFNESS SPRING_PROGRESSION SWAYBAR_STIFFNESS TRAVEL RIDE_HEIGHT ROLL_CENTER FRONT_WEIGHT_BIAS SHOCK_BLOWOUT
AERO_CG AERO_COEFFICIENT FRONT_AXLE DRAG GROUND_EFFECT NOS_CAPACITY NOS_DISENGAGE TORQUE_BOOST RECHARGE_MIN_SPEED
RECHARGE_MAX_SPEED RECHARGE_MIN RECHARGE_MAX FLOW_RATE SPOOL SPOOL_TIME_UP SPOOL_TIME_DOWN PSI VACUUM HIGH_BOOST LOW_BOOST
HornType TrafficEngType Bounty CENTER_OF_GRAVITY HEADLIGHTS MODEL MANUFACTURER DEFAULT_BASE_PAINT DEFAULT_SKIN COP TRAFFIC UPGRADE engine_upgrades
""".split()


def _tokens(s):
    yield s
    for t in re.findall(r"[A-Za-z0-9_]+", s):
        yield t
    for t in s.split("::"):
        yield t


def build(extra_strings=()):
    cand = set(EXTRA)
    for d in DECOMPS:
        for fn, col in (("strings.tsv", 2), ("functions.tsv", 1)):
            p = os.path.join(d, fn)
            if not os.path.exists(p):
                continue
            for line in open(p, encoding="latin1"):
                parts = line.rstrip("\n").split("\t")
                if len(parts) > col:
                    for t in _tokens(parts[col].replace("\\\\", "\\")):
                        cand.add(t)
                        if t.startswith("Num_"):
                            cand.add(t[4:])
    for d in CARDIRS:
        if os.path.isdir(d):
            for n in os.listdir(d):
                cand.update((n, n.lower(), n.upper()))
    for s in extra_strings:
        cand.add(s)
        for t in _tokens(s):
            cand.add(t)
    more = set()
    for c in cand:
        more.add(c.lower()); more.add(c.upper())
    cand |= more
    out = {}
    for c in cand:
        if c and len(c) < 128:
            out.setdefault(hash32(c), c)
    return out
