#!/usr/bin/env python3
"""bundle.sh --boot/--game selection: each game's app holds the chosen executable, guide and plist texts.

Number: count of wrong facts across the cases (executable Mach-O UUID against the file given, guide SHA-256 against the
game's guide, copyright line names the game, unknown --game exits 2). Correct script: 0. Broken control (a bundle.sh
copy that ignores --boot/--game): 4 wrong facts (Carbon executable, guide, copyright, unknown game accepted).
Needs the built runtime/boot and recomp/out/carbon/boot_carbon next to this checkout (exit 3 without them)."""
import hashlib, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MW = os.path.join(HERE, "boot")
CARBON = os.path.join(HERE, "..", "recomp", "out", "carbon", "boot_carbon")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def uuid(p):
    """The Mach-O build UUID: unchanged by the ad-hoc signature bundle.sh adds."""
    return subprocess.run(["dwarfdump", "--uuid", p], capture_output=True, text=True).stdout.split()[1]


def build(script, out, *args):
    return subprocess.run(["sh", script, out, "--name", "T", *args], cwd=os.path.dirname(script),
                          capture_output=True, text=True)


def wrong_facts(script):
    bad = []
    with tempfile.TemporaryDirectory() as t:
        for game, boot, guide, title in (("mw", MW, "guide.md", "Most Wanted"),
                                         ("carbon", CARBON, "guide_carbon.md", "Carbon")):
            out = os.path.join(t, game)
            os.mkdir(out)
            args = ["--boot", boot, "--game", game] if game == "carbon" else []
            r = build(script, out, *args)
            app = os.path.join(out, "T.app", "Contents")
            if r.returncode:
                bad += [game + " build failed: " + r.stderr[-200:]]
                continue
            if uuid(os.path.join(app, "MacOS", "NFSMW")) != uuid(boot):
                bad.append(game + " executable")
            if sha(os.path.join(app, "Resources", "Guide.md")) != sha(os.path.join(HERE, "docs", guide)):
                bad.append(game + " guide")
            if title not in open(os.path.join(app, "Info.plist")).read():
                bad.append(game + " copyright")
        r = build(script, t, "--game", "bogus")
        if r.returncode != 2:
            bad.append("unknown game exit %d" % r.returncode)
    return bad


def main():
    if not (os.path.exists(MW) and os.path.exists(CARBON)):
        print("bundle_game_test: built boot or boot_carbon missing; skipped")
        return 3
    script = os.path.join(HERE, "bundle.sh")
    bad = wrong_facts(script)
    # Control: the same script with the new selection removed must be caught.
    ctl = os.path.join(HERE, "bundle_ctl_tmp.sh")
    src = open(script).read()
    src = src.replace("--boot) BOOT=$2 ;;", "--boot) : ;;").replace("--game) GAME=$2 ;;", "--game) : ;;")
    try:
        open(ctl, "w").write(src)
        ctl_bad = wrong_facts(ctl)
    finally:
        os.unlink(ctl)
    print("wrong facts: script %d, control %d %s" % (len(bad), len(ctl_bad), ctl_bad))
    for b in bad:
        print("  FAIL", b)
    if bad or not ctl_bad:
        return 1
    print("bundle_game_test: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
