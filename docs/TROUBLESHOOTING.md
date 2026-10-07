# Troubleshooting

## Setup

**"Apple could not verify" when opening the Setup app** — it is not notarized by Apple (that needs a paid developer
account). Click **Done**, then **System Settings → Privacy & Security → Open Anyway**. You only need to do this once.
The game app it builds is made on your Mac and opens normally.

**The Setup app stopped with an error** — click **Show the log**; the last lines of `setup.log` say why. Building
again continues from the one-time analysis if that finished.

**"This speed.exe can't be used"** — the port is a translation of the PC Black Edition's `speed.exe` (version 1.3,
English), so it needs that exe from your game. Other versions and languages contain different code, and the app
would not work with them. The setup changed nothing.

**"game folder incomplete"** — point the setup at the folder that contains `speed.exe` together with `GLOBAL`,
`TRACKS`, `CARS`, `FRONTEND`, `MOVIES` and `SOUND`. A disc image or installer is not enough; it must be an installed
game folder.

**The analysis step is slow** — it runs once (a few minutes on a recent Mac, longer on older ones); later setups reuse it. If it was interrupted,
run the setup again: it starts the analysis over cleanly.

**"Failed to establish a new connection" or "No matching distribution found for capstone"** — the setup could not
reach the Python package index (pypi.org) to install its build tools. This is the network, not your game folder.
Check the connection, then look for anything that filters connections: a VPN, a proxy, a firewall app such as Little
Snitch or LuLu, or a content filter. Allow Python and *NFS Most Wanted Native Setup* to connect (or pause the filter)
and click **Build** again. To check, open https://pypi.org/simple/capstone/ in a browser: if it does not load, the
network is blocking it. Running `./setup.sh` from Terminal is another way round a filter that only blocks the app.

**"unable to load libxcrun ... need 'x86_64'" or "Process 'command 'xcrun'' finished with non-zero exit value 1"**
— the setup was run by an Intel-only Python (often an old Homebrew in `/usr/local`, copied over from an Intel Mac),
which runs under Rosetta; the Command Line Tools on macOS 27 have no Intel half. Update to the latest setup: it now
skips Intel Pythons (using its own Apple Silicon Python instead) and rebuilds the environment an earlier run made.
Then click **Build** again. Nothing needs to be uninstalled.

**Download of Ghidra or Java failed** — check the internet connection and run the setup again. If a download is
blocked on your network, `./setup.sh --help` shows how to point it at copies you downloaded yourself (they are
checked against the same hashes).

**Build errors** — make sure the Xcode Command Line Tools are installed (`xcode-select --install`) and up to date,
then run `./setup.sh` again. If it still fails, open an issue with the last 50 lines of `build/setup.log`.

## Playing

**Black screen or crash at start** — check `Logs/` in `~/Library/Application Support/NFS Most Wanted Native/`.
A line starting `mods: rejected` means a settings file was edited by hand and is invalid; remove the line or the
file, and the defaults return.

**Stutter on the first drive** — Metal prepares shaders the first time it sees each one. It settles after a few
minutes and does not come back on later launches.

**Low frame rate at 4K** — lower *Smooth edges* to *Off*, or set the game's resolution to 2560x1440 in its Video
menu. On a TV, the TV's own refresh setting matters: 60 Hz is right for this game.

**Controller does nothing** — connect it before starting a race, check it in System Settings → Game Controllers, and
make sure the F10 menu is closed (the game ignores the controller while it is open).

**A button change on the Controls screen comes back as "Button 1"** — that was 1.0.1 and earlier, which wrote its
own layout over the screen's changes. Update to the latest version and set the buttons again (see *Change the
buttons* in the companion guide).

**"Read Me First" on the disk image will not open** — some Macs refuse to open a text file from a disk image. The
same text is in the Setup app (**Read Me** button, top right), and the guides are on the project's GitHub page.

**No sound in 5.1, or voices from the wrong speakers** — use *Stereo* in the game's Audio Mode, or turn on *Dolby
Digital (for TVs and receivers)* in F10 → Mods → Audio. Some TV/soundbar HDMI chains scramble 6-channel PCM.

**Texture pack not used** — the log says why: look for `texture pack:`. The folder must be
`TRACKS/TexWizardX360/` in the game folder the setup was run on, and the setup must be run again after adding it.

**Sparks and light trails missing** — the log line starting `core.nfsmw: sparks and light trails` says why; usually
`XenonEffects.tpk` wasn't in the game folder's `scripts` folder when the setup ran (add it and build again).

**Light pools missing** — F10 → Graphics → *Light pools from lamps* must be on and the look must be *Definitive*;
the log line starting `core.nfsmw: light pools` says whether the setup built them.

## Reporting a bug

Open an issue with: your Mac model and macOS version, what you did, what you expected, and the log from `Logs/`.
Please do **not** attach game files or saves that contain game data.
